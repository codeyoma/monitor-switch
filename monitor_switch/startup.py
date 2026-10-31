"""Per-user login registration and stable application installation."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

APP_ID = 'local.monitorswitch.app'
AGENT_LABEL = 'local.monitorswitch.login'
RUN_VALUE = 'MonitorSwitch'


def data_directory(platform=None, home=None, appdata=None):
    platform, home = platform or sys.platform, Path(home or Path.home())
    base = home/'Library/Application Support' if platform == 'darwin' else Path(appdata or os.environ.get('LOCALAPPDATA',home/'.config'))
    return base/'MonitorSwitch'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as stream:
            name = stream.name
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def windows_command(argv):
    command = subprocess.list2cmdline([str(value) for value in argv])
    if len(command.encode('utf-16-le'))//2 > 260:
        raise ValueError('The Windows login command exceeds 260 characters. Use a shorter installation path.')
    return command


class WindowsRunStore:
    key = r'Software\Microsoft\Windows\CurrentVersion\Run'

    def read(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.key) as handle:
                return winreg.QueryValueEx(handle,RUN_VALUE)[0]
        except FileNotFoundError:
            return None

    def write(self, command):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,self.key,0,winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle,RUN_VALUE,0,winreg.REG_SZ,command)

    def delete(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,self.key,0,winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle,RUN_VALUE)
        except FileNotFoundError:
            pass


class LoginStartup:
    def __init__(self, command, *, platform=None, home=None, appdata=None, runner=subprocess.run, registry=None):
        self.platform, self.home = platform or sys.platform, Path(home or Path.home())
        self.command = [str(value) for value in command]
        self.data = data_directory(self.platform,self.home,appdata)
        self.preferences = self.data/'startup.json'
        self.agent = self.home/'Library/LaunchAgents'/f'{AGENT_LABEL}.plist'
        self.runner, self.registry = runner, registry or WindowsRunStore()

    def preference(self):
        if not self.preferences.exists():
            return None
        value = json.loads(self.preferences.read_text(encoding='utf-8'))
        if value.get('managed_by') != APP_ID or type(value.get('enabled')) is not bool:
            raise ValueError('Invalid login preference file; existing file preserved.')
        return value

    def agent_document(self):
        return {'Label':AGENT_LABEL,'ProgramArguments':self.command,'RunAtLoad':True,
                'LimitLoadToSessionType':'Aqua','MonitorSwitchManaged':True}

    def _owned_agent(self):
        if not self.agent.exists():
            return None
        value = plistlib.loads(self.agent.read_bytes())
        if value.get('Label') != AGENT_LABEL or value.get('MonitorSwitchManaged') is not True:
            raise ValueError('The login agent path belongs to another configuration; existing file preserved.')
        return value

    def enabled(self):
        if self.platform == 'darwin':
            value = self._owned_agent()
            return bool(value and value.get('RunAtLoad') and value.get('ProgramArguments') == self.command)
        if self.platform == 'win32':
            return self.registry.read() == windows_command(self.command)
        return False

    def _launchctl(self, *args, required=False):
        result = self.runner(['/bin/launchctl',*args],capture_output=True,text=True,timeout=8)
        if required and result.returncode:
            raise RuntimeError((result.stderr or result.stdout or 'Login registration failed.').strip()[-500:])
        return result.returncode == 0

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Invalid login setting.')
        previous = self.preference()
        if self.platform == 'darwin':
            owned = self._owned_agent()
            domain = f'gui/{os.getuid()}'
            loaded = self._launchctl('print',f'{domain}/{AGENT_LABEL}')
            if loaded and not owned:
                raise ValueError('The login-agent name is already in use; existing service preserved.')
            if loaded:
                self._launchctl('bootout',f'{domain}/{AGENT_LABEL}',required=True)
            if enabled:
                self.agent.parent.mkdir(parents=True,exist_ok=True)
                payload = plistlib.dumps(self.agent_document())
                temporary = self.agent.with_suffix('.plist.tmp')
                temporary.write_bytes(payload);temporary.chmod(0o600)
                os.replace(temporary,self.agent)
                try:
                    self._launchctl('bootstrap',domain,str(self.agent),required=True)
                except Exception:
                    if owned:
                        self.agent.write_bytes(plistlib.dumps(owned))
                        self.agent.chmod(0o600)
                        if loaded:
                            self._launchctl('bootstrap',domain,str(self.agent))
                    else:
                        self.agent.unlink(missing_ok=True)
                    raise
            elif owned:
                self.agent.unlink()
        elif self.platform == 'win32':
            current = self.registry.read()
            expected = windows_command(self.command)
            previous_command = previous.get('command') if previous else None
            allowed = (None,expected,windows_command(previous_command) if previous_command else None)
            if current not in allowed:
                raise ValueError('The Windows login entry belongs to another command; existing value preserved.')
            self.registry.write(expected) if enabled else self.registry.delete()
        else:
            raise RuntimeError('Login registration supports macOS and Windows only.')
        write_json(self.preferences,{'managed_by':APP_ID,'enabled':enabled,'command':self.command})

    def ensure_first_run(self):
        # Do not re-enable an entry the user has subsequently disabled.
        if self.preference() is None:
            self.set_enabled(True)


def app_bundle(executable=None):
    for parent in Path(executable or sys.executable).resolve().parents:
        if parent.suffix == '.app':
            return parent
    return None


def install_mac_bundle(source, home=None):
    source = Path(source).resolve()
    home = Path(home or Path.home())
    target = home/'Applications/Monitor Switch.app'
    def identity(path):
        return plistlib.loads((path/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier')
    if identity(source) != APP_ID:
        raise ValueError('Unexpected Mac application identity.')
    if source == target.resolve():
        return target
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and identity(target) != APP_ID:
        raise ValueError('Another application occupies the installation path; existing app preserved.')
    temporary = target.parent/f'.MonitorSwitch-install-{uuid.uuid4().hex}.app'
    backup = None
    try:
        subprocess.run(['/usr/bin/ditto',str(source),str(temporary)],check=True)
        if target.exists():
            backup = data_directory('darwin',home)/'Previous Applications'/f'Monitor Switch-{uuid.uuid4().hex}.app'
            backup.parent.mkdir(parents=True,exist_ok=True)
            target.rename(backup)
        try:
            temporary.rename(target)
        except Exception:
            if backup and not target.exists():
                backup.rename(target)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def login_command(root, *, install=False):
    root = Path(root)
    if sys.platform == 'darwin':
        bundle = app_bundle()
        if bundle:
            if install:
                bundle = install_mac_bundle(bundle)
            return ['/usr/bin/open','-g','-a',str(bundle),'--args','--background']
    executable = Path(sys.executable)
    if sys.platform == 'win32' and executable.name.lower() == 'python.exe':
        candidate = executable.with_name('pythonw.exe')
        if candidate.exists():
            executable = candidate
    return [str(executable),str(root/'app.py'),'--background'] if not getattr(sys,'frozen',False) else [str(executable),'--background']
