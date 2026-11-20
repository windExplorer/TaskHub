"""TaskHub —— 本地 LLM 调度中转站。

接收 AstrBot 插件（CosyVoice3 TTS + ComfyUI 绘图）的标准接口请求，
进行资源监控、优先级排队、GPU 感知并发调度，并透传 / 调度到真实后端。

接口契约见 docs/backend-api.md 与 docs/comfyui-backend-api.md（插件零改动）。

启动：
    uv run uvicorn main:app --host 0.0.0.0 --port 9000
    uv run python main.py start --port 9000
WebUI：http://127.0.0.1:9000/ui
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from adapters import BackendError, ComfyAdapter, TTSAdapter
from adapters.nsfw import NsfwChecker
from adapters.tts import clean_prompt_text
from config import Config
from inspector import UpstreamInspector
from monitor import ResourceMonitor
from scheduler import Scheduler, Task
from storage import Storage

logger = logging.getLogger('middle_station.main')


class _WsNoiseFilter(logging.Filter):
    """过滤浏览器断开 WebSocket 引发的无害 asyncio 回调噪音。

    现象：Windows 下客户端（浏览器刷新/关闭）断开 WS 连接时，asyncio 回调
    _ProactorBasePipeTransport._call_connection_lost 抛 ConnectionResetError
    (WinError 10054)，被记成 ERROR 刷屏。对功能无影响，直接挡掉。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if 'Exception in callback' in msg and '_call_connection_lost' in msg:
            return False
        return True


def _apply_log_filters():
    if not getattr(_apply_log_filters, '_applied', False):
        _apply_log_filters._applied = True
        logging.getLogger('asyncio').addFilter(_WsNoiseFilter())


def _setup_logging(log_buffer: 'LogBuffer'):
    """配置日志双通道：控制台 + WebUI 日志面板。

    注意：root logger 默认 WARNING，而 uvicorn 的 dictConfig 不含 root 配置，
    若不显式把 middle_station 提到 INFO，任务生命周期日志（queued/started/finished）
    会被静默丢弃 —— 出问题时日志面板只剩 ERROR，无从排查。
    """
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    ms = logging.getLogger('middle_station')
    ms.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) for h in ms.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        ms.addHandler(sh)
    # WebUI 日志面板挂 root：除 middle_station 外还能收到 asyncio 等库的 ERROR
    root = logging.getLogger()
    for h in [h for h in root.handlers if isinstance(h, LogBuffer)]:
        root.removeHandler(h)     # create_app 可能被调用两次（模块级 + CLI），先清旧的
    log_buffer.setFormatter(fmt)
    root.addHandler(log_buffer)

    # 文件日志：pythonw 无控制台，日志必须自己落盘（VBS 后台启动靠它排障）。
    try:
        _log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', '') == str(_log_file)
                   for h in root.handlers):
            fh = logging.FileHandler(_log_file, encoding='utf-8')
            fh.setFormatter(fmt)
            root.addHandler(fh)
    except OSError:
        pass


# ============================================================================
# 全局状态（由 create_app 初始化）
# ============================================================================
cfg: Config = Config.load()
_config_path: str = os.environ.get('MIDDLE_CONFIG', 'middle-station.yaml')
_started_at: float = time.time()          # 进程启动时刻，前端据此判断重启是否完成
_restarting: bool = False
_root_dir: Path = Path(__file__).resolve().parent   # 仓库根目录（pid/日志放这里）
_pid_file: Path = _root_dir / 'taskhub.pid'
_log_file: Path = _root_dir / 'logs' / 'server.log'
monitor: Optional[ResourceMonitor] = None
scheduler: Optional[Scheduler] = None
storage: Optional[Storage] = None
tts: Optional[TTSAdapter] = None
comfy: Optional[ComfyAdapter] = None
nsfw_checker: Optional[NsfwChecker] = None
inspector: Optional[UpstreamInspector] = None
broadcaster: Optional['WSBroadcaster'] = None
log_buffer: Optional['LogBuffer'] = None
_app: Optional[FastAPI] = None


# ============================================================================
# 日志环形缓冲（供 /ws/logs）
# ============================================================================
class LogBuffer(logging.Handler):
    def __init__(self, maxlen: int = 100):
        super().__init__()
        self.maxlen = maxlen
        self.buf: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord):
        try:
            line = self.format(record)
            with self._lock:
                self.buf.append({
                    'ts': round(time.time(), 3),
                    'level': record.levelname,
                    'message': line,
                })
        except Exception:  # noqa: BLE001
            pass

    def recent(self) -> List[Dict]:
        with self._lock:
            return list(self.buf)


# ============================================================================
# WebSocket 事件广播（任务状态变更）
# ============================================================================
class WSBroadcaster:
    def __init__(self):
        self._queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._queues.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            self._queues.discard(q)

    async def publish(self, data: Dict):
        async with self._lock:
            for q in list(self._queues):
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    pass


def _new_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


