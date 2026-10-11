"""Check desktop-only retirement of unrecoverable remnant reservations after restart."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop


class ScanStartupRecoveryTests(unittest.TestCase):
    """Keep saved observations and IDs intact while clearing reservations with no surviving review."""

    @classmethod
    def setUpClass(cls):
        """Share the application required by real review and restart controls."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create a saved map and inventory baseline before opening any desktop review."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-scan-startup-")
        self.previous_data = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        self.window = None

    def tearDown(self):
        """Close callbacks, restore the caller's profile/mode and remove isolated data."""
        self.close_window()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def open_window(self):
        """Construct a real desktop while retaining saved profile data."""
        self.window = native_desktop.LoggerWindow()
        self.window._poll.stop()

    def close_window(self):
        """Exercise the production close path without retaining an old in-memory review."""
        if self.window is not None:
            self.window.close()
            self.window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()
            self.window = None

    def saved_state(self):
        """Snapshot saved logs, counters and Undo identity independently of the transient reservation."""
        state = logger.get_state()
        with logger._connect() as db:
            commits = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
            snapshots = [tuple(row) for row in db.execute("SELECT * FROM currency_snapshots")]
        return (state["current_map_id"], state["next_remnant_id"], state["scan_commit_count"],
                commits, snapshots, logger.undo_action())

    def hold_review(self, mode):
        """Hold a real opened or visible-seed review with its persisted reservation."""
        if mode == "opened":
            result = {"mode": mode, "status": "Review the opened remnant.", "can_use": False,
                      "first_recipe": None, "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}]}
        else:
            with logger._connect() as db:
                stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
            result = {"mode": mode, "status": "Review the visible remnant.",
                      "remnants": [{"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                                    "seed_rune": stage["seed_rune"], "candidates": [3], "can_commit": False}]}
        self.window.show_result(mode, {**result, **logger.scan_context()})
        return logger.get_state()["ocr_pending"]

    def test_restart_retires_unrecoverable_opened_and_seed_reservations_and_admits_next_scan(self):
        """Restart permits the next inventory capture without consuming remnant IDs or altering saved observations."""
        for mode in ("opened", "seed"):
            with self.subTest(mode=mode):
                self.open_window()
                baseline = self.saved_state()
                pending = self.hold_review(mode)
                self.assertIsNotNone(pending)
                self.close_window()
                # Shared core initialization must preserve reservations while a
                # live GUI exists; recovery belongs only to the new desktop.
                logger._READY = False
                logger.initialize()
                self.assertEqual(logger.get_state()["ocr_pending"], pending)
                self.open_window()
                self.assertIsNone(logger.get_state()["ocr_pending"])
                self.assertIsNone(self.window.pending_review_kind)
                self.assertIn("unfinished remnant", self.window.review_summary.text().lower())
                self.assertEqual(self.saved_state(), baseline)
                jobs = []
                self.window._submit = lambda label, work, done=None: jobs.append((work, done))
                with patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image):
                    self.window._inventory_captured(Image.new("RGB", (600, 400), "blue"), live=False)
                self.assertEqual(self.window.pending_review_kind, "currency")
                self.assertEqual(len(jobs), 1)
                self.window.reject_review()
                self.close_window()

    def test_core_reinitialization_preserves_a_live_manual_review(self):
        """A child/profile initializer cannot discard a review still owned by the active desktop."""
        self.open_window()
        pending = self.hold_review("opened")
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(self.window.pending_review_kind, "remnant")
        with self.assertRaisesRegex(ValueError, "Save or reject"):
            self.window._require_remnant_review_finished()

    def test_clean_startup_does_not_create_a_discard_or_logging_action(self):
        """A clean desktop launch preserves saved state and displays the normal empty review."""
        baseline = self.saved_state()
        self.open_window()
        self.assertEqual(self.saved_state(), baseline)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertNotIn("discarded", self.window.review_summary.text().lower())


if __name__ == "__main__":
    unittest.main()
