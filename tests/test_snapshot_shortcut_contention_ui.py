"""Real SQLite writer contention through Snapshot signals and shortcut key events.

The only platform adapter replaced is the Windows listener. Logger validation,
preference writes, capture ownership and inventory approval use production code.
"""

import copy
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QGridLayout

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class SnapshotShortcutContentionUITests(unittest.TestCase):
    """Keep the HUD responsive and drafts accurate when a second connection owns its writer."""

    @classmethod
    def setUpClass(cls):
        """Reuse one QApplication for actual signals and key-event delivery."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create isolated data, saved shortcuts and a real logger window."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-snapshot-shortcut-busy-")
        self.previous_data, self.previous_ready = store.DATA_DIR, logger._READY
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.manager = HotkeyManager(supported=True)
        self.registrations = []

        def listen(shortcuts, ready, outcome, stop):
            """Record actual listener replacements while stubbing Windows messages only."""
            self.registrations.append((dict(shortcuts), stop, threading.current_thread()))
            outcome["thread_id"] = 17
            ready.set()
            stop.wait(30)

        self.manager._message_loop = listen
        self.native_patch = patch("ctypes.WinDLL", create=True, return_value=Mock())
        self.manager_patch = patch.object(service, "HOTKEY", self.manager)
        self.native_patch.start()
        self.manager_patch.start()
        self.manager.configure("F8")
        self.manager.configure_for("currency", "F9")
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.writer = None

    def tearDown(self):
        """Close real writers/listeners before restoring the original profile."""
        self.release_writer()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.manager_patch.stop()
        self.native_patch.stop()
        store.DATA_DIR, logger._READY = self.previous_data, self.previous_ready
        self.temporary.cleanup()

    def hold_writer(self):
        """Acquire a second connection's SQLite write reservation without mocking failure."""
        self.writer = sqlite3.connect(store.DATA_DIR / "scans.sqlite3", timeout=0)
        self.writer.execute("BEGIN IMMEDIATE")

    def release_writer(self):
        """Release and explicitly close the test writer on every path."""
        if self.writer is not None:
            self.writer.rollback()
            self.writer.close()
            self.writer = None

    def meta(self, key, default=None):
        """Read independently committed preferences from the actual logger database."""
        with logger._connect() as db:
            return logger._meta(db, key, default)

    def choose_phase(self, phase):
        """Emit the real Snapshot currentIndexChanged signal and require prompt feedback."""
        before = time.monotonic()
        with patch.object(sys, "excepthook") as unhandled:
            self.window.inventory_phase.setCurrentIndex(self.window.inventory_phase.findData(phase))
            self.app.processEvents()
        if self.writer is not None:
            self.assertLess(time.monotonic() - before, 1.0)
        unhandled.assert_not_called()

    def review_capture(self, phase="start"):
        """Display recognized rows with the same immutable ownership as an OCR completion."""
        select(self.window.inventory_phase, phase)
        capture = {"map_id": logger.currency_target_map(phase), "phase": phase,
                   "context": logger.scan_context(), "strictness": 50,
                   "image": Image.new("RGB", (480, 200), "darkblue")}
        self.window._inventory_capture_context = self.window._inventory_reading = capture
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7}],
                                     "unknown": []}, live=False, expected_map_id=capture["map_id"],
                                    capture=capture)
        return capture

    def test_snapshot_write_failure_rolls_selector_back_to_saved_phase(self):
        """A manual Snapshot change cannot leave the selector ahead of its saved preference."""
        self.choose_phase("end")
        self.assertEqual(self.meta("inventory_scan_phase"), "end")
        self.hold_writer()
        self.choose_phase("start")
        self.assertEqual(self.window.inventory_phase.currentData(), "end")
        self.assertEqual(self.meta("inventory_scan_phase"), "end")
        self.assertIn("database is busy", self.window.statusBar().currentMessage())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.release_writer()
        self.choose_phase("start")
        self.assertEqual(self.meta("inventory_scan_phase"), "start")
        self.assertEqual(self.window.inventory_phase.currentData(), "start")

    def test_held_review_write_failure_preserves_capture_and_retries_correct_phase(self):
        """A blocked Start-to-End change retains rows until retry commits End exactly once."""
        self.window.tabs.setCurrentIndex(0)
        capture = self.review_capture()
        context, summary, tooltip = copy.deepcopy(capture["context"]), self.window.review_summary.text(), \
                                    self.window.approve_scan_button.toolTip()
        self.hold_writer()
        self.choose_phase("end")
        self.assertEqual(self.window.inventory_phase.currentData(), "start")
        self.assertEqual(self.window._pending_currency_phase, "start")
        self.assertEqual(capture["phase"], "start")
        self.assertEqual(capture["context"], context)
        self.assertEqual(self.window._pending_currency_map, "M0001")
        self.assertEqual(self.window.review_summary.text(), summary)
        self.assertEqual(self.window.approve_scan_button.toolTip(), tooltip)
        self.assertEqual(self.window.inventory_table.item(0, 1).text(), "Chaos Orb")
        self.assertEqual(self.window.inventory_table.item(0, 2).text(), "7")
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertFalse(self.window._failed_review)
        self.assertEqual(logger.currency_for_map("M0001")["end"], {})
        self.release_writer()
        self.choose_phase("end")
        self.assertEqual(self.meta("inventory_scan_phase"), "end")
        self.assertEqual(capture["phase"], "end")
        self.assertEqual(self.window._pending_currency_phase, "end")
        self.window.approve_scan_button.click()
        self.window.approve_scan_button.click()
        snapshot = logger.currency_for_map("M0001")
        self.assertEqual((snapshot["start"], snapshot["end"]), ({}, {"Chaos Orb": 7}))
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_review_rollback_uses_captured_phase_even_if_saved_preference_differs(self):
        """Hotkey-bound Start retains ownership when the previous stored preference was End."""
        self.choose_phase("end")
        capture = self.review_capture("start")
        self.hold_writer()
        self.choose_phase("end")
        self.assertEqual(self.meta("inventory_scan_phase"), "end")
        self.assertEqual(self.window.inventory_phase.currentData(), "start")
        self.assertEqual(capture["phase"], "start")
        self.release_writer()
        self.window.approve_scan_button.click()
        snapshot = logger.currency_for_map("M0001")
        self.assertEqual((snapshot["start"], snapshot["end"]), ({"Chaos Orb": 7}, {}))
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def press_assignment(self, target):
        """Capture a real Ctrl+F10 event for either general or dedicated scan keys."""
        self.window.arm_hotkey(target)
        before = time.monotonic()
        with patch.object(sys, "excepthook") as unhandled:
            self.app.sendEvent(self.window, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F10,
                                                     Qt.KeyboardModifier.ControlModifier))
            self.app.processEvents()
        if self.writer is not None:
            self.assertLess(time.monotonic() - before, 1.0)
        unhandled.assert_not_called()

    def test_shortcut_write_failure_retires_attempted_listener_and_restores_saved_keys(self):
        """Both shortcut paths restore real registration definitions after failed persistence."""
        self.window.tabs.setCurrentIndex(6)
        for target in ("default", "currency"):
            with self.subTest(target=target):
                saved = self.meta("ocr_shortcut")
                self.hold_writer()
                self.press_assignment(target)
                self.assertFalse(self.window._capturing_hotkey)
                self.assertEqual(self.meta("ocr_shortcut"), saved)
                self.assertEqual(self.manager.status()["combo"], "F8")
                self.assertEqual(self.manager.status()["combos"]["currency"], "F9")
                self.assertTrue(self.manager.status()["registered"])
                self.assertIn("database is busy", self.window.statusBar().currentMessage())
                active, _, thread = self.registrations[-1]
                self.assertEqual(active["default"][1], 0x77)
                self.assertEqual(active["currency"][1], 0x78)
                self.assertTrue(thread.is_alive())
                for _, stop, previous_thread in self.registrations[:-1]:
                    self.assertTrue(stop.is_set())
                    self.assertFalse(previous_thread.is_alive())
                self.release_writer()
                self.press_assignment(target)
                saved = self.meta("ocr_shortcut")
                self.assertEqual(saved["combo"] if target == "default" else saved["combos"][target],
                                 "Ctrl+F10")
                self.assertTrue(self.manager.status()["registered"])
                # Reset between independent target cases through the real manager.
                self.manager.configure("F8")
                self.manager.configure_for("currency", "F9")
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_clear_shortcut_write_failure_restores_definitions_and_keeps_retry_available(self):
        """Real Clear buttons restore both key targets, whether capture was armed or idle."""
        self.window.tabs.setCurrentIndex(6)
        grid = next(layout for layout in self.window.findChildren(QGridLayout)
                    if layout.indexOf(self.window.hotkey_label) >= 0)
        for target in ("default", "currency"):
            clear = grid.itemAtPosition(0 if target == "default" else 4, 3).widget()
            for armed in (False, True):
                with self.subTest(target=target, armed=armed):
                    saved = self.meta("ocr_shortcut")
                    if armed:
                        self.window.arm_hotkey(target)
                    self.hold_writer()
                    before = time.monotonic()
                    with patch.object(sys, "excepthook") as unhandled:
                        clear.click()
                        self.app.processEvents()
                    self.assertLess(time.monotonic() - before, 1.0)
                    unhandled.assert_not_called()
                    self.assertFalse(self.window._capturing_hotkey)
                    self.assertEqual(self.meta("ocr_shortcut"), saved)
                    self.assertEqual(self.manager.status()["combo"], "F8")
                    self.assertEqual(self.manager.status()["combos"]["currency"], "F9")
                    self.assertTrue(self.manager.status()["registered"])
                    self.assertIn("database is busy", self.window.statusBar().currentMessage())
                    active, _, current_thread = self.registrations[-1]
                    self.assertEqual((active["default"][1], active["currency"][1]), (0x77, 0x78))
                    self.assertTrue(current_thread.is_alive())
                    for _, stop, retired in self.registrations[:-1]:
                        self.assertTrue(stop.is_set())
                        self.assertFalse(retired.is_alive())
                    self.release_writer()
                    self.press_assignment(target)
                    saved = self.meta("ocr_shortcut")
                    self.assertEqual(saved["combo"] if target == "default" else saved["combos"][target],
                                     "Ctrl+F10")
                    clear.click()
                    saved = self.meta("ocr_shortcut")
                    self.assertEqual(saved["combo"] if target == "default" else saved["combos"][target], "")
                    other = self.manager.status()["combos"]["currency"] if target == "default" else \
                            self.manager.status()["combo"]
                    self.assertEqual(other, "F9" if target == "default" else "F8")
                    self.assertTrue(self.manager.status()["registered"])
                    self.manager.configure("F8")
                    self.manager.configure_for("currency", "F9")
                    self.assertEqual(logger.get_state()["scan_commit_count"], 0)


if __name__ == "__main__":
    unittest.main()
