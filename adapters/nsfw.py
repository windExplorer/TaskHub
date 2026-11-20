"""nsfw-check 送检适配：出图完成后把图片送独立检测服务（X:/_WorkSpace/Mine/nsfw-check）。

只做 HTTP 调用，失败一律降级为 {'available': False}，绝不阻塞出图主流程。
检测记录/自动复核结论由 nsfw-check 侧持久化（SQLite），中转站不重复保存。
"""
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger('middle_station.nsfw')


class NsfwChecker:
    def __init__(self, cfg):
        self.base_url = cfg.base_url.rstrip('/')
        self.token = cfg.token or ''
        self.mode = str(cfg.mode or 'label').lower()
        self.timeout = float(cfg.timeout)
        self.wait_seconds = float(cfg.wait_seconds)
        self.output_dir = str(cfg.output_dir or '')
        # 调度控制：送检前预热模型（消除卸载后的冷启动，实测 ~4s -> ~0.45s）
        self.prewarm = bool(getattr(cfg, 'prewarm', True))
        self._warm_lock = None  # 延迟创建（绑定事件循环）
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=self.timeout + 10.0, write=30.0, pool=5.0))

    @property
    def enabled(self) -> bool:
        return True

    def _headers(self) -> dict:
        h = {}
        if self.token:
            h['Authorization'] = f'Bearer {self.token}'
        return h

    def _image_url_for(self, subfolder: str, filename: str) -> str:
        """拼 ComfyUI output 文件的本地 file:// 引用（同机时不重复存图）。
        output_dir 未配置则返回空串，由检测服务按 SAVE_MODE 自行决定。"""
        if not self.output_dir:
            return ''
        try:
            full = Path(self.output_dir) / (subfolder or '') / filename
            return full.resolve().as_uri()
        except (OSError, ValueError):
            return ''

    async def check(self, image: bytes, filename: str,
                    subfolder: str = '') -> Optional[dict]:
        """送检一张图。返回 nsfw-check 的 detect JSON；失败返回降级 verdict。"""
        try:
            files = {'file': (filename, image, 'application/octet-stream')}
            data = {
                'source': 'taskhub',
                'image_url': self._image_url_for(subfolder, filename),
            }
            r = await self._client.post(
                f'{self.base_url}/detect', files=files, data=data,
                headers=self._headers())
            if r.status_code != 200:
                logger.warning('[nsfw] detect http %s: %s', r.status_code, r.text[:200])
                return {'available': False, 'error': f'http {r.status_code}'}
            body = r.json()
            return body
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] detect failed: %s', e)
            return {'available': False, 'error': str(e)}

    async def close(self):
        await self._client.aclose()

    def _get_warm_lock(self):
        """预热去重：同进程多个送检并发时只预热一次。"""
        if self._warm_lock is None:
            import asyncio
            self._warm_lock = asyncio.Lock()
        return self._warm_lock

    async def warm(self, force: bool = False) -> bool:
        """请 nsfw-check 预热/加载模型（POST /models/load）。

        非 force 时先探测：两模型都 loaded 就跳过，避免无谓开销。
        失败一律降级为 False，绝不阻塞送检主流程。
        """
        if not force:
            health = await self.probe(timeout=2.0)
            if isinstance(health, dict) and health.get('main_loaded') and health.get('aux_loaded'):
                return True
        lock = self._get_warm_lock()
        async with lock:
            try:
                r = await self._client.post(f'{self.base_url}/models/load',
                                            headers=self._headers(), timeout=self.timeout + 10.0)
                ok = r.status_code == 200
                if ok:
                    logger.info('[nsfw] models prewarmed')
                else:
                    logger.warning('[nsfw] warm http %s: %s', r.status_code, r.text[:200])
                return ok
            except Exception as e:  # noqa: BLE001
                logger.warning('[nsfw] warm failed: %s', e)
                return False

    async def release(self) -> bool:
        """请 nsfw-check 卸载模型（显存托管联动：切离 comfyui 任务时调用）。"""
        try:
            r = await self._client.post(f'{self.base_url}/models/unload',
                                        headers=self._headers(), timeout=15.0)
            ok = r.status_code == 200
            if ok:
                logger.info('[nsfw] models released (type-switch cleanup)')
            else:
                logger.warning('[nsfw] release http %s', r.status_code)
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] release failed: %s', e)
            return False

    def status(self) -> dict:
        return {'enabled': True, 'base_url': self.base_url, 'mode': self.mode,
                'prewarm': self.prewarm}

    async def probe(self, timeout: float = 3.0) -> Optional[dict]:
        """探测 nsfw-check 健康状态（GET /healthz）；失败返回 None，绝不抛异常。

        /healthz 无需 token。模型未加载（available/loaded=false）仍算服务可达，
        由前端按字段展示，避免把「预热中」误标成「服务不可用」。
        """
        try:
            r = await self._client.get(f'{self.base_url}/healthz', timeout=timeout)
            if r.status_code != 200:
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] probe failed: %s', e)
            return None
