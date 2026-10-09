"""Mocked Windows shortcut/capture routing checks, including map ownership and clipboard-versus-screen fallbacks."""

import itertools
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialog

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey, hover_copy
from PoE2_Data_Logger.ui import region_select


class PlatformTests(unittest.TestCase):
    """Check mocked Windows hotkeys, fresh clipboard reads, and native-pixel capture regions."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication needed by these widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Initialize temporary logger storage for platform capture and hotkey tests."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the original data directory and remove the isolated logger database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def test_shortcut_replacement_does_not_wait_while_holding_listener_lock(self):
        """Verify shortcut replacement does not wait while holding listener lock."""
        manager = hotkey.HotkeyManager(supported=True)
        def listen(shortcuts, ready, outcome, stop):
            """Signal listener readiness, then acquire the manager lock after shutdown is requested."""
            outcome["thread_id"] = 17
            ready.set()
            stop.wait(5)
            with manager._lock:
                manager.error = ""
        manager._message_loop = listen
        with patch("ctypes.WinDLL", create=True, return_value=Mock()):
            manager.configure("F8", persist=False)
            started = time.monotonic()
            manager.configure("F9", persist=False)
            elapsed = time.monotonic() - started
            self.assertEqual(manager.status()["combo"], "F9")
            self.assertTrue(manager.status()["registered"])
            manager._unregister()
        self.assertLess(elapsed, 1)
        self.assertFalse(manager.status()["registered"])

    def test_listener_startup_exception_releases_waiter(self):
        """Verify listener startup exception releases waiter."""
        manager = hotkey.HotkeyManager(supported=True)
        with patch("ctypes.WinDLL", create=True, side_effect=OSError("unavailable")):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                manager._register({"default": (0, 0x77)})
        self.assertFalse(manager.status()["registered"])

    def test_clipboard_contention_is_retried(self):
        """Verify clipboard contention is retried."""
        user = Mock()
        user.GetClipboardSequenceNumber.side_effect = itertools.chain([1], itertools.repeat(2))
        user.GetAsyncKeyState.return_value = 0
        text = "Item Class: Waystones\nRarity: Rare\nStorm Peak"
        with patch.object(hover_copy.sys, "platform", "win32"), patch(
                "ctypes.WinDLL", create=True, return_value=user), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", return_value=True), patch.object(
                hover_copy, "_clipboard_text", side_effect=["", text]) as read, patch.object(
                hover_copy.time, "sleep"):
            self.assertEqual(hover_copy.read_hovered_text(), text)
        self.assertEqual(read.call_count, 2)
        user.SendInput.assert_not_called()

    def test_existing_clipboard_text_is_not_reused_without_fresh_copy(self):
        """Verify existing clipboard text is not reused without fresh copy."""
        user = Mock()
        user.GetClipboardSequenceNumber.return_value = 91
        user.GetAsyncKeyState.return_value = 0
        with patch.object(hover_copy.sys, "platform", "win32"), patch(
                "ctypes.WinDLL", create=True, return_value=user), patch(
                "PoE2_Data_Logger.platform.live_watch.game_foreground", return_value=True), patch.object(
                hover_copy, "_clipboard_text", return_value="Item Class: Waystones\nExample") as read:
            self.assertIsNone(hover_copy.read_hovered_text(timeout=0))
        self.assertEqual(read.call_count, 0)

    def test_oversized_combined_capture_is_rejected_before_grabbing(self):
        """Verify oversized combined capture is rejected before grabbing."""
        grab = Mock()
        with patch.object(hover_copy, "_tooltip_bounds", return_value=(0, 0, 1920, 1080)):
            with self.assertRaisesRegex(ValueError, "Combined capture"):
                hover_copy.capture_remnant_context({"x": 32768, "y": 32768, "w": 4096, "h": 100}, grab)
        grab.assert_not_called()

    def test_capture_preserves_panel_and_tooltip_crops(self):
        """Verify capture preserves panel and tooltip crops."""
        with patch.object(hover_copy, "_tooltip_bounds", return_value=(0, 0, 800, 600)):
            panel, tooltip = hover_copy.capture_remnant_context(
                {"x": 100, "y": 150, "w": 250, "h": 300},
                lambda bbox, **kwargs: Image.new("RGB", (bbox[2] - bbox[0], bbox[3] - bbox[1]), (20, 40, 60)))
        self.assertEqual(panel.size, (250, 300))
        self.assertEqual(tooltip.size, (800, 600))

    def test_currency_hotkey_freezes_phase(self):
        """Verify currency hotkey freezes phase."""
        logger.start_map()
        with logger._connect() as db:
            logger._set_meta(db, "inventory_region", {"x": 0, "y": 0, "w": 480, "h": 200})
            logger._set_meta(db, "inventory_scan_phase", "end")
        manager = hotkey.HotkeyManager(supported=False, grabber=lambda **kwargs: Image.new("RGB", (480, 200)))
        manager.capture("currency")
        result = manager.status()["latest"]["result"]
        with logger._connect() as db:
            logger._set_meta(db, "inventory_scan_phase", "start")
        self.assertEqual(result["phase"], "end")
        self.assertEqual(result["map_id"], "M0001")
        logger.validate_scan_context(result)

    def test_cancelled_hotkey_result_is_discarded_and_capture_lock_released(self):
        """Verify cancelled hotkey result is discarded and capture lock released."""
        manager = hotkey.HotkeyManager(supported=False)
        self.assertTrue(manager._capture_lock.acquire(False))
        revision = manager._capture_revision
        manager.cancel_capture()
        manager._finish_capture({"mode": "opened", "error": "", "result": {}}, b"capture", revision)
        self.assertIsNone(manager.status()["latest"])
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_copied_waystone_is_read_before_ocr_and_keeps_captured_image(self):
        """Verify copied Waystone is read before OCR and keeps captured image."""
        calls = []
        text = "Item Class: Waystones\nRarity: Rare\nStorm Peak\nWaystone (Tier 16)\n--------\nWaystone Drop Chance: +87%\n--------\n30% increased Rarity of Items found in this Area"
        with logger._connect() as db:
            logger._set_meta(db, "live_region", {"x": 0, "y": 0, "w": 200, "h": 100})
        def copy():
            """Record the hover-copy call and return controlled Waystone clipboard text."""
            calls.append("copy")
            return text
        manager = hotkey.HotkeyManager(supported=True, hover_reader=copy, focused=lambda: True,
            grabber=lambda **kwargs: Image.new("RGB", (200, 100)),
            tooltip_grabber=lambda: Image.new("RGB", (800, 600), (20, 40, 60)))
        with patch.object(hotkey.sys, "platform", "linux"), patch.object(hotkey, "scan_opened", side_effect=AssertionError("OCR ran")):
            manager.capture()
        event = manager.status()["latest"]
        self.assertEqual(calls, ["copy"])
        self.assertEqual(event["error"], "")
        self.assertEqual(event["mode"], "item")
        self.assertEqual(event["result"]["name"], "Storm Peak")
        self.assertTrue(manager.image(event["id"]))

    def test_scaled_region_editor_returns_native_pixels(self):
        """Convert a controlled half-size logical selection back to its native pixels."""
        current = {"x": 100, "y": 200, "w": 400, "h": 200}
        with patch.object(region_select, "_logical_capture_bounds",
                          return_value=QRect(0, 0, 960, 540)):
            editor = region_select.RegionEditor(current, screen_bounds=(0, 0, 1920, 1080))
        self.assertEqual((editor.width(), editor.height()), (960, 540))
        self.assertEqual(editor.region(), current)
        editor.confirm()
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
        editor.close()

    def test_region_drag_stays_inside_capture_bounds(self):
        """Clip dragged selections using a synthetic display with equal native and logical bounds."""
        with patch.object(region_select, "_logical_capture_bounds", side_effect=QRect):
            editor = region_select.RegionEditor(screen_bounds=(0, 0, 1920, 1080))
        press = Mock()
        press.button.return_value = Qt.MouseButton.LeftButton
        press.position.return_value = QPointF(editor.width() - 20, 100)
        editor.mousePressEvent(press)
        move = Mock()
        move.position.return_value = QPointF(-300, editor.height() + 200)
        editor.mouseMoveEvent(move)
        region = editor.region()
        self.assertGreaterEqual(region["x"], 0)
        self.assertGreaterEqual(region["y"], 0)
        self.assertLessEqual(region["x"] + region["w"], 1920)
        self.assertLessEqual(region["y"] + region["h"], 1080)
        editor.confirm()
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
        editor.close()

    def test_stale_region_is_rejected_before_capture(self):
        """Verify stale region is rejected before capture."""
        with logger._connect() as db:
            logger._set_meta(db, "live_region", {"x": 3000, "y": 0, "w": 400, "h": 200})
        with self.assertRaisesRegex(ValueError, "outside the game"):
            region_select.region_for("live_region", (0, 0, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
