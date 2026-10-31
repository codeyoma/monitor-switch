import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from monitor_switch.core import default_config, validate_config, save_config, load_config, switch
from monitor_switch.backends import Monitor, parse_m1ddc, parse_ddcctl, MacBackend, WindowsBackend


class FakeBackend:
    def __init__(self):
        self.monitors = [Monitor('dell-id', 'DELL P2425', 'dell-id'), Monitor('lg-id', 'LG ULTRAGEAR', 'lg-id')]
        self.sent = []
        self.barrier = threading.Barrier(2)
        self.closed = False

    @contextmanager
    def snapshot(self):
        try:
            yield self.monitors
        finally:
            self.closed = True

    def set_source(self, monitor, code, mode):
        # Both writes must begin before either can disconnect the sending host.
        if len(self.monitors) == 2:
            self.barrier.wait(timeout=2)
        self.sent.append((monitor.id, code, mode))
        if monitor.id == 'lg-id' and code == 18:
            raise RuntimeError('DDC unavailable')


def configured():
    c = default_config()
    for row, identity in zip(c['monitors'], ['dell-id', 'lg-id']):
        row.update(id=identity, enabled=True, mac=15, windows=17)
    return c


class CoreTests(unittest.TestCase):
    def test_no_guessed_inputs_in_default_config(self):
        c = default_config()
        self.assertIsNone(c['monitors'][0]['windows'])
        with self.assertRaises(ValueError):
            validate_config(c, require_enabled=True)

    def test_validate_all_before_any_hardware_write(self):
        b = FakeBackend()
        c = configured()
        c['monitors'][1]['windows'] = 65536
        with self.assertRaises(ValueError):
            switch(c, 'windows', b)
        self.assertEqual(b.sent, [])

    def test_duplicate_identity_rejected(self):
        c = configured()
        c['monitors'][1]['id'] = 'dell-id'
        with self.assertRaises(ValueError):
            validate_config(c)

    def test_both_commands_start_before_disconnect_and_partial_failure_survives(self):
        b = FakeBackend()
        c = configured()
        c['monitors'][1]['windows'] = 18
        results = switch(c, 'windows', b)
        self.assertEqual([r['status'] for r in results], ['sent', 'uncertain'])
        self.assertEqual(len(b.sent), 2)
        self.assertTrue(b.closed)

    def test_missing_monitor_never_falls_back_to_another(self):
        b = FakeBackend()
        b.monitors = b.monitors[:1]
        results = switch(configured(), 'windows', b)
        self.assertEqual([r['status'] for r in results], ['sent', 'unavailable'])
        self.assertEqual(b.sent, [('dell-id', 17, 'standard')])

    def test_config_roundtrip_and_invalid_json_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'config.json'
            save_config(configured(), p)
            self.assertEqual(load_config(p), configured())
            p.write_text('{bad')
            with self.assertRaises(ValueError):
                load_config(p)
            self.assertEqual(p.read_text(), '{bad')

    def test_bad_target_bool_and_mode_rejected(self):
        for key, value in [('mac', True), ('mode', 'made-up')]:
            c = configured()
            c['monitors'][0][key] = value
            with self.assertRaises(ValueError):
                validate_config(c)
        with self.assertRaises(ValueError):
            switch(configured(), 'linux', FakeBackend())


class ParserTests(unittest.TestCase):
    def test_m1ddc_uuid_instead_of_changing_index(self):
        monitors = parse_m1ddc('[1] DELL P2425 (11111111-2222-3333-4444-555555555555)\n - Display ID: 2\n[3] LG ULTRAGEAR (AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE)\n - Display ID: 3\n')
        self.assertEqual(len(monitors), 2)
        self.assertEqual(monitors[1].selector, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE')
        self.assertEqual(monitors[0].display_id, 2)

    def test_ddcctl_selects_uuid_instead_of_a_stale_index(self):
        output = 'D: CGDisplay 11111111-2222-3333-4444-555555555555 dispID(#2) (1200x1920 90°) 90.0 DPI\nD: CGDisplay AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE dispID(#3) (1920x1080 0°) HiDPI\n'
        monitors = parse_ddcctl(output)
        self.assertEqual(monitors[1].selector, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE')
        self.assertEqual(monitors[1].id, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE')

    def test_ddcctl_exit_zero_with_error_is_failure(self):
        b = MacBackend(tool='/fake/ddcctl')
        b.run = lambda *a, **k: 'E: Failed to send DDC command!'
        with self.assertRaises(RuntimeError):
            b.set_source(Monitor('id', 'Dell', '1'), 17, 'standard')

    def test_unpatched_m1ddc_is_refused_before_any_ddc_read(self):
        b = MacBackend(tool='/fake/m1ddc')
        b.run = lambda *a, **k: '[1] DELL P2425 (11111111-2222-3333-4444-555555555555)\n'
        with self.assertRaisesRegex(RuntimeError, 'validated DDC replies'):
            with b.snapshot():
                self.fail('Unpatched helper accepted')

    def test_windows_rejects_lg_alternate_address_instead_of_sending_wrong_packet(self):
        b = WindowsBackend(native=object())
        with self.assertRaisesRegex(RuntimeError, 'alternate'):
            b.set_source(Monitor('id', 'LG', '1', handle=123), 144, 'lg-alt')

    def test_standard_input_uses_low_byte_but_retains_raw_reply(self):
        class Native:
            def read(self, handle):
                return 0x110F
        m = Monitor('id', 'Dell', '1', handle=123)
        self.assertEqual(WindowsBackend(native=Native()).read_source(m), 15)
        self.assertEqual(m.raw_current, 0x110F)


if __name__ == '__main__':
    unittest.main()
