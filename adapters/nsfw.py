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
        return {'enabled': True, 'base_url': self.base_url, 'mode': self.mode}
