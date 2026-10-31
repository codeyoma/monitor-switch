import threading
import time
import unittest

from monitor_switch.usb_events import USBEventWatcher
from monitor_switch.usb_native import CMFilter, target_usb_link, notification_link, USB_DEVICE_GUID
import ctypes


class FakeSource:
    def __init__(self):
        self.current, self.queries = 0, 0
        self.started, self.closed = False, False
        self.failure = None

    def start(self):
        self.started = True

    def stop(self):
        self.closed = True

    def count(self):
        assert self.started
        self.queries += 1
        if self.failure:
            raise RuntimeError(self.failure)
        return self.current


class EventTests(unittest.TestCase):
    def setUp(self):
        self.source, self.results = FakeSource(), []

        def factory(changed, failed):
            self.source.changed, self.source.failed = changed, failed
            return self.source

        self.watcher = USBEventWatcher(self.results.append, source_factory=factory, settle=0.03)
        self.thread = threading.Thread(target=self.watcher.run)
        self.thread.start()
        self.assertTrue(self.watcher.ready.wait(1))

    def tearDown(self):
        self.watcher.stop()
        self.thread.join(1)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.source.closed)

    def wait_queries(self, count):
        deadline = time.monotonic() + 1
        while self.source.queries < count and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertEqual(self.source.queries, count)

    def test_idle_has_one_baseline_and_no_repeated_queries(self):
        self.assertEqual(self.source.queries, 1)
        time.sleep(0.15)
        self.assertEqual(self.source.queries, 1)
        self.assertFalse(any(row['trigger'] for row in self.results))

    def test_event_burst_causes_one_delayed_query_and_one_arrival(self):
        self.source.current = 1
        for _ in range(5):
            self.source.changed()
        self.assertEqual(self.source.queries, 1)
        self.wait_queries(2)
        self.assertTrue(self.results[-1]['trigger'])
        time.sleep(0.1)
        self.assertEqual(self.source.queries, 2)

    def test_query_error_does_not_rearm_or_retry(self):
        self.source.current = 1
        self.source.changed()
        self.wait_queries(2)
        self.source.failure = 'query failed'
        self.source.changed()
        self.wait_queries(3)
        self.assertEqual(self.results[-1]['state'], 'error')
        self.source.failure = None
        time.sleep(0.1)
        self.assertEqual(self.source.queries, 3)
        self.source.changed()
        self.wait_queries(4)
        self.assertFalse(self.results[-1]['trigger'])

    def test_off_on_establishes_new_baseline_without_arrival(self):
        self.watcher.set_enabled(False)
        self.source.current = 1
        self.source.changed()
        time.sleep(0.07)
        self.assertEqual(self.source.queries, 1)
        self.watcher.set_enabled(True)
        self.wait_queries(2)
        self.assertTrue(self.results[-1]['baseline'])
        self.assertFalse(self.results[-1]['trigger'])

    def test_windows_notification_layout_and_physical_device_filter(self):
        self.assertEqual(ctypes.sizeof(CMFilter),416)
        self.assertTrue(target_usb_link(r'\\?\usb#vid_262a&pid_10e3#instance#{guid}'))
        self.assertTrue(target_usb_link(r'\\?\USB#VID_262A&PID_10E3&REV_0100#instance'))
        self.assertFalse(target_usb_link(r'\\?\USB#VID_262A&PID_10E3&MI_00#child'))
        self.assertFalse(target_usb_link(r'\\?\USB#VID_262A&PID_0001#other'))

    def test_windows_notification_reads_only_terminated_utf16_before_padding(self):
        link = r'\\?\USB#VID_262A&PID_10E3#instance'
        header = bytes(8) + USB_DEVICE_GUID
        data = header + link.encode('utf-16-le') + b'\0\0' + b'\x00\xD8'
        self.assertEqual(notification_link(data),link)
        self.assertIsNone(notification_link(header + link.encode('utf-16-le')))
        self.assertIsNone(notification_link(b'\x02'+data[1:]))

    def test_query_overtaken_by_an_event_cannot_emit_stale_arrival(self):
        original = self.source.count
        entered, release = threading.Event(), threading.Event()
        first = [True]

        def delayed():
            value = original()
            if first[0]:
                first[0] = False
                entered.set()
                release.wait(1)
            return value

        self.source.count = delayed
        self.source.current = 1
        self.source.changed()
        try:
            self.assertTrue(entered.wait(1))
            self.source.current = 0
            self.source.changed()
        finally:
            release.set()
        self.wait_queries(3)
        self.assertFalse(any(row['trigger'] for row in self.results))

    def test_registration_failure_stops_without_queries_or_polling_fallback(self):
        results=[]
        source=FakeSource()
        source.start=lambda: (_ for _ in ()).throw(RuntimeError('subscription failed'))
        watcher=USBEventWatcher(results.append,source_factory=lambda changed,failed:source)
        thread=threading.Thread(target=watcher.run)
        thread.start();thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(source.queries,0)
        self.assertTrue(source.closed)
        self.assertEqual(results[-1]['state'],'error')
        self.assertIn('no polling fallback',results[-1]['message'])


if __name__ == '__main__':
    unittest.main()
