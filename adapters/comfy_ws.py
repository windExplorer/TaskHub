"""真实 ComfyUI 的 WebSocket 事件订阅（进度 / 心跳 / 队列水位）。

为什么需要它：`/queue` 只能回答「prompt 在不在跑」，无法区分「正在算」和「卡死」——
两种情况 prompt 都停在 `queue_running`。只有 WS 事件才是「它真的在往前算」的证据：
- `progress_state`（ComfyUI ≥0.3 节点级）/ `progress`（旧版采样器步数）
- `executing` / `executed` / `execution_cached` / `execution_start` / `execution_error`
- `status`（广播，含 queue_remaining，可当上游心跳）

关键实现约束（对照 ComfyUI 源码 server.py / execution.py / comfy_execution/progress.py）：
这些事件是 `send_sync(event, data, server.client_id)` 发出的，而 `server.client_id` 在
执行期间被设为该 prompt 的 `extra_data["client_id"]`（即 /prompt 请求体里的 client_id）。
`send_json(event, data, sid)` 在 `sid not in sockets` 时**静默丢弃**，因此必须用
`?clientId=<提交时使用的 client_id>` 建连，才能收到本中转站提交任务的进度；
`status` 事件 sid=None 走广播，任何连接都能收到。
"""
import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger('middle_station.comfyui.ws')

try:                                     # websockets >= 13 的新 asyncio 客户端
    from websockets.asyncio.client import connect as _ws_connect
except ImportError:                      # 老版本回退
    from websockets.client import connect as _ws_connect  # type: ignore


class ComfyEventStream:
    """常驻订阅真实 ComfyUI /ws，把事件回调给上层；断线自动重连。"""

    def __init__(self, base_url: str, client_id: str,
                 on_event: Callable[[str, Dict], Awaitable[None]],
                 reconnect_min: float = 1.0, reconnect_max: float = 15.0,
                 open_timeout: float = 10.0):
        self.base_url = base_url.rstrip('/')
        self.client_id = client_id
        self.on_event = on_event
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.open_timeout = open_timeout

        self.connected = False
        self.connected_at: float = 0.0
        self.last_event_ts: float = 0.0          # 任意事件（含广播 status）的到达时间
        self.last_event_type: str = ''
        self.queue_remaining: Optional[int] = None
        self.errors: int = 0
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    @property
    def ws_url(self) -> str:
        base = self.base_url
        if base.startswith('https://'):
            base = 'wss://' + base[len('https://'):]
        elif base.startswith('http://'):
            base = 'ws://' + base[len('http://'):]
        return f'{base}/ws?clientId={self.client_id}'

    def start(self):
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._loop())
            logger.info('comfyui progress stream: subscribing %s', self.ws_url)

    async def stop(self):
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def snapshot(self) -> Dict:
        return {
            'connected': self.connected,
            'connected_seconds': round(time.time() - self.connected_at, 1) if self.connected else 0.0,
            'last_event_ts': self.last_event_ts,
            'last_event_ago': round(time.time() - self.last_event_ts, 1) if self.last_event_ts else None,
            'last_event_type': self.last_event_type,
            'queue_remaining': self.queue_remaining,
            'errors': self.errors,
        }

    async def _loop(self):
        backoff = self.reconnect_min
        while not self._stopping:
            try:
                async with _ws_connect(self.ws_url, open_timeout=self.open_timeout,
                                       max_size=None, ping_interval=20, ping_timeout=20) as ws:
                    self.connected = True
                    self.connected_at = time.time()
                    self.errors = 0
                    backoff = self.reconnect_min
                    logger.info('comfyui progress stream connected (%s)', self.client_id)
                    async for raw in ws:
                        if isinstance(raw, (bytes, bytearray)):
                            continue          # 二进制帧是预览图，忽略
                        await self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.errors += 1
                logger.warning('comfyui progress stream disconnected: %s (retry in %.0fs)',
                               e, backoff)
            finally:
                if self.connected:
                    self.connected = False
                    logger.info('comfyui progress stream closed')
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(self.reconnect_max, backoff * 2)

    async def _dispatch(self, raw: Any):
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        etype = str(msg.get('type') or '')
        data = msg.get('data')
        if not isinstance(data, dict):
            data = {}
        self.last_event_ts = time.time()
        self.last_event_type = etype
        if etype == 'status':
            info = ((data.get('status') or {}).get('exec_info') or {})
            rem = info.get('queue_remaining')
            if isinstance(rem, int):
                self.queue_remaining = rem
        try:
            await self.on_event(etype, data)
        except Exception as e:  # noqa: BLE001
            logger.warning('comfyui event handler failed (%s): %s', etype, e)
