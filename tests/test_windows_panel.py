"""Verify the translucent panel owns its shadow, including the native HWND."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from monitor_switch.ui import apply_theme
from monitor_switch.quick_panel import QuickPanel


class WindowsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def test_windows_translucent_popup_disables_the_extra_system_shadow(self):
        with patch('monitor_switch.quick_panel.sys.platform', 'win32'):
            panel = QuickPanel('windows')
        try:
            self.assertEqual(panel.windowType(), Qt.WindowType.Popup)
            self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(panel.windowFlags() & Qt.WindowType.NoDropShadowWindowHint,
                            'The system shadow must not outline the transparent window rectangle.')
            self.assertIsNotNone(panel.surface.graphicsEffect(), 'Keep the rounded app shadow.')
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()

    @unittest.skipUnless(sys.platform == 'win32', 'Requires the Windows window manager.')
    def test_native_windows_panel_has_no_system_shadow_after_reopening(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root/'tests/native_windows_quick_panel_smoke.py')],
                                cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='windows'),
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
