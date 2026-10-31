"""Experimental NVIDIA route for the two LG input values verified by the user.

Uses the NVAPI ABI and the LG alternate packet demonstrated by winddc.
No driver DLL or NVIDIA SDK code is distributed with this module.
"""
import ctypes
import struct
import sys


LG_INPUTS = {'mac': 0x90, 'windows': 0xD0}
U32 = ctypes.c_uint32
U8 = ctypes.c_uint8
HANDLE = ctypes.c_void_p


class I2CInfo(ctypes.Structure):
    _fields_ = [
        ('version', U32), ('display_mask', U32), ('is_ddc', U8),
        ('device_address', U8), ('register', ctypes.POINTER(U8)),
        ('register_size', U32), ('data', ctypes.POINTER(U8)),
        ('data_size', U32), ('speed', U32), ('speed_khz', ctypes.c_int32),
        ('port', U8), ('port_is_set', U32),
    ]


def windows_displays():
    """Return exact monitor interface paths and their current Win32 display names."""
    from .backends import WindowsNative
    native = WindowsNative()
    handles = []

    @native.Callback
    def collect(handle, _hdc, _rect, _data):
        handles.append(handle)
        return True

    native.check(native.user.EnumDisplayMonitors(None, None, collect, 0))
    rows = []
    for handle in handles:
        info = native.Info()
        info.cbSize = ctypes.sizeof(info)
        native.check(native.user.GetMonitorInfoW(handle, ctypes.byref(info)))
        # Enumerate all children, so cloned/multi-monitor associations are rejected.
        index = 0
        while True:
            device = native.Device()
            device.cb = ctypes.sizeof(device)
            if not native.user.EnumDisplayDevicesW(info.szDevice, index, ctypes.byref(device), 1):
                break
            if device.DeviceID:
                rows.append((device.DeviceID, info.szDevice))
            index += 1
            if index > 64:
                raise RuntimeError('Excessive monitor count; no command sent.')
    return rows


class Nvidia:
    def __init__(self):
        if sys.platform != 'win32' or struct.calcsize('P') != 8:
            raise RuntimeError('Use 64-bit Python on the Windows NVIDIA PC.')
        self.dll = ctypes.CDLL('nvapi64.dll', winmode=0x800)  # System32 only.
        self.query = self.dll.nvapi_QueryInterface
        self.query.argtypes, self.query.restype = [U32], HANDLE
        self.error_message = self.function(0x6C2D048C, ctypes.c_int, ctypes.POINTER(ctypes.c_char))
        self.unload = self.function(0xD22BDD7E)
        self.checked(self.function(0x0150E828)())

    def function(self, identifier, *args):
        pointer = self.query(identifier)
        if not pointer:
            raise RuntimeError(f'NVIDIA interface 0x{identifier:08X} is unavailable.')
        return ctypes.CFUNCTYPE(ctypes.c_int, *args)(pointer)

    def checked(self, status):
        if status:
            text = ctypes.create_string_buffer(64)
            self.error_message(status, text)
            raise RuntimeError(f'NVAPI {status}: {text.value.decode(errors="replace")}')

    def close(self):
        self.unload()

    def resolve(self, logical_name):
        display = HANDLE()
        get_display = self.function(0x35C29134, ctypes.c_char_p, ctypes.POINTER(HANDLE))
        self.checked(get_display(logical_name.encode('ascii'), ctypes.byref(display)))
        if not display.value:
            raise RuntimeError('NVIDIA returned an empty display handle.')
        handles, count = (HANDLE * 64)(), U32()
        get_gpus = self.function(0x34EF9506, HANDLE, ctypes.POINTER(HANDLE), ctypes.POINTER(U32))
        self.checked(get_gpus(display, handles, ctypes.byref(count)))
        if count.value != 1 or not handles[0]:
            raise RuntimeError('LG does not map to exactly one NVIDIA GPU; no command sent.')
        output = U32()
        get_output = self.function(0xD995937E, HANDLE, ctypes.POINTER(U32))
        self.checked(get_output(display, ctypes.byref(output)))
        mask = output.value
        if not mask or mask > 0xFFFFFF or mask & (mask - 1):
            raise RuntimeError('NVIDIA returned an ambiguous output mask; no command sent.')
        return handles[0], mask

    def set_lg_input(self, logical_name, target, before_write):
        self.write_prepared(self.resolve(logical_name), 'lg-alt', LG_INPUTS[target], before_write)

    def write_prepared(self, route, mode, code, before_write):
        if mode not in ('standard', 'lg-alt') or type(code) is not int or not 1 <= code <= 65535:
            raise ValueError('Invalid configured input protocol or code.')
        source_address, vcp = (0x50, 0xF4) if mode == 'lg-alt' else (0x51, 0x60)
        gpu, mask = route
        write = self.function(0xE812EB07, HANDLE, ctypes.POINTER(I2CInfo))
        packet = [0x84, 0x03, vcp, (code >> 8) & 0xFF, code & 0xFF]
        checksum = 0x6E ^ source_address
        for byte in packet:
            checksum ^= byte
        data = (U8 * 6)(*packet, checksum)
        source = (U8 * 1)(source_address)
        info = I2CInfo()
        info.version = ctypes.sizeof(I2CInfo) | (3 << 16)
        info.display_mask, info.is_ddc, info.device_address = mask, 1, 0x6E
        info.register, info.register_size = source, 1
        info.data, info.data_size = data, 6
        info.speed, info.speed_khz = 0xFFFF, 3  # NVAPI enum 3 means 33 kHz.
        before_write({'display_mask': f'0x{mask:08X}',
                      'packet': bytes(data).hex(' ').upper()})
        # One intentional write. Never retry after a disconnection or driver error.
        self.checked(write(gpu, ctypes.byref(info)))


