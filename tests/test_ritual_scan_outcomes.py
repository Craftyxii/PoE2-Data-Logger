"""Every Ritual scan saves to the map shown when it was taken, or says why it did not.

Captures travel the real hotkey -> window -> database path; recognition is injected
through futures, so these tests check outcomes, not OCR accuracy.
"""
from concurrent.futures import Future
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import native_desktop as ui


class RetainedFuture(Future):
    """Recognition that completes only when the test says so."""
    def cancel(self):
        return False


class RitualScanHarness(unittest.TestCase):
    """Drive Ritual captures through the hotkey manager and an automatic-saving window."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-ritual-outcomes-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({"ocr_auto_commit": True, "activity_type": "Ritual"})
        self.manager = hotkey.HotkeyManager(supported=False)
        self.hotkey_patch = patch.object(service, "HOTKEY", self.manager)
        self.hotkey_patch.start()
        self.window = ui.LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()
        self.futures = []
        self.submit_patch = patch.object(ui.ocr_workers, "submit", side_effect=self.submit)
        self.submit_patch.start()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.submit_patch.stop()
        self.hotkey_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def submit(self, request):
        future = RetainedFuture()
        self.futures.append(future)
        return future

    @staticmethod
    def png(index):
        raw = io.BytesIO()
        Image.new("RGB", (600, 250), (index % 256, (index * 37) % 256, 90)).save(raw, format="PNG")
        return raw.getvalue()

    def capture(self, index):
        """Press the Ritual hotkey on a page and let the window pick the capture up."""
        result = {"captured": True, "map_id": logger.get_state()["current_map_id"], **logger.scan_context(),
                  "_ocr_strictness_values": {"ritual": 50}}
        with self.manager._lock:
            self.manager._publish_event({"mode": "ritual", "result": result, "error": ""}, self.png(index))
        self.window.poll()
        self.app.processEvents()

    @staticmethod
    def reading(name, rolls, *, tribute=5000, needs_review=False, deferred=False):
        return {"items": [{"category": "Item", "name": name, "quantity": 1, "tribute": 100,
                           "source": "Ritual offer", "deferred": deferred, "score": .99, "name_match": 1,
                           "needs_review": needs_review}],
                "raw_text": f"{tribute} TRIBUTE", "reward_count": 1, "needs_review": needs_review,
                "coverage_uncertain": False, "tribute_available": tribute, "rerolls_remaining": rolls}

    def finish(self, index, reading):
        """Complete the recognition started by capture number ``index``."""
        self.futures[index].set_result(reading)
        self.app.processEvents()

    def pages(self, map_id="M0001"):
        return [page["items"][0]["name"] if page["items"] else None for page in logger.ritual_pages_for_map(map_id)]

    def message(self):
        return self.window.review_summary.text()


class RitualVisibleOutcomeTests(RitualScanHarness):
    def test_new_page_saves_and_confirms(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.assertEqual(self.pages(), ["Divine Orb"])
        self.assertIn("✓ Ritual page saved as Scan Commit #1.", self.window.ritual_status.text())
        self.assertIsNone(self.window.pending_review_kind)

    def test_repeat_page_is_dropped_with_what_it_matched(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.capture(2)
        self.finish(1, self.reading("Exalted Orb", 6))
        self.assertEqual(self.pages(), ["Divine Orb"])
        self.assertEqual(self.message(), "✓ Already saved: M0001 page 1 had 6 rolls left (Scan Commit #1).")

    def test_page_of_only_deferred_rewards_without_counters_says_nothing_new(self):
        self.capture(1)
        reading = self.reading("Divine Orb", None, deferred=True)
        reading["tribute_available"] = None
        self.finish(0, reading)
        self.assertEqual(self.pages(), [])
        self.assertEqual(self.message(), "✓ Ritual page read: every reward is deferred, nothing new to log.")

    def test_failed_reading_keeps_the_review_with_reject(self):
        self.capture(1)
        self.futures[0].set_exception(TimeoutError("OCR timed out after 90 seconds"))
        self.app.processEvents()
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertIn("Scan failed: OCR timed out after 90 seconds", self.message())
        self.assertTrue(self.window.reject_scan_button.isVisible())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.window.reject_scan_button.click()
        self.app.processEvents()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.pages(), [])

    def test_save_failure_keeps_the_rows_and_says_so(self):
        self.capture(1)
        with patch.object(logger, "save_ritual_page", side_effect=OSError("disk busy")):
            self.finish(0, self.reading("Divine Orb", 6))
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertIn("Automatic save could not finish", self.window.ritual_status.text())
        self.assertEqual(self.window.ritual_table.rowCount(), 1)
        self.assertEqual(self.pages(), [])

    def test_held_page_waits_for_approval_then_saves(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertIn("Auto-commit held", self.window.ritual_status.text())
        self.window.review_ritual_row(self.window.ritual_table.cellWidget(0, 6), True)
        self.window.approve_scan_button.click()
        self.app.processEvents()
        self.assertEqual(self.pages(), ["Divine Orb"])


class RitualUndoOrderTests(RitualScanHarness):
    def test_undo_reverses_the_last_recorded_action_in_order(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.window.finish_map()
        self.app.processEvents()
        self.capture(2)
        self.finish(1, self.reading("Exalted Orb", 6))
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], ["Exalted Orb"]))
        # The Ritual page was the last thing done after + New map: it goes first.
        self.assertEqual(logger.undo_last_action()["action"], "Ritual approval")
        self.window.refresh()
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], []))
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        # Then + New map: the previous map is current and editable again.
        self.assertEqual(logger.undo_last_action()["action"], "New map")
        self.window.refresh()
        state = logger.get_state()
        self.assertEqual((state["current_map_id"], state["pending_new_map"]), ("M0001", False))
        # A page scanned now belongs to the reopened map and is checked against its pages.
        self.capture(3)
        self.finish(2, self.reading("Chaos Orb", 6))
        self.assertEqual(self.message(), "✓ Already saved: M0001 page 1 had 6 rolls left (Scan Commit #1).")
        self.capture(4)
        self.finish(3, self.reading("Chaos Orb", 5))
        self.assertEqual(self.pages("M0001"), ["Divine Orb", "Chaos Orb"])


if __name__ == "__main__":
    unittest.main()
