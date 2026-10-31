"""Build on the target OS. PyInstaller cannot cross-compile Windows executables."""
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor_switch.version import VERSION, REPOSITORY
import json
os.chdir(ROOT)
if sys.platform not in ('darwin', 'win32'):
    raise SystemExit('Build on macOS or Windows.')
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed',
           '--onedir', '--name', 'Monitor Switch', '--add-data', 'THIRD_PARTY.md:.',
           '--add-data', 'LICENSE:.', '--add-data', 'README.md:.',
           '--add-data', 'BUILDING.md:.',
           '--add-data', 'WINDOWS-RELAY-SETUP.md:.', '--add-data', 'licenses:licenses']
command += ['--add-data','relay-monitors.example.json:.','--add-data','assets:assets',
            '--copy-metadata', 'cryptography', '--copy-metadata', 'cffi',
            '--copy-metadata', 'certifi',
            '--copy-metadata', 'PySide6_Essentials', '--copy-metadata', 'shiboken6']
if sys.platform == 'darwin':
    icon = ROOT/'assets/app/MonitorSwitch.icns'
    subprocess.run([sys.executable,str(ROOT/'scripts/build_app_icon.py')],check=True)
    command += ['--icon',str(icon)]
    helper = 'm1ddc' if platform.machine() == 'arm64' else 'ddcctl'
    if not (ROOT / '.tools' / helper).exists():
        raise SystemExit('Run scripts/setup-mac.sh first.')
    command += ['--osx-bundle-identifier', 'local.monitorswitch.app', '--add-binary',
                f'.tools/{helper}:tools', '--add-data', f'.tools/{helper}-LICENSE.txt:tools']
command += ['app.py']
subprocess.run(command, check=True)
if sys.platform == 'darwin':
    bundle = ROOT/'dist/Monitor Switch.app'
    info = bundle/'Contents/Info.plist'
    value = plistlib.loads(info.read_bytes())
    value['LSUIElement'] = True
    value['CFBundleShortVersionString'] = VERSION
    value['CFBundleVersion'] = VERSION
    value['NSHumanReadableCopyright'] = 'Monitor Switch · MIT; third-party notices included'
    info.write_bytes(plistlib.dumps(value))
    subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(bundle)],check=True)
destination = ROOT / 'dist' / 'Monitor Switch'
(destination/'release-version.json').write_text(json.dumps({'version':VERSION,'repository':REPOSITORY})+'\n')
shutil.copy2('README.md', destination)
shutil.copy2('THIRD_PARTY.md', destination)
print('Built in dist/. Test the packaged app before distributing it.')
