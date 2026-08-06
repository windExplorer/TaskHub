"""资源监控：CPU / RAM / GPU / VRAM 采集 + 历史环形缓冲。

GPU 通过 NVML（nvidia-ml-py，模块名 pynvml 或 nvidia_smi）读取；
无 NVIDIA GPU / 未装驱动时 GPU 指标返回 0，调度器自动退化为纯并发控制。
"""
import asyncio
import logging
import time
from collections import deque
from typing import Dict, Optional

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


class ResourceMonitor:
    """后台定时采集资源指标，供调度器 / 监控接口 / WebSocket 使用。"""

    def __init__(self, interval: float = 2.0, gpu_threshold: float = 0.8):
        self.interval = interval
        self.gpu_threshold = gpu_threshold

        self.cpu: float = 0.0
        self.ram_total: int = 0
        self.ram_available: int = 0
        self.ram_used: int = 0

        self.gpu: float = 0.0        # 0~1
        self.gpu_free_gb: float = 0.0
        self.gpu_total_gb: float = 0.0
        self.nvidia_ok: bool = False

        self.history: deque = deque(maxlen=7200)   # (timestamp, snapshot)
        self._task: Optional[asyncio.Task] = None
        self._init_nvml()

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

    def sample(self) -> Dict:
        """采集一次当前资源快照并写入历史。"""
        self.cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        self.ram_total = vm.total
        self.ram_available = vm.available
        self.ram_used = vm.used

        if self.nvidia_ok:
            try:
                handle = nvml.nvmlDeviceGetHandleByIndex(0)
                self.gpu = nvml.nvmlDeviceGetUtilizationRates(handle).gpu / 100.0
                mi = nvml.nvmlDeviceGetMemoryInfo(handle)
                self.gpu_free_gb = mi.free / (1024 ** 3)
                self.gpu_total_gb = mi.total / (1024 ** 3)
            except Exception as e:  # noqa: BLE001
                logger.debug('GPU sample failed: %s', e)
                self.gpu = 0.0

        snap = self.snapshot()
        self.history.append((time.time(), snap))
        return snap

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
