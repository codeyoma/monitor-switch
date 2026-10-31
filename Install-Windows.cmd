@echo off
setlocal
cd /d "%~dp0"
py -3 scripts\install_windows.py
if errorlevel 1 (
  echo Installation failed. Read the message above, then retry.
  pause
  exit /b 1
)
pause
