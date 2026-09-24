"""ComfyUI 适配层：/prompt 单飞调度 + 只读接口透传。

对应 docs/comfyui-backend-api.md：
- POST /prompt：核心调度点。同一时刻只向真实 ComfyUI 放行 N 个任务（N=serialize_concurrent，
  建议 1 = 单飞），其余在中转站内阻塞排队；提交成功后**立即**返回 prompt_id。
- GET /history/{prompt_id}：排队中（尚未提交真实后端）返回空 {}，避免插件误判完成。
- POST /upload/image：透传（multipart 原样转发，保证 name 落到真实 ComfyUI input 目录）。
- GET /history、GET /view：透传。
- 槽位占用覆盖任务完整生命周期（提交 -> 执行 -> 出图），由 _watch 轮询释放。
- 任务状态跟随真实出图进度：提交后任务保持 running，后台 _watch 轮询真实 /history，
  出图完成才由 scheduler.confirm_task() 置为 done；超时/执行失败置 failed。
- 槽位一定会被释放：_watch 有三重兜底 —— ①出图完成；②硬超时 watch_timeout；
  ③「丢失检测」：prompt 既不在真实 /queue 也不在 /history（例如真实 ComfyUI 中途
  重启/OOM 崩溃，prompt 被丢弃），grace 期满后连续确认即判失败。
  没有这条兜底时，被丢弃的 prompt 会让 _watch 永久轮询，单飞槽位被永久占用，
  后续所有任务（含 TTS）全部排队饿死 —— 这就是「任务跑了 5000+ 秒」的根因。
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Optional, Tuple

import httpx

from scheduler import BackendError, Task

from .comfy_ws import ComfyEventStream

logger = logging.getLogger('middle_station.comfyui')


class ComfyAdapter:
    def __init__(self, cfg, scheduler=None):
        self.base_url = cfg.comfyui.base_url.rstrip('/')
        self.watch_interval = cfg.comfyui.watch_interval
        self.watch_timeout = cfg.comfyui.watch_timeout
        self.watch_lost_grace = max(0.0, float(getattr(cfg.comfyui, 'watch_lost_grace', 30.0)))
        self.watch_lost_confirm = max(1, int(getattr(cfg.comfyui, 'watch_lost_confirm', 2)))
        # 提交给真实 ComfyUI 的 client_id：进度事件只发给「提交该 prompt 的 client」
        # （ComfyUI send_sync 带 sid，sid 不在线就丢弃），所以由中转站统一持有并订阅。
        self.client_id = getattr(cfg.comfyui, 'client_id', None) or f'taskhub-{uuid.uuid4().hex[:8]}'
        self._scheduler = scheduler
        self.sem = asyncio.Semaphore(cfg.comfyui.serialize_concurrent)
        self.states: Dict[str, str] = {}      # 虚拟prompt_id -> queued/running/done/failed
        self.task_ids: Dict[str, str] = {}    # 虚拟prompt_id -> task_id
        self.pid_map: Dict[str, str] = {}     # 虚拟prompt_id -> 真实ComfyUI prompt_id
        self._watchers: Dict[str, Task] = {}  # 虚拟prompt_id -> task（用于回写状态）
        # 失败任务的合成 history 条目：插件轮询 /history/{虚拟pid} 时立刻拿到明确失败，
        # 而不是永远空 {}（否则插件要一直轮询到自己的超时）。
        self.failed_entries: Dict[str, Dict] = {}
        # 真实进度（订阅 /ws 得到）：real_pid -> {nodes_done, nodes_total, node, step, step_total, ts, state}
        self.progress: Dict[str, Dict] = {}
        self._tasks_by_real: Dict[str, Task] = {}
        self._vpids_by_real: Dict[str, str] = {}
        self._watch_tasks: Dict[str, asyncio.Task] = {}
        self._push_at: Dict[str, float] = {}
        self._flush_tasks: Dict[str, asyncio.Task] = {}
        self.push_interval = 1.0    # 进度推送节流窗口（秒）
        self.progress_stream: Optional[ComfyEventStream] = None
        if getattr(cfg.comfyui, 'progress_stream', True):
            self.progress_stream = ComfyEventStream(
                self.base_url, self.client_id, self._on_event)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0, read=cfg.server.infer_timeout + 30.0,
                write=30.0, pool=10.0))

    def start(self):
        """订阅真实 ComfyUI 的进度事件（断线自动重连）。"""
        if self.progress_stream is not None:
            self.progress_stream.start()

    async def close(self):
        if self.progress_stream is not None:
            await self.progress_stream.stop()
        # 停掉所有 _watch（否则关闭时会因 client 已释放而刷 "watch failed" 日志）
        for wt in list(self._watch_tasks.values()):
            wt.cancel()
        for ft in list(self._flush_tasks.values()):
            ft.cancel()
        self._watch_tasks.clear()
        self._flush_tasks.clear()
        await self._client.aclose()

    # ---------- 真实进度（/ws 事件） ----------

    async def _on_event(self, etype: str, data: Dict):
        """把真实 ComfyUI 的事件折算成任务进度，节流后推给调度器（UI 实时展示）。"""
        pid = data.get('prompt_id')
        if not pid:
            return
        prog = self.progress.get(pid)
        if prog is None:
            if etype not in ('execution_start', 'executing', 'progress', 'progress_state', 'executed'):
                return
            prog = self.progress[pid] = {
                'nodes_done': 0, 'nodes_total': self._nodes_total(pid), 'nodes_cached': 0,
                'node': None, 'step': None, 'step_total': None,
                'state': 'running', 'ts': time.time(), 'started': time.time(),
            }
        prog['ts'] = time.time()
        if etype == 'execution_cached':
            cached = data.get('nodes') or []
            prog['nodes_cached'] = len(cached)
            prog['nodes_done'] = max(prog['nodes_done'], len(cached))
        elif etype == 'executing':
            node = data.get('node')
            if node is None:
                prog['state'] = 'finishing'
            else:
                prog['node'] = str(node)
                if prog['state'] != 'running':
                    prog['state'] = 'running'
        elif etype == 'executed':
            prog['nodes_done'] = max(prog['nodes_done'], prog['nodes_done'] + 1)
            prog['step'] = prog['step_total'] = None
        elif etype == 'progress':
            prog['node'] = str(data.get('node') or prog.get('node') or '')
            prog['step'] = data.get('value')
            prog['step_total'] = data.get('max')
        elif etype == 'progress_state':
            self._merge_progress_state(prog, data.get('nodes') or {})
        elif etype == 'execution_error':
            prog['state'] = 'error'
        elif etype == 'execution_start':
            prog['state'] = 'running'
        self._push_progress(pid)

    @staticmethod
    def _merge_progress_state(prog: Dict, nodes: Dict):
        """合并 progress_state（ComfyUI ≥0.3 的节点级进度）。"""
        done = 0
        best = None
        for node_id, st in nodes.items():
            state = str((st or {}).get('state') or '')
            if state in ('success', 'finished', 'error'):
                done += 1
            if st and (st.get('max') or 0):
                best = (node_id, st.get('value'), st.get('max'), state)
        prog['nodes_done'] = max(prog['nodes_done'], done)
        if best:
            prog['node'] = str(best[0])
            prog['step'] = best[1]
            prog['step_total'] = best[2]

    def _nodes_total(self, real_pid: str) -> int:
        task = self._tasks_by_real.get(real_pid)
        prompt = ((task.payload or {}) if task else {}).get('prompt')
        return len(prompt) if isinstance(prompt, dict) else 0

    def _push_progress(self, real_pid: str, force: bool = False):
        task = self._tasks_by_real.get(real_pid)
        if task is None or self._scheduler is None:
            return
        prog = self.progress.get(real_pid) or {}
        now = time.time()
        last = self._push_at.get(real_pid, 0.0)
        node_changed = getattr(task, 'progress', {}).get('node') != prog.get('node')
        if not force and not node_changed and now - last < self.push_interval:
            # 节流：进度事件很密，最多 push_interval 推一次（换节点时立即推）。
            # 但要补一次末尾推送，否则「最后一步之后事件停了」会让 UI 永远停在旧值。
            self._schedule_flush(real_pid, self.push_interval - (now - last))
            return
        self._push_at[real_pid] = now
        self._scheduler.set_task_progress(task, {
            'nodes_done': prog.get('nodes_done', 0),
            'nodes_total': prog.get('nodes_total', 0),
            'node': prog.get('node'),
            'step': prog.get('step'),
            'step_total': prog.get('step_total'),
            'state': prog.get('state', 'running'),
            'updated_at': round(now, 3),
        })

    def _schedule_flush(self, real_pid: str, delay: float):
        """节流窗口结束时补推一次进度（合并密集事件，又不会丢最后一步）。"""
        existing = self._flush_tasks.get(real_pid)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._flush_later(real_pid, delay))
        self._flush_tasks[real_pid] = task

    async def _flush_later(self, real_pid: str, delay: float):
        try:
            await asyncio.sleep(max(0.05, delay))
            self._push_progress(real_pid, force=True)
        except asyncio.CancelledError:
            raise
        finally:
            if self._flush_tasks.get(real_pid) is asyncio.current_task():
                self._flush_tasks.pop(real_pid, None)

    # ---------- 核心：提交任务（单飞调度） ----------

    async def submit_prompt(self, task: Task):
        """worker 执行体：等待单飞槽位 -> 转发 -> 立即返回 {prompt_id}。

        单飞槽位覆盖任务完整生命周期（提交 -> 执行 -> 出图）：
        - 在 acquire 前标记任务「等待中」（waiting）；
        - 拿到槽位后标记「运行中」并提交；
        - 槽位由后台 _watch 在出图完成/失败/超时后 release，期间后续任务阻塞等待，
          保证同一时刻真实 ComfyUI 只跑一个任务。
        """
        payload = task.payload
        virtual_pid = payload.get('_virtual_pid') or task.task_id
        # 等待单飞槽位：标记「等待中」，让任务列表清晰展示排队（而非显示成运行中）
        if self._scheduler is not None:
            self._scheduler.set_task_status(task, 'waiting')
        await self.sem.acquire()
        if self._scheduler is not None:
            self._scheduler.set_task_status(task, 'running')
        try:
            data = await self._submit_upstream(payload)
        except BaseException:
            # 提交阶段任何失败（含被取消）都必须归还槽位，否则单飞槽位永久泄漏
            self.sem.release()
            raise
        real_pid = data['prompt_id']
        # 建立 虚拟pid -> 真实pid 映射：插件用虚拟 pid 轮询 /history，中转站负责透传
        self.states[virtual_pid] = 'running'
        self.task_ids[virtual_pid] = task.task_id
        self._watchers[virtual_pid] = task
        self.pid_map[virtual_pid] = real_pid
        self._tasks_by_real[real_pid] = task
        self._vpids_by_real[real_pid] = virtual_pid
        self.progress[real_pid] = {
            'nodes_done': 0, 'nodes_total': self._nodes_total(real_pid) or len(payload.get('prompt') or {}),
            'nodes_cached': 0, 'node': None, 'step': None, 'step_total': None,
            'state': 'queued', 'ts': time.time(), 'started': time.time(),
        }
        self.failed_entries.pop(virtual_pid, None)
        # 提交成功：任务保持 running（pending_confirm），
        # 等待 _watch 轮询真实出图结果后由 confirm_task() 终结；槽位由 _watch 释放。
        task.pending_confirm = True
        self._watch_tasks[virtual_pid] = asyncio.create_task(
            self._watch(virtual_pid, real_pid, self.sem))
        return data   # 真实 pid 仅在内部使用，对外返回虚拟 pid

    def _forward_payload(self, payload: dict) -> dict:
        """转发给真实 ComfyUI 的请求体：剥掉中转站私有字段，统一 client_id。

        统一 client_id 的原因见 comfy_ws.py：ComfyUI 的 executing/progress 事件只发给
        「提交该 prompt 的 client」，中转站必须用同一个 id 建 WS 才收得到进度。
        """
        body = {k: v for k, v in (payload or {}).items() if not str(k).startswith('_')}
        orig = body.get('client_id')
        if orig and orig != self.client_id:
            body['_client_id_original'] = orig
        body['client_id'] = self.client_id
        return body

    async def _submit_upstream(self, payload: dict) -> dict:
        """转发 POST /prompt；失败映射为 BackendError（状态码原样透传上游）。"""
        body = self._forward_payload(payload)
        try:
            resp = await self._client.post(f'{self.base_url}/prompt', json=body)
        except httpx.TimeoutException as e:
            raise BackendError(504, f'upstream submit timeout: {e}') from e
        except httpx.RequestError as e:
            raise BackendError(500, f'upstream unreachable: {e}') from e
        if resp.status_code != 200:
            # 上游校验失败（如 400），原样回传，插件依赖状态码/文案展示
            raise BackendError(resp.status_code,
                               resp.text[:300] or f'upstream http {resp.status_code}')
        try:
            data = resp.json()
        except ValueError as e:
            raise BackendError(500, f'bad upstream json: {e}') from e
        if not isinstance(data, dict) or not data.get('prompt_id'):
            raise BackendError(500, 'upstream returned no prompt_id')
        return data

    def _finish(self, prompt_id: str, status: str, code: int, error: str = ''):
        """出图终结：更新本地状态表，并把结果回写到调度器任务。"""
        self.states[prompt_id] = status
        if status != 'done':
            # 让插件轮询 /history/{虚拟pid} 时立刻看到终态失败，不再空转等待
            self.failed_entries[prompt_id] = {
                'prompt': [],
                'outputs': {},
                'status': {
                    'status_str': 'error',
                    'completed': False,
                    'messages': [['execution_error', {
                        'prompt_id': prompt_id,
                        'exception_message': f'taskhub: {error or status}',
                    }]],
                },
                'meta': {},
            }
        else:
            self._push_progress(prompt_id, force=True)
        task = self._watchers.pop(prompt_id, None)
        # 终结即停掉该任务的 _watch（它负责归还单飞槽位），避免槽位被继续占住
        watch_task = self._watch_tasks.pop(prompt_id, None)
        if watch_task is not None and watch_task is not asyncio.current_task():
            watch_task.cancel()
        real_pid = self.pid_map.get(prompt_id)
        if real_pid:
            self._tasks_by_real.pop(real_pid, None)
            self._push_at.pop(real_pid, None)
            flush = self._flush_tasks.pop(real_pid, None)
            if flush is not None:
                flush.cancel()
        if task is not None and self._scheduler is not None:
            self._scheduler.confirm_task(task, status, code, error)

    def abort_task(self, task: Task, code: int = 500, error: str = 'aborted by inspector') -> bool:
        """外部判定任务已不可救（如自检发现上游把 prompt 丢了）时终结它并释放槽位。"""
        virtual_pid = ((task.payload or {}).get('_virtual_pid')) or task.task_id
        if virtual_pid not in self._watchers:
            return False
        self._finish(virtual_pid, 'failed', code, error)
        return True

    async def history_entry(self, real_pid: str):
        """查真实 /history/{real_pid}。返回 (entry|None, 查询是否成功)。

        entry 为 None 表示上游还没有该 prompt 的结果（可能仍在执行，也可能已被丢弃）。
        """
        try:
            r = await self._client.get(f'{self.base_url}/history/{real_pid}')
        except httpx.RequestError:
            return None, False
        if r.status_code != 200:
            return None, False
        try:
            hist = r.json()
        except ValueError:
            return None, False
        if not isinstance(hist, dict):
            return None, True
        return hist.get(real_pid), True

    async def _in_upstream_queue(self, real_pid: str):
        """查真实 /queue：返回 (是否在队列中(待执行/执行中), 查询是否成功)。

        中转站内部使用；插件本身不调用 /queue（见 docs/comfyui-backend-api.md §1）。
        """
        probe = await self.upstream_probe()
        if not probe['reachable']:
            return True, False       # 查询失败时保守认为「还在队列」，避免误判
        return (real_pid in probe['running'] or real_pid in probe['pending']), True

    async def upstream_probe(self, system_stats: bool = False,
                             timeout: float = 5.0) -> Dict:
        """探测真实上游：/queue 里有哪些 prompt（自检与丢失判定共用）。

        返回 {'reachable', 'running', 'pending', 'vram_free_gb', 'vram_total_gb'}，
        reachable=False 表示上游 /queue 不可达（此时不应做「丢失」判定）。
        """
        out: Dict = {
            'reachable': False,
            'running': set(),
            'pending': set(),
            'vram_free_gb': None,
            'vram_total_gb': None,
        }
        try:
            r = await self._client.get(f'{self.base_url}/queue', timeout=timeout)
        except httpx.RequestError:
            return out
        if r.status_code != 200:
            return out
        try:
            data = r.json()
        except ValueError:
            return out
        if not isinstance(data, dict):
            return out
        out['reachable'] = True
        for key, bucket in (('queue_running', 'running'), ('queue_pending', 'pending')):
            for item in data.get(key) or []:
                pid = item.get('prompt_id') if isinstance(item, dict) else (
                    item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None)
                if pid:
                    out[bucket].add(pid)
        if system_stats:
            try:
                rs = await self._client.get(f'{self.base_url}/system_stats', timeout=timeout + 3.0)
                dev = ((rs.json() or {}).get('devices') or [{}])[0] if rs.status_code == 200 else {}
                if dev.get('vram_total'):
                    out['vram_total_gb'] = round(dev['vram_total'] / (1024 ** 3), 2)
                    out['vram_free_gb'] = round((dev.get('vram_free') or 0) / (1024 ** 3), 2)
            except (httpx.RequestError, ValueError):
                pass
        return out

    def watching(self) -> Dict[str, Dict]:
        """当前被中转站跟踪的所有真实 prompt（real_pid -> 进度/任务信息），供自检核对。"""
        out: Dict[str, Dict] = {}
        for real_pid, task in self._tasks_by_real.items():
            out[real_pid] = {
                'task_id': task.task_id,
                'task': task,
                'virtual_pid': self._vpids_by_real.get(real_pid),
                'progress': self.progress.get(real_pid) or {},
            }
        return out

    async def _watch(self, virtual_pid: str, real_pid: str,
                     sem: Optional[asyncio.Semaphore] = None):
        """轮询真实 /history 直至 real_pid 出图；结果回写到虚拟 pid；超时/丢失/异常标记 failed。

        sem 在任务终结时释放（单飞槽位覆盖完整生命周期）。三条终结路径保证槽位必然释放：
        1) 出图完成 -> done / execution error -> failed；
        2) 硬超时 watch_timeout（0 = 不启用）-> failed；
        3) 丢失检测：过了 watch_lost_grace 后，连续 watch_lost_confirm 次既不在真实
           /queue 也不在真实 /history，说明上游把 prompt 丢了（重启/崩溃）-> failed。
        """
        started = time.time()
        deadline = (started + self.watch_timeout) if (self.watch_timeout and self.watch_timeout > 0) else None
        lost_check_at = started + self.watch_lost_grace
        missing = 0
        try:
            while True:
                if deadline is not None and time.time() >= deadline:
                    logger.error('comfyui task %s watch timeout after %.0fs',
                                 real_pid, time.time() - started)
                    self._finish(virtual_pid, 'failed', 504, 'draw timeout, no result')
                    return
                await asyncio.sleep(self.watch_interval)
                entry, ok = await self.history_entry(real_pid)
                if entry is not None:
                    status_str = (entry.get('status') or {}).get('status_str', 'success')
                    if status_str == 'error':
                        logger.error('comfyui task %s execution error', real_pid)
                        self._finish(virtual_pid, 'failed', 500, 'comfyui execution error')
                    else:
                        self._finish(virtual_pid, 'done', 200, '')
                    return
                if ok and time.time() >= lost_check_at:
                    present, queue_ok = await self._in_upstream_queue(real_pid)
                    if queue_ok:
                        missing = 0 if present else missing + 1
                        if missing >= self.watch_lost_confirm:
                            logger.error(
                                'comfyui prompt %s lost: not in /queue nor /history '
                                '(upstream restarted or dropped it), releasing slot', real_pid)
                            self._finish(virtual_pid, 'failed', 500,
                                         'upstream lost the prompt (comfyui restarted?), please retry')
                            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception('comfyui watch failed for %s', real_pid)
            self._finish(virtual_pid, 'failed', 500, str(e))
        finally:
            if sem is not None:
                sem.release()

    # ---------- 只读 / 上传透传 ----------

    async def history_one(self, prompt_id: str) -> Tuple[int, bytes, Optional[str]]:
        """GET /history/{prompt_id}。

        支持中转站生成的虚拟 pid：排队中（未提交）返回空 {}；已提交则映射到真实
        pid 透传，并把结果键替换回虚拟 pid（插件用 prompt_id in hist 判断完成）；
        失败/超时/丢失的任务返回合成错误条目，让插件立刻结束轮询。
        非虚拟 pid（如真实 ComfyUI 直连场景）直接透传真实后端。
        """
        if self.states.get(prompt_id) == 'queued':
            return 200, b'{}', 'application/json'
        failed = self.failed_entries.get(prompt_id)
        if failed is not None:
            # 任务已失败/超时/丢失：返回合成结果，插件据此立即判定失败而不是空转轮询
            return 200, json.dumps({prompt_id: failed}).encode(), 'application/json'
        real = self.pid_map.get(prompt_id)
        if real:
            status, content, ctype = await self._forward('GET', f'/history/{real}')
            if status == 200:
                try:
                    hist = json.loads(content)
                    if real in hist:
                        content = json.dumps({prompt_id: hist[real]}).encode()
                except Exception:  # noqa: BLE001
                    pass
            return status, content, ctype
        return await self._forward('GET', f'/history/{prompt_id}')

    async def history_all(self, query: str = '') -> Tuple[int, bytes, Optional[str]]:
        return await self._forward('GET', '/history' + (f'?{query}' if query else ''))

    async def view(self, query: str = '') -> Tuple[int, bytes, Optional[str]]:
        return await self._forward('GET', '/view' + (f'?{query}' if query else ''))

    async def upload(self, body: bytes, content_type: str) -> Tuple[int, bytes, Optional[str]]:
        return await self._forward(
            'POST', '/upload/image', content=body,
            headers={'Content-Type': content_type})

    async def _forward(self, method: str, path: str, **kw) -> Tuple[int, bytes, Optional[str]]:
        try:
            resp = await self._client.request(method, f'{self.base_url}{path}', **kw)
        except httpx.TimeoutException as e:
            raise BackendError(504, f'upstream timeout: {e}') from e
        except httpx.RequestError as e:
            raise BackendError(500, f'upstream unreachable: {e}') from e
        ctype = resp.headers.get('content-type')
        return resp.status_code, resp.content, ctype

    # ---------- 状态 ----------

    def status(self) -> Dict:
        return {
            'base_url': self.base_url,
            'client_id': self.client_id,
            'serialize_concurrent': self.sem._value,  # noqa: SLF001
            'states': dict(self.states),
            'watching': list(self._tasks_by_real.keys()),
            'progress': self.progress_stream.snapshot() if self.progress_stream else None,
        }
