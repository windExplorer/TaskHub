"""系统托盘图标（Windows/macOS/Linux）。

仅 daemon 后台模式启用：图标运行在主线程，uvicorn 跑在子线程。
菜单：打开控制台 / 开机自启（勾选）/ 退出。

依赖 pystray + Pillow；未安装时调用方应降级为无托盘的纯后台模式。
"""
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger('middle_station.tray')

_ROOT = Path(__file__).resolve().parent
_AUTOSTART_NAME = 'TaskHub.bat'


def _autostart_path() -> Optional[Path]:
    """当前用户的「启动」文件夹路径；非 Windows 返回 None。"""
    if os.name != 'nt':
        return None
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    return Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup' / _AUTOSTART_NAME


def is_autostart_enabled() -> bool:
    p = _autostart_path()
    return bool(p and p.exists())


def set_autostart(enabled: bool) -> bool:
    """写入/删除启动文件夹里的 TaskHub.bat（仅当前用户）。返回是否成功。"""
    p = _autostart_path()
    if p is None:
        return False
    try:
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)
            # 用当前解释器启动 daemon；路径加引号防止空格目录出错
            exe = sys.executable
            main_py = _ROOT / 'main.py'
            line = f'@echo off\r\ncd /d "{_ROOT}"\r\nstart "" "{exe}" "{main_py}" start --daemon\r\n'
            p.write_text(line, encoding='utf-8')
        else:
            if p.exists():
                p.unlink()
        return True
    except OSError as e:
        logger.warning('set_autostart failed: %s', e)
        return False


def _make_image():
    """用 Pillow 画一个简单的 TaskHub 图标（靛蓝底 + 三个连线节点），免外部图片。"""
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角底
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill=(79, 70, 229, 255))
    # 三个节点 + 连线（ Waypoints 意象）
    nodes = [(18, 40), (32, 20), (46, 40)]
    d.line([nodes[0], nodes[1], nodes[2]], fill=(255, 255, 255, 210), width=4, joint='curve')
    for (x, y) in nodes:
        d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 255, 255, 255))
    return img


def run_tray(host: str, port: int, on_quit: Callable[[], None]) -> None:
    """在主线程运行托盘图标（阻塞直到用户点退出）。"""
    import pystray
    from pystray import MenuItem

    dashboard_url = f'http://127.0.0.1:{port}/ui'
    checked = is_autostart_enabled()

    def on_open(icon, item):
        webbrowser.open(dashboard_url)

    def on_autostart(icon, item):
        nonlocal checked
        new = set_autostart(not checked)
        if new:
            checked = not checked
        icon.update_menu()

    def on_quit_called(icon, item):
        on_quit()
        icon.stop()

    menu = pystray.Menu(
        MenuItem(f'打开控制台', on_open, default=True),
        MenuItem('开机自启', on_autostart, checked=lambda item: checked),
        MenuItem('退出', on_quit_called),
    )
    icon = pystray.Icon('TaskHub', _make_image(), f'TaskHub (Port {port})', menu)
    logger.info('[tray] icon running (dashboard=%s, autostart=%s)', dashboard_url, checked)
    icon.run()
