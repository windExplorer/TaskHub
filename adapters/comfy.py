"""ComfyUI 适配层：/prompt 单飞调度 + 只读接口透传。

对应 docs/comfyui-backend-api.md：
- POST /prompt：核心调度点。同一时刻只向真实 ComfyUI 放行 N 个任务（N=serialize_concurrent，
  建议 1 = 单飞），其余在中转站内阻塞排队；提交成功后**立即**返回 prompt_id。
- GET /history/{prompt_id}：排队中（尚未提交真实后端）返回空 {}，避免插件误判完成。
- POST /upload/image：透传（multipart 原样转发，保证 name 落到真实 ComfyUI input 目录）。
- GET /history、GET /view：透传。
- 槽位占用覆盖任务完整生命周期（提交 -> 执行 -> 出图），由 _watch 轮询释放。
"""
import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

from scheduler import BackendError, Task

logger = logging.getLogger('middle_station.comfyui')


class ComfyAdapter:
    def __init__(self, cfg):
        self.base_url = cfg.comfyui.base_url.rstrip('/')
        self.watch_interval = cfg.comfyui.watch_interval
        self.watch_timeout = cfg.comfyui.watch_timeout
        self.sem = asyncio.Semaphore(cfg.comfyui.serialize_concurrent)
        self.states: Dict[str, str] = {}      # prompt_id -> queued/running/done/failed
        self.task_ids: Dict[str, str] = {}    # prompt_id -> task_id
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0, read=cfg.server.infer_timeout + 30.0,
                write=30.0, pool=10.0))

    async def close(self):
        await self._client.aclose()

    # ---------- 核心：提交任务（单飞调度） ----------

    async def submit_prompt(self, task: Task):
        """worker 执行体：等待单飞槽位 -> 转发 -> 立即返回 {prompt_id}。

        注意：槽位在整个任务生命周期内占用（提交 -> 执行 -> 出图），
        由后台 _watch 轮询真实 /history 确认完成后释放。
        """
        payload = task.payload
        async with self.sem:
            try:
                resp = await self._client.post(f'{self.base_url}/prompt', json=payload)
            except httpx.TimeoutException as e:
                raise BackendError(504, f'upstream submit timeout: {e}') from e
            except httpx.RequestError as e:
                raise BackendError(500, f'upstream unreachable: {e}') from e
            if resp.status_code != 200:
                # 上游校验失败（如 400），原样回传，插件依赖状态码/文案展示
                raise BackendError(resp.status_code, resp.text[:300] or f'upstream http {resp.status_code}')
            try:
                data = resp.json()
            except ValueError as e:
                raise BackendError(500, f'bad upstream json: {e}') from e
            pid = data.get('prompt_id')
            if not pid:
                raise BackendError(500, 'upstream returned no prompt_id')
            self.states[pid] = 'running'
            self.task_ids[pid] = task.task_id
            asyncio.create_task(self._watch(pid))
            return data   # {'prompt_id': pid}，立即返回给插件

    async def _watch(self, prompt_id: str):
        """轮询真实 /history 直至该 prompt_id 完成；超时标记 failed。"""
        deadline = time.time() + self.watch_timeout
        try:
            while time.time() < deadline:
                await asyncio.sleep(self.watch_interval)
                try:
                    r = await self._client.get(f'{self.base_url}/history/{prompt_id}')
                except httpx.RequestError:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    hist = r.json()
                except ValueError:
                    continue
                if prompt_id in hist:
                    self.states[prompt_id] = 'done'
                    return
            self.states[prompt_id] = 'failed'
        except Exception:  # noqa: BLE001
            self.states[prompt_id] = 'failed'

    # ---------- 只读 / 上传透传 ----------

    async def history_one(self, prompt_id: str) -> Tuple[int, bytes, Optional[str]]:
        """GET /history/{prompt_id}。排队中未提交 -> 返回空 {}。"""
        if self.states.get(prompt_id) == 'queued':
            return 200, b'{}', 'application/json'
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
            'serialize_concurrent': self.sem._value,  # noqa: SLF001
            'states': dict(self.states),
        }
