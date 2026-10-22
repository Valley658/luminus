@echo off
setlocal
cd /d "%~dp0"
python "%~dp0scripts\server.py" --stop
if errorlevel 1 pause
