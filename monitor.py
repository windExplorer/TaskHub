"""资源监控：CPU / RAM / GPU / VRAM 采集 + 历史环形缓冲。

GPU 通过 NVML（nvidia-ml-py，模块名 pynvml 或 nvidia_smi）读取；
无 NVIDIA GPU / 未装驱动时 GPU 指标返回 0，调度器自动退化为纯并发控制。

除周期采样（调度器降并发用）外，还提供 `device_status()`：即时采集一份**完整**设备状态
（CPU 型号/频率、内存、磁盘、本进程占用，以及每张卡的型号/利用率/显存/温度/功耗/风扇/频率），
供 `GET /device` 使用。动态指标逐项 try，某项不支持（如被动散热卡无风扇转速）不影响其它项。
"""
import asyncio
import logging
import os
import platform
import socket
import time
from collections import deque
from typing import Dict, List, Optional

import psutil

logger = logging.getLogger('middle_station.monitor')

try:
    import pynvml
    nvml = pynvml
except ImportError:  # nvidia-ml-py 部分版本模块名为 nvidia_smi
    try:
        import nvidia_smi as nvml  # type: ignore
    except ImportError:
        nvml = None

_TEMP_GPU = getattr(nvml, 'NVML_TEMPERATURE_GPU', 0) if nvml else 0
_CLOCK_GRAPHICS = getattr(nvml, 'NVML_CLOCK_GRAPHICS', 0) if nvml else 0


