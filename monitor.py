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
import subprocess
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
        self.os_info: Dict = {}
        self.cpu_model: str = ''
        self.cpu_cores_physical: Optional[int] = psutil.cpu_count(logical=False)
        self.cpu_cores_logical: Optional[int] = psutil.cpu_count(logical=True)
        self.boot_ts: float = psutil.boot_time()
        self.disk_path: str = os.path.abspath('.')
        self.driver_version: str = ''
        self.gpu_static: List[Dict] = []
        self.start_ts: float = time.time()

        # 传感器（CPU 温度/机箱风扇）探测结果缓存
        self._sensors: Dict = {}
        self._sensors_at: float = 0.0

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
        self.os_info = self._os_info()
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

    # Windows EditionID -> 中文版次（注册表里只有英文标识）
    _EDITION_CN = {
        'Core': '家庭版', 'CoreN': '家庭版', 'CoreSingleLanguage': '家庭单语言版',
        'CoreCountrySpecific': '家庭中文版', 'Professional': '专业版',
        'ProfessionalN': '专业版', 'ProfessionalWorkstation': '专业工作站版',
        'Enterprise': '企业版', 'EnterpriseN': '企业版', 'Education': '教育版',
        'EducationN': '教育版', 'ProfessionalEducation': '专业教育版',
        'IoTEnterprise': 'IoT 企业版', 'ServerStandard': '服务器标准版',
    }

    def _os_info(self) -> Dict:
        """系统版本详情。

        只用「免依赖、免子进程」的来源：`platform.win32_ver()` 判 Windows 11，
        注册表 `CurrentVersion` 拿版次/功能更新版本/内部版本号/UBR/安装时间
        （WMI 的 Caption 更友好但要起子进程，这里不划算）。
        注意注册表 `ProductName` 在 Win11 上仍写「Windows 10」（MS 历史遗留），不可信。
        """
        info: Dict = {
            'name': f'{platform.system()} {platform.release()}',
            'kernel': platform.version(),
            'arch': platform.machine(),
            'python': platform.python_version(),
        }
        if os.name == 'nt':
            try:
                import winreg
                cv = r'SOFTWARE\Microsoft\Windows NT\CurrentVersion'
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cv) as k:
                    def rd(name):
                        try:
                            return winreg.QueryValueEx(k, name)[0]
                        except Exception:  # noqa: BLE001
                            return None
                    edition_id = rd('EditionID') or ''
                    build = str(rd('CurrentBuildNumber') or rd('CurrentBuild') or '')
                    ubr = rd('UBR')
                    info.update({
                        'edition_id': edition_id,
                        'edition': self._EDITION_CN.get(edition_id, edition_id or None),
                        'display_version': rd('DisplayVersion') or rd('ReleaseId'),
                        'build': build or None,
                        'ubr': ubr,
                        'install_time': rd('InstallDate'),
                    })
            except Exception as e:  # noqa: BLE001
                logger.debug('os info failed: %s', e)
        # 组合展示名与完整版本号（10.0.26200.9550）
        edition = info.get('edition')
        info['caption'] = ' '.join(x for x in (info.get('name'), edition) if x)
        ver = info.get('kernel') or ''
        if info.get('ubr') is not None and ver:
            info['version'] = f'{ver}.{info["ubr"]}'
        else:
            info['version'] = ver or None
        return info

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

    # ---------- CPU 温度 / 机箱风扇（Windows 没有免驱来源） ----------

    async def hw_sensors(self, ttl: float = 15.0) -> Dict:
        """CPU 温度 / 主板温度 / 机箱风扇转速，带 TTL 缓存。

        Windows 上 CPU 封装温度只能由带内核驱动（WinRing0）的工具提供，纯 Python/WMI 读不到；
        因此优先读 LibreHardwareMonitor / OpenHardwareMonitor 的 WMI 命名空间（装了就有：
        CPU Package / 每核温度 / 主板温度 / 机箱风扇 RPM），没有则退化为 ACPI 性能计数器里的
        热区温度（通常反映主板/PCH，不是 CPU 核心）。探测要起 PowerShell 子进程（约 0.3s），
        所以按 ttl 缓存；结果里始终带 `note` 说明缺口原因，前端据实展示而不是静默缺项。
        """
        now = time.time()
        if self._sensors and now - self._sensors_at < ttl:
            return self._sensors
        try:
            self._sensors = await asyncio.to_thread(self._probe_hw_sensors)
        except Exception as e:  # noqa: BLE001
            logger.debug('hw sensors probe failed: %s', e)
            self._sensors = self._empty_sensors(f'探测失败：{type(e).__name__}')
        self._sensors_at = now
        return self._sensors

    @staticmethod
    def _empty_sensors(note: str = '') -> Dict:
        return {'cpu_package_c': None, 'cpu_cores_c': {}, 'other_c': {},
                'acpi_zones_c': {}, 'system_fans': [], 'source': None, 'note': note}

    _PS_SENSORS = (
        "$r=@();"
        "try{Get-CimInstance -Namespace root/CIMV2 "
        "-ClassName Win32_PerfFormattedData_Counters_ThermalZoneInformation -ErrorAction Stop|"
        "ForEach-Object{$r+=('ACPI|'+$_.Name+'|'+$_.HighPrecisionTemperature)}}catch{};"
        "foreach($ns in 'root/LibreHardwareMonitor','root/OpenHardwareMonitor'){"
        "try{Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop|"
        "ForEach-Object{if($_.SensorType -eq 'Temperature' -or $_.SensorType -eq 'Fan'){"
        "$r+=($_.SensorType+'|'+$_.Name+'|'+$_.Value)}}}catch{}};"
        "$r -join \"`n\""
    )

    def _probe_hw_sensors(self) -> Dict:
        """真正的探测（阻塞，跑在线程里）。先试跨平台的 psutil，再走 Windows 子进程。"""
        out = self._empty_sensors()

        # Linux/BSD：psutil 直接提供（Windows 上没有这两个接口）
        if hasattr(psutil, 'sensors_temperatures'):
            try:
                for chip, entries in (psutil.sensors_temperatures() or {}).items():
                    for e in entries:
                        label = (e.label or chip or '').strip()
                        if not label:
                            continue
                        if 'package' in label.lower():
                            out['cpu_package_c'] = round(e.current, 1)
                        elif 'core' in label.lower() or 'cpu' in label.lower():
                            out['cpu_cores_c'][label] = round(e.current, 1)
                        else:
                            out['other_c'][label] = round(e.current, 1)
                out['source'] = 'psutil'
            except Exception as e:  # noqa: BLE001
                logger.debug('psutil temperatures failed: %s', e)
            if hasattr(psutil, 'sensors_fans'):
                try:
                    for chip, entries in (psutil.sensors_fans() or {}).items():
                        for f in entries:
                            out['system_fans'].append(
                                {'name': (f.label or chip or 'fan'), 'rpm': f.current})
                except Exception as e:  # noqa: BLE001
                    logger.debug('psutil fans failed: %s', e)
            if out['cpu_package_c'] is None and not out['cpu_cores_c'] and not out['system_fans']:
                out['note'] = 'psutil 未提供温度/风扇传感器'
            return out

        if os.name != 'nt':
            out['note'] = '当前平台未实现传感器读取'
            return out

        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', self._PS_SENSORS],
                capture_output=True, text=True, timeout=20)
        except Exception as e:  # noqa: BLE001
            out['note'] = f'传感器探测失败：{type(e).__name__}'
            return out

        lhm = False
        for line in (r.stdout or '').splitlines():
            parts = line.strip().split('|')
            if len(parts) != 3:
                continue
            kind, name, raw = parts[0].strip(), parts[1].strip(), parts[2].strip()
            try:
                val = float(raw)
            except ValueError:
                continue
            if kind == 'ACPI':       # HighPrecisionTemperature：0.1K
                out['acpi_zones_c'][name] = round(val / 10.0 - 273.15, 1)
            elif kind == 'Temperature':      # LHM：已是 °C
                lhm = True
                low = name.lower()
                if 'package' in low:
                    out['cpu_package_c'] = round(val, 1)
                elif 'core' in low:
                    out['cpu_cores_c'][name] = round(val, 1)
                else:
                    out['other_c'][name] = round(val, 1)
            elif kind == 'Fan':              # LHM：RPM
                lhm = True
                out['system_fans'].append({'name': name, 'rpm': round(val)})

        if lhm:
            out['source'] = 'librehardwaremonitor'
            out['note'] = ''
        elif out['acpi_zones_c']:
            out['source'] = 'acpi-perf'
            out['note'] = ('CPU 封装/核心温度与机箱风扇转速需要带内核驱动的工具'
                           '（LibreHardwareMonitor / HWiNFO，启用 WMI provider）；'
                           'acpi_zones_c 来自 ACPI 热区，通常反映主板/PCH')
        else:
            out['note'] = ('读不到 CPU 温度/机箱风扇：Windows 无免驱来源，'
                           '请安装 LibreHardwareMonitor 或 HWiNFO 并启用 WMI provider')
        return out

    def sample(self) -> Dict:
        """采集一次当前资源快照并写入历史（后台周期采样用）。"""
        snap = self._read_metrics()
        self.history.append((time.time(), snap))
        return snap

    async def device_status(self) -> Dict:
        """即时采集一份完整设备状态（GET /device 用，不写入历史、不依赖采样周期）。"""
        self._read_metrics()
        sensors = await self.hw_sensors()
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
            'os': self.os_info or {'name': self.os_name},
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
            # CPU/主板温度与机箱风扇：Windows 上取决于是否装了 LHM/HWiNFO，见 note
            'thermal': {
                'cpu_package_c': sensors.get('cpu_package_c'),
                'cpu_cores_c': sensors.get('cpu_cores_c') or {},
                'other_c': sensors.get('other_c') or {},
                'acpi_zones_c': sensors.get('acpi_zones_c') or {},
                'source': sensors.get('source'),
                'note': sensors.get('note') or None,
            },
            'fans': {
                'gpu': [{'index': g.get('index'), 'percent': g.get('fan_percent')}
                        for g in self.gpus if g.get('fan_percent') is not None],
                'system': sensors.get('system_fans') or [],
                'note': None if sensors.get('system_fans')
                        else '机箱/CPU 风扇转速需要 LibreHardwareMonitor 或 HWiNFO（启用 WMI provider）',
            },
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
