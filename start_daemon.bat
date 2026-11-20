@echo off
setlocal
cd /d "%~dp0"
title TaskHub Daemon Start

echo ============================================
echo   TaskHub  -  start in background
echo ============================================
echo.

rem ---------- 1. dependency sync ----------
echo [1/3] syncing dependencies (uv sync)...
uv sync >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv sync failed. Install uv first, then retry.
    pause
    exit /b 1
)

rem ---------- 2. free port if occupied by old instance ----------
echo [2/3] checking port...
set FOUND=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :9000 ^| findstr LISTENING') do (
    set FOUND=%%a
)
if defined FOUND (
    echo [info] port 9000 held by old process PID %FOUND%, killing it...
    taskkill /f /pid %FOUND% /t >nul 2>&1
    timeout /t 1 /nobreak >nul
) else (
    echo [info] port 9000 is free.
)

rem ---------- 3. start daemon ----------
echo [3/3] starting TaskHub in background...
echo.
uv run python main.py start --daemon
if errorlevel 1 (
    echo [ERROR] daemon start failed. See logs\server.log
    pause
    exit /b 1
)

echo.
echo [info] dashboard: http://127.0.0.1:9000/ui
echo [info] log:       logs\server.log
echo [info] stop:      run stop.bat  (or tray icon -^> Quit)
echo.
pause >nul
endlocal
