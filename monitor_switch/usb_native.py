"""Native USB arrival/removal subscriptions. Callbacks only wake the observer."""
import ctypes as C
import re
import sys
import threading
import uuid

from .usb import VID, PID, windows_count

P, U32, I32 = C.c_void_p, C.c_uint32, C.c_int32
USB_DEVICE_GUID = uuid.UUID('A5DCBF10-6530-11D2-901F-00C04FB951ED').bytes_le
_UNRELEASED = []  # Keep callbacks alive if an OS unregister operation fails.


def target_usb_link(link):
    link = link.upper()
    for prefix in ('\\\\?\\', '\\??\\'):
        if link.startswith(prefix):
            link = link[len(prefix):]
            break
    return bool(re.match(r'^USB#VID_262A&PID_10E3(?:&REV_[0-9A-F]{4})?#', link))


def notification_link(raw):
    if (not 26 <= len(raw) <= 65536 or int.from_bytes(raw[:4],'little') != 0
            or raw[8:24] != USB_DEVICE_GUID):
        return None
    # Stop at the aligned UTF-16 terminator before decoding any structure padding.
    for end in range(24,len(raw)-1,2):
        if raw[end:end+2] == b'\0\0':
            try:
                return raw[24:end].decode('utf-16-le')
            except UnicodeDecodeError:
                return None
    return None


class CMFilterUnion(C.Union):
    _fields_ = [('guid', C.c_ubyte * 16), ('handle', P), ('instance', C.c_uint16 * 200)]


class CMFilter(C.Structure):
    _fields_ = [('size', U32), ('flags', U32), ('kind', I32), ('reserved', U32), ('data', CMFilterUnion)]


class WindowsUSBEvents:
    def __init__(self, changed, failed):
        if sys.platform != 'win32':
            raise RuntimeError('Windows USB notifications require Windows.')
        self.changed, self.failed, self.handle = changed, failed, P()
        self.api = C.WinDLL('cfgmgr32', use_last_error=True)
        callback_type = C.WINFUNCTYPE(U32, P, P, I32, P, U32)

        @callback_type
        def callback(_handle, _context, action, data, size):
            try:
                if action not in (0,1) or not data or not 26 <= size <= 65536:
                    return 0
                raw = C.string_at(data, size)
                link = notification_link(raw)
                if link is not None and target_usb_link(link):
                    self.changed()
            except Exception as exc:
                self.failed('Windows USB notification error: ' + str(exc))
            return 0

        self.callback = callback  # Must outlive every native callback.
        self.api.CM_Register_Notification.argtypes = [C.POINTER(CMFilter), P, callback_type, C.POINTER(P)]
        self.api.CM_Register_Notification.restype = U32
        self.api.CM_Unregister_Notification.argtypes = [P]
        self.api.CM_Unregister_Notification.restype = U32

    def start(self):
        filters = CMFilter()
        filters.size = C.sizeof(filters)
        filters.kind = 0  # CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE
        filters.data.guid[:] = USB_DEVICE_GUID
        status = self.api.CM_Register_Notification(C.byref(filters), None, self.callback, C.byref(self.handle))
        if status:
            raise RuntimeError(f'CM_Register_Notification failed (0x{status:08X}).')

    def count(self):
        return windows_count()

    def stop(self):
        if self.handle.value:
            # Called by the observer thread, never from inside the OS callback.
            status = self.api.CM_Unregister_Notification(self.handle)
            if status:
                _UNRELEASED.append(self)
                raise RuntimeError(f'CM_Unregister_Notification failed (0x{status:08X}).')
            self.handle = P()


