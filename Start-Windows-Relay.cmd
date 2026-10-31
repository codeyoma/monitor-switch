@echo off
setlocal
if not exist "%LOCALAPPDATA%\MonitorSwitch\venv\Scripts\pythonw.exe" goto setup
if not exist "%LOCALAPPDATA%\MonitorSwitch\app\app.py" goto setup
start "Monitor Switch" "%LOCALAPPDATA%\MonitorSwitch\venv\Scripts\pythonw.exe" "%LOCALAPPDATA%\MonitorSwitch\app\app.py"
exit /b 0
:setup
call "%~dp0Install-Windows.cmd"
