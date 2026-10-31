"""DDC transports. Never choose a display by its position in a previous scan."""
import ctypes
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UUID = r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}'


@dataclass
class Monitor:
    id: str
    name: str
    selector: str
    display_id: int = 0
    handle: int = 0
    current: int | None = None
    raw_current: int | None = None
    read_mode: str | None = None
    error: str = ''

    def public(self):
        result = asdict(self)
        result.pop('handle')
        return result


def decoded_source(monitor, raw, mode):
    monitor.raw_current = raw
    # MCCS input select is a simple non-continuous value in the low byte.
    # Some Dell firmware puts extra data in the high byte. Preserve that raw reply.
    value = raw & 0xFF if mode == 'standard' else raw
    if not 1 <= value <= 255:
        raise RuntimeError('No usable input reply. DDC/CI may be disabled or unsupported on this connection.')
    return value


def parse_m1ddc(output):
    monitors = []
    for line in output.splitlines():
        match = re.match(rf'^\[\d+\] (.*?) \(({UUID})\)\s*$', line)
        if match:
            name, identity = match.groups()
            monitors.append(Monitor(identity.upper(), name, identity.upper()))
        elif monitors and (match := re.match(r'\s*- Display ID:\s*(\d+)', line)):
            monitors[-1].display_id = int(match[1])
    return monitors


def parse_ddcctl(output):
    monitors = []
    for match in re.finditer(rf'CGDisplay ({UUID}) dispID\(#(\d+)\)', output):
        identity, display_id = match.groups()
        monitors.append(Monitor(identity.upper(), f'External display {len(monitors)+1}',
                                identity.upper(), int(display_id)))
    names = dict((identity.upper(), name) for identity, name in re.findall(
        rf'MonitorSwitch display ({UUID}) name: (.+)', output))
    for monitor in monitors:
        monitor.name = names.get(monitor.id, monitor.name)
    return monitors


def find_tool():
    name = 'm1ddc' if os.uname().machine == 'arm64' else 'ddcctl'
    override = os.environ.get('MONITOR_SWITCH_DDC_TOOL')
    candidates = [override] if override else [
        str(ROOT / '.tools' / name),
        str(Path(getattr(sys, '_MEIPASS', ROOT)) / 'tools' / name),
        shutil.which(name), f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}']
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(f'{name} not found. Run scripts/setup-mac.sh; see README.md.')