class ResourceMonitor:
    """后台定时采集资源指标，供调度器 / 监控接口 / WebSocket 使用。"""

    def __init__(self, interval: float = 2.0, gpu_threshold: float = 0.8):
        self.interval = interval
        self.gpu_threshold = gpu_threshold

        self.cpu: float = 0.0
        self.ram_total: int = 0
        self.ram_available: int = 0
        self.ram_used: int = 0

        self.gpu: float = 0.0        # 0~1（第 0 张卡，调度器降并发看它）
        self.gpu_free_gb: float = 0.0
        self.gpu_total_gb: float = 0.0
        self.nvidia_ok: bool = False
        self.gpus: List[Dict] = []   # 各卡动态指标（型号/利用率/显存/温度/功耗/风扇/频率）

        self.history: deque = deque(maxlen=7200)   # (timestamp, snapshot)
        self._task: Optional[asyncio.Task] = None

        # 静态信息（启动时读一次）：CPU 型号/核数、主机名、磁盘挂载点、各卡型号与显存总量、驱动
        self.hostname: str = ''
        self.os_name: str = ''
        self.cpu_model: str = ''
        self.cpu_cores_physical: Optional[int] = psutil.cpu_count(logical=False)
        self.cpu_cores_logical: Optional[int] = psutil.cpu_count(logical=True)
        self.boot_ts: float = psutil.boot_time()
        self.disk_path: str = os.path.abspath('.')
        self.driver_version: str = ''
        self.gpu_static: List[Dict] = []
        self.start_ts: float = time.time()

        self._init_nvml()
        self._init_static()

    def _init_nvml(self):
        if nvml is None:
            logger.warning('NVML 不可用（未安装 nvidia-ml-py 或无 NVIDIA 驱动），GPU 指标恒为 0')
            self.nvidia_ok = False
            return
        try:
            nvml.nvmlInit()
            self.nvidia_ok = True
            logger.info('NVML 初始化成功')
        except Exception as e:  # noqa: BLE001
            logger.warning('NVML 初始化失败，GPU 指标恒为 0: %s', e)
            self.nvidia_ok = False

    def _init_static(self):
        """启动时读一次主机/显卡的静态信息（型号、核数、驱动版本等）。"""
        try:
            self.hostname = socket.gethostname()
        except Exception:  # noqa: BLE001
            self.hostname = ''
        self.os_name = f'{platform.system()} {platform.release()}'
        self.cpu_model = self._cpu_model()

        if not self.nvidia_ok:
            return
        try:
            dv = nvml.nvmlSystemGetDriverVersion()
            self.driver_version = dv.decode() if isinstance(dv, bytes) else str(dv)
        except Exception:  # noqa: BLE001
            self.driver_version = ''
        try:
            for i in range(nvml.nvmlDeviceGetCount()):
                h = nvml.nvmlDeviceGetHandleByIndex(i)
                name = nvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode()
                try:
                    mi = nvml.nvmlDeviceGetMemoryInfo(h)
                    total_gb = round(mi.total / (1024 ** 3), 2)
                except Exception:  # noqa: BLE001
                    total_gb = 0.0
                self.gpu_static.append({'index': i, 'name': str(name), 'mem_total_gb': total_gb})
            logger.info('检测到 %d 张 NVIDIA GPU：%s（驱动 %s）',
                        len(self.gpu_static),
                        ', '.join(g['name'] for g in self.gpu_static), self.driver_version or '?')
        except Exception as e:  # noqa: BLE001
            logger.debug('GPU static info failed: %s', e)

    @staticmethod
    def _cpu_model() -> str:
        """CPU 型号：Windows 读注册表拿市场名，其它平台退化到 platform.processor()。"""
        if os.name == 'nt':
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as k:
                    return str(winreg.QueryValueEx(k, 'ProcessorNameString')[0]).strip()
            except Exception:  # noqa: BLE001
                pass
        return platform.processor() or ''

    def _read_metrics(self) -> Dict:
        """读一次动态指标（不含写历史）：CPU / RAM / 每张 GPU。"""
        self.cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        self.ram_total = vm.total
        self.ram_available = vm.available
        self.ram_used = vm.used

        if self.nvidia_ok:
            self.gpus = []
            try:
                count = nvml.nvmlDeviceGetCount()
            except Exception as e:  # noqa: BLE001
                logger.debug('GPU count failed: %s', e)
                count = 0
            for i in range(count):
                row = dict(next((g for g in self.gpu_static if g['index'] == i), None)
                           or {'index': i, 'name': '', 'mem_total_gb': 0.0})
                try:
                    h = nvml.nvmlDeviceGetHandleByIndex(i)
                    row['util_percent'] = nvml.nvmlDeviceGetUtilizationRates(h).gpu
                    mi = nvml.nvmlDeviceGetMemoryInfo(h)
                    row['mem_total_gb'] = round(mi.total / (1024 ** 3), 2)
                    row['mem_used_gb'] = round(mi.used / (1024 ** 3), 2)
                    row['mem_free_gb'] = round(mi.free / (1024 ** 3), 2)
                    row['mem_percent'] = round(mi.used / mi.total * 100, 1) if mi.total else 0.0
                    row.update(self._gpu_extras(h))
                    self.gpus.append(row)
                except Exception as e:  # noqa: BLE001
                    logger.debug('GPU %d sample failed: %s', i, e)
            if self.gpus:
                g0 = self.gpus[0]
                self.gpu = (g0.get('util_percent') or 0) / 100.0
                self.gpu_free_gb = g0.get('mem_free_gb') or 0.0
                self.gpu_total_gb = g0.get('mem_total_gb') or 0.0
            else:
                self.gpu = 0.0

        return self.snapshot()

    @staticmethod
    def _gpu_extras(handle) -> Dict:
        """温度 / 功耗 / 上限 / 风扇 / 频率：逐项 try，老卡或被动散热缺项就省略。"""
        out: Dict = {}
        try:
            out['temperature_c'] = nvml.nvmlDeviceGetTemperature(handle, _TEMP_GPU)
        except Exception:  # noqa: BLE001
            pass
        try:
            out['power_w'] = round(nvml.nvmlDeviceGetPowerUsage(handle) / 1000.0, 1)
        except Exception:  # noqa: BLE001
            pass
        try:
            out['power_limit_w'] = round(nvml.nvmlDeviceGetEnforcedPowerLimit(handle) / 1000.0, 1)
        except Exception:  # noqa: BLE001
            pass
        try:
            out['fan_percent'] = nvml.nvmlDeviceGetFanSpeed(handle)
        except Exception:  # noqa: BLE001
            pass
        try:
            out['clock_mhz'] = nvml.nvmlDeviceGetClockInfo(handle, _CLOCK_GRAPHICS)
        except Exception:  # noqa: BLE001
            pass
        try:
            out['mem_clock_mhz'] = nvml.nvmlDeviceGetClockInfo(
                handle, getattr(nvml, 'NVML_CLOCK_MEM', 2))
        except Exception:  # noqa: BLE001
            pass
        return out

    def sample(self) -> Dict:
        """采集一次当前资源快照并写入历史（后台周期采样用）。"""
        snap = self._read_metrics()
        self.history.append((time.time(), snap))
        return snap

    def device_status(self) -> Dict:
        """即时采集一份完整设备状态（GET /device 用，不写入历史、不依赖采样周期）。"""
        self._read_metrics()
        now = time.time()

        disk = {}
        try:
            du = psutil.disk_usage(self.disk_path)
            disk = {
                'path': self.disk_path,
                'total_gb': round(du.total / (1024 ** 3), 1),
                'used_gb': round(du.used / (1024 ** 3), 1),
                'free_gb': round(du.free / (1024 ** 3), 1),
                'percent': du.percent,
            }
        except Exception as e:  # noqa: BLE001
            logger.debug('disk usage failed: %s', e)

        process = {}
        try:
            p = psutil.Process(os.getpid())
            with p.oneshot():
                process = {
                    'pid': p.pid,
                    'uptime_s': round(now - p.create_time(), 1),
                    'threads': p.num_threads(),
                    'rss_gb': round(p.memory_info().rss / (1024 ** 3), 3),
                    'cpu_percent': p.cpu_percent(interval=None),
                }
        except Exception as e:  # noqa: BLE001
            logger.debug('process info failed: %s', e)

        freq = {}
        try:
            f = psutil.cpu_freq()
            if f:
                freq = {'current_mhz': round(f.current), 'max_mhz': round(f.max) if f.max else None}
        except Exception:  # noqa: BLE001
            pass

        return {
            'ts': now,
            'hostname': self.hostname,
            'os': self.os_name,
            'boot_time': self.boot_ts,
            'host_uptime_s': round(now - self.boot_ts, 1),
            'service_uptime_s': round(now - self.start_ts, 1),
            'cpu': {
                'model': self.cpu_model,
                'percent': round(self.cpu, 1),
                'cores_physical': self.cpu_cores_physical,
                'cores_logical': self.cpu_cores_logical,
                **freq,
            },
            'ram': {
                'total_gb': round(self.ram_total / (1024 ** 3), 2),
                'used_gb': round(self.ram_used / (1024 ** 3), 2),
                'available_gb': round(self.ram_available / (1024 ** 3), 2),
                'percent': round(self.ram_used / self.ram_total * 100, 1) if self.ram_total else 0.0,
            },
            'disk': disk,
            'process': process,
            'nvidia_ok': self.nvidia_ok,
            'driver_version': self.driver_version,
            'gpus': self.gpus,
        }

    def snapshot(self) -> Dict:
        return {
            'cpu_percent': round(self.cpu, 1),
            'ram_available_gb': round(self.ram_available / (1024 ** 3), 2),
            'ram_used_gb': round(self.ram_used / (1024 ** 3), 2),
            'ram_total_gb': round(self.ram_total / (1024 ** 3), 2),
            'gpu_load': round(self.gpu, 3),
            'gpu_free_gb': round(self.gpu_free_gb, 2),
            'gpu_total_gb': round(self.gpu_total_gb, 2),
            'gpu_threshold': self.gpu_threshold,
        }

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while True:
            try:
                self.sample()
            except Exception as e:  # noqa: BLE001
                logger.warning('monitor sample failed: %s', e)
            await asyncio.sleep(self.interval)
