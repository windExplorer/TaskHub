"""TTS 适配层：对接 CosyVoice 标准接口（multipart 表单转发）。

对应 docs/backend-api.md §2 标准接口契约：
- GET  /                     健康检查 + 真实采样率（插件首次合成前必读）
- POST /inference_zero_shot  零样本合成，返回裸 int16 PCM（无 WAV 头）
- POST /inference_instruct2  指令合成（兼容保留）
- GET  /voices               参考音频列表（排错用）

状态码映射（§3）：
- 上游非 200 原样透传（400/429/503/504/500）
- 上游超时 -> 504；上游失联 -> 500；空音频 -> 500
"""
import asyncio
import logging
import os
import time
from typing import Dict, Optional

import httpx

from scheduler import BackendError, Task

logger = logging.getLogger('middle_station.tts')


def clean_prompt_text(text: Optional[str]) -> Optional[str]:
    """对照插件 `_looks_polluted`：污染则丢弃，交由后端 voices.json 回退。

    污染判定：长度 > 150，或含 LLM 标记（如 <|endofprompt|>）、system 片段。
    """
    if not text:
        return None
    t = text.strip()
    if len(t) > 150:
        return None
    low = t.lower()
    if '<|endofprompt|>' in low or 'system' in low[:200]:
        return None
    return t


class TTSAdapter:
    def __init__(self, cfg, probe_interval: float = 30.0):
        self.base_url = cfg.tts.base_url.rstrip('/')
        self.sample_rate = cfg.tts.sample_rate
        self.model_loaded = True
        self.voices: Dict[str, str] = dict(cfg.tts.voices or {})
        self.voices_dir = cfg.tts.voices_dir
        self.voices_json = cfg.tts.voices_json
        self.probe_interval = probe_interval

        # read 超时必须大于「排队最坏时长 + 单次推理最坏时长」
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0, read=cfg.server.infer_timeout + 30.0,
                write=30.0, pool=10.0))
        self._probe_task: Optional[asyncio.Task] = None
        self._load_voices()

    # ---------- 参考音频映射 ----------

    def _load_voices(self):
        """加载 文件名 -> prompt_text 映射（voices.json 优先，其次扫描 voices_dir）。"""
        merged: Dict[str, str] = {}
        if self.voices_json and os.path.exists(self.voices_json):
            try:
                import json
                with open(self.voices_json, 'r', encoding='utf-8') as f:
                    merged.update(json.load(f))
            except Exception as e:  # noqa: BLE001
                logger.warning('load voices_json failed: %s', e)
        if self.voices_dir and os.path.isdir(self.voices_dir):
            for name in sorted(os.listdir(self.voices_dir)):
                if name.lower().endswith(('.wav', '.mp3', '.flac', '.ogg')):
                    merged.setdefault(name, '')
        # 配置内联 voices 覆盖
        merged.update({k: v for k, v in self.voices.items() if v})
        self.voices = merged
        logger.info('voices loaded: %d entries', len(self.voices))

    def voice_text(self, wav_path: str) -> Optional[str]:
        base = os.path.basename(wav_path.replace('\\', '/'))
        return self.voices.get(base) or self.voices.get(wav_path)

    # ---------- 探测 / 健康检查 ----------

    async def probe(self) -> Optional[Dict]:
        """GET /：拉取后端真实采样率，覆盖本地配置（否则音调会变）。"""
        try:
            r = await self._client.get(f'{self.base_url}/', timeout=10.0)
            if r.status_code == 200:
                data = r.json()
                sr = data.get('sample_rate')
                if sr:
                    sr = int(sr)
                    if sr != self.sample_rate:
                        logger.info('backend sample_rate %s (was %s), overriding', sr, self.sample_rate)
                    self.sample_rate = sr
                self.model_loaded = bool(data.get('model_loaded', True))
                return data
            self.model_loaded = False
        except Exception as e:  # noqa: BLE001
            self.model_loaded = False
            logger.warning('backend probe failed (%s): %s', self.base_url, e)
        return None

    async def start(self):
        await self.probe()
        self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop(self):
        if self._probe_task:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _probe_loop(self):
        while True:
            await asyncio.sleep(self.probe_interval)
            try:
                await self.probe()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 合成执行体（worker 调用） ----------

    async def synthesize(self, task: Task):
        """POST /inference_zero_shot 转发，返回裸 int16 PCM 字节。"""
        payload = task.payload
        tts_text = payload['tts_text']
        prompt_text = payload.get('prompt_text')
        wav_path = payload.get('prompt_wav_path')
        wav_file = payload.get('prompt_wav')   # (filename, bytes) | None

        data: Dict[str, str] = {'tts_text': tts_text}
        if prompt_text:
            data['prompt_text'] = prompt_text
        files = None
        if wav_file:
            files = {'prompt_wav': (wav_file[0], wav_file[1], 'audio/wav')}
        elif wav_path:
            data['prompt_wav_path'] = wav_path

        r = await self._post(f'{self.base_url}/inference_zero_shot', data=data, files=files)
        return r

    async def synthesize_instruct(self, task: Task):
        """POST /inference_instruct2 转发（兼容保留）。"""
        payload = task.payload
        data: Dict[str, str] = {
            'tts_text': payload['tts_text'],
            'instruct_text': payload['instruct_text'],
        }
        wav_path = payload.get('prompt_wav_path')
        wav_file = payload.get('prompt_wav')
        files = None
        if wav_file:
            files = {'prompt_wav': (wav_file[0], wav_file[1], 'audio/wav')}
        elif wav_path:
            data['prompt_wav_path'] = wav_path
        r = await self._post(f'{self.base_url}/inference_instruct2', data=data, files=files)
        return r

    async def _post(self, url: str, data: Dict, files=None) -> bytes:
        try:
            resp = await self._client.post(url, data=data, files=files)
        except httpx.TimeoutException as e:
            raise BackendError(504, f'upstream inference timeout: {e}') from e
        except httpx.RequestError as e:
            raise BackendError(500, f'upstream unreachable: {e}') from e
        if resp.status_code != 200:
            raise BackendError(resp.status_code, resp.text[:200] or f'upstream http {resp.status_code}')
        if not resp.content:
            raise BackendError(500, 'empty audio returned')
        return resp.content

    # ---------- 只读辅助 ----------

    async def voices_info(self) -> Dict:
        return {
            'voices_dir': self.voices_dir,
            'files': sorted(self.voices.keys()),
            'texts': {k: (v if v else '') for k, v in self.voices.items()},
        }

    def health(self) -> Dict:
        return {
            'status': 'ok' if self.model_loaded else 'loading',
            'model_loaded': self.model_loaded,
            'sample_rate': self.sample_rate,
        }
