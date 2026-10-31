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
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        profiles = json.loads((Path(__file__).resolve().parents[1]/'relay-monitors.example.json').read_text())
        (self.root/'relay-monitors.json').write_text(json.dumps(profiles))
        self.commands, self.events = [], []
        def runner(config, target, **kwargs):
            self.commands.append((config,target))
            return {'results':[dict(id=p['id'],status='sent') for p in config['monitors']]}
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
            window.show();window.close()
            self.assertFalse(window.isVisible())
        finally:
            resident.icon.hide()
            window.tray_managed=False
            window.close()