class MacUSBAPI:
    def __init__(self):
        if sys.platform != 'darwin':
            raise RuntimeError('IOKit USB notifications require macOS.')
        self.io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.callback_type = C.CFUNCTYPE(None, P, U32)
        self.entry_type = C.CFUNCTYPE(None, P, C.c_ulong, P)
        signatures = [
            (self.io.IOServiceMatching, [C.c_char_p], P),
            (self.io.IOServiceGetMatchingServices, [U32,P,C.POINTER(U32)], I32),
            (self.io.IOIteratorNext, [U32], U32), (self.io.IOObjectRelease,[U32],I32),
            (self.io.IONotificationPortCreate,[U32],P),
            (self.io.IONotificationPortDestroy,[P],None),
            (self.io.IONotificationPortGetRunLoopSource,[P],P),
            (self.io.IOServiceAddMatchingNotification,[P,C.c_char_p,P,self.callback_type,P,C.POINTER(U32)],I32),
            (self.cf.CFStringCreateWithCString,[P,C.c_char_p,U32],P),
            (self.cf.CFNumberCreate,[P,C.c_long,P],P),
            (self.cf.CFDictionaryCreateMutable,[P,C.c_long,P,P],P),
            (self.cf.CFDictionarySetValue,[P,P,P],None),
            (self.cf.CFRelease,[P],None), (self.cf.CFRetain,[P],P),
            (self.cf.CFRunLoopGetCurrent,[],P), (self.cf.CFRunLoopRun,[],None),
            (self.cf.CFRunLoopStop,[P],None), (self.cf.CFRunLoopWakeUp,[P],None),
            (self.cf.CFRunLoopAddSource,[P,P,P],None), (self.cf.CFRunLoopRemoveSource,[P,P,P],None),
            (self.cf.CFRunLoopObserverCreate,[P,C.c_ulong,C.c_ubyte,C.c_long,self.entry_type,P],P),
            (self.cf.CFRunLoopAddObserver,[P,P,P],None), (self.cf.CFRunLoopRemoveObserver,[P,P,P],None),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result
        self.key_callbacks = C.addressof(C.c_byte.in_dll(self.cf,'kCFTypeDictionaryKeyCallBacks'))
        self.value_callbacks = C.addressof(C.c_byte.in_dll(self.cf,'kCFTypeDictionaryValueCallBacks'))
        self.mode = P.in_dll(self.cf,'kCFRunLoopDefaultMode').value

    def string(self, value):
        result = self.cf.CFStringCreateWithCString(None,value.encode(),0x08000100)
        if not result:
            raise RuntimeError('Cannot allocate USB matching string.')
        return result

    def matching(self):
        matching = self.io.IOServiceMatching(b'IOUSBHostDevice')
        if not matching:
            raise RuntimeError('Cannot create USB device matching dictionary.')
        values = []
        try:
            properties = self.cf.CFDictionaryCreateMutable(None,0,self.key_callbacks,self.value_callbacks)
            if not properties:
                raise RuntimeError('Cannot allocate USB matching properties.')
            values.append(properties)
            for name, value in [('idVendor',VID),('idProduct',PID)]:
                key = self.string(name)
                values.append(key)
                integer = I32(value)
                number = self.cf.CFNumberCreate(None,3,C.byref(integer))  # SInt32
                if not number:
                    raise RuntimeError('Cannot allocate USB matching number.')
                values.append(number)
                self.cf.CFDictionarySetValue(properties,key,number)
            key = self.string('IOPropertyMatch')
            values.append(key)
            self.cf.CFDictionarySetValue(matching,key,properties)
            return matching  # IOService* consumes this owned dictionary.
        except Exception:
            self.cf.CFRelease(matching)
            raise
        finally:
            for value in reversed(values):
                self.cf.CFRelease(value)

    def drain(self, iterator):
        for count in range(4096):
            device = self.io.IOIteratorNext(iterator)
            if not device:
                return count
            self.io.IOObjectRelease(device)
        raise RuntimeError('USB iterator exceeded its limit.')

    def count(self):
        iterator = U32()
        status = self.io.IOServiceGetMatchingServices(0,self.matching(),C.byref(iterator))
        if status:
            raise RuntimeError(f'IOKit USB query failed (0x{status & 0xFFFFFFFF:08X}).')
        try:
            return self.drain(iterator.value)
        finally:
            if iterator.value:
                self.io.IOObjectRelease(iterator.value)


class MacUSBEvents:
    def __init__(self, changed, failed):
        self.changed, self.failed = changed, failed
        self.api = MacUSBAPI()
        self.ready, self.stopping = threading.Event(), threading.Event()
        self.lock, self.loop, self.error, self.thread = threading.Lock(), None, None, None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='USB IOKit notifications', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.stop()
            raise RuntimeError('IOKit notification registration timed out.')
        if self.error:
            raise RuntimeError(self.error)

    def _run(self):
        api, port, source, observer, loop, iterators = self.api, None, None, None, None, []
        try:
            loop = api.cf.CFRetain(api.cf.CFRunLoopGetCurrent())
            with self.lock:
                self.loop = loop
            port = api.io.IONotificationPortCreate(0)
            if not port:
                raise RuntimeError('Cannot create IOKit USB notification port.')
            source = api.io.IONotificationPortGetRunLoopSource(port)
            if not source:
                raise RuntimeError('Cannot obtain the IOKit USB run-loop source.')
            api.cf.CFRunLoopAddSource(loop,source,api.mode)

            @api.callback_type
            def callback(_context, iterator):
                try:
                    if api.drain(iterator):
                        self.changed()
                except Exception as exc:
                    self.failed(str(exc))

            self.callback = callback
            for kind in (b'IOServiceFirstMatch',b'IOServiceTerminate'):
                iterator = U32()
                status = api.io.IOServiceAddMatchingNotification(port,kind,api.matching(),callback,None,C.byref(iterator))
                if status:
                    raise RuntimeError(f'IOKit USB subscription failed (0x{status & 0xFFFFFFFF:08X}).')
                iterators.append(iterator.value)
                api.drain(iterator.value)  # Arm; existing devices are baseline, not arrivals.

            @api.entry_type
            def entered(_observer, _activity, _context):
                self.ready.set()  # The run loop is actually running before stop() can return.
                if self.stopping.is_set():
                    api.cf.CFRunLoopStop(loop)

            self.entry_callback = entered
            observer = api.cf.CFRunLoopObserverCreate(None,1,0,0,entered,None)
            if not observer:
                raise RuntimeError('Cannot create USB run-loop entry observer.')
            api.cf.CFRunLoopAddObserver(loop,observer,api.mode)
            api.cf.CFRunLoopRun()  # Blocks in the OS; no recurring timeout.
            if not self.stopping.is_set():
                self.failed('IOKit USB notification run loop ended unexpectedly.')
        except Exception as exc:
            self.error = str(exc)
            self.failed(self.error)
        finally:
            with self.lock:
                self.loop = None
            for iterator in iterators:
                api.io.IOObjectRelease(iterator)
            if observer:
                api.cf.CFRunLoopRemoveObserver(loop,observer,api.mode)
                api.cf.CFRelease(observer)
            if source:
                api.cf.CFRunLoopRemoveSource(loop,source,api.mode)
            if port:
                api.io.IONotificationPortDestroy(port)
            if loop:
                api.cf.CFRelease(loop)
            self.ready.set()

    def count(self):
        return self.api.count()

    def stop(self):
        self.stopping.set()
        with self.lock:
            loop = self.api.cf.CFRetain(self.loop) if self.loop else None
        if loop:
            self.api.cf.CFRunLoopStop(loop)
            self.api.cf.CFRunLoopWakeUp(loop)
            self.api.cf.CFRelease(loop)
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                _UNRELEASED.append(self)
                raise RuntimeError('IOKit USB notification thread has not stopped.')
