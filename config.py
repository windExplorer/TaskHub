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


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


@dataclass
class ServerConfig:
    host: str = '0.0.0.0'
    port: int = 9000
    max_concurrent: int = 3
    # 硬约束：queue_wait + infer_timeout ≤ 调用方读超时 − 10s 余量
    # （AstrBot 语音插件默认 150s → 两者之和保持 ≤ 140，否则插件先断连、音频白算）
    queue_wait: int = 30          # 入队等待超时（秒），超时回 429
    infer_timeout: int = 110      # 单次推理超时（秒），超时回 504
    db_path: str = './middle-station.db'
    log_max_lines: int = 100
    frontend_dir: str = './webui/dist'
    # 任务类型切换后、执行新任务前先清理残留显存（当前仅 ComfyUI 侧有卸载接口）
    vram_cleanup_on_type_switch: bool = True
    # 业务开关：拒绝接某类新任务（在跑的不受影响）。运行时改内存即生效。
    tasks_enabled: Dict[str, bool] = field(default_factory=lambda: {'tts': True, 'comfyui': True})


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
    watch_interval: float = 2.0                # 轮询真实 /history 间隔
    watch_timeout: float = 900.0               # 出图跟踪硬超时（秒），0 = 不限制
    watch_lost_grace: float = 30.0             # 提交后 N 秒起核对真实 /queue，判定 prompt 是否被上游丢弃
    watch_lost_confirm: int = 2                # 连续 N 次核对都既不在 /queue 也不在 /history 才判定「丢失」
    progress_stream: bool = True               # 订阅真实 ComfyUI /ws 拿真实进度（心跳/节点/步数）
    client_id: Optional[str] = None            # 提交给真实 ComfyUI 的 client_id（None = 自动生成 taskhub-xxxx）
    free_vram_on_type_switch: bool = True      # 任务类型切离 comfyui 时调用真实 /free 卸载模型释放显存


@dataclass
class MonitoringConfig:
    gpu_threshold: float = 0.8                 # GPU 算力占用率阈值（超过则降并发）
    vram_threshold: float = 0.25               # 可用显存比例下限（低于则降并发，防 OOM）
    vram_min_free_gb: float = 1.0              # 可用显存绝对下限（GB），低于则降并发
    interval_seconds: float = 2.0              # 采样间隔


@dataclass
class InspectorConfig:
    """上游自检巡检：周期性核对「调度器认为在跑的任务」与「真实上游」是否一致。"""
    enabled: bool = True
    interval: float = 10.0                     # 巡检周期（秒）
    slow_factor: float = 3.0                   # 耗时 > 同类任务基线 P95 × factor 视为异常（只是告警，不杀任务）
    slow_min_seconds: float = 60.0             # 基线阈值下限（秒），样本不足时用它兜底
    stall_seconds: float = 240.0               # 上游在跑但这么久没有任何进度事件 → 疑似卡住（告警）
    history_hours: float = 24.0                # 计算耗时基线用的历史窗口
    baseline_ttl: float = 300.0                # 耗时基线缓存有效期（秒）
    system_stats_every: int = 3                # 每 N 轮巡检查一次 /system_stats（上游显存）