# ============================================================================
# 生命周期
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global nsfw_checker
    await monitor.start()
    scheduler.start()
    await tts.start()

    # NSFW 启动探测：配置 enabled=true 但 nsfw-check 服务没装/没启动时，
    # 直接禁用送检（comfy.nsfw=None），走原来的生图/发图流程，零开销零 warning。
    if nsfw_checker is not None:
        healthy = await nsfw_checker.probe(timeout=2.0)
        if healthy is None:
            logger.warning('[nsfw] service unreachable at %s at startup; NSFW 送检已自动禁用（生图/发图不受影响）',
                           cfg.nsfw.base_url)
            await nsfw_checker.close()
            nsfw_checker = None
            comfy.nsfw = None

    comfy.start()                 # 订阅真实 ComfyUI 的进度事件（/ws）
    inspector.start()             # 上游自检巡检
    logger.info('TaskHub up: http://%s:%s (tts=%s, comfyui=%s, client_id=%s, nsfw=%s)',
                cfg.server.host, cfg.server.port, cfg.tts.base_url,
                cfg.comfyui.base_url, comfy.client_id,
                f'{cfg.nsfw.mode}@{cfg.nsfw.base_url}' if nsfw_checker is not None else 'off')
    yield
    await inspector.stop()
    await tts.stop()
    await monitor.stop()
    await scheduler.stop()
    await comfy.close()
    if nsfw_checker is not None:
        await nsfw_checker.close()
    logger.info('TaskHub stopped')


async def _on_task_update(task: Task):
    """任务状态变更：持久化 + 推送 WebSocket。"""
    try:
        await storage.record(task)
    except Exception as e:  # noqa: BLE001
        logger.warning('record task failed: %s', e)
    await broadcaster.publish({'event': 'task_update', 'task': task.to_dict()})


async def _on_task_live_update(task: Task):
    """高频实时更新（真实进度 / 自检异常）：只推 WebSocket，不落库。"""
    await broadcaster.publish({'event': 'task_update', 'task': task.to_dict()})


async def _vram_cleanup_on_type_switch(prev_type: str, next_type: str):
    """任务类型切换时释放上游残留显存（由 Scheduler 在安全窗口调用）。

    显存残留主要来自 ComfyUI：出图完成后模型仍常驻显存，切到 TTS 时可能挤占
    CosyVoice 的加载空间导致后续任务失败。CosyVoice 侧已自行释放，故这里只
    处理「切离 comfyui」。清理失败由调度器捕获告警，不影响任务流转。
    """
    if not cfg.server.vram_cleanup_on_type_switch:
        return
    if prev_type == 'comfyui' and cfg.comfyui.free_vram_on_type_switch:
        await comfy.free_vram()
    # 显存托管联动：切离 comfyui 时顺带请 nsfw-check 也卸载模型（它与 ComfyUI 可能同卡）
    if prev_type == 'comfyui' and nsfw_checker is not None:
        await nsfw_checker.release()


def _self_check() -> Dict:
    if inspector is None:
        return {'level': 'ok', 'count': 0, 'items': []}
    return inspector.summary()


async def _device_status(raw: bool = False) -> Dict:
    """聚合「本机 + 上游后端 + 调度」的当前状态（GET /device）。

    「上游聚合」= 中转站自己作为客户端，把真实后端的设备/健康信息一次问全并归一化后返回：
    - ComfyUI：`GET /queue`（在跑什么）+ `GET /system_stats`（OS/Python/PyTorch/版本/上游自身显存）
    - CosyVoice：`GET /`（健康检查，含真实采样率与 model_loaded）
    上游任何一个挂了都只是 `reachable: false`，不影响本机和另一个上游的数据。
    """
    host = await monitor.device_status()
    comfy_probe, comfy_stats, cosy_probe, nsfw_probe = await asyncio.gather(
        comfy.upstream_probe(timeout=3.0),
        comfy.system_stats(timeout=3.0),
        tts.probe(timeout=3.0),
        nsfw_checker.probe(timeout=3.0) if nsfw_checker is not None else asyncio.sleep(0, result=None),
        return_exceptions=True,
    )
    if isinstance(comfy_probe, BaseException):
        comfy_probe = {'reachable': False, 'running': set(), 'pending': set(),
                       'vram_free_gb': None, 'vram_total_gb': None}
    if isinstance(comfy_stats, BaseException):
        comfy_stats = None
    if isinstance(cosy_probe, BaseException):
        cosy_probe = None
    if isinstance(nsfw_probe, BaseException):
        nsfw_probe = None

    if nsfw_checker is not None:
        nsfw_info = {
            **nsfw_checker.status(),
            'reachable': isinstance(nsfw_probe, dict),
            'health': nsfw_probe if isinstance(nsfw_probe, dict) else None,
        }
    else:
        nsfw_info = {'enabled': False, 'base_url': cfg.nsfw.base_url,
                     'mode': cfg.nsfw.mode, 'reachable': False, 'health': None}

    sys_info: Dict = {}
    dev_info: list = []
    if isinstance(comfy_stats, dict):
        s = comfy_stats.get('system') or {}
        sys_info = {
            'comfyui_version': s.get('comfyui_version'),
            'os': s.get('os'),
            'python': s.get('python_version'),
            'pytorch': s.get('pytorch_version'),
            'ram_total_gb': round((s.get('ram_total') or 0) / (1024 ** 3), 2) or None,
            'ram_free_gb': round((s.get('ram_free') or 0) / (1024 ** 3), 2) or None,
            'argv': s.get('argv'),
        }
        for d in (comfy_stats.get('devices') or []):
            dev_info.append({
                'name': d.get('name'),
                'type': d.get('type'),
                'vram_total_gb': round((d.get('vram_total') or 0) / (1024 ** 3), 2) or None,
                'vram_free_gb': round((d.get('vram_free') or 0) / (1024 ** 3), 2) or None,
                'torch_vram_total_gb': round((d.get('torch_vram_total') or 0) / (1024 ** 3), 3) or None,
                'torch_vram_free_gb': round((d.get('torch_vram_free') or 0) / (1024 ** 3), 3) or None,
            })

    stream = comfy.progress_snapshot()
    comfy_info = {
        'base_url': cfg.comfyui.base_url,
        'reachable': bool(comfy_probe.get('reachable')),
        'system_stats_ok': isinstance(comfy_stats, dict),
        'client_id': comfy.client_id,
        'queue': {
            'running': sorted(comfy_probe.get('running') or []),
            'pending': sorted(comfy_probe.get('pending') or []),
            # 上游自己的口径（来自 /ws 的 status 广播；未订阅时为空）
            'queue_remaining': (stream or {}).get('queue_remaining'),
        },
        'progress_stream': stream,
        'system': sys_info,
        'devices': dev_info,
    }
    if raw and isinstance(comfy_stats, dict):
        comfy_info['raw_system_stats'] = comfy_stats

    cosy_status = tts.status()
    cosy_info = {
        **cosy_status,
        'reachable': isinstance(cosy_probe, dict),
        'info': cosy_probe if isinstance(cosy_probe, dict) else None,
    }

    st = scheduler.get_status(limit=0)
    return {
        'ts': time.time(),
        'host': host,
        'upstreams': {'comfyui': comfy_info, 'cosyvoice': cosy_info, 'nsfw': nsfw_info},
        'scheduler': {
            'queue_length': st['queue_length'],
            'running': st['running'],
            'max_concurrent': st['max_concurrent'],
            'effective_concurrent': st['effective_concurrent'],
            'throttle_reason': st['throttle_reason'],
            'total_queued': st['total_queued'],
            'total_completed': st['total_completed'],
        },
        'self_check': _self_check(),
    }


