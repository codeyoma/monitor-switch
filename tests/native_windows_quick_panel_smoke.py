"""Inspect a real Windows popup without starting the relay or touching monitors."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

if sys.platform != 'win32':
    raise SystemExit('Run this check on Windows.')
os.environ['QT_QPA_PLATFORM'] = 'windows'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtWidgets import QApplication
from monitor_switch.ui import QuickPanel, apply_theme

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
apply_theme(app)
panel = QuickPanel('windows')
user32 = ctypes.WinDLL('user32', use_last_error=True)
get_class_style = user32.GetClassLongW
get_class_style.argtypes = [wintypes.HWND, ctypes.c_int]
get_class_style.restype = wintypes.DWORD
observations = []


def class_style(widget):
    ctypes.set_last_error(0)
    value = get_class_style(int(widget.winId()), -26)
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


# Characterize the original popup on this Qt build, so the native assertion
# cannot pass merely because the runner never adds shadows in the first place.
reference = QuickPanel('windows')
reference.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, False)
assert class_style(reference) & 0x00020000, 'The original popup must have CS_DROPSHADOW.'
reference.close()
reference.deleteLater()


def check():
    try:
        style = class_style(panel)
        assert panel.isVisible(), 'The quick panel must open.'
        assert not style & 0x00020000, 'CS_DROPSHADOW adds a rectangular system shadow.'
        assert panel.surface.graphicsEffect() is not None, 'Keep the rounded app shadow.'
        observations.append(dict(class_style=hex(style), system_shadow=False))
        panel.hide()
        if len(observations) < 3:
            QTimer.singleShot(50, reopen)
        else:
            print(json.dumps(dict(passed=True, openings=observations, monitor_commands_sent=0)))
            app.exit(0)
    except BaseException:
        import traceback
        traceback.print_exc()
        app.exit(1)


def reopen():
    try:
        area = app.primaryScreen().availableGeometry()
        panel.show_at(QRect(area.center().x(), area.bottom()-40, 20, 20))
        QTimer.singleShot(100, check)
    except BaseException:
        import traceback
        traceback.print_exc()
        app.exit(1)


QTimer.singleShot(0, reopen)
QTimer.singleShot(10000, lambda: app.exit(2))
raise SystemExit(app.exec())
