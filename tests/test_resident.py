import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor_switch.service import RelayService


class FakeWatcher:
    def __init__(self, callback, enabled=True):
        self.callback, self.enabled = callback, enabled
        self.done = threading.Event()

    def run(self):
        self.callback(dict(state='absent',trigger=False,baseline=True))
        self.done.wait()

    def stop(self):
        self.done.set()

    def set_enabled(self, enabled):
        self.enabled = enabled

    def current(self, result):
        return self.enabled and not self.done.is_set()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        hosts = patch('monitor_switch.service.network_hosts', return_value=['127.0.0.1'])
        hosts.start()
        self.addCleanup(hosts.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        profiles = json.loads((Path(__file__).resolve().parents[1]/'relay-monitors.example.json').read_text())
        (self.root/'relay-monitors.json').write_text(json.dumps(profiles))
        self.commands, self.events = [], []
        def runner(config, target, **kwargs):
            self.commands.append((config,target))
            return {'results':[dict(id=p['id'],status='sent',write_attempted=True) for p in config['monitors']]}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = listener.getsockname()[1]
        self.service = RelayService(self.root,self.events.append,data=self.root/'data',runner=runner,
                                    watcher_factory=FakeWatcher,address=('127.0.0.1',port))

    def tearDown(self):
        self.service.stop()
        self.directory.cleanup()

    def test_login_without_network_starts_listener_without_switching(self):
        with patch('monitor_switch.service.network_hosts',return_value=[]):
            self.service.start()
            self.assertTrue(self.service.server_thread.is_alive())
            self.assertEqual(self.commands,[])
            with self.assertRaisesRegex(RuntimeError,'Connect Windows'):
                self.service.export_pairing(self.root/'pair.json')
        self.assertFalse((self.root/'pair.json').exists())

    def test_manual_and_automatic_share_the_existing_batch_and_dedup(self):
        self.service.start()
        self.service.manual('mac')
        self.assertEqual(len(self.commands[0][0]['monitors']),2)
        event = dict(state='present',trigger=True)
        self.service.observed_usb(event)
        self.service.observed_usb(event)
        self.assertEqual([command[1] for command in self.commands],['mac','windows'])
        self.service.set_auto(False)
        self.service.observed_usb(event)
        self.assertEqual(len(self.commands),2)
        self.assertFalse(json.loads(self.service.state_path.read_text())['usb_auto'])

    def test_restart_preserves_pairing_key_and_disabled_auto(self):
        self.service.start()
        key = self.service.pairing['key']
        self.service.set_auto(False)
        self.service.stop()
        self.service.start()
        self.assertEqual(self.service.pairing['key'],key)
        self.assertFalse(self.service.auto_enabled.is_set())

    def test_manual_windows_request_from_mac_then_windows_usb_arrival_sends_nothing(self):
        from monitor_switch.relay import request
        self.service.start()
        pairing = self.service.pairing | {'host': '127.0.0.1'}
        identities = [p['id'] for p in self.service.profiles]
        request(pairing, 'POST', '/switch', {'target': 'windows', 'monitors': identities})
        self.assertEqual(len(self.commands), 1)
        self.service.observed_usb(dict(state='present', trigger=True))
        self.assertEqual(len(self.commands), 1, 'Already-requested inputs must not receive another native command.')
        result = [e for e in self.events if e['kind'] == 'result'][-1]['result']
        self.assertEqual([r['status'] for r in result['results']], ['skipped', 'skipped'])
        self.assertTrue(all(r['write_attempted'] is False for r in result['results']))

    def test_automatic_request_switches_only_the_monitor_with_a_different_last_input(self):
        self.service.start()
        dell, lg = self.service.profiles
        self.service.send('windows', [dell['id']])
        self.service.send('mac', [lg['id']])
        result = self.service.send('windows', [dell['id'], lg['id']], automatic=True)
        self.assertEqual([p['id'] for p in self.commands[-1][0]['monitors']], [lg['id']])
        self.assertEqual([r['status'] for r in result['results']], ['skipped', 'sent'])

    def test_mac_usb_relay_skips_the_same_input_and_returns_the_explanation(self):
        from monitor_switch.relay import request
        self.service.start()
        pairing = self.service.pairing | {'host': '127.0.0.1'}
        data = {'target': 'mac', 'monitors': [p['id'] for p in self.service.profiles]}
        request(pairing, 'POST', '/switch', data)
        result = request(pairing, 'POST', '/switch', data | {'automatic': True})
        self.assertEqual(len(self.commands), 1)
        self.assertFalse(result['physical_switch_confirmed'])
        self.assertTrue(all(r['status']=='skipped' and not r['write_attempted'] for r in result['results']))
        self.assertTrue(all('no input command sent' in r['message'] for r in result['results']))

    def test_explicit_manual_button_always_allows_reapplying_the_input(self):
        self.service.start()
        self.service.manual('windows')
        self.service.manual('windows')
        self.assertEqual([command[1] for command in self.commands], ['windows', 'windows'])

    def test_failed_opposite_command_invalidates_history_and_is_not_silently_skipped(self):
        self.service.start()
        runner = self.service.runner
        self.service.manual('windows')
        def failed(*args, **kwargs):
            raise RuntimeError('The driver failed after a possible write.')
        self.service.runner = failed
        result = self.service.manual('mac')
        self.assertTrue(all(r['status']=='uncertain' for r in result['results']))
        self.service.runner = runner
        self.service.observed_usb(dict(state='present', trigger=True))
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(self.commands[-1][1], 'windows')
        self.assertEqual(len(self.commands[-1][0]['monitors']), 2)

    def test_partial_native_result_is_not_reused_as_a_complete_success(self):
        self.service.start()
        runner = self.service.runner
        self.service.runner = lambda config, target, **kwargs: {'results': [dict(
            id=config['monitors'][0]['id'], status='sent')]}
        result = self.service.manual('windows')
        self.assertTrue(all(r['status']=='uncertain' for r in result['results']))
        self.service.runner = runner
        self.service.observed_usb(dict(state='present', trigger=True))
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(len(self.commands[-1][0]['monitors']), 2)

    def test_restart_does_not_assume_a_previous_command_is_still_the_displayed_input(self):
        self.service.start()
        self.service.manual('windows')
        self.service.stop()
        self.service.start()
        self.service.observed_usb(dict(state='present', trigger=True))
        self.assertEqual(len(self.commands), 2)

    def test_changed_input_mapping_is_not_skipped_using_the_previous_code(self):
        self.service.start()
        self.service.manual('windows')
        dell, _lg = self.service.profiles
        dell['windows'] = 16
        self.service.observed_usb(dict(state='present', trigger=True))
        profiles = self.commands[-1][0]['monitors']
        self.assertEqual([p['id'] for p in profiles], [dell['id']])
        self.assertEqual(profiles[0]['windows'], 16)

    def test_direct_request_cannot_overlap_a_manual_request(self):
        self.service.start()
        entered, release = threading.Event(), threading.Event()
        original = self.service.runner
        def blocked(config, target, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(config, target, **kwargs)
        self.service.runner = blocked
        thread = threading.Thread(target=lambda:self.service.manual('windows'))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(RuntimeError, 'progress'):
                self.service.send('mac', [p['id'] for p in self.service.profiles])
        finally:
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual([command[1] for command in self.commands], ['windows'])

    def test_direct_request_blocks_http_and_usb_until_completion(self):
        from monitor_switch.relay import request
        self.service.start()
        entered, release = threading.Event(), threading.Event()
        original = self.service.runner
        calls = []
        def blocked(config, target, **kwargs):
            calls.append(target)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            return original(config, target, **kwargs)
        self.service.runner = blocked
        identities = [p['id'] for p in self.service.profiles]
        thread = threading.Thread(target=lambda:self.service.send('windows', identities))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(RuntimeError) as raised:
                request(self.service.pairing | {'host':'127.0.0.1'}, 'POST', '/switch',
                        {'target':'mac', 'monitors':identities})
            self.assertEqual(raised.exception.status, 'unavailable')
            self.service.observed_usb(dict(state='present', trigger=True))
            self.assertEqual(calls, ['windows'])
        finally:
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_shutdown_waits_for_a_direct_request_and_rejects_new_writes(self):
        self.service.start()
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        original = self.service.runner
        def blocked(config, target, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(config, target, **kwargs)
        self.service.runner = blocked
        identities = [p['id'] for p in self.service.profiles]
        request_thread = threading.Thread(target=lambda:self.service.send('windows', identities))
        request_thread.start()
        def stop():
            self.service.stop();stopped.set()
        stop_thread = threading.Thread(target=stop)
        try:
            self.assertTrue(entered.wait(1))
            stop_thread.start()
            self.assertTrue(self.service.stopping.wait(1))
            self.assertFalse(stopped.wait(0.8), 'Shutdown must wait for the admitted native request.')
            result = self.service.send('mac', identities)
            self.assertTrue(all(row['status']=='unavailable' for row in result['results']))
        finally:
            release.set();request_thread.join(2)
            if stop_thread.ident is not None:stop_thread.join(2)
        self.assertFalse(request_thread.is_alive())
        self.assertFalse(stop_thread.is_alive())
        self.assertTrue(stopped.is_set())


try:
    from PySide6.QtWidgets import QApplication
    from monitor_switch.gui import Window
    from monitor_switch.tray import Resident, SingleInstance, WindowsWindow
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None,'GUI dependencies unavailable')
class ResidentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_close_hides_window_keeps_observer_and_icon_alive(self):
        window = Window(demo=True,auto_scan=False)
        resident = Resident(window)
        try:
            window.show()
            window.close()
            self.assertFalse(window.isVisible())
            self.assertFalse(window.closing)
            self.assertTrue(resident.icon.isVisible())
            resident.show_window()
            self.assertTrue(window.isVisible())
            self.assertFalse(resident.login.isEnabled())
        finally:
            resident.icon.hide()
            window.tray_managed = False
            window.close()

    def test_menu_respects_unsaved_changes_and_uses_manual_routes(self):
        window = Window(demo=True,auto_scan=False)
        resident = Resident(window)
        try:
            with patch.object(window,'start') as start:
                resident.mac.trigger()
                self.assertEqual(start.call_args.args[0]['target'],'mac')
                window.changed()
                self.assertFalse(resident.mac.isEnabled())
                window.switch_to('windows')
                self.assertEqual(start.call_count,1)
        finally:
            resident.icon.hide()
            window.tray_managed = False
            window.close()

    def test_single_instance_second_foreground_launch_opens_existing_window(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = SingleInstance(directory,True), SingleInstance(directory,True)
            opened = []
            first.show_requested.connect(lambda:opened.append(True))
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                deadline=time.monotonic()+1
                while not opened and time.monotonic()<deadline:
                    self.app.processEvents()
                self.assertEqual(opened,[True])
            finally:
                first.close()

    def test_windows_tray_adapter_starts_without_writes_and_rolls_back_failed_toggle(self):
        class Service:
            def __init__(self,root,notify):
                self.notify, self.commands = notify, []
                self.auto_enabled = threading.Event()
                self.profiles = []
            def start(self):
                self.auto_enabled.set()
                self.notify(dict(kind='usb',result=dict(state='present',baseline=True)))
                return self
            def manual(self,target): self.commands.append(target)
            def set_auto(self,enabled): raise OSError('Cannot save USB preference')
            def stop(self): pass
        window = WindowsWindow(service_factory=Service)
        resident = Resident(window)
        def finish():
            deadline=time.monotonic()+2
            while window.job and time.monotonic()<deadline:
                self.app.processEvents()
                time.sleep(0.001)
            self.assertIsNone(window.job)
        try:
            finish()
            self.assertTrue(window.ready)
            self.assertEqual(window.service.commands,[])
            self.assertIn('present',window.usb_status.text())
            resident.mac.trigger();finish()
            self.assertEqual(window.service.commands,['mac'])
            resident.automatic.trigger();finish()
            self.assertTrue(window.usb_toggle.isChecked())
            self.assertTrue(resident.automatic.isChecked())
            self.assertIn('Cannot save USB preference',window.log.toPlainText())
            window.observed(dict(kind='result',target='mac',result={
                'results':[dict(id='lg-id',status='uncertain',write_attempted=True)],
                'error':'Worker did not exit within the additional 2-second deadline.'}))
            self.assertIn('2-second deadline',window.log.toPlainText())
            window.show();window.close()
            self.assertFalse(window.isVisible())
        finally:
            resident.icon.hide()
            window.tray_managed=False
            window.close()
