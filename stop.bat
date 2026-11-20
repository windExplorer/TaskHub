@echo off
setlocal
cd /d "%~dp0"
title TaskHub Stop

echo ============================================
echo   TaskHub  -  stop service
echo ============================================
echo.

uv run python main.py stop

echo.
pause >nul
endlocal
