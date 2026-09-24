"""上游自检巡检：周期性核对「调度器认为在跑的任务」与「真实上游」是否一致。

为什么需要它：调度器的状态全在内存，任务卡住时外部只能看到一个越来越大的
`run_seconds`（例如「任务跑了 5000 秒」）。真正的判据必须来自上游：
- `GET /queue`：prompt 在不在上游手里（待执行 / 执行中）
- `GET /history/{pid}`：是早就算完了、还是被丢弃了
- `WS /ws` 事件：上游到底有没有在往前算（进度心跳）——只有它能区分「正在算」与「卡死」
- `GET /system_stats`：上游显存水位
- 本站 DB 历史耗时：判断「这次是不是明显超时了」

巡检发现的异常只做两件事：写日志、标在任务/面板上（`anomaly`）；**唯一会主动处置的是
「上游已把 prompt 丢弃」**（终结任务并释放单飞槽位），因为此时任务已不可能成功，
继续占着槽位会把整条流水线堵死。其余一律只告警，不杀任务（避免误杀长时间的合法出图）。
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger('middle_station.inspector')

LEVEL_ORDER = {'ok': 0, 'info': 1, 'warn': 2, 'error': 3}


class UpstreamInspector:
    def __init__(self, cfg, scheduler, comfy, storage, tts=None, monitor=None):
        ins = cfg.inspector
        self.enabled = ins.enabled
        self.interval = max(1.0, float(ins.interval))
        self.slow_factor = float(ins.slow_factor)
        self.slow_min = float(ins.slow_min_seconds)
        self.stall_seconds = float(ins.stall_seconds)
        self.history_hours = float(ins.history_hours)
        self.baseline_ttl = float(ins.baseline_ttl)
        self.system_stats_every = max(1, int(ins.system_stats_every))
        self.lost_grace = max(0.0, float(getattr(cfg.comfyui, 'watch_lost_grace', 30.0)))
        self.lost_confirm = max(2, int(getattr(cfg.comfyui, 'watch_lost_confirm', 2)))
        self.vram_min_free_gb = float(cfg.monitoring.vram_min_free_gb)
        self.slot_wait_warn = max(300.0, self.slow_min * 10)

        self._scheduler = scheduler
        self._comfy = comfy
        self._storage = storage
        self._tts = tts
        self._monitor = monitor

        self.anomalies: Dict[str, Dict] = {}      # key -> {level, code, msg, ts, task_id?}
        self.last_probe: Dict = {}
        self.last_run: float = 0.0
        self.cycles: int = 0
        self._missing: Dict[str, int] = {}        # real_pid -> 连续「上游查无此 prompt」次数
        self._sticky: Dict[str, float] = {}       # 异常 key -> 保持展示到该时间戳
        self._baseline: Dict[str, Dict] = {}
        self._baseline_at = 0.0
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()   # 串行化巡检，避免周期巡检与手动触发（/self-check/run）并发

    # ---------- 生命周期 ----------

    def start(self):
        if not self.enabled:
            logger.info('inspector disabled by config')
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info('inspector started (interval=%.0fs, 每 %.0fs 核对上游 /queue + /ws 进度)',
                        self.interval, self.interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self):
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception('inspector cycle failed: %s', e)
            await asyncio.sleep(self.interval)

    # ---------- 查询 / 对外 ----------

    def summary(self) -> Dict:
        items = sorted(self.anomalies.values(), key=lambda a: -LEVEL_ORDER.get(a['level'], 0))
        level = 'ok'
        for a in items:
            if LEVEL_ORDER.get(a['level'], 0) > LEVEL_ORDER.get(level, 0):
                level = a['level']
        return {
            'level': level,
            'count': len(items),
            'items': items,
            'last_run': self.last_run,
            'cycles': self.cycles,
            'probe': self.last_probe,
            'progress_stream': (self._comfy.progress_stream.snapshot()
                                if getattr(self._comfy, 'progress_stream', None) else None),
        }

    # ---------- 异常表维护 ----------

    def _set(self, key: str, level: str, code: str, msg: str,
             task_id: Optional[str] = None, sticky: float = 0.0):
        old = self.anomalies.get(key)
        entry = {'key': key, 'level': level, 'code': code, 'msg': msg,
                 'task_id': task_id, 'ts': time.time()}
        self.anomalies[key] = entry
        if sticky > 0:
            self._sticky[key] = time.time() + sticky
        # 只在「新增 / 等级或类型变化」时打日志，避免每轮巡检刷屏
        if old is None or old.get('code') != code or old.get('level') != level:
            log = {'error': logger.error, 'warn': logger.warning,
                   'info': logger.info}.get(level, logger.info)
            log('[self-check] %s%s: %s', code, f' ({task_id})' if task_id else '', msg)

    def _clear(self, key: str):
        old = self.anomalies.pop(key, None)
        self._sticky.pop(key, None)
        if old is not None:
            logger.info('[self-check] 恢复正常: %s (%s)', old.get('code'), old.get('msg'))

    def _prune(self, keep: set):
        now = time.time()
        for key in list(self.anomalies):
            if key in keep:
                continue
            if self._sticky.get(key, 0.0) > now:
                continue       # 粘性提示（如「上游已丢弃」）留一会儿再撤
            self._clear(key)

    def _worst_for(self, *keys) -> Optional[Dict]:
        best: Optional[Dict] = None
        for key in keys:
            a = self.anomalies.get(key)
            if a and (best is None or LEVEL_ORDER.get(a['level'], 0) > LEVEL_ORDER.get(best['level'], 0)):
                best = a
        if not best:
            return None
        return {'level': best['level'], 'code': best['code'], 'msg': best['msg'], 'ts': best['ts']}

    async def _baseline_for(self, task_type: str) -> Dict:
        now = time.time()
        if now - self._baseline_at > self.baseline_ttl or not self._baseline:
            self._baseline = await self._storage.duration_baseline(hours=self.history_hours)
            self._baseline_at = now
        return self._baseline.get(task_type) or {}

    # ---------- 巡检主流程 ----------

    async def check_once(self) -> Dict:
        async with self._lock:
            return await self._check_cycle()

    async def _check_cycle(self) -> Dict:
        now = time.time()
        self.cycles += 1
        self.last_run = now
        keep: set = set()

        probe = await self._comfy.upstream_probe(
            system_stats=(self.cycles % self.system_stats_every == 1))
        # 显存每 N 轮才查一次，其余轮次沿用上次的值
        if probe.get('vram_free_gb') is None:
            probe['vram_free_gb'] = self.last_probe.get('vram_free_gb')
            probe['vram_total_gb'] = self.last_probe.get('vram_total_gb')
        self.last_probe = {
            'reachable': probe['reachable'],
            'running': sorted(probe['running']),
            'pending': sorted(probe['pending']),
            'vram_free_gb': probe.get('vram_free_gb'),
            'vram_total_gb': probe.get('vram_total_gb'),
        }
        watching = self._comfy.watching()

        # ① 上游不可达：一切「丢失」判定都不可信，只报不可达
        if not probe['reachable']:
            key = 'upstream'
            keep.add(key)
            self._set(key, 'error', 'upstream_unreachable',
                      '真实 ComfyUI 的 /queue 不可达（进程是否已退出？），无法核对任务状态')
        elif self.last_probe.get('vram_free_gb') is not None \
                and self.last_probe['vram_free_gb'] < self.vram_min_free_gb:
            key = 'resource:vram'
            keep.add(key)
            self._set(key, 'warn', 'upstream_vram_low',
                      f'上游显存仅剩 {self.last_probe["vram_free_gb"]}GB '
                      f'(< {self.vram_min_free_gb}GB)，出图/语音可能 OOM')

        # ② 逐任务核对（每条规则一个独立 key，互不覆盖；任务上取最严重的一条）
        for real_pid, info in watching.items():
            task = info['task']
            prog = info.get('progress') or {}
            started = task.started_at or task.created_at
            run_s = max(0.0, now - started)
            in_running = real_pid in probe['running']
            in_queue = in_running or real_pid in probe['pending']
            k_lost = f'task:{task.task_id}:lost'
            k_slow = f'task:{task.task_id}:slow'
            k_stall = f'task:{task.task_id}:stalled'
            stream = getattr(self._comfy, 'progress_stream', None)

            # 规则一：上游已丢弃该 prompt（不在队列、不在历史）→ 终结任务，释放单飞槽位
            confirmed_lost = False
            if probe['reachable'] and not in_queue and run_s >= self.lost_grace:
                entry, ok = await self._comfy.history_entry(real_pid)
                if ok and entry is None:
                    self._missing[real_pid] = self._missing.get(real_pid, 0) + 1
                    confirmed_lost = self._missing[real_pid] >= self.lost_confirm
                else:
                    self._missing.pop(real_pid, None)   # 已出图或查询失败，不算丢失
            else:
                self._missing.pop(real_pid, None)
            if confirmed_lost:
                self._comfy.abort_task(
                    task, 500, 'upstream lost the prompt (detected by inspector), please retry')
                self._set(k_lost, 'error', 'lost',
                          f'上游已丢弃该 prompt（不在 /queue 也不在 /history，'
                          f'可能重启/OOM 崩溃），已终止任务并释放槽位；运行 {run_s:.0f}s',
                          task.task_id, sticky=120.0)
                keep.add(k_lost)      # 任务已离开 watching，靠 sticky 再展示一会儿
            else:
                self._clear(k_lost)

            # 规则二：耗时超基线（只告警，不杀）
            base = await self._baseline_for(task.task_type)
            limit = max(self.slow_min, (base.get('p95') or 0.0) * self.slow_factor)
            if run_s > limit:
                keep.add(k_slow)
                where = '上游执行中' if in_running else ('上游排队中' if in_queue else '上游状态未知')
                self._set(k_slow, 'warn', 'slow',
                          f'耗时 {run_s:.0f}s 超过基线阈值 {limit:.0f}s'
                          f'（同类成功任务 P95 {base.get("p95") or 0:.0f}s × {self.slow_factor:g}，'
                          f'样本 {base.get("n") or 0}）；{where}', task.task_id)
            else:
                self._clear(k_slow)

            # 规则三：上游在跑但长时间没有任何进度事件（只有 /ws 在线时这个判据才有效）
            last_event = max(prog.get('ts') or 0.0, started)
            if stream is not None and stream.connected and in_running \
                    and run_s > self.stall_seconds and now - last_event > self.stall_seconds:
                keep.add(k_stall)
                self._set(k_stall, 'warn', 'stalled',
                          f'上游在跑但已 {now - last_event:.0f}s 没有进度事件（疑似卡住）；'
                          f'累计运行 {run_s:.0f}s', task.task_id)
            else:
                self._clear(k_stall)

            self._scheduler.set_task_anomaly(task, self._worst_for(k_lost, k_slow, k_stall))

        # ③ 上游有执行/排队中的 prompt，但不是本站已知任务（ComfyUI 网页端或重启前的遗留）
        ours = set(watching.keys())
        ext_running = probe['running'] - ours
        ext_pending = probe['pending'] - ours
        if probe['reachable'] and (ext_running or ext_pending):
            key = 'upstream:external'
            keep.add(key)
            self._set(key, 'info', 'upstream_external',
                      f'上游有 {len(ext_running)} 个执行中 / {len(ext_pending)} 个排队的'
                      f'非本站已知任务（网页端提交或重启前遗留），本站任务需等它让出资源')

        # ④ 等单飞槽位过久（正常应远小于此）
        for task in list(self._scheduler.tasks.values()):
            if task.status != 'waiting':
                continue
            waited = max(0.0, now - (task.started_at or task.created_at))
            if waited > self.slot_wait_warn:
                key = f'task:{task.task_id}:slot_wait'
                keep.add(key)
                self._set(key, 'warn', 'slot_wait',
                          f'等待单飞槽位 {waited:.0f}s（可能上一个任务卡住，'
                          f'看它的异常标记）', task.task_id)
                self._scheduler.set_task_anomaly(task, self._worst_for(key))

        # ⑤ 语音后端就绪性（CosyVoice 未加载时语音会持续 503）
        if self._tts is not None and not getattr(self._tts, 'model_loaded', True):
            key = 'tts'
            keep.add(key)
            self._set(key, 'warn', 'tts_not_ready',
                      f'CosyVoice 后端未就绪（{getattr(self._tts, "base_url", "?")}），语音任务会 503')

        # 清理已恢复的异常（含任务已终结的）
        self._prune(keep)
        # 任务上已无对应异常时撤掉标记（粘性异常仍保留展示）
        for task in list(self._scheduler.tasks.values()):
            if not task.anomaly:
                continue
            keys = [f'task:{task.task_id}:{s}' for s in ('lost', 'slow', 'stalled', 'slot_wait')]
            if self._worst_for(*keys) is None:
                self._scheduler.set_task_anomaly(task, None)
        for real_pid in list(self._missing):
            if real_pid not in watching:
                self._missing.pop(real_pid, None)
        return self.anomalies
