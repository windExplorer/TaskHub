@echo off
setlocal
title TaskHub
cd /d "%~dp0"

echo ============================================
echo   TaskHub  -  one-click launcher
echo ============================================

rem ---------- 1. dependency sync ----------
echo [1/3] syncing dependencies (uv sync)...
uv sync >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv sync failed. Check Python/uv installation.
    pause
    exit /b 1
)

rem ---------- 2. free port 9000 if occupied by old instance ----------
echo [2/3] checking port 9000...
set FOUND=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :9000 ^| findstr LISTENING') do (
    set FOUND=%%a
)
if defined FOUND (
    echo [info] port 9000 held by old process PID %FOUND%, killing it...
    taskkill /f /pid %FOUND% >nul 2>&1
    timeout /t 1 /nobreak >nul
) else (
    echo [info] port 9000 is free.
)

rem ---------- 3. start service ----------
echo [3/3] starting TaskHub at http://127.0.0.1:9000  (WebUI: /ui)
echo   press Ctrl+C to stop, or just close this window.
echo.
uv run python main.py start

echo.
echo [info] service exited. Press any key to close.
pause >nul
endlocal
