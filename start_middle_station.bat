@echo off
setlocal
title TaskHub
cd /d "%~dp0"

echo ============================================
echo   TaskHub  -  one-click launcher
echo ============================================

rem ---------- 1. dependency sync ----------
echo [1/4] syncing dependencies (uv sync)...
uv sync >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv sync failed. Check Python/uv installation.
    pause
    exit /b 1
)

rem ---------- 2. build frontend if dist missing ----------
echo [2/4] checking WebUI build (webui\dist)...
if not exist "webui\dist\index.html" (
    echo [info] webui/dist not found, building frontend with npm install + npm run build...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] npm not found. Install Node.js, or copy webui\dist from an existing install
        pause
        exit /b 1
    )
    pushd webui
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo [ERROR] npm install failed.
        popd
        pause
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        echo [ERROR] npm run build failed.
        popd
        pause
        exit /b 1
    )
    popd
)

rem ---------- 3. free port 9000 if occupied by old instance ----------
echo [3/4] checking port 9000...
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

rem ---------- 4. start service ----------
echo [4/4] starting TaskHub at http://127.0.0.1:9000  (WebUI: /ui)
echo   press Ctrl+C to stop, or just close this window.
echo.
uv run python main.py start

echo.
echo [info] service exited. Press any key to close.
pause >nul
endlocal
