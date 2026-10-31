import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from unittest.mock import patch
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from monitor_switch.gui import Window
from monitor_switch.tray import Resident
from monitor_switch.ui import Shell, apply_theme


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        self.window=Window(demo=True,auto_scan=False)
        self.resident=Resident(self.window)

    def tearDown(self):
        self.resident.panel.hide();self.resident.icon.hide()
        self.window.job=None;self.window.tray_managed=False;self.window.close()
        self.window.deleteLater();self.app.processEvents()

    def test_navigation_changes_pages_through_accessible_tab_selection(self):
        shell=self.window.shell
        self.assertEqual(shell.pages.currentIndex(),0)
        shell.navigation.setCurrentIndex(1)
        self.assertEqual(shell.pages.currentWidget(),shell.monitor_page)
        shell.navigation.setCurrentIndex(2)
        self.assertEqual(shell.pages.currentWidget(),shell.activity_page)
        shell.set_page(0)
        self.assertEqual(shell.navigation.currentIndex(),0)

    def test_quick_panel_uses_same_routes_and_honors_unsaved_and_busy_guards(self):
        window,panel=self.window,self.resident.panel
        with patch.object(window,'start') as start:
            panel.mac.click()
            self.assertEqual(start.call_args.args[0]['target'],'mac')
            window.changed()
            self.assertFalse(panel.mac.isEnabled())
            panel.windows.click()
            self.assertEqual(start.call_count,1)
            self.assertTrue(panel.feedback.isHidden() is False)
            self.assertIn('저장',panel.feedback.text())
            window.dirty=False;window.job=object();window.refresh()
            self.assertFalse(panel.windows.isEnabled())
            panel.mac.click()
            self.assertEqual(start.call_count,1)

    def test_panel_opens_from_toolbar_and_escape_keeps_resident_alive(self):
        self.window.shell.quick_button.click()
        panel=self.resident.panel
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.surface.width(),320)
        self.assertEqual(panel.width(),356)
        self.assertLess(panel.height(),440)
        QApplication.sendEvent(panel,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier))
        self.assertFalse(panel.isVisible())
        self.assertTrue(self.resident.icon.isVisible())
        self.assertFalse(self.window.closing)

    def test_tray_opening_press_cannot_dismiss_the_panel_it_opens(self):
        resident=self.resident
        # Cocoa emits activated while handling the status button's mouse-down.
        resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        popup=QApplication.activePopupWidget()
        if popup is not None:
            press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(20,-12),
                QPointF(20,-12),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(popup,press)
        self.app.processEvents()
        self.assertTrue(resident.panel.isVisible(),
            'The status-button press must finish before its popup opens.')

    def test_mac_repeated_status_activation_keeps_the_open_panel_visible(self):
        resident=self.resident
        with patch('monitor_switch.tray.sys.platform','darwin'):
            for reason in (QSystemTrayIcon.ActivationReason.Unknown,
                           QSystemTrayIcon.ActivationReason.Trigger,
                           QSystemTrayIcon.ActivationReason.Trigger,
                           QSystemTrayIcon.ActivationReason.DoubleClick):
                resident.icon.activated.emit(reason)
                self.app.processEvents()
                self.assertTrue(resident.panel.isVisible(),
                    'Repeated status activation must not toggle the panel shut.')

    def test_mac_first_click_immediately_after_dismissal_opens_panel(self):
        import time
        self.resident.panel.last_hidden=time.monotonic()
        with patch('monitor_switch.tray.sys.platform','darwin'):
            self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
            self.app.processEvents()
            self.assertTrue(self.resident.panel.isVisible())

    def test_windows_unknown_activation_does_not_open_panel(self):
        with patch('monitor_switch.tray.sys.platform','win32'):
            self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Unknown)
            self.app.processEvents()
            self.assertFalse(self.resident.panel.isVisible())

    def test_mac_right_click_opens_quick_panel_without_a_context_menu(self):
        with patch('monitor_switch.tray.sys.platform','darwin'), \
                patch.object(self.resident.menu,'popup') as context_menu:
            for reason in (QSystemTrayIcon.ActivationReason.Context,
                           QSystemTrayIcon.ActivationReason.Trigger,
                           QSystemTrayIcon.ActivationReason.Context):
                self.resident.icon.activated.emit(reason)
                self.app.processEvents()
                self.assertTrue(self.resident.panel.isVisible())
            context_menu.assert_not_called()

    def test_mac_status_click_hides_settings_and_only_explicit_gear_reopens_it(self):
        with patch('monitor_switch.tray.sys.platform','darwin'):
            self.resident.show_window()
            self.assertTrue(self.window.isVisible())
            self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Unknown)
            self.app.processEvents()
            self.assertTrue(self.resident.panel.isVisible())
            self.assertFalse(self.window.isVisible())
            self.resident.panel.settings.click()
            self.assertFalse(self.resident.panel.isVisible())
            self.assertTrue(self.window.isVisible())

    def test_mac_reopen_uses_panel_and_windows_reopen_uses_settings(self):
        with patch('monitor_switch.tray.sys.platform','darwin'):
            self.resident.show_default();self.app.processEvents()
            self.assertTrue(self.resident.panel.isVisible())
            self.assertFalse(self.window.isVisible())
        with patch('monitor_switch.tray.sys.platform','win32'):
            self.resident.show_default();self.app.processEvents()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.resident.panel.isVisible())

    def test_windows_left_click_opens_panel_and_right_click_opens_context_menu(self):
        with patch('monitor_switch.tray.sys.platform','win32'), \
                patch.object(self.resident.menu,'popup') as context_menu:
            self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
            self.app.processEvents()
            self.assertTrue(self.resident.panel.isVisible())
            self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)
            self.app.processEvents()
            self.assertFalse(self.resident.panel.isVisible())
            context_menu.assert_called_once()

    def test_open_panel_still_dismisses_on_a_later_outside_press(self):
        self.resident.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.app.processEvents()
        panel=self.resident.panel
        self.assertTrue(panel.isVisible())
        press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(20,-12),
            QPointF(20,-12),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(panel,press)
        self.assertFalse(panel.isVisible())

    def test_concise_menu_and_status_do_not_assert_a_current_input(self):
        actions=[action.text() for action in self.resident.menu.actions()]
        self.assertIn('Mac으로 전환',actions)
        self.assertIn('Windows로 전환',actions)
        self.assertIn('Monitor Switch 종료',actions)
        self.assertFalse(any('262A' in value or '10E3' in value for value in actions))
        self.window.shell.update_usb('HiFime · ambiguous',True)
        self.assertIn('보류',self.window.shell.auto_help.text())
        self.window.shell.update_usb('HiFime · error',True)
        self.assertIn('확인 필요',self.window.shell.auto_help.text())
        self.assertNotIn('현재 입력: Mac',self.resident.icon.toolTip())

    def test_bundled_assets_work_without_network(self):
        from monitor_switch.ui import ASSETS,icon
        self.assertTrue((ASSETS/'fonts/NotoSansKR.ttf').is_file())
        self.assertFalse(icon('monitor').isNull())
        self.assertTrue((ASSETS.parent/'licenses/lucide-LICENSE.txt').is_file())

    def test_login_refresh_never_rewrites_os_registration_but_user_toggle_does(self):
        class Startup:
            value=True
            writes=[]
            def enabled(self):return self.value
            def set_enabled(self,value):self.value=value;self.writes.append(value)
        startup=Startup();self.resident.startup=startup
        self.resident.refresh()
        self.assertTrue(self.window.shell.login.isChecked())
        self.assertEqual(startup.writes,[])
        self.window.shell.login.setChecked(False)
        self.assertEqual(startup.writes,[False])
        self.assertFalse(self.resident.login.isChecked())

    def test_last_request_selection_is_unknown_at_startup_and_clears_on_partial_failure(self):
        shell=self.window.shell
        self.assertIsNone(shell.last_target)
        self.assertFalse(shell.windows.selected)
        shell.record_outcome('windows',[{'status':'sent'},{'status':'sent'}]);self.resident.refresh()
        self.assertTrue(shell.windows.selected)
        self.assertTrue(self.resident.panel.windows.selected)
        self.assertEqual(self.resident.status_action.text(),'마지막 요청: Windows')
        shell.record_outcome('mac',[{'status':'skipped'}]);self.resident.refresh()
        self.assertEqual(shell.last_target,'windows')
        shell.record_outcome('mac',[{'status':'sent'},{'status':'uncertain'}]);self.resident.refresh()
        self.assertIsNone(shell.last_target)
        self.assertFalse(self.resident.panel.windows.selected)
        self.assertIn('확인 필요',self.resident.status_action.text())

    def test_display_picker_changes_only_the_edited_profile(self):
        window=self.window
        with patch.object(window,'start') as start:
            window.shell.picker.buttons[1].click()
            self.assertEqual(window.shell.monitor_tabs.currentIndex(),1)
            self.assertFalse(window.dirty)
            self.assertIsNone(window.shell.last_target)
            start.assert_not_called()

    def test_visible_shortcuts_are_scoped_to_the_application_and_use_existing_guards(self):
        for action in (self.resident.mac,self.resident.windows,self.resident.settings_action):
            self.assertFalse(action.shortcut().isEmpty())
            self.assertEqual(action.shortcutContext(),Qt.ShortcutContext.ApplicationShortcut)
        with patch.object(self.window,'start') as start:
            self.window.changed();self.resident.mac.trigger();start.assert_not_called()
