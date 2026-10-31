import unittest
import plistlib
from types import SimpleNamespace
from unittest.mock import patch
from monitor_switch.usb import AutomaticRequests, mac_count
from monitor_switch.usb_events import USBPresence


class USBTests(unittest.TestCase):
    def test_mac_plist_dictionary_root_counts_physical_device_once(self):
        device = dict(idVendor=0x262A,idProduct=0x10E3,IORegistryEntryID=123)
        root = {'IORegistryEntryChildren':[{'IORegistryEntryChildren':[device,device]}]}
        result = SimpleNamespace(returncode=0,stdout=plistlib.dumps(root))
        with patch('monitor_switch.usb.subprocess.run',return_value=result):
            self.assertEqual(mac_count(),1)
        result.stdout = plistlib.dumps({'IORegistryEntryChildren':['bad node']})
        with patch('monitor_switch.usb.subprocess.run',return_value=result):
            with self.assertRaises(RuntimeError):
                mac_count()
    def test_same_host_reenumeration_is_deduplicated_but_fast_round_trip_is_allowed(self):
        gate = AutomaticRequests()
        self.assertTrue(gate.allow('mac',['dell','lg'],True))
        self.assertFalse(gate.allow('mac',['dell','lg'],True))
        self.assertTrue(gate.allow('windows',['dell','lg'],True))
        self.assertTrue(gate.allow('mac',['dell','lg'],True))
        self.assertTrue(gate.allow('mac',['dell','lg'],False))
        self.assertTrue(gate.allow('mac',['dell','lg'],True))
    def test_startup_baseline_does_not_switch_and_new_stable_arrival_does(self):
        tracker = USBPresence()
        self.assertFalse(tracker.observe(1)['trigger'])
        self.assertFalse(tracker.observe(0)['trigger'])
        self.assertTrue(tracker.observe(1)['trigger'])
        self.assertFalse(tracker.observe(1)['trigger'])

    def test_removal_never_selects_other_host(self):
        tracker = USBPresence()
        tracker.observe(1)
        self.assertFalse(tracker.observe(0)['trigger'])

    def test_errors_and_ambiguity_do_not_rearm_a_present_device(self):
        tracker = USBPresence()
        tracker.observe(1)
        self.assertFalse(tracker.observe(None, error='query failed')['trigger'])
        self.assertFalse(tracker.observe(2)['trigger'])
        self.assertFalse(tracker.observe(1)['trigger'])

    def test_startup_ambiguity_waits_for_unambiguous_baseline(self):
        tracker = USBPresence()
        self.assertFalse(tracker.observe(2)['trigger'])
        self.assertFalse(tracker.observe(1)['trigger'])
        self.assertFalse(tracker.observe(1)['trigger'])

    def test_ambiguity_resolution_is_a_new_baseline_not_a_new_arrival(self):
        tracker = USBPresence()
        tracker.observe(0)
        tracker.observe(2)
        self.assertFalse(tracker.observe(1)['trigger'])
        self.assertFalse(tracker.observe(1)['trigger'])


if __name__ == '__main__':
    unittest.main()
