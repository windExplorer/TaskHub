"""告警通知通道：Gotify + 邮件（SMTP）。

只负责“发一条通知”，状态判定/去重在 ServiceManager 里做。
任何通道失败都只记录日志并返回结果，绝不影响被监控服务。
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Callable, Dict

import httpx

logger = logging.getLogger('middle_station.notifier')


class Notifier:
    def __init__(self, config_provider: Callable[[], Dict[str, Any]]):
        self._config_provider = config_provider

    async def send(self, title: str, message: str, level: str = 'info',
                   force: bool = False) -> Dict[str, str]:
        """按配置分发到已启用通道。force=True 用于测试（忽略总开关，但仍看通道开关）。"""
        cfg = self._config_provider() or {}
        if not cfg.get('enabled') and not force:
            return {'status': 'disabled'}
        channels = cfg.get('channels') or {}
        jobs = []
        gotify = channels.get('gotify') or {}
        if gotify.get('enabled'):
            jobs.append(('gotify', self._send_gotify(gotify, title, message, level)))
        email = channels.get('email') or {}
        if email.get('enabled'):
            jobs.append(('email', self._send_email(email, title, message)))
        if not jobs:
            return {'status': 'no_enabled_channel'}
        results = await asyncio.gather(
            *(job for _, job in jobs), return_exceptions=True)
        out: Dict[str, str] = {}
        for (name, _), result in zip(jobs, results):
            if isinstance(result, BaseException):
                out[name] = f'error: {type(result).__name__}: {result}'
                logger.warning('[alert] %s failed: %s', name, result)
            else:
                out[name] = str(result)
        return out

    async def _send_gotify(self, cfg: Dict[str, Any], title: str,
                           message: str, level: str) -> str:
        url = str(cfg.get('url') or '').strip().rstrip('/')
        token = str(cfg.get('token') or '').strip()
        if not url or not token:
            return 'skipped: url/token empty'
        priority = {'error': 8, 'warn': 5, 'info': 3}.get(level, 3)
        try:
            priority = int(cfg.get('priority', priority))
        except (TypeError, ValueError):
            pass
        payload = {
            'title': title,
            'message': message,
            'priority': priority,
            'extras': {'client::display': {'contentType': 'text/plain'}},
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f'{url}/message', params={'token': token}, json=payload)
        if r.status_code >= 400:
            return f'error: http {r.status_code} {r.text[:120]}'
        return 'sent'

    async def _send_email(self, cfg: Dict[str, Any], title: str, message: str) -> str:
        to = cfg.get('to_addrs') or []
        if isinstance(to, str):
            to = [x.strip() for x in to.split(',') if x.strip()]
        host = str(cfg.get('host') or '').strip()
        if not host or not to:
            return 'skipped: host/to_addrs empty'
        user = str(cfg.get('user') or '').strip()
        password = str(cfg.get('password') or '')
        try:
            port = int(cfg.get('port', 465))
        except (TypeError, ValueError):
            port = 465
        use_ssl = bool(cfg.get('ssl', True))
        use_starttls = bool(cfg.get('starttls', False)) and not use_ssl
        sender = str(cfg.get('from_addr') or user or '').strip()
        return await asyncio.to_thread(
            self._send_email_sync, host, port, use_ssl, use_starttls,
            user, password, sender, [str(x) for x in to], title, message)

    @staticmethod
    def _send_email_sync(host: str, port: int, use_ssl: bool, use_starttls: bool,
                         user: str, password: str, sender: str, to: list,
                         subject: str, body: str) -> str:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = sender or user or 'taskhub@localhost'
        msg['To'] = ', '.join(to)
        msg.set_content(body)
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=12)
        else:
            server = smtplib.SMTP(host, port, timeout=12)
        try:
            if use_starttls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
        return 'sent'
