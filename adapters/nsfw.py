"""nsfw-check 送检适配：出图完成后把图片送独立检测服务（X:/_WorkSpace/Mine/nsfw-check）。

只做 HTTP 调用，失败一律降级为 {'available': False}，绝不阻塞出图主流程。
检测记录/自动复核结论由 nsfw-check 侧持久化（SQLite），中转站不重复保存。
"""
import logging
from pathlib import Path
from typing import Optional, Tuple

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
        """送检一张图。返回 nsfw-check 的 detect JSON；失败返回降级 verdict。

        v0.3 起走 /detect/simple（精简响应，无部位坐标框等调试信息），
        业务字段 level/action/score/diverge/labels/record_id 齐全。
        simple 无 available 字段，200 即判定有效——补 True 维持
        history['nsfw'] 契约（comfy 聚合日志与插件门控都读它）。
        """
        try:
            files = {'file': (filename, image, 'application/octet-stream')}
            data = {
                'source': 'taskhub',
                'image_url': self._image_url_for(subfolder, filename),
            }
            r = await self._client.post(
                f'{self.base_url}/detect/simple', files=files, data=data,
                headers=self._headers())
            if r.status_code != 200:
                logger.warning('[nsfw] detect http %s: %s', r.status_code, r.text[:200])
                return {'available': False, 'error': f'http {r.status_code}'}
            body = r.json()
            body['available'] = bool(body.get('ok', True))
            return body
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] detect failed: %s', e)
            return {'available': False, 'error': str(e)}

    async def proxy_detect(self, body: bytes, content_type: str) -> Tuple[int, dict]:
        """把 multipart 请求体原样转发到 nsfw-check /detect/simple。

        插件指令远程检测的中转：插件无需配置 nsfw-check 地址/令牌（与本站
        送检共用同一配置），Token 注入也只发生在本站。返回 (status, json)；
        上游不可达等异常降级为 (502, {'ok': False, ...})，绝不抛异常。
        """
        try:
            r = await self._client.post(
                f'{self.base_url}/detect/simple',
                content=body,
                headers={**self._headers(), 'Content-Type': content_type},
            )
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = {'ok': False, 'error': f'invalid json from upstream (http {r.status_code})'}
            return r.status_code, payload
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] proxy detect failed: %s', e)
            return 502, {'ok': False, 'error': str(e)}

    async def detect_task(self, task) -> dict:
        """调度器 handler：把插件指令的检测请求作为任务执行。

        task.payload = {'body': bytes, 'ctype': str}
        返回 payload dict（直接对接 _submit_json 的 JSONResponse）。上游非 200 时
        把 status 写到 task.status_code，scheduler.submit() 据此回传错误码；
        同时回写 task.nsfw（带 available），让任务列表/WebUI 展示送检结论。
        """
        payload = task.payload or {}
        body = bytes(payload.get('body') or b'')
        ctype = str(payload.get('ctype') or 'application/octet-stream')
        # 预热放在任务执行阶段（而非调度器类型切换钩子），避免阻塞整个 worker。
        if self.prewarm:
            await self.warm()
        status, result = await self.proxy_detect(body, ctype)
        task.status_code = status
        if status != 200:
            task.error = (str(result.get('error')) if isinstance(result, dict) else '') \
                or f'upstream http {status}'
        verdict = dict(result) if isinstance(result, dict) else {'ok': False, 'error': str(result)}
        verdict['available'] = status == 200 and bool(verdict.get('ok', True))
        task.nsfw = verdict
        return verdict

    async def proxy_review(self, record_id: str, decision: str, note: str = '') -> Tuple[int, dict]:
        """人工复核结论写通中转：POST nsfw-check /records/{id}/review。

        decision 只接受 pass/block（与 nsfw-check 契约一致，端点层已校验）。
        返回 (status, json)；异常降级 (502, {...})。
        """
        try:
            r = await self._client.post(
                f'{self.base_url}/records/{record_id}/review',
                json={'decision': decision, 'note': note or 'synced from menghui review'},
                headers=self._headers(),
            )
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = {'ok': False, 'error': f'invalid json from upstream (http {r.status_code})'}
            return r.status_code, payload
        except Exception as e:  # noqa: BLE001
            logger.warning('[nsfw] proxy review failed: %s', e)
            return 502, {'ok': False, 'error': str(e)}

    async def close(self):
        await self._client.aclose()

    def _get_warm_lock(self):
        """预热去重：同进程多个送检并发时只预热一次。"""
        if self._warm_lock is None:
            import asyncio
            self._warm_lock = asyncio.Lock()
        return self._warm_lock

    # healthz enabled 键 -> loaded 字段名映射（v0.3 起 5 组模型）
    _LOADED_KEYS = {'main': 'main_loaded', 'nudenet': 'aux_loaded',
                    'anime_segm': 'anime_segm_loaded',
                    'nevermore': 'nevermore_loaded',
                    'wd_rating': 'wd_rating_loaded'}

    async def warm(self, force: bool = False) -> bool:
        """请 nsfw-check 预热/加载模型（POST /models/load）。

        非 force 时先探测：所有已启用模型都 loaded 才跳过（否则 nevermore/
        wd_rating 等副模型永远预热不到，首检吃冷启动）。
        失败一律降级为 False，绝不阻塞送检主流程。
        """
        if not force:
            health = await self.probe(timeout=2.0)
            if isinstance(health, dict):
                enabled = health.get('enabled') or {}
                if enabled:
                    if all(bool(health.get(self._LOADED_KEYS.get(name, '')))
                           for name, on in enabled.items() if on):
                        return True
                elif health.get('main_loaded') and health.get('aux_loaded'):
                    return True  # 旧版 healthz 无 enabled 字典，退回主/辅判定
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
