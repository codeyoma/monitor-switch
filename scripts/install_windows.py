"""Install the Windows tray app in a stable per-user directory, then enable login startup."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or not (3,11) <= sys.version_info[:2] < (3,16):
        raise RuntimeError('Install 64-bit Python 3.11–3.15 from python.org, then run the installer again.')
    data = Path(os.environ['LOCALAPPDATA'])/'MonitorSwitch'
    target, environment = data/'app', data/'venv'
    data.mkdir(parents=True,exist_ok=True)
    if not (environment/'Scripts/python.exe').exists():
        subprocess.run([sys.executable,'-m','venv',str(environment)],check=True)
    python = environment/'Scripts/python.exe'
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    # Check the resident lock using the installed Qt runtime before replacing files.
    check = 'from PySide6.QtCore import QLockFile; import sys; lock=QLockFile(sys.argv[1]); sys.exit(0 if lock.tryLock(0) else 1)'
    result = subprocess.run([str(python),'-c',check,str(data/'resident.lock')])
    if result.returncode:
        raise RuntimeError('Quit Monitor Switch from its tray menu, then run this installer again.')
    stage = Path(tempfile.mkdtemp(prefix='install-',dir=data))
    backup = None
    try:
        for name in ('app.py','requirements.txt','relay-monitors.example.json','README.md',
                     'WINDOWS-RELAY-SETUP.md','THIRD_PARTY.md','LICENSE'):
            shutil.copy2(ROOT/name,stage/name)
        for name in ('monitor_switch','scripts','licenses','assets'):
            shutil.copytree(ROOT/name,stage/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # Preserve user-edited mappings when updating an existing installation.
        if (target/'relay-monitors.json').exists():
            shutil.copy2(target/'relay-monitors.json',stage/'relay-monitors.json')
            if not (data/'relay-monitors.json').exists():
                shutil.copy2(target/'relay-monitors.json',data/'relay-monitors.json')
        if target.exists():
            backup = data/'Previous Installs'/uuid.uuid4().hex
            backup.parent.mkdir(parents=True,exist_ok=True)
            target.rename(backup)
        try:
            stage.rename(target)
        except Exception:
            if backup and backup.exists() and not target.exists():
                backup.rename(target)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    pythonw = environment/'Scripts/pythonw.exe'
    register = ('import sys; from monitor_switch.startup import LoginStartup; '
                'LoginStartup([sys.argv[1],sys.argv[2],"--background"]).set_enabled(True)')
    subprocess.run([str(python),'-c',register,str(pythonw),str(target/'app.py')],cwd=target,check=True)
    subprocess.Popen([str(pythonw),str(target/'app.py')],cwd=target,
                     creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    print('Installed. Monitor Switch is in the system tray and will start after you sign in.')
    print('Installation:', target)
    print('Use the tray menu to export the Mac connection file, change startup, or quit.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Installation failed:',exc)
        raise SystemExit(1)
