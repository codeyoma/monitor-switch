import ctypes
import unittest

from monitor_switch.nvidia import I2CInfo, Nvidia, switch_lg, switch_group


class DriverBoundary:
    """A stand-in for NVAPI's foreign-function boundary, without any hardware."""
    def __init__(self, status=0):
        self.calls = []
        self.status = status

    def write(self, gpu, info_pointer):
        info = ctypes.cast(info_pointer, ctypes.POINTER(I2CInfo)).contents
        self.calls.append({
            'gpu': gpu, 'mask': info.display_mask, 'version': info.version,
            'ddc': info.is_ddc, 'address': info.device_address,
            'register': bytes(info.register[:info.register_size]),
            'payload': bytes(info.data[:info.data_size]),
            'speed': info.speed, 'speed_khz': info.speed_khz,
            'port_set': info.port_is_set,
        })
        return self.status


class NvidiaTests(unittest.TestCase):
    def api(self, driver):
        api = object.__new__(Nvidia)
        api.function = lambda *args: driver.write
        api.resolve = lambda name: (123, 4)
        api.checked = lambda status: (_ for _ in ()).throw(RuntimeError('driver error')) if status else None
        api.close = lambda: None
        return api

    def test_verified_lg_values_reach_nvapi_with_correct_address_and_wire_packet(self):
        driver = DriverBoundary()
        api = self.api(driver)
        for target, expected in [('mac', b'\x84\x03\xF4\x00\x90\xDD'),
                                 ('windows', b'\x84\x03\xF4\x00\xD0\x9D')]:
            result = switch_lg('lg-id', target, api_factory=lambda: api,
                               enumerate_displays=lambda: [('dell-id', 'DISPLAY1'), ('lg-id', 'DISPLAY2')])
            self.assertEqual(result['status'], 'sent')
            self.assertFalse(result['physical_switch_confirmed'])
            self.assertEqual(driver.calls[-1], {
                'gpu': 123, 'mask': 4, 'version': 0x30040, 'ddc': 1,
                'address': 0x6E, 'register': b'\x50', 'payload': expected,
                'speed': 0xFFFF, 'speed_khz': 3, 'port_set': 0})

    def test_missing_or_duplicate_identity_never_uses_another_monitor(self):
        driver = DriverBoundary()
        for displays in [[('dell-id', 'DISPLAY1')],
                         [('lg-id', 'DISPLAY1'), ('lg-id', 'DISPLAY2')],
                         [('lg-id', 'DISPLAY1'), ('dell-id', 'DISPLAY1')]]:
            result = switch_lg('lg-id', 'windows', api_factory=lambda: self.api(driver),
                               enumerate_displays=lambda: displays)
            self.assertEqual(result['status'], 'unavailable')
            self.assertFalse(result['write_attempted'])
        self.assertEqual(driver.calls, [])

    def test_multiple_nvidia_gpus_or_output_mask_cannot_receive_a_write(self):
        for gpu_count, output_mask in [(2, 4), (1, 0), (1, 3)]:
            driver = DriverBoundary()
            api = object.__new__(Nvidia)
            api.checked = lambda status: None
            api.close = lambda: None

            def get_display(name, pointer):
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 456
                return 0

            def get_gpus(display, handles, pointer):
                handles[0] = 123
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))[0] = gpu_count
                return 0

            def get_output(display, pointer):
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))[0] = output_mask
                return 0

            functions = {0x35C29134: get_display, 0x34EF9506: get_gpus,
                         0xD995937E: get_output, 0xE812EB07: driver.write}
            api.function = lambda identifier, *args: functions[identifier]
            result = switch_lg('lg-id', 'windows', api_factory=lambda: api,
                               enumerate_displays=lambda: [('lg-id', 'DISPLAY2')])
            self.assertEqual(result['status'], 'unavailable')
            self.assertFalse(result['write_attempted'])
            self.assertEqual(driver.calls, [])

    def test_unknown_target_cannot_send_arbitrary_vcp_or_value(self):
        driver = DriverBoundary()
        result = switch_lg('lg-id', '144', api_factory=lambda: self.api(driver),
                           enumerate_displays=lambda: [('lg-id', 'DISPLAY1')])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(driver.calls, [])

    def test_native_failure_remains_uncertain_without_retry(self):
        driver = DriverBoundary(status=-1)
        result = switch_lg('lg-id', 'windows', api_factory=lambda: self.api(driver),
                           enumerate_displays=lambda: [('lg-id', 'DISPLAY1')])
        self.assertEqual(result['status'], 'uncertain')
        self.assertTrue(result['write_attempted'])
        self.assertEqual(len(driver.calls), 1)

    def test_journal_records_uncertainty_before_native_write(self):
        driver = DriverBoundary()
        events = []
        original = driver.write

        def write(gpu, pointer):
            self.assertEqual(events[-1]['status'], 'uncertain')
            self.assertTrue(events[-1]['write_attempted'])
            return original(gpu, pointer)

        driver.write = write
        switch_lg('lg-id', 'mac', api_factory=lambda: self.api(driver),
                  enumerate_displays=lambda: [('lg-id', 'DISPLAY2')],
                  journal=lambda report: events.append(dict(report)))
        self.assertEqual(events[-1]['status'], 'sent')

    def test_i2c_layout_matches_nvidia_windows_x64_sdk(self):
        self.assertEqual(ctypes.sizeof(I2CInfo), 64)
        self.assertEqual(I2CInfo.register.offset, 16)
        self.assertEqual(I2CInfo.data.offset, 32)
        self.assertEqual(I2CInfo.port_is_set.offset, 56)

    def test_group_prepares_both_routes_before_standard_and_alternate_writes(self):
        driver = DriverBoundary()
        api = self.api(driver)
        prepared = []

        def resolve(name):
            prepared.append(name)
            return 123, 4 if name == 'DISPLAY1' else 8

        api.resolve = resolve
        original = driver.write

        def write(gpu, info):
            self.assertEqual(set(prepared), {'DISPLAY1', 'DISPLAY2'})
            return original(gpu, info)

        driver.write = write
        profiles = [dict(id='dell', label='Dell', mode='standard', mac=17, windows=15),
                    dict(id='lg', label='LG', mode='lg-alt', mac=144, windows=208)]
        result = switch_group(profiles, 'mac', api_factory=lambda: api,
                              enumerate_displays=lambda: [('dell','DISPLAY1'),('lg','DISPLAY2')])
        self.assertEqual([r['status'] for r in result['results']], ['sent','sent'])
        by_mask = {r['mask']:r for r in driver.calls}
        self.assertEqual(by_mask[4]['register'], b'\x51')
        self.assertEqual(by_mask[4]['payload'], b'\x84\x03\x60\x00\x11\xC9')
        self.assertEqual(by_mask[8]['register'], b'\x50')
        self.assertEqual(by_mask[8]['payload'], b'\x84\x03\xF4\x00\x90\xDD')


if __name__ == '__main__':
    unittest.main()