# ============================================================================
# 提交辅助
# ============================================================================
def _build_config_from_dict(data: Dict) -> Config:
    """用提交的完整 dict 构造 Config（经临时 yaml 走标准 load，环境变量仍优先）。"""
    import tempfile
    import yaml
    allowed = set(Config().to_dict().keys())
    clean = {k: v for k, v in data.items() if k in allowed and isinstance(v, dict)}
    if not clean:
        raise ValueError('empty or invalid config')
    fd, tmp = tempfile.mkstemp(suffix='.yaml', prefix='taskhub-cfg-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yaml.safe_dump(clean, f, allow_unicode=True, sort_keys=False)
        return Config.load(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


async def _submit(task: Task, media_type: str = 'application/octet-stream',
                  queue_wait: Optional[float] = None,
                  infer_timeout: Optional[float] = None) -> Response:
    # 业务开关：拒绝新任务（在跑的不受影响）。comfyui /prompt 不走 _submit，在路由单独判。
    if not cfg.server.tasks_enabled.get(task.task_type, True):
        raise HTTPException(status_code=503, detail=f'{task.task_type} 业务已被 WebUI 关闭')
    scheduler.add_task(task)
    result, code, err = await scheduler.submit(
        task, queue_wait=queue_wait, infer_timeout=infer_timeout)
    if code == 200:
        resp = Response(content=result, media_type=media_type)
        resp.headers['X-Queue-Position'] = str(task.queue_position)
        return resp
    raise HTTPException(status_code=code, detail=err or f'http {code}')


async def _parse_tts_form(form) -> Dict:
    tts_text = (form.get('tts_text') or '').strip()
    if not tts_text:
        raise HTTPException(status_code=400, detail='tts_text required')
    prompt_text = clean_prompt_text(form.get('prompt_text'))
    prompt_wav_path = form.get('prompt_wav_path') or None
    wav_bytes = None
    wav_file = form.get('prompt_wav')
    if wav_file is not None and hasattr(wav_file, 'read'):
        content = await wav_file.read()
        if content:
            wav_bytes = (getattr(wav_file, 'filename', None) or 'prompt.wav', content)
    if not prompt_wav_path and not wav_bytes:
        raise HTTPException(status_code=400, detail='prompt_wav or prompt_wav_path required')
    # 已知 voices 映射时自动补 prompt_text（后端亦会按文件名回退）
    if prompt_text is None and prompt_wav_path:
        prompt_text = tts.voice_text(prompt_wav_path)
    return {
        'tts_text': tts_text,
        'prompt_text': prompt_text,
        'prompt_wav_path': prompt_wav_path,
        'prompt_wav': wav_bytes,
    }


# ============================================================================
# 路由
# ============================================================================
def _register_routes(app: FastAPI):

    # ---- 健康检查 / 监控 ----
    @app.get('/')
    async def health_check():
        """插件首次合成前必读：返回真实采样率。"""
        return tts.health()

    @app.get('/health')
    async def health():
        return {
            'status': 'ok',
            'gpu_load': monitor.gpu,
            'model_loaded': tts.model_loaded,
            'queue_length': scheduler.queued_count(),
            'running': scheduler.running_tasks,
            'max_concurrent': scheduler.max_concurrent,
            'sample_rate': tts.sample_rate,
            'self_check': _self_check(),
            'started_at': _started_at,   # 进程启动时刻，供 WebUI 判断重启是否完成
        }

    @app.get('/monitor')
    async def monitor_api():
        snap = monitor.snapshot()
        st = scheduler.get_status(limit=0)
        return {
            **snap,
            'ts': time.time(),
            'queue_length': st['queue_length'],
            'running': st['running'],
            'max_concurrent': st['max_concurrent'],
            'effective_concurrent': st['effective_concurrent'],
            'throttle_reason': st['throttle_reason'],
            'self_check': _self_check(),
        }

    @app.get('/device')
    async def device_api(raw: int = 0):
        """当前设备状态：本机（CPU/内存/磁盘/进程/每张显卡）+ 上游（ComfyUI / CosyVoice）+ 调度概况。

        - 即时采样：不等 monitor 的 2s 周期；每次只向上游各发 1~2 个只读请求。
        - 上游不可达时只体现为 `reachable: false`，接口本身仍 200，不会变慢或失败。
        - `?raw=1` 额外带上真实 ComfyUI `/system_stats` 的原始 JSON（排障用）。
        """
        return await _device_status(bool(raw))

    @app.get('/self-check')
    async def self_check_api():
        """上游自检详情：异常清单 + 上游探测快照 + 进度流状态。"""
        return _self_check()

    @app.post('/self-check/run')
    async def self_check_run():
        """手动触发一次自检（排查时不用等下一个周期）。"""
        if inspector is None:
            raise HTTPException(status_code=503, detail='inspector unavailable')
        await inspector.check_once()
        return _self_check()

    @app.get('/stats')
    async def stats_api(hours: int = 24):
        return await storage.stats(hours=hours)

    @app.get('/stats/detail')
    async def stats_detail_api(hours: int = 24):
        """统计详情（汇总 + 类型/状态分布 + 时间序列），供统计页图表使用。"""
        return await storage.stats_detail(hours=hours)

    @app.get('/config')
    async def config_api():
        """当前生效配置（只读；修改请编辑 middle-station.yaml 后重启）。"""
        return {
            'config_file': 'middle-station.yaml',
            'server': {
                'host': cfg.server.host,
                'port': cfg.server.port,
                'max_concurrent': cfg.server.max_concurrent,
                'queue_wait': cfg.server.queue_wait,
                'infer_timeout': cfg.server.infer_timeout,
                'db_path': cfg.server.db_path,
                'tasks_enabled': dict(cfg.server.tasks_enabled),
            },
            'tts': {
                'base_url': cfg.tts.base_url,
                'sample_rate': tts.sample_rate,      # 探测后真实值
                'model_loaded': tts.model_loaded,
                'voices_dir': cfg.tts.voices_dir,
                'voices': len(tts.voices),
            },
            'comfyui': {
                'base_url': cfg.comfyui.base_url,
                'client_id': comfy.client_id,
                'serialize_concurrent': cfg.comfyui.serialize_concurrent,
                'watch_interval': cfg.comfyui.watch_interval,
                'watch_timeout': cfg.comfyui.watch_timeout,
                'watch_lost_grace': cfg.comfyui.watch_lost_grace,
                'watch_lost_confirm': cfg.comfyui.watch_lost_confirm,
                'progress_stream': cfg.comfyui.progress_stream,
            },
            'monitoring': {
                'gpu_threshold': cfg.monitoring.gpu_threshold,
                'interval_seconds': cfg.monitoring.interval_seconds,
            },
            'inspector': {
                'enabled': cfg.inspector.enabled,
                'interval': cfg.inspector.interval,
                'slow_factor': cfg.inspector.slow_factor,
                'slow_min_seconds': cfg.inspector.slow_min_seconds,
                'stall_seconds': cfg.inspector.stall_seconds,
            },
            'nsfw': {
                'enabled': cfg.nsfw.enabled,
                'base_url': cfg.nsfw.base_url,
                'mode': cfg.nsfw.mode,
                'timeout': cfg.nsfw.timeout,
                'wait_seconds': cfg.nsfw.wait_seconds,
                'prewarm': cfg.nsfw.prewarm,
            },
            'started_at': _started_at,
        }

    @app.get('/config/raw')
    async def config_raw_api():
        """全量可编辑配置（写回用）。"""
        return cfg.to_dict()

    @app.post('/nsfw/warm')
    async def nsfw_warm_api(force: int = 0):
        """手动预热 nsfw-check 模型（调度控制，无需重启）。force=1 强制重载。"""
        if nsfw_checker is None:
            raise HTTPException(status_code=404, detail='nsfw checker is disabled')
        ok = await nsfw_checker.warm(force=bool(force))
        health = await nsfw_checker.probe(timeout=3.0)
        return {'ok': ok, 'health': health}

    @app.post('/nsfw/release')
    async def nsfw_release_api():
        """手动卸载 nsfw-check 模型、释放显存（调度控制，无需重启）。"""
        if nsfw_checker is None:
            raise HTTPException(status_code=404, detail='nsfw checker is disabled')
        ok = await nsfw_checker.release()
        return {'ok': ok}

    @app.put('/config')
    async def config_save(request: Request):
        """保存配置到 middle-station.yaml（业务开关同时即时生效，其余需重启）。"""
        global cfg, _restarting
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail='invalid json body')
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail='config must be an object')
        try:
            new = _build_config_from_dict(data)
        except (ValueError, TypeError, OSError) as e:
            raise HTTPException(status_code=400, detail=f'invalid config: {e}')

        # 写 yaml（.bak 备份），再把内存 cfg 整体替换，使业务开关即时生效。
        # 注意：调度器/适配器仍持有旧快照，除开关外的字段需重启才真正生效。
        old_types = dict(cfg.server.tasks_enabled)
        new.server.tasks_enabled = dict(data.get('server', {}).get('tasks_enabled', new.server.tasks_enabled))
        try:
            new.save(_config_path)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f'write config failed: {e}')
        cfg = new
        if old_types != cfg.server.tasks_enabled:
            logger.info('[config] tasks_enabled changed in memory: %s -> %s (effective immediately)',
                        old_types, cfg.server.tasks_enabled)
        return {
            'ok': True,
            'config_file': _config_path,
            'needs_restart': True,
            'tasks_enabled': dict(cfg.server.tasks_enabled),
            'note': '业务开关已即时生效；其余配置需重启服务后生效',
        }

    @app.post('/restart')
    async def restart_api():
        """重启服务：拉起新进程（等待端口释放），随后退出当前进程。"""
        global _restarting
        if _restarting:
            raise HTTPException(status_code=409, detail='restart already in progress')
        _restarting = True

        async def _do_restart():
            await asyncio.sleep(1.0)  # 让 HTTP 响应先返回
            try:
                host = cfg.server.host
                port = cfg.server.port
                cmd = [sys.executable, str(Path(__file__).resolve()), 'start',
                       '--host', host, '--port', str(port), '--config', _config_path,
                       '--wait-free', '--tray']
                pid = _spawn_detached(cmd, _log_file)
                logger.info('[restart] spawned new process pid=%s: %s (log -> %s)',
                            pid, ' '.join(cmd), _log_file)
            except Exception as e:  # noqa: BLE001
                logger.warning('[restart] spawn failed: %s', e)
                _restarting = False
                return
            os._exit(0)

        asyncio.create_task(_do_restart())
        return {'ok': True, 'restarting': True, 'old_started_at': _started_at}

    @app.post('/shutdown')
    async def shutdown_api():
        """关闭服务（WebUI 用）：延迟退出当前进程。pid 文件由 stop 命令/下次启动清理。"""
        async def _do_shutdown():
            await asyncio.sleep(1.0)  # 让 HTTP 响应先返回
            logger.info('[shutdown] service stopping via WebUI')
            os._exit(0)

        asyncio.create_task(_do_shutdown())
        return {'ok': True, 'stopping': True}

    @app.get('/logs/tail')
    async def logs_tail(lines: int = 200):
        """读取落盘日志最后 N 行（daemon/重启后的控制台输出），供 WebUI 历史回填。"""
        lines = max(1, min(2000, lines))
        if not _log_file.exists():
            return {'file': str(_log_file), 'lines': []}
        try:
            with open(_log_file, 'r', encoding='utf-8', errors='replace') as f:
                tail = deque(f, maxlen=lines)
            return {'file': str(_log_file), 'lines': [x.rstrip('\r\n') for x in tail]}
        except OSError as e:
            return {'file': str(_log_file), 'lines': [], 'error': str(e)}

    @app.get('/queue')
    async def queue_api(limit: int = 100):
        return scheduler.get_status(limit=limit)

    @app.get('/tasks')
    async def tasks_api(page: int = 1, page_size: int = 20, task_type: str = '',
                        status: str = '', keyword: str = ''):
        """分页 + 筛选查询历史任务（全量任务页 /ui/tasks.html 用）。"""
        return await storage.query_tasks(
            page=page, page_size=page_size,
            task_type=task_type or None, status=status or None, keyword=keyword or None)

    @app.post('/tasks/{task_id}/promote')
    async def promote_task(task_id: str, priority: int = -1):
        if not scheduler.promote(task_id, priority):
            raise HTTPException(status_code=404, detail='task not found or not queued')
        return {'ok': True, 'task_id': task_id, 'priority': priority}

    @app.post('/tasks/{task_id}/cancel')
    async def cancel_task(task_id: str):
        if not scheduler.cancel(task_id):
            raise HTTPException(status_code=404, detail='task not found or not queued')
        return {'ok': True, 'task_id': task_id}

    # ---- TTS 标准接口（docs/backend-api.md §2） ----
    @app.post('/inference_zero_shot')
    async def inference_zero_shot(request: Request):
        form = await request.form()
        payload = await _parse_tts_form(form)
        task = Task(
            priority=0, created_at=time.time(), task_id=_new_id('tts'),
            task_type='tts', payload=payload, handler=tts.synthesize,
            estimated_duration=30.0, resource_weight=1.0,
        )
        return await _submit(task)

    @app.post('/inference_instruct2')
    async def inference_instruct2(request: Request):
        form = await request.form()
        payload = await _parse_tts_form(form)
        instruct_text = (form.get('instruct_text') or '').strip()
        if not instruct_text:
            raise HTTPException(status_code=400, detail='instruct_text required')
        payload['instruct_text'] = instruct_text
        task = Task(
            priority=0, created_at=time.time(), task_id=_new_id('tts'),
            task_type='tts', payload=payload, handler=tts.synthesize_instruct,
            estimated_duration=30.0, resource_weight=1.0,
        )
        return await _submit(task)

    @app.get('/voices')
    async def voices_api():
        return await tts.voices_info()

    @app.get('/voices/{name}/audio')
    async def voice_audio_api(name: str):
        """代理上游 tts_serve 的参考音频文件，供插件音色库试听（不走合成）。"""
        result = await tts.voice_audio(name)
        if not result:
            raise HTTPException(status_code=404, detail='参考音频不存在或上游不可用')
        data, content_type = result
        return Response(content=data, media_type=content_type,
                        headers={'Cache-Control': 'public, max-age=86400'})

    # ---- ComfyUI 标准接口（docs/comfyui-backend-api.md §2） ----
    @app.post('/prompt')
    async def comfy_prompt(request: Request):
        if not cfg.server.tasks_enabled.get('comfyui', True):
            raise HTTPException(status_code=503, detail='comfyui 业务已被 WebUI 关闭')
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail='invalid json body')
        if not isinstance(body, dict) or 'prompt' not in body:
            raise HTTPException(status_code=400, detail='body must contain "prompt"')
        # 中转站立即返回一个虚拟 prompt_id（不等槽位、不阻塞、不因排队超时取消），
        # 任务进入队列后由 ComfyAdapter 映射到真实 prompt_id，插件照常轮询 /history。
        virtual_pid = uuid.uuid4().hex
        comfy.states[virtual_pid] = 'queued'
        task = Task(
            priority=0, created_at=time.time(), task_id=_new_id('comfy'),
            task_type='comfyui',
            payload={**body, '_virtual_pid': virtual_pid},
            handler=comfy.submit_prompt,
            estimated_duration=120.0, resource_weight=2.0,
            handler_timeout=0,   # 等单飞槽位 + 提交不限时，排队不因超时失败/取消
        )
        scheduler.add_task(task)
        resp = JSONResponse({'prompt_id': virtual_pid})
        resp.headers['X-Queue-Position'] = str(task.queue_position)
        return resp

    @app.post('/upload/image')
    async def comfy_upload(request: Request):
        body = await request.body()
        ctype = request.headers.get('content-type', '')
        status, content, _ct = await comfy.upload(body, ctype)
        if status != 200:
            raise HTTPException(status_code=status, detail=content.decode('utf-8', 'replace')[:200])
        return Response(content=content, media_type='application/json')

    @app.get('/history/{prompt_id}')
    async def comfy_history_one(prompt_id: str):
        status, content, ctype = await comfy.history_one(prompt_id)
        if status != 200:
            raise HTTPException(status_code=status, detail=content.decode('utf-8', 'replace')[:200])
        return Response(content=content, media_type=ctype or 'application/json')

    @app.get('/history')
    async def comfy_history(request: Request):
        status, content, ctype = await comfy.history_all(request.url.query)
        if status != 200:
            raise HTTPException(status_code=status, detail=content.decode('utf-8', 'replace')[:200])
        return Response(content=content, media_type=ctype or 'application/json')

    @app.get('/view')
    async def comfy_view(request: Request):
        status, content, ctype = await comfy.view(request.url.query)
        if status != 200:
            raise HTTPException(status_code=status, detail=content.decode('utf-8', 'replace')[:200])
        return Response(content=content, media_type=ctype or 'application/octet-stream')

    # ---- WebSocket ----
    @app.websocket('/ws/monitor')
    async def ws_monitor(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                snap = monitor.snapshot()
                st = scheduler.get_status(limit=0)
                await websocket.send_json({
                    **snap,
                    'ts': time.time(),
                    'queue_length': st['queue_length'],
                    'running': st['running'],
                    'max_concurrent': st['max_concurrent'],
                    'effective_concurrent': st['effective_concurrent'],
                    'throttle_reason': st['throttle_reason'],
                    'self_check': _self_check(),
                })
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass

    @app.websocket('/ws/tasks')
    async def ws_tasks(websocket: WebSocket):
        await websocket.accept()
        q = await broadcaster.subscribe()
        try:
            # init 只发最近 300 条（内存中的任务有淘汰护栏，历史全量在 SQLite /tasks）
            recent = sorted(
                scheduler.tasks.values(), key=lambda t: t.created_at, reverse=True
            )[:300]
            await websocket.send_json({
                'event': 'init',
                'tasks': [t.to_dict() for t in recent],
            })
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    await websocket.send_json(data)
                except asyncio.TimeoutError:
                    await websocket.send_json({'event': 'ping'})
        except WebSocketDisconnect:
            pass
        finally:
            await broadcaster.unsubscribe(q)

    @app.websocket('/ws/logs')
    async def ws_logs(websocket: WebSocket):
        await websocket.accept()
        seen = 0
        try:
            while True:
                items = log_buffer.recent()
                if len(items) < seen:      # 缓冲被截断，重置
                    seen = 0
                if len(items) > seen:
                    for line in items[seen:]:
                        await websocket.send_json(line)
                    seen = len(items)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            pass

    # ---- WebUI 静态页 ----
    import os
    if os.path.isdir(cfg.server.frontend_dir):
        # 关闭前端静态文件缓存，避免修改后浏览器仍用旧版（首屏空白等问题的常见元凶）
        @app.middleware('http')
        async def _no_cache_ui(request, call_next):
            resp = await call_next(request)
            if request.url.path.startswith('/ui'):
                resp.headers.update({
                    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0',
                })
            return resp

        app.mount('/ui', StaticFiles(directory=cfg.server.frontend_dir, html=True), name='ui')
        logger.info('WebUI mounted at /ui from %s', cfg.server.frontend_dir)
    else:
        logger.warning('frontend dir %s not found, /ui disabled', cfg.server.frontend_dir)


# ============================================================================
# 应用工厂
# ============================================================================
def create_app(config_path: Optional[str] = None) -> FastAPI:
    global cfg, monitor, scheduler, storage, tts, comfy, inspector, broadcaster, log_buffer, _app, nsfw_checker, _config_path

    _apply_log_filters()

    if config_path:
        cfg = Config.load(config_path)
        _config_path = config_path

    monitor = ResourceMonitor(cfg.monitoring.interval_seconds, cfg.monitoring.gpu_threshold)
    scheduler = Scheduler(
        max_concurrent=cfg.server.max_concurrent,
        queue_timeout=cfg.server.queue_wait,
        infer_timeout=cfg.server.infer_timeout,
        gpu_threshold=cfg.monitoring.gpu_threshold,
        vram_threshold=cfg.monitoring.vram_threshold,
        vram_min_free_gb=cfg.monitoring.vram_min_free_gb,
        gpu_load_provider=lambda: monitor.gpu,
        vram_provider=lambda: (monitor.gpu_free_gb, monitor.gpu_total_gb),
        type_switch_cleanup=_vram_cleanup_on_type_switch,
    )
    storage = Storage(cfg.server.db_path)
    tts = TTSAdapter(cfg)
    # NSFW 送检：启用时出图成功后自动把结果图片发给 nsfw-check（label/block 模式见 NsfwConfig）
    nsfw_checker = NsfwChecker(cfg.nsfw) if cfg.nsfw.enabled else None
    comfy = ComfyAdapter(cfg, scheduler, nsfw=nsfw_checker)
    inspector = UpstreamInspector(cfg, scheduler, comfy, storage, tts=tts, monitor=monitor)
    broadcaster = WSBroadcaster()
    log_buffer = LogBuffer(cfg.server.log_max_lines)

    _setup_logging(log_buffer)
    # 上次进程遗留的 running/queued 行只存在于 DB，启动时收敛为终态（避免任务页永远显示运行中）
    storage.reconcile_stale()

    app = FastAPI(title='TaskHub', description='本地 LLM 调度', version='1.3.0', lifespan=lifespan)
    _register_routes(app)
    scheduler.on_task_update(_on_task_update)
    scheduler.on_task_live_update(_on_task_live_update)
    _app = app
    return app


# ============================================================================
# CLI（ts-station / python main.py）
# ============================================================================
@click.group()
def cli():
    pass


def _spawn_detached(cmd: list, log_file: Path) -> int:
    """启动一个真正脱离终端的后台进程，stdout/stderr 追加到 log_file。返回子进程 pid。

    Windows：必须用 DETACHED_PROCESS（完全脱离控制台）。仅 CREATE_NEW_PROCESS_GROUP
    只隔离 Ctrl+C，关闭控制台窗口时 Windows 仍会向附加进程发 CTRL_CLOSE_EVENT 把它杀掉，
    这正是"关终端后台就没了/托盘图标消失"的根因。DETACHED_PROCESS 不能与新进程组/
    CREATE_NO_WINDOW 同用，故单独使用。
    """
    creation = 0
    if os.name == 'nt':
        creation = getattr(subprocess, 'DETACHED_PROCESS', 0)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_file, 'a', encoding='utf-8', buffering=1)
    except OSError:
        log_fh = subprocess.DEVNULL
    proc = subprocess.Popen(cmd, cwd=str(_root_dir), creationflags=creation,
                            stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT)
    return proc.pid