def switch_group(monitors, target, *, api_factory=Nvidia,
                 enumerate_displays=windows_displays, journal=lambda result: None):
    """Resolve every output first, then serialize driver transactions on the GPU."""
    import threading
    if target not in ('mac', 'windows') or not 1 <= len(monitors) <= 2:
        raise ValueError('Invalid switch group.')
    if len({row['id'].casefold() for row in monitors}) != len(monitors):
        raise ValueError('Duplicate configured monitor identity.')
    results = [dict(id=row['id'], label=row['label'], code=row[target],
                    status='unavailable', write_attempted=False, physical_switch_confirmed=False)
               for row in monitors]
    lock = threading.Lock()

    def record(index, **changes):
        with lock:
            results[index].update(changes)
            journal({'results': [dict(item) for item in results]})

    api, prepared = None, []
    try:
        displays = enumerate_displays()
        api = api_factory()
        for index, row in enumerate(monitors):
            try:
                if row['mode'] not in ('standard', 'lg-alt') or type(row[target]) is not int or not 1 <= row[target] <= 65535:
                    raise ValueError('Invalid configured input.')
                matches = [name for identity, name in displays if identity.casefold() == row['id'].casefold()]
                if len(matches) != 1 or sum(name == matches[0] for _, name in displays) != 1:
                    raise RuntimeError('Configured monitor missing or ambiguous; no command sent.')
                prepared.append((index, row, api.resolve(matches[0])))
            except Exception as exc:
                record(index, error=str(exc))
        def send(item):
            index, row, route = item
            try:
                api.write_prepared(route, row['mode'], row[target],
                                   lambda packet: record(index, **packet, status='uncertain', write_attempted=True))
                record(index, status='sent')
            except Exception as exc:
                record(index, error=str(exc))

        # Keep the prepared GPU/output routes and make one driver transaction
        # at a time, including when both monitors share the same GPU.
        for item in prepared:
            send(item)
    except Exception as exc:
        for index, result in enumerate(results):
            if not result['write_attempted']:
                record(index, error=str(exc))
    finally:
        if api:
            try:
                api.close()
            except Exception:
                pass
    report = {'results': results}
    journal(report)
    return report


def switch_lg(identity, target, *, api_factory=Nvidia,
              enumerate_displays=windows_displays, journal=lambda report: None):
    report = {'monitor_id': identity, 'target': target, 'protocol': 'lg-alt-nvapi',
              'vcp': '0xF4', 'source_address': '0x50', 'status': 'unavailable',
              'write_attempted': False, 'physical_switch_confirmed': False}
    api = None
    try:
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError('An exact monitor interface path is required.')
        if target not in LG_INPUTS:
            raise ValueError('Only mac and windows are allowed.')
        displays = enumerate_displays()
        matches = [(item_id, name) for item_id, name in displays
                   if item_id.casefold() == identity.casefold()]
        if len(matches) != 1:
            raise RuntimeError('The configured LG is missing or duplicated; no command sent.')
        _, name = matches[0]
        if sum(1 for _, logical_name in displays if logical_name == name) != 1:
            raise RuntimeError('Multiple monitors share this Windows display; use extended desktop.')
        report.update(logical_display=name, input_code=LG_INPUTS[target])
        api = api_factory()

        def before_write(route):
            report.update(route, status='uncertain', write_attempted=True)
            journal(report)

        api.set_lg_input(name, target, before_write)
        report['status'] = 'sent'
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if api is not None:
            try:
                api.close()
            except Exception as exc:
                report['cleanup_error'] = str(exc)
    journal(report)
    return report
