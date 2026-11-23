"""外部服务进程面板：启动 / 停止 / 重启 / 状态监听 / 告警。

设计要点：
- 所有服务由 middle-station.yaml 的 services.items 驱动，新增服务只改配置。
- 启动一律脱离控制台（Windows DETACHED_PROCESS），日志追加到独立文件，不再弹黑框。
- 第三方启动器启动的进程没有本进程持有的 Popen 句柄，靠 process_match 兜底识别；
  停止时按进程树杀，避免留下 python 子进程。
- 默认 auto_restart=false：只告警不自动拉起；需要时逐服务打开。
"""
import asyncio
import logging
import os
import shlex
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import psutil

logger = logging.getLogger('middle_station.service_manager')


class ServiceManager:
    def __init__(self, config_provider: Callable[[], Dict[str, Any]],
                 root_dir: Path, notifier):
        self._config_provider = config_provider
        self._root = Path(root_dir)
        self._notifier = notifier
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._procs: Dict[str, subprocess.Popen] = {}
        self._started_at: Dict[str, float] = {}
        self._failures: Dict[str, int] = {}
        self._last_state: Dict[str, str] = {}
        self._alert_at: Dict[tuple, float] = {}
        self._restart_times: Dict[str, List[float]] = {}
        self._suppress_down_until: Dict[str, float] = {}

    # ---------- 生命周期 ----------
    @property
    def config(self) -> Dict[str, Any]:
        return self._config_provider() or {}

    def items(self) -> List[Dict[str, Any]]:
        return [x for x in (self.config.get('items') or []) if isinstance(x, dict)]

    def _item(self, name: str) -> Optional[Dict[str, Any]]:
        for item in self.items():
            if item.get('name') == name:
                return item
        return None

    async def start(self):
        self._client = httpx.AsyncClient(timeout=3.0, follow_redirects=True)
        # 开机自启：只拉起标记 autostart 的服务，逐个错峰避免同时抢显存。
        for i, item in enumerate(self.items()):
            if not (item.get('enabled', True) and item.get('managed', True)
                    and item.get('autostart')):
                continue
            name = str(item.get('name') or '')
            try:
                current = await self._inspect(item)
                if current['state'] in ('running', 'starting', 'unhealthy'):
                    continue
                result = await self.start_service(name)
                logger.info('[services] autostart %s: %s', name, result)
                if i:
                    await asyncio.sleep(1.5)
            except Exception as e:  # noqa: BLE001
                logger.warning('[services] autostart %s failed: %s', name, e)
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------- 对外 API ----------
    async def status(self) -> Dict[str, Any]:
        cfg = self.config
        items = self.items()
        states = await asyncio.gather(*(self._inspect(x) for x in items),
                                      return_exceptions=True)
        out = []
        for item, state in zip(items, states):
            if isinstance(state, BaseException):
                state = {'name': item.get('name'), 'state': 'error',
                         'error': str(state), 'alive': False, 'health': 'unknown',
                         'health_detail': '', 'pid': None, 'cpu': 0, 'rss_mb': 0,
                         'uptime_s': None, 'managed': bool(item.get('managed', True))}
            out.append(state)
        return {
            'enabled': bool(cfg.get('enabled', True)),
            'check_interval': float(cfg.get('check_interval', 10)),
            'start_grace': float(cfg.get('start_grace', 60)),
            'services': out,
        }

    async def start_service(self, name: str) -> Dict[str, Any]:
        item = self._item(name)
        if item is None:
            raise KeyError(name)
        if not item.get('managed', True) or item.get('self'):
            raise ValueError(f'{name} is not manageable (self/readonly service)')
        async with self._lock:
            current = await self._inspect(item)
            if current['alive'] or current['state'] in ('running', 'unhealthy'):
                return {'ok': True, 'already_running': True, 'service': current}
            cmd = self._command(item)
            if not cmd:
                raise ValueError(f'{name}: command is empty')
            cwd = self._resolve(item.get('cwd') or '.')
            if not cwd.is_dir():
                raise ValueError(f'{name}: cwd not found: {cwd}')
            log_path = self._resolve(item.get('log') or f'logs/services/{name}.log')
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, 'a', encoding='utf-8', buffering=1)
            log_fh.write(f'\n===== TaskHub start {time.strftime("%Y-%m-%d %H:%M:%S")}: '
                         f'{" ".join(cmd)} (cwd={cwd}) =====\n')
            env = {**os.environ}
            for k, v in (item.get('env') or {}).items():
                env[str(k)] = str(v)
            env.setdefault('PYTHONIOENCODING', 'utf-8')
            env.setdefault('PYTHONUTF8', '1')
            creation = getattr(subprocess, 'DETACHED_PROCESS', 0) if os.name == 'nt' else 0
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(cwd), env=env, creationflags=creation,
                    stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT)
            except OSError as e:
                log_fh.close()
                raise RuntimeError(f'{name}: spawn failed: {e}') from e
            self._procs[name] = proc
            self._started_at[name] = time.time()
            self._failures[name] = 0
            self._restart_times.pop(name, None)
            logger.info('[services] started %s pid=%s log=%s', name, proc.pid, log_path)
            return {'ok': True, 'pid': proc.pid, 'service': await self._inspect(item)}

    async def stop_service(self, name: str) -> Dict[str, Any]:
        item = self._item(name)
        if item is None:
            raise KeyError(name)
        if not item.get('managed', True) or item.get('self'):
            raise ValueError(f'{name} is not manageable (self/readonly service)')
        async with self._lock:
            procs = await asyncio.to_thread(self._collect_processes, item)
            if not procs:
                return {'ok': True, 'already_stopped': True}
            pids = [p.pid for p in procs]
            for p in procs:
                await asyncio.to_thread(self._kill_tree, p.pid)
            self._procs.pop(name, None)
            self._started_at.pop(name, None)
            self._failures[name] = 0
            self._suppress_down_until[name] = time.time() + max(
                30.0, float(self.config.get('check_interval', 10)) * 3)
            logger.info('[services] stopped %s pids=%s', name, pids)
            return {'ok': True, 'pids': pids}

    async def restart_service(self, name: str) -> Dict[str, Any]:
        stopped = await self.stop_service(name)
        await asyncio.sleep(1.0)
        started = await self.start_service(name)
        return {'ok': bool(stopped.get('ok') and started.get('ok')),
                'stopped': stopped, 'started': started}

    def logs(self, name: str, lines: int = 200) -> Dict[str, Any]:
        item = self._item(name)
        if item is None:
            raise KeyError(name)
        path = self._resolve(item.get('log') or f'logs/services/{name}.log')
        if not path.exists():
            return {'file': str(path), 'lines': []}
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            tail = deque(f, maxlen=max(1, min(2000, lines)))
        return {'file': str(path), 'lines': [x.rstrip('\r\n') for x in tail]}

    # ---------- 进程 / 健康探测 ----------
    @staticmethod
    def _command(item: Dict[str, Any]) -> List[str]:
        cmd = item.get('command') or []
        if isinstance(cmd, str):
            try:
                cmd = shlex.split(cmd, posix=False)
            except ValueError:
                cmd = cmd.split()
        return [str(x).strip().strip('"') for x in cmd if str(x).strip()]

    def _resolve(self, path: str) -> Path:
        p = Path(str(path)).expanduser()
        return p if p.is_absolute() else self._root / p

    def _collect_processes(self, item: Dict[str, Any]) -> List[psutil.Process]:
        found: Dict[int, psutil.Process] = {}
        name = str(item.get('name') or '')
        proc = self._procs.get(name)
        if proc is not None and proc.poll() is None:
            try:
                found[proc.pid] = psutil.Process(proc.pid)
            except psutil.Error:
                pass
        # 第三方启动器拉起的进程没有 Popen 句柄：按监听端口反查 PID 最可靠。
        try:
            port = int(item.get('port') or 0)
        except (TypeError, ValueError):
            port = 0
        if port:
            try:
                for conn in psutil.net_connections(kind='inet'):
                    pid = conn.pid
                    if (conn.status == psutil.CONN_LISTEN and pid and pid != os.getpid()
                            and conn.laddr and conn.laddr.port == port and pid not in found):
                        found[pid] = psutil.Process(pid)
            except (psutil.Error, OSError) as e:
                logger.debug('[services] port lookup %s failed: %s', port, e)
        matches = [str(m).lower() for m in (item.get('process_match') or []) if m]
        if matches:
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                pid = p.info.get('pid')
                if not pid or pid == os.getpid() or pid in found:
                    continue
                text = ' '.join([p.info.get('name') or ''] +
                                [str(x) for x in (p.info.get('cmdline') or [])]).lower()
                if any(m in text for m in matches):
                    found[pid] = p
        return list(found.values())

    @staticmethod
    def _kill_tree(pid: int):
        if os.name == 'nt':
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           creationflags=flags, capture_output=True, check=False)
            return
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass

    async def _probe(self, url: str) -> tuple:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=3.0, follow_redirects=True)
        try:
            r = await self._client.get(url, timeout=3.0)
            return (r.status_code < 400, f'http {r.status_code}')
        except Exception as e:  # noqa: BLE001
            return (False, type(e).__name__)

    async def _inspect(self, item: Dict[str, Any]) -> Dict[str, Any]:
        name = str(item.get('name') or '')
        managed = bool(item.get('managed', True)) and not bool(item.get('self'))
        is_self = bool(item.get('self'))
        procs = [] if is_self else await asyncio.to_thread(self._collect_processes, item)
        alive = bool(procs)
        pid = procs[0].pid if procs else None
        cpu = 0.0
        rss = 0
        uptime = None
        if procs:
            try:
                cpu = round(sum(p.cpu_percent() for p in procs), 1)
                rss = round(sum(p.memory_info().rss for p in procs) / (1024 * 1024))
                uptime = round(time.time() - min(p.create_time() for p in procs))
            except psutil.Error:
                pass

        health_url = str(item.get('health_url') or '')
        if is_self:
            health_ok, detail, health = True, 'self', 'ok'
        elif health_url:
            health_ok, detail = await self._probe(health_url)
            health = 'ok' if health_ok else 'fail'
        else:
            health_ok, detail, health = None, 'no health_url', 'unknown'

        grace = float(self.config.get('start_grace', 60))
        started = self._started_at.get(name, 0)
        in_grace = bool(started and time.time() - started < grace)
        if is_self:
            state = 'running'
        elif in_grace:
            state = 'starting'
        elif health_ok is None:
            state = 'running' if alive else 'stopped'
        elif health_ok:
            state = 'running'
        elif alive:
            state = 'unhealthy'
        else:
            state = 'stopped'

        return {
            'name': name,
            'label': str(item.get('label') or name),
            'state': state,
            'health': health,
            'health_detail': detail,
            'health_url': health_url,
            'alive': alive,
            'in_grace': in_grace,
            'pid': pid,
            'cpu_percent': cpu,
            'rss_mb': rss,
            'uptime_s': uptime,
            'managed': managed,
            'self': is_self,
            'autostart': bool(item.get('autostart')),
            'auto_restart': bool(item.get('auto_restart')),
            'log': str(item.get('log') or f'logs/services/{name}.log'),
            'error': str(self._failures.get(name, 0)),
        }

    # ---------- 监听循环 / 告警 ----------
    async def _loop(self):
        while True:
            cfg = self.config
            if cfg.get('enabled', True):
                for item in self.items():
                    if not item.get('enabled', True):
                        continue
                    try:
                        await self._check_once(item)
                    except Exception as e:  # noqa: BLE001
                        logger.warning('[services] check %s failed: %s', item.get('name'), e)
            await asyncio.sleep(max(3.0, float(cfg.get('check_interval', 10))))

    async def _check_once(self, item: Dict[str, Any]):
        name = str(item.get('name') or '')
        if item.get('self'):
            return
        st = await self._inspect(item)
        now = time.time()
        cfg = self.config
        failing = bool(
            not st['in_grace'] and
            (st['health'] == 'fail' or (st['health'] == 'unknown' and not st['alive']))
        )
        threshold = max(1, int(cfg.get('fail_threshold', 2)))
        self._failures[name] = (self._failures.get(name, 0) + 1) if failing else 0

        prev = self._last_state.get(name)
        if self._failures[name] < threshold:
            if prev == 'down':
                self._last_state[name] = 'up'
                await self._alert(
                    item, 'up',
                    f'服务 {st["label"]}（{name}）已恢复正常：{st["health_detail"]}。')
            return

        suppressed = now < self._suppress_down_until.get(name, 0)
        if suppressed:
            self._last_state[name] = 'down'
            self._failures[name] = 0
            return

        if prev != 'down':
            self._last_state[name] = 'down'
            detail = (f'服务 {st["label"]}（{name}）连续 {self._failures[name]} 次检测异常。'
                      f'健康状态：{st["health_detail"]}，进程：{"存活" if st["alive"] else "未运行"}。')
            await self._alert(item, 'down', detail)
            if not item.get('auto_restart'):
                self._restart_times.pop(name, None)
        else:
            self._last_state[name] = 'down'

        if not item.get('auto_restart') or not item.get('managed', True):
            return
        window = max(60.0, float(cfg.get('restart_window', 300)))
        cooldown = max(10.0, float(cfg.get('restart_cooldown', 60)))
        maximum = max(0, int(cfg.get('max_restart_attempts', 3)))
        times = [t for t in self._restart_times.get(name, []) if now - t < window]
        if len(times) >= maximum or (times and now - times[-1] < cooldown):
            return
        times.append(now)
        self._restart_times[name] = times
        try:
            result = await (self.restart_service(name) if st['alive'] and st['state'] == 'unhealthy'
                            else self.start_service(name))
            logger.info('[services] auto_restart %s: %s', name, result)
        except Exception as e:  # noqa: BLE001
            await self._alert(item, 'restart_failed',
                              f'服务 {st["label"]}（{name}）自动拉起失败：{e}')

    async def _alert(self, item: Dict[str, Any], event: str, detail: str):
        cfg = self.config
        alerts = cfg.get('alerts') or {}
        if not alerts.get('enabled'):
            return
        events = alerts.get('events') or {}
        defaults = {'down': True, 'up': False, 'restart_failed': True}
        if not events.get(event, defaults.get(event, False)):
            return
        key = (str(item.get('name') or ''), event)
        cooldown = max(0.0, float(alerts.get('cooldown_seconds', 60)))
        now = time.time()
        if now - self._alert_at.get(key, 0) < cooldown:
            return
        self._alert_at[key] = now
        label = str(item.get('label') or item.get('name') or '')
        title = f'[TaskHub] {label} · ' + {
            'down': '服务异常',
            'up': '服务已恢复',
            'restart_failed': '自动拉起失败',
        }.get(event, event)
        result = await self._notifier.send(
            title, detail, 'error' if event in ('down', 'restart_failed') else 'info')
        logger.info('[services] alert %s %s: %s', label, event, result)
