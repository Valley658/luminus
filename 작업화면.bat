@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-tunnel.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "http://127.0.0.1/index2.html"