class MacBackend:
    def __init__(self, tool=None):
        self.tool = tool or find_tool()
        self.m1 = Path(self.tool).name == 'm1ddc'

    def run(self, args, allow_listing_exit=False):
        try:
            result = subprocess.run([self.tool, *args], capture_output=True, text=True,
                                    timeout=8, encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('DDC tool timed out. Input state is unknown; no automatic retry.') from exc
        output = (result.stdout + '\n' + result.stderr).strip()
        # ddcctl can return zero after a failed DDC write, and one for a valid list.
        failed = re.search(r'(^|\n)\s*E:', output) or re.search(
            r'communication failure|could not find|does not exist|invalid command', output, re.I)
        if failed or (result.returncode and not allow_listing_exit):
            raise RuntimeError(output[-1200:] or f'DDC tool exited {result.returncode}.')
        return output

    @contextmanager
    def snapshot(self):
        if self.m1:
            output = self.run(['display', 'list', 'detailed'], allow_listing_exit=True)
            monitors = parse_m1ddc(output)
            if monitors and 'MonitorSwitch reply validation: v1' not in output:
                raise RuntimeError('This m1ddc lacks validated DDC replies. Run scripts/setup-mac.sh '
                                   'to build the patched helper.')
            cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
            cg.CGDisplayIsBuiltin.argtypes = [ctypes.c_uint32]
            cg.CGDisplayIsBuiltin.restype = ctypes.c_uint32
            monitors = [m for m in monitors if not cg.CGDisplayIsBuiltin(m.display_id)]
        else:
            output = self.run([], allow_listing_exit=True)
            monitors = parse_ddcctl(output)
            if 'MonitorSwitch UUID support: v1' not in output:
                raise RuntimeError('This ddcctl lacks stable UUID selection. Run scripts/setup-mac.sh '
                                   'to build the patched helper; numeric selection is unsafe after disconnects.')
        if not monitors and output and 'No external display' not in output:
            raise RuntimeError('No usable external displays in DDC tool output:\n' + output[-800:])
        yield monitors

    def read_source(self, monitor, mode='standard'):
        if self.m1:
            output = self.run(['display', monitor.selector, 'get',
                               'input-alt' if mode == 'lg-alt' else 'input'])
            lines = re.findall(r'^\s*(\d+)\s*$', output, re.M)
            value = int(lines[-1]) if lines else 0
        else:
            if mode != 'standard':
                raise RuntimeError('LG alternate addressing requires m1ddc on Apple Silicon.')
            output = self.run(['-u', monitor.selector, '-i', '?'])
            match = re.search(r'I: VCP control #96 .*?current:\s*(\d+)', output)
            value = int(match[1]) if match else 0
        return decoded_source(monitor, value, mode)

    def set_source(self, monitor, code, mode):
        if self.m1:
            output = self.run(['display', monitor.selector, 'set',
                               'input-alt' if mode == 'lg-alt' else 'input', str(code)])
        else:
            if mode != 'standard':
                raise RuntimeError('LG alternate addressing requires m1ddc on Apple Silicon.')
            output = self.run(['-u', monitor.selector, '-i', str(code)])
        if re.search(r'(^|\n)\s*E:', output):
            raise RuntimeError(output[-1200:])


class WindowsNative:
    """Pointer-sized handles and explicit Win32 signatures, including on 64-bit Python."""
    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.dx = ctypes.WinDLL('Dxva2', use_last_error=True)

        class Physical(ctypes.Structure):
            _fields_ = [('handle', w.HANDLE), ('description', w.WCHAR * 128)]

        class Info(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('rcMonitor', w.RECT),
                        ('rcWork', w.RECT), ('dwFlags', w.DWORD), ('szDevice', w.WCHAR * 32)]

        class Device(ctypes.Structure):
            _fields_ = [('cb', w.DWORD), ('DeviceName', w.WCHAR * 32),
                        ('DeviceString', w.WCHAR * 128), ('StateFlags', w.DWORD),
                        ('DeviceID', w.WCHAR * 128), ('DeviceKey', w.WCHAR * 128)]

        self.Physical, self.Info, self.Device = Physical, Info, Device
        self.Callback = ctypes.WINFUNCTYPE(w.BOOL, w.HMONITOR, w.HDC,
                                          ctypes.POINTER(w.RECT), w.LPARAM)
        signatures = [
            (self.user.EnumDisplayMonitors, [w.HDC, ctypes.POINTER(w.RECT), self.Callback, w.LPARAM]),
            (self.user.GetMonitorInfoW, [w.HMONITOR, ctypes.POINTER(Info)]),
            (self.user.EnumDisplayDevicesW, [w.LPCWSTR, w.DWORD, ctypes.POINTER(Device), w.DWORD]),
            (self.dx.GetNumberOfPhysicalMonitorsFromHMONITOR, [w.HMONITOR, ctypes.POINTER(w.DWORD)]),
            (self.dx.GetPhysicalMonitorsFromHMONITOR, [w.HMONITOR, w.DWORD, ctypes.POINTER(Physical)]),
            (self.dx.DestroyPhysicalMonitors, [w.DWORD, ctypes.POINTER(Physical)]),
            (self.dx.GetVCPFeatureAndVCPFeatureReply, [w.HANDLE, w.BYTE, ctypes.POINTER(ctypes.c_int),
                                                    ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]),
            (self.dx.SetVCPFeature, [w.HANDLE, w.BYTE, w.DWORD]),
        ]
        for function, args in signatures:
            function.argtypes, function.restype = args, w.BOOL

    def check(self, ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def snapshot(self):
        logical = []

        @self.Callback
        def collect(handle, _hdc, _rect, _data):
            logical.append(handle)
            return True

        self.check(self.user.EnumDisplayMonitors(None, None, collect, 0))
        owned = []
        monitors = []
        try:
            for handle in logical:
                info = self.Info()
                info.cbSize = ctypes.sizeof(info)
                self.check(self.user.GetMonitorInfoW(handle, ctypes.byref(info)))
                device = self.Device()
                device.cb = ctypes.sizeof(device)
                # The interface path is specific to this monitor/connector on this host.
                found = self.user.EnumDisplayDevicesW(info.szDevice, 0, ctypes.byref(device), 1)
                identity = device.DeviceID if found else ''
                name = device.DeviceString if found else info.szDevice
                if not identity:
                    # A transient DISPLAY1 ordinal is never safe to persist as identity.
                    continue
                count = self.w.DWORD()
                if not self.dx.GetNumberOfPhysicalMonitorsFromHMONITOR(handle, ctypes.byref(count)):
                    monitors.append(Monitor(identity, name, '', error='Physical DDC handle unavailable.'))
                    continue
                if not count.value:
                    monitors.append(Monitor(identity, name, '', error='No physical DDC monitor.'))
                    continue
                array = (self.Physical * count.value)()
                if not self.dx.GetPhysicalMonitorsFromHMONITOR(handle, count, array):
                    monitors.append(Monitor(identity, name, '', error='Cannot acquire physical DDC handles.'))
                    continue
                owned.append((count.value, array))
                if count.value != 1:
                    # Mirrored/tiled sets need a stronger physical identity mapping.
                    monitors.append(Monitor(identity, name, '', error='Multiple physical displays share this logical display; use extended desktop.'))
                    continue
                physical = array[0]
                monitors.append(Monitor(identity, name or physical.description, identity,
                                        handle=physical.handle))
            yield monitors
        finally:
            for count, array in owned:
                self.dx.DestroyPhysicalMonitors(count, array)

    def read(self, handle):
        current, maximum, kind = self.w.DWORD(), self.w.DWORD(), ctypes.c_int()
        self.check(self.dx.GetVCPFeatureAndVCPFeatureReply(
            handle, 0x60, ctypes.byref(kind), ctypes.byref(current), ctypes.byref(maximum)))
        return current.value

    def write(self, handle, code):
        self.check(self.dx.SetVCPFeature(handle, 0x60, code))


class WindowsBackend:
    def __init__(self, native=None):
        self.native = native if native is not None else WindowsNative()

    def snapshot(self):
        return self.native.snapshot()

    def check_monitor(self, monitor, mode):
        if mode != 'standard':
            raise RuntimeError('LG alternate DDC addressing is unavailable through Dxva2. '
                               'Use standard 0x60 if supported, or the monitor OSD.')
        if not monitor.handle:
            raise RuntimeError(monitor.error or 'No physical DDC handle available.')

    def read_source(self, monitor, mode='standard'):
        self.check_monitor(monitor, mode)
        value = self.native.read(monitor.handle)
        return decoded_source(monitor, value, mode)

    def set_source(self, monitor, code, mode):
        self.check_monitor(monitor, mode)
        self.native.write(monitor.handle, code)


def backend():
    if sys.platform == 'darwin':
        return MacBackend()
    if sys.platform == 'win32':
        return WindowsBackend()
    raise RuntimeError('Supported platforms: macOS and Windows.')
