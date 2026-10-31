"""Run explicitly on macOS: verify real Cocoa windows, without monitor writes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if sys.platform!='darwin':raise SystemExit('Run this check on macOS.')
os.environ['QT_QPA_PLATFORM']='cocoa'

from PySide6.QtCore import QEvent,QTimer,Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication,QSystemTrayIcon,QWidget
from monitor_switch.gui import Window
from monitor_switch.tray import Resident
from monitor_switch.ui import apply_theme

app=QApplication([]);app.setQuitOnLastWindowClosed(False);apply_theme(app)
window=Window(demo=True,auto_scan=False);resident=Resident(window)
observations=[]
focus_probe=QWidget();focus_probe.setWindowTitle('Native panel focus check')


def check(stage,panel,main):
    value=dict(stage=stage,panel_visible=resident.panel.isVisible(),main_visible=window.isVisible(),
               application_state=app.applicationState().name,panel_geometry=resident.panel.geometry().getRect())
    observations.append(value);print(json.dumps(value),flush=True)
    assert value['panel_visible']==panel and value['main_visible']==main,stage
    if panel:
        assert value['application_state']=='ApplicationActive',stage+' must accept keyboard focus'
        screen=app.screenAt(resident.panel.frameGeometry().center())
        assert screen and screen.availableGeometry().contains(resident.panel.frameGeometry()),stage+' must stay on screen'


def guarded(action):
    try:action()
    except BaseException:
        import traceback
        traceback.print_exc();finish(1)


def first_click():
    window.hide()
    resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Unknown)
    QTimer.singleShot(300,lambda:guarded(after_first_click))


def after_first_click():
    check('background_status_click',True,False)
    QApplication.sendEvent(resident.panel,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier))
    check('escape',False,False)
    resident.show_window()
    resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    QTimer.singleShot(300,lambda:guarded(after_right_click))


def after_right_click():
    check('status_click_with_settings_previously_open',True,False)
    focus_probe.show();focus_probe.raise_();focus_probe.activateWindow()
    QTimer.singleShot(200,lambda:guarded(after_focus_change))


def after_focus_change():
    check('focus_moved_outside_panel',False,False)
    focus_probe.hide()
    resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    QTimer.singleShot(200,lambda:guarded(open_explicit_settings))


def open_explicit_settings():
    check('reopen_after_outside_focus',True,False)
    resident.panel.settings.click()
    check('explicit_settings_button',False,True)
    resident.show_default()
    QTimer.singleShot(200,lambda:guarded(after_reopen))


def after_reopen():
    check('application_reopen',True,False)
    finish(0)


def finish(code):
    focus_probe.close();resident.icon.hide();resident.panel.hide();window.tray_managed=False;window.close()
    print(json.dumps(dict(passed=code==0,monitor_commands_sent=0)),flush=True)
    app.exit(code)


QTimer.singleShot(300,lambda:guarded(first_click))
raise SystemExit(app.exec())