def _wait_port_free(host: str, port: int, timeout: float = 40.0) -> bool:
    """等待端口释放（自我重启时新进程用）。"""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect((host if host not in ('0.0.0.0', '') else '127.0.0.1', port))
            s.close()
            time.sleep(0.4)
            continue
        except OSError:
            s.close()
            return True
    return False


@cli.command()
@click.option('--host', default=None, help='listen host')
@click.option('--port', default=None, type=int, help='listen port')
@click.option('--config', default=None, type=click.Path(exists=False), help='config yaml path')
@click.option('--reload', is_flag=True, help='auto reload on code change')
@click.option('--wait-free', is_flag=True, hidden=True, help='wait until port is free before binding (self-restart)')
@click.option('--daemon', is_flag=True, help='run in background with tray icon (logs -> logs/server.log)')
@click.option('--tray', is_flag=True, hidden=True, help='run tray icon in main thread (internal, used by --daemon)')
def start(host, port, config, reload, wait_free, daemon, tray):
    """启动中转站服务（--daemon 后台常驻 + 托盘；默认前台运行）。"""
    app = create_app(config)
    h = host or cfg.server.host
    p = port or cfg.server.port

    if daemon:
        # 后台模式：spawn 一个不带 --daemon 但带 --tray 的子进程（避免递归）。
        # 托盘图标必须在子进程主线程运行，uvicorn 放子线程。
        if not _wait_port_free(h, p):
            click.echo(f'port {h}:{p} still busy, abort daemon start')
            return
        child_cmd = [sys.executable, str(Path(__file__).resolve()), 'start',
                     '--host', h, '--port', str(p), '--config', config or _config_path,
                     '--wait-free', '--tray']
        if reload:
            child_cmd.append('--reload')
        child_pid = _spawn_detached(child_cmd, _log_file)
        click.echo(f'TaskHub started in background (pid={child_pid}).')
        click.echo(f'  log:  {_log_file}')
        click.echo(f'  stop: python main.py stop  (or run stop.bat / tray 退出)')
        return

    if wait_free and not _wait_port_free(h, p):
        logger.error('port %s:%s still busy after wait, abort start', h, p)
        return
    # 写 PID：无论从终端脚本启动还是 WebUI 重启拉起，pid 文件始终指向当前进程。
    try:
        _pid_file.write_text(str(os.getpid()), encoding='utf-8')
    except OSError as e:
        logger.warning('write pid file failed: %s', e)
    logger.info('starting TaskHub on %s:%s (pid=%s, tray=%s)', h, p, os.getpid(), tray)

    def cleanup_pid():
        try:
            if _pid_file.exists() and _pid_file.read_text(encoding='utf-8').strip() == str(os.getpid()):
                _pid_file.unlink()
        except OSError:
            pass

    if tray:
        # 托盘模式：uvicorn 在子线程，主线程跑托盘图标；点退出 → should_exit + icon.stop。
        try:
            from tray import run_tray
        except ImportError as e:
            logger.warning('tray unavailable (%s), falling back to headless daemon', e)
            try:
                uvicorn.run(app, host=h, port=p, reload=reload, access_log=False, log_level='info')
            finally:
                cleanup_pid()
            return

        uvicorn_cfg = uvicorn.Config(app, host=h, port=p, reload=reload,
                                     access_log=False, log_level='info')
        server = uvicorn.Server(uvicorn_cfg)

        def serve():
            server.run()

        t = threading.Thread(target=serve, name='uvicorn', daemon=True)
        t.start()

        def on_quit():
            server.should_exit = True

        try:
            run_tray(h, p, on_quit)
        finally:
            server.should_exit = True
            t.join(timeout=8)
            cleanup_pid()
        return

    try:
        uvicorn.run(app, host=h, port=p, reload=reload, access_log=False, log_level='info')
    finally:
        # 正常退出时清理 PID（os._exit 的重启路径不走这里，由新进程覆盖）
        cleanup_pid()


