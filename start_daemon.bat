@echo off
setlocal
cd /d "%~dp0"
title TaskHub Daemon Start

echo ============================================
echo   TaskHub  -  start in background
echo ============================================
echo.

rem 后台常驻：关闭本窗口后服务继续运行；日志见 logs\server.log
uv run python main.py start --daemon

echo.
echo [info] log:  logs\server.log
echo [info] stop: run stop.bat
echo.
pause >nul
endlocal
