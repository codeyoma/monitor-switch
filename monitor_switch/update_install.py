"""Independent, per-user replacement with startup preservation and rollback."""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

from .startup import APP_ID, LoginStartup, app_bundle, data_directory, install_mac_bundle, write_json
from .updates import extract_archive, platform_key, verified_manifest, verify_archive, version_tuple
from .version import REPOSITORY, VERSION


def installed_target():
    return Path.home()/'Applications/Monitor Switch.app' if sys.platform == 'darwin' else data_directory()/'app'


def executable_at(path):
    return Path(path)/('Contents/MacOS/Monitor Switch' if sys.platform == 'darwin' else 'Monitor Switch.exe')


def installed_here():
    return bool(getattr(sys, 'frozen', False) and
                Path(sys.executable).resolve() == executable_at(installed_target()).resolve())


def check_application(path, version):
    path = Path(path)
    if sys.platform == 'darwin':
        info = plistlib.loads((path/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != APP_ID or info.get('CFBundleShortVersionString') != version:
            raise ValueError('Unexpected application identity or version.')
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(path)],
                       check=True, capture_output=True, timeout=60)
    else:
        info = json.loads((path/'release-version.json').read_text(encoding='utf-8'))
        if info != {'version': version, 'repository': REPOSITORY}:
            raise ValueError('Unexpected Windows application identity or version.')
    if not executable_at(path).is_file():
        raise ValueError('The update has no application executable.')


