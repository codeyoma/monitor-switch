@echo off
setlocal
cd /d "%~dp0"
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe scripts\build.py
if errorlevel 1 goto failed
echo Built: dist\Monitor Switch\Monitor Switch.exe
echo Keep the entire folder, including _internal. It is not a standalone exe.
pause
exit /b 0
:failed
echo Build failed. Read the error above.
pause
exit /b 1