@dataclass
class NsfwConfig:
    """出图后自动送检 nsfw-check（独立检测服务，见 X:/_WorkSpace/Mine/nsfw-check）。

    mode:
      label — 检测结论随 history 返回（entry['nsfw']），是否拦截由调用方决定；
      block — action=block 的图不返回，插件直接收到「生成失败（NSFW 拦截）」。
    output_dir: ComfyUI output 目录。填写后送检带 file:// 引用（检测服务与中转站
      同机时不重复存图）；留空则检测服务按自己的 SAVE_MODE 决定是否落盘。
    """
    enabled: bool = False
    base_url: str = 'http://127.0.0.1:8890'
    token: str = ''
    mode: str = 'label'                        # label | block
    timeout: float = 25.0                      # 单次检测 HTTP 超时（秒）
    wait_seconds: float = 15.0                 # history 轮询等待检测完成的上限（秒）
    output_dir: str = ''                       # ComfyUI output 目录（可选）


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    comfyui: ComfyUIConfig = field(default_factory=ComfyUIConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    inspector: InspectorConfig = field(default_factory=InspectorConfig)
    nsfw: NsfwConfig = field(default_factory=NsfwConfig)

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
        cfg.server.vram_cleanup_on_type_switch = _env_bool(
            'MIDDLE_VRAM_CLEANUP_ON_TYPE_SWITCH',
            bool(s.get('vram_cleanup_on_type_switch', cfg.server.vram_cleanup_on_type_switch)))
        te = s.get('tasks_enabled', {}) or {}
        cfg.server.tasks_enabled = {
            'tts': bool(te.get('tts', True)),
            'comfyui': bool(te.get('comfyui', True)),
        }

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
        cfg.comfyui.watch_lost_grace = _env_float(
            'MIDDLE_COMFYUI_WATCH_LOST_GRACE',
            float(c.get('watch_lost_grace', cfg.comfyui.watch_lost_grace)))
        cfg.comfyui.watch_lost_confirm = _env_int(
            'MIDDLE_COMFYUI_WATCH_LOST_CONFIRM',
            int(c.get('watch_lost_confirm', cfg.comfyui.watch_lost_confirm)))
        cfg.comfyui.progress_stream = _env_bool(
            'MIDDLE_COMFYUI_PROGRESS_STREAM',
            bool(c.get('progress_stream', cfg.comfyui.progress_stream)))
        cfg.comfyui.client_id = _env('MIDDLE_COMFYUI_CLIENT_ID') or c.get('client_id') or None
        cfg.comfyui.free_vram_on_type_switch = _env_bool(
            'MIDDLE_COMFYUI_FREE_VRAM_ON_TYPE_SWITCH',
            bool(c.get('free_vram_on_type_switch', cfg.comfyui.free_vram_on_type_switch)))

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

        # inspector
        i = data.get('inspector', {}) or {}
        cfg.inspector.enabled = _env_bool(
            'MIDDLE_INSPECTOR_ENABLED', bool(i.get('enabled', cfg.inspector.enabled)))
        cfg.inspector.interval = _env_float(
            'MIDDLE_INSPECTOR_INTERVAL', float(i.get('interval', cfg.inspector.interval)))
        cfg.inspector.slow_factor = _env_float(
            'MIDDLE_INSPECTOR_SLOW_FACTOR', float(i.get('slow_factor', cfg.inspector.slow_factor)))
        cfg.inspector.slow_min_seconds = _env_float(
            'MIDDLE_INSPECTOR_SLOW_MIN', float(i.get('slow_min_seconds', cfg.inspector.slow_min_seconds)))
        cfg.inspector.stall_seconds = _env_float(
            'MIDDLE_INSPECTOR_STALL', float(i.get('stall_seconds', cfg.inspector.stall_seconds)))
        cfg.inspector.history_hours = _env_float(
            'MIDDLE_INSPECTOR_HISTORY_HOURS', float(i.get('history_hours', cfg.inspector.history_hours)))
        cfg.inspector.baseline_ttl = _env_float(
            'MIDDLE_INSPECTOR_BASELINE_TTL', float(i.get('baseline_ttl', cfg.inspector.baseline_ttl)))
        cfg.inspector.system_stats_every = _env_int(
            'MIDDLE_INSPECTOR_SYSTEM_STATS_EVERY',
            int(i.get('system_stats_every', cfg.inspector.system_stats_every)))

        # nsfw
        n = data.get('nsfw', {}) or {}
        cfg.nsfw.enabled = _env_bool('MIDDLE_NSFW_ENABLED', bool(n.get('enabled', cfg.nsfw.enabled)))
        cfg.nsfw.base_url = _env('MIDDLE_NSFW_BASE_URL') or n.get('base_url', cfg.nsfw.base_url)
        cfg.nsfw.token = _env('MIDDLE_NSFW_TOKEN') or n.get('token', cfg.nsfw.token)
        cfg.nsfw.mode = _env('MIDDLE_NSFW_MODE') or n.get('mode', cfg.nsfw.mode)
        cfg.nsfw.timeout = _env_float('MIDDLE_NSFW_TIMEOUT', float(n.get('timeout', cfg.nsfw.timeout)))
        cfg.nsfw.wait_seconds = _env_float(
            'MIDDLE_NSFW_WAIT_SECONDS', float(n.get('wait_seconds', cfg.nsfw.wait_seconds)))
        cfg.nsfw.output_dir = _env('MIDDLE_NSFW_OUTPUT_DIR') or n.get('output_dir', cfg.nsfw.output_dir)

        return cfg

    def to_dict(self) -> Dict:
        """导出为可写回 yaml 的字典（只含文件字段，不含运行时探测值）。"""
        return {
            'server': {
                'host': self.server.host,
                'port': self.server.port,
                'max_concurrent': self.server.max_concurrent,
                'queue_wait': self.server.queue_wait,
                'infer_timeout': self.server.infer_timeout,
                'db_path': self.server.db_path,
                'log_max_lines': self.server.log_max_lines,
                'frontend_dir': self.server.frontend_dir,
                'vram_cleanup_on_type_switch': self.server.vram_cleanup_on_type_switch,
                'tasks_enabled': dict(self.server.tasks_enabled),
            },
            'tts': {
                'base_url': self.tts.base_url,
                'sample_rate': self.tts.sample_rate,
                'voices_dir': self.tts.voices_dir,
                'voices_json': self.tts.voices_json,
                'voices': dict(self.tts.voices),
            },
            'comfyui': {
                'base_url': self.comfyui.base_url,
                'serialize_concurrent': self.comfyui.serialize_concurrent,
                'watch_interval': self.comfyui.watch_interval,
                'watch_timeout': self.comfyui.watch_timeout,
                'watch_lost_grace': self.comfyui.watch_lost_grace,
                'watch_lost_confirm': self.comfyui.watch_lost_confirm,
                'progress_stream': self.comfyui.progress_stream,
                'client_id': self.comfyui.client_id,
                'free_vram_on_type_switch': self.comfyui.free_vram_on_type_switch,
            },
            'monitoring': {
                'gpu_threshold': self.monitoring.gpu_threshold,
                'vram_threshold': self.monitoring.vram_threshold,
                'vram_min_free_gb': self.monitoring.vram_min_free_gb,
                'interval_seconds': self.monitoring.interval_seconds,
            },
            'inspector': {
                'enabled': self.inspector.enabled,
                'interval': self.inspector.interval,
                'slow_factor': self.inspector.slow_factor,
                'slow_min_seconds': self.inspector.slow_min_seconds,
                'stall_seconds': self.inspector.stall_seconds,
                'history_hours': self.inspector.history_hours,
                'baseline_ttl': self.inspector.baseline_ttl,
                'system_stats_every': self.inspector.system_stats_every,
            },
            'nsfw': {
                'enabled': self.nsfw.enabled,
                'base_url': self.nsfw.base_url,
                'token': self.nsfw.token,
                'mode': self.nsfw.mode,
                'timeout': self.nsfw.timeout,
                'wait_seconds': self.nsfw.wait_seconds,
                'output_dir': self.nsfw.output_dir,
            },
        }

    def save(self, path: Optional[str] = None) -> str:
        """写回 yaml（保留原文件为 .bak）。返回写入路径。"""
        import shutil
        import yaml
        path = path or _env('MIDDLE_CONFIG', DEFAULT_CONFIG_PATH)
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + '.bak')
            except OSError as e:
                logger.warning('backup config failed: %s', e)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.to_dict(), f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
        logger.info('saved config to %s', path)
        return path