def existing_version(target):
    """Recognize our installation before replacing anything at its path."""
    target = Path(target)
    if not target.exists():
        return None
    if sys.platform == 'darwin':
        info = plistlib.loads((target/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != APP_ID:
            raise ValueError('Another application occupies the installation path.')
        return info.get('CFBundleShortVersionString')
    if (target/'release-version.json').exists():
        info = json.loads((target/'release-version.json').read_text())
        if info.get('repository') != REPOSITORY:
            raise ValueError('Another application occupies the installation path.')
        return info.get('version')
    if not ((target/'app.py').is_file() and (target/'monitor_switch/startup.py').is_file()):
        raise ValueError('Unrecognized Windows installation; existing files preserved.')
    return None  # The previous Python source installer has no version marker.


def preserve_legacy_mapping(target, data):
    """Migrate mappings before a source installation is replaced by a binary."""
    destination = Path(data)/'relay-monitors.json'
    if not destination.exists() and (Path(target)/'relay-monitors.json').exists():
        value = json.loads((Path(target)/'relay-monitors.json').read_text(encoding='utf-8'))
        write_json(destination, value)


def synchronize_startup():
    target = installed_target()
    command = (['/usr/bin/open', '-g', '-a', str(target), '--args', '--background']
               if sys.platform == 'darwin' else [str(executable_at(target)), '--background'])
    startup = LoginStartup(command)
    preference = startup.preference()
    # Preserve an external disabling of the login entry as well as our own toggle.
    if preference is None:
        startup.ensure_first_run()
    else:
        if preference.get('command') == command:
            return  # Do not re-bootstrap an unchanged Mac RunAtLoad agent.
        if sys.platform == 'win32':
            from .startup import windows_command
            enabled = startup.registry.read() == windows_command(preference['command'])
        else:
            enabled = startup.enabled()
        startup.set_enabled(enabled)


def atomic_replace(source, target, backup):
    """Keep the previous installation until the replacement reports healthy."""
    target, source, backup = Path(target), Path(source), Path(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent/f'.MonitorSwitch-{uuid.uuid4().hex}'
    try:
        shutil.copytree(source, temporary, symlinks=True)
        if target.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            temporary.rename(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def wait_for_exit(pid, timeout=90):
    """Wait for one process using an OS event, with no monitor/USB polling."""
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise ValueError('Invalid application process.')
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Process already exited.
                return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.WaitForSingleObject(handle, int(timeout*1000)) != 0:
                raise TimeoutError('The application did not finish stopping.')
        finally:
            kernel.CloseHandle(handle)
    else:
        import select
        with closing(select.kqueue()) as queue:
            try:
                events = queue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)], 1, timeout)
            except ProcessLookupError:
                return
            if not events:
                raise TimeoutError('The application did not finish stopping.')


def spawn_helper(stage):
    stage = Path(stage)
    key = platform_key()
    payload = stage/'payload'/('Monitor Switch.app' if key == 'macos-arm64' else 'app')
    subprocess.Popen([str(executable_at(payload)), '--apply-update', str(stage), str(os.getpid())],
                     cwd=stage, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **({'creationflags': subprocess.DETACHED_PROCESS} if sys.platform == 'win32'
                                        else {'start_new_session': True}))


def apply_update(stage, pid):
    data, target = data_directory(), installed_target()
    stage = Path(stage).resolve()
    if stage.parent != data.resolve() or not re.fullmatch(r'update-[A-Za-z0-9_-]+', stage.name):
        raise ValueError('Unexpected update staging directory.')
    wait_for_exit(pid)
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(data/'resident.lock'))
    if not lock.tryLock(0):
        raise RuntimeError('Another Monitor Switch instance is running. Update deferred.')
    backup = data/'Previous Installs'/uuid.uuid4().hex
    child = None
    previous_startup = None
    previous_enabled = False
    replaced, startup_touched = False, False
    original_exists = target.exists()
    command = [str(executable_at(target)), '--background']
    token = uuid.uuid4().hex
    health = data/f'update-health-{token}.json'
    try:
        old_version = existing_version(target)
        previous_startup = LoginStartup([]).preference()
        if previous_startup:
            command = previous_startup['command']
            previous_enabled = LoginStartup(command).enabled()
        elif sys.platform == 'win32' and (target/'app.py').is_file():
            command = [str(data/'venv/Scripts/pythonw.exe'), str(target/'app.py'), '--background']
        manifest = verified_manifest((stage/'update.json').read_bytes(), (stage/'update.sig').read_bytes())
        version, key = manifest['version'], platform_key()
        if version != VERSION:
            raise ValueError('The update helper has the wrong version.')
        verify_archive(stage/'release.zip', manifest['assets'][key])
        source = extract_archive(stage/'release.zip', stage/'install-payload', key=key)
        check_application(source, version)
        if sys.platform == 'win32':
            preserve_legacy_mapping(target, data)
        if old_version and version_tuple(old_version) >= version_tuple(version):
            raise ValueError('A newer or equal version is already installed.')
        atomic_replace(source, target, backup)
        replaced = True
        lock.unlock()
        child = subprocess.Popen([str(executable_at(target)), '--background', '--update-health', token],
                                 cwd=target, close_fds=True)
        deadline = time.monotonic()+60
        while not health.exists():
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('The updated app did not start. Restoring the previous version.')
            time.sleep(0.25)
        value = json.loads(health.read_text())
        if value != {'version': version, 'pid': child.pid} or child.poll() is not None:
            raise RuntimeError('The updated app failed its startup check.')
        startup_touched = True
        synchronize_startup()
        write_json(data/'update-result.json', {'version': version, 'ok': True})
        (stage/'complete').touch()
    except Exception:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(15)
            except subprocess.TimeoutExpired:
                child.kill();child.wait(5)
        if not lock.isLocked() and not lock.tryLock(0):
            raise RuntimeError('Cannot restore while another app instance is running.')
        if replaced:
            failed = stage/'failed-install'
            target.rename(failed)
            if backup.exists():
                backup.rename(target)
        if original_exists and target.exists():
            try:
                if startup_touched and previous_startup:
                    LoginStartup(previous_startup['command']).set_enabled(previous_enabled)
            finally:
                lock.unlock()
                subprocess.Popen(command, cwd=target, close_fds=True)
        raise
    finally:
        health.unlink(missing_ok=True)
        lock.unlock()
    # The helper's own executable remains in stage until it exits. A later check
    # cleans successful stages; Windows cannot delete its running executable.


def report_failure(exc):
    write_json(data_directory()/'update-result.json', {'version': VERSION, 'ok': False, 'error': str(exc)[:1000]})


def bootstrap_install(background=False):
    """First launch of a downloaded bundle installs it at the stable user path."""
    if not getattr(sys, 'frozen', False) or installed_here():
        return False
    from PySide6.QtCore import QLockFile
    data = data_directory()
    data.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data/'resident.lock'))
    if not lock.tryLock(0):
        # Let the normal single-instance path show the already-running app.
        return False
    try:
        target = installed_target()
        old_version = existing_version(target)
        if old_version is None or version_tuple(old_version) < version_tuple(VERSION):
            if sys.platform == 'darwin':
                install_mac_bundle(app_bundle())
            else:
                preserve_legacy_mapping(target, data)
                atomic_replace(Path(sys.executable).parent, target, data/'Previous Installs'/uuid.uuid4().hex)
        synchronize_startup()
    finally:
        lock.unlock()
    subprocess.Popen([str(executable_at(target)), *(['--background'] if background else [])],
                     cwd=target, close_fds=True)
    return True
