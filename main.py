"""Middle Station —— AstrBot 插件任务调度中转站。

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
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from adapters import BackendError, ComfyAdapter, TTSAdapter
from adapters.tts import clean_prompt_text
from config import Config
from monitor import ResourceMonitor
from scheduler import Scheduler, Task
from storage import Storage

logger = logging.getLogger('middle_station.main')


# ============================================================================
# 全局状态（由 create_app 初始化）
# ============================================================================
cfg: Config = Config.load()
monitor: Optional[ResourceMonitor] = None
scheduler: Optional[Scheduler] = None
storage: Optional[Storage] = None
tts: Optional[TTSAdapter] = None
comfy: Optional[ComfyAdapter] = None
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
    await monitor.start()
    scheduler.start()
    await tts.start()
    logger.info('middle station up: http://%s:%s (tts=%s, comfyui=%s)',
                cfg.server.host, cfg.server.port, cfg.tts.base_url, cfg.comfyui.base_url)
    yield
    await tts.stop()
    await monitor.stop()
    await scheduler.stop()
    await comfy.close()
    logger.info('middle station stopped')


async def _on_task_update(task: Task):
    """任务状态变更：持久化 + 推送 WebSocket。"""
    try:
        await storage.record(task)
    except Exception as e:  # noqa: BLE001
        logger.warning('record task failed: %s', e)
    await broadcaster.publish({'event': 'task_update', 'task': task.to_dict()})


# ============================================================================
# 提交辅助
# ============================================================================
async def _submit(task: Task, media_type: str = 'application/octet-stream',
                  queue_wait: Optional[float] = None,
                  infer_timeout: Optional[float] = None) -> Response:
    scheduler.add_task(task)
    result, code, err = await scheduler.submit(
        task, queue_wait=queue_wait, infer_timeout=infer_timeout)
    if code == 200:
        return Response(content=result, media_type=media_type)
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
        }

    @app.get('/stats')
    async def stats_api(hours: int = 24):
        return await storage.stats(hours=hours)

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
                'serialize_concurrent': cfg.comfyui.serialize_concurrent,
                'watch_interval': cfg.comfyui.watch_interval,
                'watch_timeout': cfg.comfyui.watch_timeout,
            },
            'monitoring': {
                'gpu_threshold': cfg.monitoring.gpu_threshold,
                'interval_seconds': cfg.monitoring.interval_seconds,
            },
        }

    @app.get('/queue')
    async def queue_api(limit: int = 100):
        return scheduler.get_status(limit=limit)

    @app.get('/tasks')
    async def tasks_api(limit: int = 200):
        return {'tasks': scheduler.get_status(limit=limit)['tasks']}

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

    # ---- ComfyUI 标准接口（docs/comfyui-backend-api.md §2） ----
    @app.post('/prompt')
    async def comfy_prompt(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail='invalid json body')
        if not isinstance(body, dict) or 'prompt' not in body:
            raise HTTPException(status_code=400, detail='body must contain "prompt"')
        task = Task(
            priority=0, created_at=time.time(), task_id=_new_id('comfy'),
            task_type='comfyui', payload=body, handler=comfy.submit_prompt,
            estimated_duration=120.0, resource_weight=2.0,
        )
        # 插件不识别排队响应：内部阻塞等待槽位，绝不回 429
        scheduler.add_task(task)
        result, code, err = await scheduler.submit(
            task, queue_wait=None, infer_timeout=cfg.server.infer_timeout)
        if code == 200:
            return JSONResponse(result)
        raise HTTPException(status_code=code, detail=err or f'http {code}')

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
                })
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass

    @app.websocket('/ws/tasks')
    async def ws_tasks(websocket: WebSocket):
        await websocket.accept()
        q = await broadcaster.subscribe()
        try:
            await websocket.send_json({
                'event': 'init',
                'tasks': [t.to_dict() for t in scheduler.tasks.values()],
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
        app.mount('/ui', StaticFiles(directory=cfg.server.frontend_dir, html=True), name='ui')
        logger.info('WebUI mounted at /ui from %s', cfg.server.frontend_dir)
    else:
        logger.warning('frontend dir %s not found, /ui disabled', cfg.server.frontend_dir)


# ============================================================================
# 应用工厂
# ============================================================================
def create_app(config_path: Optional[str] = None) -> FastAPI:
    global cfg, monitor, scheduler, storage, tts, comfy, broadcaster, log_buffer, _app

    if config_path:
        cfg = Config.load(config_path)

    monitor = ResourceMonitor(cfg.monitoring.interval_seconds, cfg.monitoring.gpu_threshold)
    scheduler = Scheduler(
        max_concurrent=cfg.server.max_concurrent,
        queue_timeout=cfg.server.queue_wait,
        infer_timeout=cfg.server.infer_timeout,
        gpu_threshold=cfg.monitoring.gpu_threshold,
        gpu_load_provider=lambda: monitor.gpu,
    )
    storage = Storage(cfg.server.db_path)
    tts = TTSAdapter(cfg)
    comfy = ComfyAdapter(cfg, scheduler)
    broadcaster = WSBroadcaster()
    log_buffer = LogBuffer(cfg.server.log_max_lines)

    # 日志 -> WebUI 控制台
    log_buffer.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    logging.getLogger().addHandler(log_buffer)

    app = FastAPI(title='Middle Station', version='1.2.0', lifespan=lifespan)
    _register_routes(app)
    scheduler.on_task_update(_on_task_update)
    _app = app
    return app


# ============================================================================
# CLI（ts-station / python main.py）
# ============================================================================
@click.group()
def cli():
    pass


@cli.command()
@click.option('--host', default=None, help='listen host')
@click.option('--port', default=None, type=int, help='listen port')
@click.option('--config', default=None, type=click.Path(exists=False), help='config yaml path')
@click.option('--reload', is_flag=True, help='auto reload on code change')
def start(host, port, config, reload):
    """启动中转站服务。"""
    app = create_app(config)
    h = host or cfg.server.host
    p = port or cfg.server.port
    logger.info('starting middle station on %s:%s', h, p)
    uvicorn.run(app, host=h, port=p, reload=reload, access_log=False, log_level='info')


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
