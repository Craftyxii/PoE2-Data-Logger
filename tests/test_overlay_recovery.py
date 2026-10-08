import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PIL import Image

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
        # The listener's WinDLL mock has no real foreground-window HWND.
        # Supply the external display bounds separately so capture reaches
        # the injected grabber on Windows, and exercise that same branch on
        # other hosts without changing Qt's or PIL's platform detection.
        bounds_patch = patch("PoE2_Data_Logger.platform.hover_copy._tooltip_bounds",
                             return_value=(100, 150, 2020, 1230))
        self.capture_bounds = bounds_patch.start()
        self.addCleanup(bounds_patch.stop)
        platform_patch = patch("PoE2_Data_Logger.platform.hotkey.sys", SimpleNamespace(platform="win32"))
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
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

    def test_worker_capture_hides_before_grab_and_restores_uncertain_review(self):
        visible_at_capture = []
        ticks = []

        def grab(**kwargs):
            visible_at_capture.append(self.window._overlay_visible)
            return Image.new("RGB", (575, 720), "tan")

        self.manager.grabber = grab
        self.manager.readers["propagation"] = lambda image: {
            "runes": [], "positions": [], "can_use": False,
            "status": "Selected recipe cursor was not clear."}
        QTimer.singleShot(0, lambda: ticks.append(True))
        worker = threading.Thread(target=lambda: self.manager.capture("propagation"))
        worker.start()
        deadline = time.monotonic() + 3
        while worker.is_alive() and time.monotonic() < deadline:
            QTest.qWait(10)
        worker.join(timeout=.1)
        self.assertFalse(worker.is_alive(), "Capture did not release the HUD handoff")
        self.app.processEvents()
        self.window.poll()
        self.app.processEvents()
        self.assertEqual(visible_at_capture, [False])
        self.capture_bounds.assert_called_once_with()
        self.assertEqual(ticks, [True])
        self.assertFalse(self.manager._capture_lock.locked())
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.approve_scan_button.isEnabled())

    def test_capture_failure_restores_hud_with_visible_error(self):
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.poll()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertIn("Capture unavailable", self.window.scan_status.text())
        self.capture_bounds.assert_called_once_with()
        self.manager.grabber.assert_called_once()
        self.assertFalse(self.manager._capture_lock.locked())
        self.assertIsNone(self.window.pending_review_kind)
        self.window.poll()
        self.assertTrue(self.window.isVisible())

    def test_capture_failure_preserves_existing_remnant_review(self):
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        self.app.processEvents()
        pending = logger.get_state()["ocr_pending"]
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.poll()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertIn("Capture unavailable", self.window.scan_status.text())
        self.capture_bounds.assert_called_once_with()
        self.manager.grabber.assert_called_once()

    def test_capture_failure_does_not_reopen_previously_hidden_hud(self):
        self.window.hide_overlay()
        self.assertFalse(self.window.isVisible())
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.poll()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_capture_failure_preserves_previously_minimized_window(self):
        self.window.showMinimized()
        self.app.processEvents()
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.poll()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())

    def test_capture_failure_does_not_override_escape_after_capture(self):
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.hide_overlay()  # Escape uses this same action.
        self.window.poll()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_capture_failure_does_not_override_disabled_overlay(self):
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        self.window.set_overlay_enabled(False)
        self.window.tabs.setCurrentIndex(4)
        self.window.poll()
        self.app.processEvents()
        self.assertFalse(self.window._overlay_enabled)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertEqual(self.window.tabs.currentIndex(), 4)

    def test_stale_capture_error_does_not_reopen_after_map_changes(self):
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        logger.finish_map("", "", "")
        logger.start_map()
        self.window.poll()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_stale_capture_error_does_not_reopen_after_session_reset(self):
        self.manager.grabber = Mock(side_effect=RuntimeError("Capture unavailable"))
        self.manager.capture("propagation")
        logger.clear_export_and_reset_ids()
        self.window.poll()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_timed_out_capture_does_not_hide_hud_after_worker_finishes(self):
        self.manager.grabber = Mock(return_value=Image.new("RGB", (575, 720), "tan"))
        worker = threading.Thread(target=lambda: self.manager.capture("propagation"))
        worker.start()
        # Hold the UI thread long enough for the worker's bounded handoff to
        # time out, then deliver its queued hide request and error together.
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), "Capture did not time out")
        self.app.processEvents()
        self.window.poll()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertIn("The HUD is busy", self.window.scan_status.text())
        self.manager.grabber.assert_not_called()
        self.assertFalse(self.manager._capture_lock.locked())


if __name__ == "__main__":
    unittest.main()