@cli.command('stop')
def stop_cmd():
    """停止 TaskHub（读取 taskhub.pid 终止进程及其子进程树）。"""
    if not _pid_file.exists():
        click.echo('no pid file found; is TaskHub running?')
        return
    try:
        pid = int(_pid_file.read_text(encoding='utf-8').strip())
    except (ValueError, OSError) as e:
        click.echo(f'invalid pid file: {e}')
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 15)  # SIGTERM
        click.echo(f'stopped TaskHub pid={pid}')
    except Exception as e:  # noqa: BLE001
        click.echo(f'stop failed: {e}')
    finally:
        try:
            _pid_file.unlink(missing_ok=True)
        except OSError:
            pass


@cli.command('status')
@click.option('--config', default=None, type=click.Path(exists=False))
def status_cmd(config):
    """打印配置摘要与运行状态。"""
    c = Config.load(config)
    print(json.dumps({
        'server': {
            'host': c.server.host, 'port': c.server.port,
            'max_concurrent': c.server.max_concurrent,
            'queue_wait': c.server.queue_wait, 'infer_timeout': c.server.infer_timeout,
            'db_path': c.server.db_path,
        },
        'tts': {'base_url': c.tts.base_url, 'sample_rate': c.tts.sample_rate,
                'voices': len(c.tts.voices)},
        'comfyui': {'base_url': c.comfyui.base_url,
                    'serialize_concurrent': c.comfyui.serialize_concurrent},
        'monitoring': {'gpu_threshold': c.monitoring.gpu_threshold,
                       'interval_seconds': c.monitoring.interval_seconds},
    }, ensure_ascii=False, indent=2))


@cli.command()
@click.option('--fmt', type=click.Choice(['csv', 'json']), default='csv')
@click.option('--out', default=None, help='output file path')
@click.option('--config', default=None, type=click.Path(exists=False))
def export(fmt, out, config):
    """导出任务记录为 CSV/JSON。"""
    c = Config.load(config)
    st = Storage(c.server.db_path)
    path = asyncio.run(st.export(fmt, out))
    click.echo(f'exported -> {path}')


app = create_app()

if __name__ == '__main__':
    cli()
