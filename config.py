"""配置系统：middle-station.yaml + 环境变量覆盖。

优先级：环境变量 > YAML 文件 > 内置默认值。
对应 docs/middle-station-plan.md 第 6 节。
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger('middle_station.config')

DEFAULT_CONFIG_PATH = 'middle-station.yaml'


def _env(name: str, default=None):
    v = os.environ.get(name)
    return v if v is not None and v != '' else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except (TypeError, ValueError):
        return default


@dataclass
class ServerConfig:
    host: str = '0.0.0.0'
    port: int = 9000
    max_concurrent: int = 3
    queue_wait: int = 30          # 入队等待超时（秒），超时回 429
    infer_timeout: int = 120      # 单次推理超时（秒），超时回 504
    db_path: str = './middle-station.db'
    log_max_lines: int = 100
    frontend_dir: str = './frontend'


@dataclass
class TTSConfig:
    base_url: str = 'http://127.0.0.1:50002'   # 真实 CosyVoice 后端
    sample_rate: int = 24000
    voices_dir: Optional[str] = None           # 服务端参考音频目录
    voices_json: Optional[str] = None          # 文件名 -> prompt_text 映射文件
    voices: Dict[str, str] = field(default_factory=dict)


@dataclass
class ComfyUIConfig:
    base_url: str = 'http://127.0.0.1:8188'    # 真实 ComfyUI
    serialize_concurrent: int = 1              # 同一时刻放行的 /prompt 数（单飞）
    watch_interval: float = 4.0                # 轮询真实 /history 间隔
    watch_timeout: float = 600.0               # 槽位跟踪超时（秒）


@dataclass
class MonitoringConfig:
    gpu_threshold: float = 0.8                 # GPU 算力占用率阈值（超过则降并发）
    vram_threshold: float = 0.25               # 可用显存比例下限（低于则降并发，防 OOM）
    vram_min_free_gb: float = 1.0              # 可用显存绝对下限（GB），低于则降并发
    interval_seconds: float = 2.0              # 采样间隔


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    comfyui: ComfyUIConfig = field(default_factory=ComfyUIConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'Config':
        path = path or _env('MIDDLE_CONFIG', DEFAULT_CONFIG_PATH)
        data: Dict = {}
        if os.path.exists(path):
            try:
                import yaml
                with open(path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                logger.info('loaded config from %s', path)
            except Exception as e:  # noqa: BLE001
                logger.warning('failed to load config %s: %s, using defaults', path, e)
        else:
            logger.info('config %s not found, using defaults', path)

        cfg = cls()

        # server
        s = data.get('server', {}) or {}
        cfg.server.host = _env('MIDDLE_HOST') or s.get('host', cfg.server.host)
        cfg.server.port = _env_int('MIDDLE_PORT', int(s.get('port', cfg.server.port)))
        cfg.server.max_concurrent = _env_int(
            'MIDDLE_MAX_CONCURRENT', int(s.get('max_concurrent', cfg.server.max_concurrent)))
        cfg.server.queue_wait = _env_int('MIDDLE_QUEUE_WAIT', int(s.get('queue_wait', cfg.server.queue_wait)))
        cfg.server.infer_timeout = _env_int('MIDDLE_INFER_TIMEOUT', int(s.get('infer_timeout', cfg.server.infer_timeout)))
        cfg.server.db_path = _env('MIDDLE_DB_PATH') or s.get('db_path', cfg.server.db_path)
        cfg.server.log_max_lines = _env_int('MIDDLE_LOG_MAX_LINES', int(s.get('log_max_lines', cfg.server.log_max_lines)))
        cfg.server.frontend_dir = _env('MIDDLE_FRONTEND_DIR') or s.get('frontend_dir', cfg.server.frontend_dir)

        # tts
        t = data.get('tts', {}) or {}
        cfg.tts.base_url = _env('MIDDLE_TTS_BASE_URL') or t.get('base_url', cfg.tts.base_url)
        cfg.tts.sample_rate = _env_int('MIDDLE_TTS_SAMPLE_RATE', int(t.get('sample_rate', cfg.tts.sample_rate)))
        cfg.tts.voices_dir = _env('MIDDLE_TTS_VOICES_DIR') or t.get('voices_dir')
        cfg.tts.voices_json = _env('MIDDLE_TTS_VOICES_JSON') or t.get('voices_json')
        cfg.tts.voices = dict(t.get('voices', {}) or {})

        # comfyui
        c = data.get('comfyui', {}) or {}
        cfg.comfyui.base_url = _env('MIDDLE_COMFYUI_BASE_URL') or c.get('base_url', cfg.comfyui.base_url)
        cfg.comfyui.serialize_concurrent = _env_int(
            'MIDDLE_COMFYUI_SERIALIZE', int(c.get('serialize_concurrent', cfg.comfyui.serialize_concurrent)))
        cfg.comfyui.watch_interval = _env_float(
            'MIDDLE_COMFYUI_WATCH_INTERVAL', float(c.get('watch_interval', cfg.comfyui.watch_interval)))
        cfg.comfyui.watch_timeout = _env_float(
            'MIDDLE_COMFYUI_WATCH_TIMEOUT', float(c.get('watch_timeout', cfg.comfyui.watch_timeout)))

        # monitoring
        m = data.get('monitoring', {}) or {}
        cfg.monitoring.gpu_threshold = _env_float(
            'MIDDLE_GPU_THRESHOLD', float(m.get('gpu_threshold', cfg.monitoring.gpu_threshold)))
        cfg.monitoring.vram_threshold = _env_float(
            'MIDDLE_VRAM_THRESHOLD', float(m.get('vram_threshold', cfg.monitoring.vram_threshold)))
        cfg.monitoring.vram_min_free_gb = _env_float(
            'MIDDLE_VRAM_MIN_FREE', float(m.get('vram_min_free_gb', cfg.monitoring.vram_min_free_gb)))
        cfg.monitoring.interval_seconds = _env_float(
            'MIDDLE_MONITOR_INTERVAL', float(m.get('interval_seconds', cfg.monitoring.interval_seconds)))

        return cfg
