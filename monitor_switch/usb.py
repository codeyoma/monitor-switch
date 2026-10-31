"""Observe arrival of the user's HiFime USB audio device, without routing audio."""
import ctypes
import plistlib
import re
import subprocess
import sys

VID, PID = 0x262A, 0x10E3
DEVICE_LABEL = 'HiFime SA9023 USB Audio · 262A:10E3'


class AutomaticRequests:
    """Called while the switch lock is held; remember attempted automatic targets.

    Deduplicate same-host re-enumeration even after an uncertain native result.
    An opposite-host arrival or an explicit manual action allows the next change.
    """
    def __init__(self):
        self.last = None

    def allow(self, target, identities, automatic):
        if not automatic:
            self.last = None
            return True
        key = (target, tuple(sorted(identities)))
        if self.last == key:
            return False
        self.last = key  # Record before any possibly delivered write.
        return True


def mac_count():
    process = subprocess.run(['/usr/sbin/ioreg', '-p', 'IOUSB', '-l', '-w', '0', '-a'],
                             capture_output=True, timeout=3)
    if process.returncode:
        raise RuntimeError('macOS USB inventory failed; previous USB state retained.')
    roots = plistlib.loads(process.stdout)
    if isinstance(roots, dict):
        roots = [roots]
    if not isinstance(roots, list):
        raise RuntimeError('Invalid macOS USB inventory; previous state retained.')
    matches = set()

    def walk(nodes):
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise RuntimeError('Invalid macOS USB node; previous state retained.')
            if node.get('idVendor') == VID and node.get('idProduct') == PID:
                matches.add(node.get('IORegistryEntryID', id(node)))
            walk(node.get('IORegistryEntryChildren', []))

    walk(roots)
    return len(matches)


def windows_count():
    from ctypes import wintypes as w
    api = ctypes.WinDLL('setupapi', use_last_error=True)

    class GUID(ctypes.Structure):
        _fields_ = [('data', ctypes.c_ubyte * 16)]

    class DeviceInfo(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('guid', GUID), ('instance', w.DWORD), ('reserved', ctypes.c_size_t)]

    api.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
    api.SetupDiGetClassDevsW.restype = w.HANDLE
    api.SetupDiEnumDeviceInfo.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(DeviceInfo)]
    api.SetupDiEnumDeviceInfo.restype = w.BOOL
    api.SetupDiGetDeviceInstanceIdW.argtypes = [w.HANDLE, ctypes.POINTER(DeviceInfo), w.LPWSTR,
                                               w.DWORD, ctypes.POINTER(w.DWORD)]
    api.SetupDiGetDeviceInstanceIdW.restype = w.BOOL
    api.SetupDiDestroyDeviceInfoList.argtypes = [w.HANDLE]
    api.SetupDiDestroyDeviceInfoList.restype = w.BOOL
    handle = api.SetupDiGetClassDevsW(None, 'USB', None, 0x02 | 0x04)  # PRESENT | ALLCLASSES
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    matches = set()
    try:
        for index in range(4096):
            info = DeviceInfo()
            info.size = ctypes.sizeof(info)
            if not api.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
                if ctypes.get_last_error() == 259:  # NO_MORE_ITEMS
                    return len(matches)
                raise ctypes.WinError(ctypes.get_last_error())
            text, needed = ctypes.create_unicode_buffer(4096), w.DWORD()
            if not api.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), text, len(text), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Match physical USB parents; audio interfaces with &MI_00 are not
            # separate devices. No location ID is used as a permanent identity.
            identity = text.value.upper()
            if re.match(r'^USB\\VID_262A&PID_10E3(?:&REV_[0-9A-F]{4})?\\', identity):
                matches.add(identity)
        raise RuntimeError('USB inventory exceeded its device limit; previous state retained.')
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)


def count_device():
    if sys.platform == 'darwin':
        return mac_count()
    if sys.platform == 'win32':
        return windows_count()
    raise RuntimeError('USB tracking is supported on macOS and Windows only.')
