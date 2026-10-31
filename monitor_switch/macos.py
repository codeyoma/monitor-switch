"""Small AppKit activation bridge for the app's own menu-bar panel."""
import ctypes
from functools import lru_cache


@lru_cache(maxsize=1)
def _activation_bridge():
    runtime=ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    runtime.objc_getClass.argtypes=[ctypes.c_char_p]
    runtime.objc_getClass.restype=ctypes.c_void_p
    runtime.sel_registerName.argtypes=[ctypes.c_char_p]
    runtime.sel_registerName.restype=ctypes.c_void_p
    send_pointer=ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)(('objc_msgSend',runtime))
    send_bool=ctypes.CFUNCTYPE(None,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_bool)(('objc_msgSend',runtime))
    application=send_pointer(runtime.objc_getClass(b'NSApplication'),runtime.sel_registerName(b'sharedApplication'))
    if not application:raise RuntimeError('AppKit application is unavailable.')
    return runtime,application,send_bool,runtime.sel_registerName(b'activateIgnoringOtherApps:')


def activate_application():
    """Activate this app; Qt's Cocoa activateWindow only makes its window key."""
    try:
        _runtime,application,send,selector=_activation_bridge()
        send(application,selector,True)
        return True
    except (OSError,AttributeError,RuntimeError):
        return False
