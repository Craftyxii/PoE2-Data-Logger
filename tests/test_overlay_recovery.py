import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class OverlayRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-overlay-recovery-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.manager = HotkeyManager(supported=True)
        self.manager.focused = None

        def listen(shortcuts, ready, outcome, stop):
            outcome["thread_id"] = 17
            ready.set()
            stop.wait(30)

        self.manager._message_loop = listen
        self.native_patch = patch("ctypes.WinDLL", create=True, return_value=Mock())
        self.native_patch.start()
        self.manager_patch = patch.object(service, "HOTKEY", self.manager)
        self.manager_patch.start()
        self.manager.configure("F8")
        self.manager.configure_for("overlay", "Ctrl+Shift+H")
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.window.set_overlay_enabled(True)
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.manager_patch.stop()
        self.native_patch.stop()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def escape(self):
        self.window.activateWindow()
        self.app.processEvents()
        QTest.keyClick(self.window, Qt.Key.Key_Escape)
        self.app.processEvents()

    def assert_taskbar_recovery(self):
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.app.processEvents()
        self.assertEqual(ticks, [True])
        self.window.showNormal()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_escape_hides_overlay_with_registered_hud_key_and_key_restores_it(self):
        self.escape()
        self.assertFalse(self.window.isVisible())
        self.manager.capture("overlay")
        self.window.poll()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_escape_in_scan_regions_retains_taskbar_recovery(self):
        self.window.tabs.setCurrentIndex(7)
        self.assertTrue(self.window._editing_regions)
        self.assertFalse(self.manager.status()["registered"])
        self.escape()
        self.assert_taskbar_recovery()
        self.window.tabs.setCurrentIndex(6)
        self.assertTrue(self.manager.status()["registered"])

    def test_cleared_hud_key_retains_taskbar_recovery(self):
        self.window.clear_hotkey("overlay")
        self.assertTrue(self.manager.status()["registered"])
        self.assertEqual(self.manager.status()["combos"]["overlay"], "")
        self.escape()
        self.assert_taskbar_recovery()

    def test_dead_listener_retains_taskbar_recovery(self):
        self.manager._unregister()
        self.escape()
        self.assert_taskbar_recovery()

    def test_listener_stopping_after_hiding_restores_taskbar_recovery(self):
        self.window.hide_overlay()
        self.assertFalse(self.window.isVisible())
        self.manager._unregister()
        self.window.poll()
        self.assert_taskbar_recovery()

    def test_hud_key_lost_after_hiding_restores_taskbar_recovery(self):
        self.window.hide_overlay()
        self.assertFalse(self.window.isVisible())
        self.manager.configure_for("overlay", "")
        self.window.poll()
        self.assert_taskbar_recovery()

    def test_hiding_while_assigning_a_key_resumes_hud_shortcut(self):
        self.window.tabs.setCurrentIndex(6)
        self.window.arm_hotkey("waystone")
        self.assertFalse(self.manager.status()["registered"])
        self.window.hide_overlay()
        self.assertFalse(self.window._capturing_hotkey)
        self.assertTrue(self.manager.status()["registered"])
        self.assertFalse(self.window.isVisible())
        self.manager.capture("overlay")
        self.window.poll()
        self.assertTrue(self.window.isVisible())

    def test_disabling_overlay_restores_a_hidden_main_window(self):
        self.window.hide_overlay()
        self.assertFalse(self.window.isVisible())
        self.window.set_overlay_enabled(False)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertFalse(self.window.overlay_escape.isEnabled())

    def test_escape_does_not_hide_when_overlay_is_disabled(self):
        self.window.set_overlay_enabled(False)
        self.escape()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())


if __name__ == "__main__":
    unittest.main()
