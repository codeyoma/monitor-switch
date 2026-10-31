import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
try:
    from PySide6.QtWidgets import QApplication
    from monitor_switch.gui import Window
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Install requirements.txt to run GUI tests.')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Window(demo=True, auto_scan=False)
        self.window.scanned({'monitors':[
            {'id':'demo-dell','name':'DELL P2425','current':15,'read_mode':'standard'},
            {'id':'demo-lg','name':'LG ULTRAGEAR','current':17,'read_mode':'standard'}]})

    def tearDown(self):
        self.window.job = None
        self.window.close()

    def test_protocol_change_blocks_capture_and_switch_until_saved(self):
        w = self.window
        self.assertTrue(w.switch_button.isEnabled())
        w.cards[1].mode.setCurrentIndex(1)
        w.capture()
        self.assertIsNone(w.cards[1].mac.currentData())
        self.assertFalse(w.switch_button.isEnabled())

    def test_busy_operation_disables_save_and_double_switch(self):
        w = self.window
        w.job = object()
        w.refresh()
        self.assertFalse(w.switch_button.isEnabled())
        self.assertFalse(w.save_button.isEnabled())

    def test_monitor_selection_updates_readout(self):
        w = self.window
        w.cards[0].monitor.setCurrentIndex(w.cards[0].monitor.findData('demo-lg'))
        self.assertIn('17', w.cards[0].readout.text())

    def test_paired_profiles_survive_restart_scan_and_skip_read_capture(self):
        profiles = [dict(role='dell', id='remote-dell', label='Dell', mode='standard', mac=17, windows=15),
                    dict(role='lg', id='remote-lg', label='LG', mode='lg-alt', mac=144, windows=208)]
        pairing = dict(version=1, host='127.0.0.1', hosts=['127.0.0.1'], port=55661,
                       server_id='0'*32, key='0'*64, monitor_id='remote-lg', monitors=profiles)
        w = self.window
        w.paired({'pairing':pairing})
        w.capture()
        self.assertEqual([row['mac'] for row in w.values()['monitors']], [17,144])
        with patch('monitor_switch.gui.load_config', return_value=w.values()):
            restarted = Window(demo=False, auto_scan=False)
        try:
            restarted.scanned({'monitors':[]})
            from monitor_switch.core import validate_config
            validate_config(restarted.values(), require_enabled=True)
            self.assertTrue(restarted.switch_button.isEnabled())
            self.assertIn('Windows helper', restarted.cards[0].readout.text())
        finally:
            restarted.close()

    def test_usb_arrival_requests_mac_once_and_query_errors_do_not_request(self):
        from monitor_switch.usb_events import USBPresence
        w = self.window
        w.usb_toggle.blockSignals(True)
        w.usb_toggle.setChecked(True)
        w.usb_toggle.blockSignals(False)
        tracker = USBPresence()
        with patch('monitor_switch.gui.sys.platform', 'darwin'), patch.object(w, 'start') as start:
            for count in [0,1,1,1]:
                w.usb_observed(tracker.observe(count))
            w.usb_observed(tracker.observe(None,error='query failed'))
            self.assertEqual(start.call_count,1)
            self.assertEqual(start.call_args.args[0]['target'],'mac')
            self.assertTrue(start.call_args.args[0]['automatic'])

    def test_windows_gui_does_not_start_a_second_usb_arrival_route(self):
        w = self.window
        w.usb_toggle.blockSignals(True)
        w.usb_toggle.setChecked(True)
        w.usb_toggle.blockSignals(False)
        with patch('monitor_switch.gui.sys.platform','win32'), patch.object(w,'start') as start:
            w.usb_observed({'state':'present','count':1,'trigger':True})
            start.assert_not_called()

    def test_usb_stop_before_thread_start_never_subscribes(self):
        from monitor_switch.gui import USBWatch
        watch = USBWatch(None)
        watch.stop()
        with patch('monitor_switch.usb_native.MacUSBEvents') as source:
            watch.start()
            finished = watch.wait(1000)
            if not finished:
                watch.stop()
                watch.wait(4000)
            self.assertTrue(finished)
            source.assert_not_called()
