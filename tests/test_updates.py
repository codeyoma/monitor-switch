import base64
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from monitor_switch import updates
from monitor_switch.update_install import atomic_replace, preserve_legacy_mapping
from monitor_switch.version import REPOSITORY


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.key_patch = patch.object(updates, 'UPDATE_PUBLIC_KEY', self.public)
        self.key_patch.start()

    def tearDown(self):
        self.key_patch.stop()
        self.directory.cleanup()

    def archive(self, names=None):
        archive = self.root/'fixture.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            for name, payload in (names or {'app/Monitor Switch.exe': b'fixture', 'app/_internal/library': b'library'}).items():
                stream.writestr(name, payload)
        return archive

    def release(self, archive, version='1.2.3'):
        key = 'windows-x64'
        manifest = dict(schema=1, repository=REPOSITORY, version=version, assets={key: dict(
            name=f'MonitorSwitch-{version}-{key}.zip', size=archive.stat().st_size,
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest())})
        payload = json.dumps(manifest).encode()
        return manifest, payload, self.key.sign(payload), key

    def test_signed_release_end_to_end_staging_and_newer_version_selection(self):
        archive = self.archive()
        release = self.release(archive)
        manifest, payload, signature, key = release
        files = {updates.API: json.dumps({'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False}).encode(),
                 updates.release_url('1.2.3', 'update.json'): payload,
                 updates.release_url('1.2.3', 'update.sig'): signature,
                 updates.release_url('1.2.3', manifest['assets'][key]['name']): archive.read_bytes()}
        def fetch(url, limit, destination=None):
            value = files[url]
            self.assertLessEqual(len(value), limit)
            if destination:
                Path(destination).write_bytes(value)
                return len(value)
            return value
        selected = updates.check_release(fetch=fetch, current='1.2.2', key=key)
        stage = updates.prepare_release(selected, data=self.root/'data', fetch=fetch)
        self.assertEqual((stage/'payload/app/Monitor Switch.exe').read_bytes(), b'fixture')
        self.assertEqual((stage/'update.sig').read_bytes(), signature)
        self.assertIsNone(updates.check_release(fetch=fetch, current='1.2.3', key=key))
        self.assertIsNone(updates.check_release(fetch=fetch, current='1.2.4', key=key))
        self.assertIsNone(updates.check_release(fetch=fetch, current='1.2.2', key='macos-arm64'))

    def test_signature_and_publisher_are_required(self):
        manifest, payload, signature, _ = self.release(self.archive())
        with self.assertRaises(InvalidSignature):
            updates.verified_manifest(payload+b' ', signature)
        manifest['repository'] = 'another/publisher'
        payload = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, 'publisher'):
            updates.verified_manifest(payload, self.key.sign(payload))

    def test_prerelease_never_downloads_assets(self):
        calls = []
        def fetch(url, limit):
            calls.append(url)
            return b'{"tag_name":"v9.0.0","prerelease":true}'
        self.assertIsNone(updates.check_release(fetch=fetch, current='1.0.0'))
        self.assertEqual(calls, [updates.API])

    def test_tampered_download_is_removed_without_touching_installed_files(self):
        archive = self.archive()
        release = self.release(archive)
        data = self.root/'data'
        data.mkdir()
        old = data/'config.json'
        old.write_text('private configuration')
        def fetch(url, limit, destination):
            destination.write_bytes(b'wrong payload')
        with self.assertRaises(ValueError):
            updates.prepare_release(release, data=data, fetch=fetch)
        self.assertEqual(list(data.iterdir()), [old])
        self.assertEqual(old.read_text(), 'private configuration')

    def test_archive_traversal_and_windows_paths_are_rejected(self):
        for name in ('app/../../escape', '/app/evil', 'other/evil', 'app/a\\b',
                     'app/evil:stream', 'app/CON.txt', 'app/file.', 'app//file'):
            with self.subTest(name=name):
                archive = self.archive({name: b'bad'})
                with self.assertRaises(ValueError):
                    updates.extract_archive(archive, self.root/'extract', key='windows-x64')
        self.assertFalse((self.root/'escape').exists())

    @unittest.skipIf(os.name == 'nt', 'macOS archive symlinks are tested on the Mac runner.')
    def test_mac_internal_framework_symlinks_preserve_executable_and_reject_escape(self):
        archive = self.root/'mac.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            executable = zipfile.ZipInfo('Monitor Switch.app/Contents/MacOS/Monitor Switch')
            executable.external_attr = (stat.S_IFREG | 0o755) << 16
            stream.writestr(executable, b'bin')
            link = zipfile.ZipInfo('Monitor Switch.app/Contents/link')
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(link, 'MacOS/Monitor Switch')
        result = updates.extract_archive(archive, self.root/'good', key='macos-arm64')
        self.assertEqual((result/'Contents/link').read_bytes(), b'bin')
        self.assertTrue((result/'Contents/MacOS/Monitor Switch').stat().st_mode & 0o100)
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr(link, '../../../escape')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            updates.extract_archive(archive, self.root/'bad', key='macos-arm64')

    def test_failed_replace_restores_old_installation_and_legacy_mapping(self):
        source, target, backup = (self.root/name for name in ('new', 'app', 'backup'))
        source.mkdir();target.mkdir()
        (source/'binary').write_text('new')
        (target/'binary').write_text('old')
        (target/'relay-monitors.json').write_text('{"monitors":["personal"]}')
        preserve_legacy_mapping(target, self.root/'data')
        self.assertEqual(json.loads((self.root/'data/relay-monitors.json').read_text())['monitors'], ['personal'])
        original = Path.rename
        def rename(path, destination):
            if path.name.startswith('.MonitorSwitch-'):
                raise OSError('Simulated replacement failure')
            return original(path, destination)
        with patch.object(Path, 'rename', rename), self.assertRaises(OSError):
            atomic_replace(source, target, backup)
        self.assertEqual((target/'binary').read_text(), 'old')
        self.assertFalse(backup.exists())
        self.assertFalse(list(self.root.glob('.MonitorSwitch-*')))

    def test_only_https_github_download_hosts_are_accepted(self):
        for value in ('http://github.com/asset', 'https://evil.example/asset',
                      'https://github.com.evil.example/asset', 'https://user@github.com/asset'):
            self.assertFalse(updates.allowed_url(value))
        self.assertTrue(updates.allowed_url('https://release-assets.githubusercontent.com/asset'))

    def test_disable_automatic_updates_is_persisted(self):
        self.assertTrue(updates.update_preferences(self.root)['automatic'])
        updates.set_automatic(False, self.root)
        self.assertFalse(updates.update_preferences(self.root)['automatic'])

    def test_failed_windows_relay_never_reports_healthy(self):
        from types import SimpleNamespace
        from monitor_switch import tray
        thread = SimpleNamespace(is_alive=lambda: True)
        window = SimpleNamespace(job=None, closing=False, ready=False,
                                 service=SimpleNamespace(server_thread=thread))
        with patch.object(tray, 'WindowsWindow', SimpleNamespace):
            self.assertFalse(tray.update_health_ready(window))
            window.ready = True
            self.assertTrue(tray.update_health_ready(window))
            window.service.server_thread = SimpleNamespace(is_alive=lambda: False)
            self.assertFalse(tray.update_health_ready(window))
            window.service.server_thread = thread
            window.job = object()
            self.assertFalse(tray.update_health_ready(window))

    def test_unchanged_mac_login_agent_is_not_bootstrapped_again(self):
        from monitor_switch import update_install
        target = self.root/'Monitor Switch.app'
        command = ['/usr/bin/open', '-g', '-a', str(target), '--args', '--background']
        class Startup:
            def __init__(self, actual):
                self.actual = actual
            def preference(self):
                return {'command': command, 'enabled': True}
            def set_enabled(self, enabled):
                raise AssertionError('Must not relaunch an unchanged RunAtLoad agent.')
        with patch.object(update_install.sys, 'platform', 'darwin'), \
             patch.object(update_install, 'installed_target', return_value=target), \
             patch.object(update_install, 'LoginStartup', Startup):
            update_install.synchronize_startup()

    def test_cancel_stops_network_child_and_cleans_staging(self):
        import threading
        from PySide6.QtCore import QCoreApplication
        from monitor_switch import update_ui
        app = QCoreApplication.instance() or QCoreApplication([])
        started, stopped = threading.Event(), threading.Event()
        class Process:
            def __init__(self, *args, **kwargs):
                started.set()
            def wait(self, timeout=None):
                if not stopped.wait(timeout):
                    raise TimeoutError('Test child did not stop.')
                return -15
            def poll(self):
                return -15 if stopped.is_set() else None
            def terminate(self):
                stopped.set()
            kill = terminate
        results = []
        with patch.object(update_ui, 'data_directory', return_value=self.root), \
             patch.object(update_ui.subprocess, 'Popen', Process):
            job = update_ui.UpdateJob(None)
            job.completed.connect(results.append)
            job.start()
            self.assertTrue(started.wait(2))
            job.cancel()
            self.assertTrue(job.wait(2000))
            app.processEvents()
        self.assertIn('cancelled', results[0]['error'].lower())
        self.assertFalse(list(self.root.glob('update-*')))

    def test_cancelled_check_can_be_followed_by_another_check(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from monitor_switch.update_ui import UpdateController
        state = SimpleNamespace(job=Mock(), result={'error': 'cancelled'}, cancelling=True,
                                refresh=Mock())
        UpdateController.finished(state)
        self.assertFalse(state.cancelling)
        self.assertIsNone(state.job)

    def test_installer_health_success_and_failed_start_rollback_preserve_settings(self):
        from PySide6.QtCore import QLockFile  # Load the actual host runtime before platform simulation.
        from monitor_switch import update_install
        for fail in (False, True, 'copy', 'preflight'):
            with self.subTest(fail=fail):
                data = self.root/str(fail)
                data.mkdir()
                target = data/'app'
                target.mkdir()
                (target/'Monitor Switch.exe').write_bytes(b'old')
                (target/'release-version.json').write_text(json.dumps({'version': '1.0.0', 'repository': REPOSITORY}))
                (data/'relay-server.json').write_text('private pairing key')
                archive = self.archive({'app/Monitor Switch.exe': b'new',
                    'app/release-version.json': json.dumps({'version': '1.2.3', 'repository': REPOSITORY}).encode()})
                release = self.release(archive)
                def fetch(url, limit, destination):
                    destination.write_bytes(archive.read_bytes())
                stage = updates.prepare_release(release, data=data, fetch=fetch)
                if fail == 'preflight':
                    (stage/'release.zip').write_bytes(b'corrupt download')
                calls, startup_changes = [], []
                class Startup:
                    def __init__(self, command):
                        self.command = command
                    def preference(self):
                        return {'command': ['old-start-command'], 'enabled': False}
                    def enabled(self):
                        return False
                    def set_enabled(self, enabled):
                        startup_changes.append((self.command, enabled))
                class Child:
                    pid = 12345
                    def poll(self):
                        return 1 if fail else None
                def launch(command, **kwargs):
                    calls.append(command)
                    if '--update-health' in command and not fail:
                        token = command[-1]
                        (data/f'update-health-{token}.json').write_text(json.dumps({'version': '1.2.3', 'pid':12345}))
                    return Child()
                real_replace = update_install.atomic_replace
                def replace(*args):
                    if fail == 'copy':
                        raise OSError('Simulated disk failure')
                    return real_replace(*args)
                with patch.object(update_install, 'data_directory', return_value=data), \
                     patch.object(update_install, 'installed_target', return_value=target), \
                     patch.object(update_install, 'platform_key', return_value='windows-x64'), \
                     patch.object(update_install, 'VERSION', '1.2.3'), \
                     patch.object(update_install.sys, 'platform', 'win32'), \
                     patch.object(update_install, 'LoginStartup', Startup), \
                     patch.object(update_install, 'synchronize_startup'), \
                     patch.object(update_install, 'wait_for_exit'), \
                     patch.object(update_install, 'atomic_replace', side_effect=replace), \
                     patch.object(update_install.subprocess, 'Popen', side_effect=launch):
                    if fail:
                        with self.assertRaises((RuntimeError, OSError, ValueError)):
                            update_install.apply_update(stage, 123)
                        self.assertEqual((target/'Monitor Switch.exe').read_bytes(), b'old')
                        self.assertEqual(calls[-1], ['old-start-command'])
                        self.assertEqual(startup_changes, [])
                    else:
                        update_install.apply_update(stage, 123)
                        self.assertEqual((target/'Monitor Switch.exe').read_bytes(), b'new')
                        self.assertTrue(json.loads((data/'update-result.json').read_text())['ok'])
                self.assertEqual((data/'relay-server.json').read_text(), 'private pairing key')


if __name__ == '__main__':
    unittest.main()
