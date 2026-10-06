import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class HotkeyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-hotkey-ui-")
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
        self.manager.configure_for("waystone", "F9")
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.tabs.setCurrentIndex(6)

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.manager_patch.stop()
        self.native_patch.stop()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def press(self, key, modifiers=Qt.KeyboardModifier.NoModifier):
        self.app.sendEvent(self.window, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))

    def saved_keys(self):
        with logger._connect() as db:
            return logger._meta(db, "ocr_shortcut", {})

    def assert_original_active(self):
        status = self.manager.status()
        self.assertEqual(status["combo"], "F8")
        self.assertEqual(status["combos"]["waystone"], "F9")
        self.assertTrue(status["registered"])
        self.assertEqual(self.saved_keys()["combo"], "F8")
        self.assertFalse(self.window._capturing_hotkey)

    def test_reserved_key_restores_existing_shortcuts_and_keeps_error_visible(self):
        self.window.arm_hotkey()
        self.press(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assert_original_active()
        self.assertIn("item copy key", self.window.statusBar().currentMessage())

    def test_duplicate_key_restores_existing_shortcuts_and_keeps_error_visible(self):
        self.window.arm_hotkey()
        self.press(Qt.Key.Key_F9)
        self.assert_original_active()
        self.assertIn("different key combination", self.window.statusBar().currentMessage())

    def test_unsupported_key_restores_existing_shortcuts(self):
        self.window.arm_hotkey()
        self.press(Qt.Key.Key_unknown)
        self.assert_original_active()
        self.assertIn("could not be captured", self.window.statusBar().currentMessage())

    def test_registration_failure_preserves_original_shortcuts(self):
        original_register = self.manager._register
        def register(shortcuts):
            if shortcuts.get("default", (0, 0))[1] == 0x79:
                raise ValueError("Shortcut is unavailable or already in use.")
            return original_register(shortcuts)
        self.window.arm_hotkey()
        with patch.object(self.manager, "_register", side_effect=register):
            self.press(Qt.Key.Key_F10)
        self.assert_original_active()
        self.assertIn("unavailable", self.window.statusBar().currentMessage())

    def test_successful_assignment_is_active_and_persisted(self):
        self.window.arm_hotkey()
        self.press(Qt.Key.Key_F10, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.manager.status()["combo"], "Ctrl+F10")
        self.assertTrue(self.manager.status()["registered"])
        self.assertEqual(self.saved_keys()["combo"], "Ctrl+F10")
        self.assertFalse(self.window._capturing_hotkey)

    def test_clear_while_armed_cancels_capture_and_preserves_other_shortcuts(self):
        self.window.arm_hotkey()
        self.window.clear_hotkey()
        self.press(Qt.Key.Key_F10)
        self.assertFalse(self.window._capturing_hotkey)
        self.assertEqual(self.manager.status()["combo"], "")
        self.assertEqual(self.manager.status()["combos"]["waystone"], "F9")
        self.assertTrue(self.manager.status()["registered"])
        self.assertEqual(self.saved_keys()["combo"], "")

    def test_clear_other_shortcut_cancels_capture_without_changing_general_key(self):
        self.window.arm_hotkey()
        self.window.clear_hotkey("waystone")
        self.press(Qt.Key.Key_F10)
        self.assertFalse(self.window._capturing_hotkey)
        self.assertEqual(self.manager.status()["combo"], "F8")
        self.assertEqual(self.manager.status()["combos"]["waystone"], "")
        self.assertTrue(self.manager.status()["registered"])

    def test_clear_last_shortcut_stays_off_when_settings_are_reloaded(self):
        self.window.clear_hotkey("waystone")
        self.window.arm_hotkey()
        self.window.clear_hotkey()
        self.manager.start()
        self.assertFalse(self.manager.status()["registered"])
        self.assertEqual(self.manager.status()["combo"], "")
        self.assertFalse(any(self.manager.status()["combos"].values()))
        self.assertEqual(self.window.hotkey_label.text(), "Off")
        self.assertEqual(self.saved_keys()["combo"], "")
        self.assertFalse(self.window._capturing_hotkey)

    def test_failed_clear_restores_listener_after_cancelling_capture(self):
        self.window.arm_hotkey()
        with patch.object(self.manager, "configure", side_effect=ValueError("Clear failed.")):
            self.window.run(self.window.clear_hotkey)
        self.assert_original_active()
        self.assertEqual(self.window.statusBar().currentMessage(), "Clear failed.")

    def test_cancel_restores_listener_and_overlay_escape(self):
        self.window._overlay_enabled = True
        self.window.arm_hotkey()
        self.assertFalse(self.window.overlay_escape.isEnabled())
        self.window.arm_hotkey()
        self.assert_original_active()
        self.assertTrue(self.window.overlay_escape.isEnabled())

    def test_modifiers_alone_keep_capture_active_until_key_is_pressed(self):
        self.window.arm_hotkey()
        self.press(Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.window._capturing_hotkey, "default")
        self.assertFalse(self.manager.status()["registered"])
        self.press(Qt.Key.Key_F10, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(self.window._capturing_hotkey)
        self.assertEqual(self.manager.status()["combo"], "Ctrl+F10")
        self.assertTrue(self.manager.status()["registered"])

    def test_region_navigation_cancels_capture_and_resumes_shortcuts_on_exit(self):
        self.window.arm_hotkey()
        self.window.tabs.setCurrentIndex(7)
        self.assertFalse(self.window._capturing_hotkey)
        self.assertTrue(self.window._editing_regions)
        self.assertFalse(self.manager.status()["registered"])
        self.press(Qt.Key.Key_Return)
        self.assertEqual(self.manager.status()["combo"], "F8")
        self.assertEqual(self.saved_keys()["combo"], "F8")
        self.window.tabs.setCurrentIndex(6)
        self.assertFalse(self.window._editing_regions)
        self.assert_original_active()

    def test_leaving_scan_settings_cancels_capture_and_restores_shortcuts(self):
        self.window.arm_hotkey()
        self.window.tabs.setCurrentIndex(4)
        self.assert_original_active()

    def test_hiding_overlay_cancels_capture_and_restores_shortcuts(self):
        self.window._overlay_enabled = True
        self.window.arm_hotkey()
        self.window.hide_overlay()
        self.assert_original_active()
        self.assertTrue(self.window.overlay_escape.isEnabled())
