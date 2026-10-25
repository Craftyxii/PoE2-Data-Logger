"""Every Ritual scan saves to the map shown when it was taken, or says why it did not.

Captures travel the real hotkey -> window -> database path; recognition is injected
through futures, so these tests check outcomes, not OCR accuracy.
"""
from concurrent.futures import Future
import io
import os
from pathlib import Path
import sqlite3
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

    def poll(self):
        """One window tick: a waiting page starts once the page before it is finished."""
        self.window.poll()
        self.app.processEvents()

    def approve_all(self):
        for row in range(self.window.ritual_table.rowCount()):
            self.window.review_ritual_row(self.window.ritual_table.cellWidget(row, 6), True)
        self.window.approve_scan_button.click()
        self.app.processEvents()


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


class RitualPagesWaitInLineTests(RitualScanHarness):
    """A page being read or reviewed is never replaced; at most two more wait behind it."""

    def test_page_being_read_is_not_replaced_by_the_next_scan(self):
        self.capture(1)
        self.capture(2)
        self.assertEqual(len(self.futures), 1)
        self.assertIn("1 Ritual page waiting.", self.window.statusBar().currentMessage())
        self.finish(0, self.reading("Divine Orb", 6))
        self.poll()
        self.assertEqual(len(self.futures), 2)
        self.finish(1, self.reading("Exalted Orb", 5))
        self.assertEqual(self.pages(), ["Divine Orb", "Exalted Orb"])
        self.assertEqual(self.window._ritual_waiting, [])

    def test_held_page_is_not_replaced_by_the_next_scan(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.capture(2)
        self.assertEqual(len(self.futures), 1)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), "Divine Orb")
        self.poll()
        self.assertEqual(len(self.futures), 1)
        self.approve_all()
        self.poll()
        self.assertEqual(len(self.futures), 2)
        self.finish(1, self.reading("Exalted Orb", 5))
        self.assertEqual(self.pages(), ["Divine Orb", "Exalted Orb"])

    def test_rejected_held_page_lets_the_waiting_page_read(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.capture(2)
        self.window.reject_scan_button.click()
        self.app.processEvents()
        self.poll()
        self.finish(1, self.reading("Exalted Orb", 5))
        self.assertEqual(self.pages(), ["Exalted Orb"])

    def test_a_third_waiting_page_is_refused_with_a_message(self):
        self.capture(1)
        self.capture(2)
        self.capture(3)
        self.assertEqual(len(self.window._ritual_waiting), 2)
        self.capture(4)
        self.assertEqual(len(self.window._ritual_waiting), 2)
        refusal = ("Two Ritual pages are already waiting. Approve or reject the current page, then scan again.")
        self.assertEqual(self.window.statusBar().currentMessage(), refusal)
        self.assertEqual(self.window.scan_status.text(), refusal)
        for index, (name, rolls) in enumerate((("Divine Orb", 6), ("Exalted Orb", 5), ("Chaos Orb", 4))):
            self.finish(index, self.reading(name, rolls))
            self.poll()
        self.assertEqual(len(self.futures), 3)
        self.assertEqual(self.pages(), ["Divine Orb", "Exalted Orb", "Chaos Orb"])

    def test_waiting_pages_keep_their_order_and_repeat_check(self):
        self.capture(1)
        self.capture(2)
        self.capture(3)
        self.finish(0, self.reading("Divine Orb", 6))
        self.poll()
        # The second press saw the same page: same roll count as the page saved before it.
        self.finish(1, self.reading("Divine Orb", 6))
        self.assertEqual(self.message(), "✓ Already saved: M0001 page 1 had 6 rolls left (Scan Commit #1).")
        self.poll()
        self.finish(2, self.reading("Exalted Orb", 5))
        self.assertEqual(self.pages(), ["Divine Orb", "Exalted Orb"])


class RitualWaitingPageSafetyTests(RitualScanHarness):
    """A waiting page leaves the line only when it is read, or when it can no longer be saved."""

    def test_waiting_page_stays_in_line_while_the_database_is_busy(self):
        self.capture(1)
        self.capture(2)
        self.finish(0, self.reading("Divine Orb", 6))
        locked = sqlite3.OperationalError("database is locked")
        locked.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch.object(logger, "ritual_icons", side_effect=locked):
            self.poll()
            self.poll()
        self.assertEqual((len(self.futures), len(self.window._ritual_waiting)), (1, 1))
        self.poll()
        self.assertEqual((len(self.futures), len(self.window._ritual_waiting)), (2, 0))
        self.finish(1, self.reading("Exalted Orb", 5))
        self.assertEqual(self.pages(), ["Divine Orb", "Exalted Orb"])

    def test_waiting_page_that_cannot_start_leaves_the_line_once_with_its_message(self):
        self.capture(1)
        self.capture(2)
        self.finish(0, self.reading("Divine Orb", 6))
        with patch.object(logger, "ritual_icons", side_effect=sqlite3.OperationalError("no such table: icons")):
            self.poll()
            self.poll()
        self.assertEqual(self.window._ritual_waiting, [])
        self.assertEqual(self.window.statusBar().currentMessage(), "no such table: icons")
        self.assertEqual(len(self.futures), 1)

    def test_start_fresh_session_drops_waiting_pages_and_says_so(self):
        self.capture(1)
        self.capture(2)
        with patch.object(ui.QMessageBox, "question", return_value=ui.QMessageBox.StandardButton.Yes):
            self.window.reset_logger()
        self.app.processEvents()
        self.assertEqual(self.window._ritual_waiting, [])
        self.assertIn("2 unsaved Ritual pages were not saved", self.message())
        self.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertEqual(self.pages(), [])

    def test_review_kept_across_new_map_looks_the_same(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        shown = (self.window.found_label.isVisible(), self.window.ritual_table.isVisible(),
                 self.window.review_kind.text())
        self.window.finish_map()
        self.app.processEvents()
        self.assertEqual((self.window.found_label.isVisible(), self.window.ritual_table.isVisible(),
                          self.window.review_kind.text()), shown)


class RitualPagesAcrossNewMapTests(RitualScanHarness):
    """A page captured before + New map is saved to the map it was captured on."""

    def test_page_read_during_new_map_saves_to_its_own_map(self):
        self.capture(1)
        self.window.finish_map()
        self.app.processEvents()
        self.assertIn("The Ritual page captured on M0001 will be saved to M0001.",
                      self.window.statusBar().currentMessage())
        self.finish(0, self.reading("Divine Orb", 6))
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], []))
        # + New map took Scan Commit #1.
        self.assertIn("✓ Ritual page saved to M0001 as Scan Commit #2.", self.window.ritual_status.text())
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")

    def test_held_review_stays_open_across_new_map_and_approve_saves_to_its_map(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.window.finish_map()
        self.app.processEvents()
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(self.window.ritual_table.item(0, 1).text(), "Divine Orb")
        self.assertTrue(self.window.approve_scan_button.isVisible())
        self.approve_all()
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], []))
        self.assertIsNone(self.window.pending_review_kind)

    def test_waiting_pages_follow_their_own_map_and_new_scans_go_to_the_new_map(self):
        self.capture(1)
        self.capture(2)
        self.window.finish_map()
        self.app.processEvents()
        self.assertIn("The 2 Ritual pages captured on M0001 will be saved to M0001.",
                      self.window.statusBar().currentMessage())
        self.finish(0, self.reading("Divine Orb", 6))
        self.poll()
        self.finish(1, self.reading("Exalted Orb", 5))
        self.capture(3)
        self.finish(2, self.reading("Chaos Orb", 8))
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb", "Exalted Orb"], ["Chaos Orb"]))

    def test_repeat_check_uses_the_pages_of_the_map_it_was_captured_on(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.capture(2)
        self.window.finish_map()
        self.app.processEvents()
        self.finish(1, self.reading("Divine Orb", 6))
        self.assertEqual(self.message(), "✓ Already saved: M0001 page 1 had 6 rolls left (Scan Commit #1).")
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], []))

    def test_held_page_survives_two_new_maps_and_names_its_own_map(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.window.finish_map()
        self.app.processEvents()
        self.window.finish_map()
        self.app.processEvents()
        self.assertIn("Map M0003 started.", self.window.statusBar().currentMessage())
        self.assertIn("The Ritual page captured on M0001 will be saved to M0001.",
                      self.window.statusBar().currentMessage())
        self.approve_all()
        self.assertEqual([self.pages(map_id) for map_id in ("M0001", "M0002", "M0003")], [["Divine Orb"], [], []])

    def test_finished_map_takes_no_page_captured_after_it(self):
        self.window.finish_map()
        self.app.processEvents()
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), ([], ["Divine Orb"]))


class RitualPagesKeptThroughUndoOnTheNextMapTests(RitualScanHarness):
    """Undo or Reset map on the next map leaves a page taken on the finished map alone."""

    def test_reset_map_on_the_next_map_keeps_a_held_review_open_with_its_corrections(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        self.window.ritual_table.item(0, 2).setText("3")
        self.window.finish_map()
        self.app.processEvents()
        self.assertIn("Finish the open Ritual page before scanning anything else, or it is not saved.",
                      self.window.statusBar().currentMessage())
        with patch.object(ui.QMessageBox, "question", return_value=ui.QMessageBox.StandardButton.Yes):
            self.window.reset_current_map()
        self.app.processEvents()
        self.assertEqual(self.message(), "M0002 reset. Undo restores its saved logs. 1 Ritual page taken on "
                                         "M0001 is kept and will be saved to M0001.")
        self.poll()
        self.assertEqual(len(self.futures), 1)
        self.assertEqual((self.window.pending_review_kind, self.window.ritual_table.item(0, 2).text()),
                         ("ritual", "3"))
        self.approve_all()
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["quantity"], 3)
        self.assertEqual(self.pages("M0002"), [])

    def test_undo_of_new_map_keeps_a_page_still_being_read_and_ignores_its_old_reading(self):
        self.capture(1)
        self.window.finish_map()
        self.app.processEvents()
        self.assertEqual(self.window.undo_map()["action"], "New map")
        self.app.processEvents()
        self.assertIn("1 Ritual page taken on M0001 is kept", self.message())
        # The reading started before Undo is discarded; the page is read again.
        self.finish(0, self.reading("Exalted Orb", 6))
        self.assertEqual(self.pages("M0001"), [])
        self.poll()
        self.finish(1, self.reading("Divine Orb", 6))
        self.assertEqual(self.pages("M0001"), ["Divine Orb"])
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")

    def test_pages_on_the_current_map_are_still_dropped_with_the_message(self):
        self.capture(1)
        self.window.finish_map()
        self.app.processEvents()
        self.capture(2)
        with patch.object(ui.QMessageBox, "question", return_value=ui.QMessageBox.StandardButton.Yes):
            self.window.reset_current_map()
        self.app.processEvents()
        self.assertEqual(self.message(), "M0002 reset. Undo restores its saved logs. 1 unsaved Ritual page was "
                                         "not saved because the saved logs changed; scan again. 1 Ritual page "
                                         "taken on M0001 is kept and will be saved to M0001.")
        self.poll()
        self.finish(1, self.reading("Divine Orb", 6))
        self.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual((self.pages("M0001"), self.pages("M0002")), (["Divine Orb"], []))


class RitualPagesDroppedByUndoTests(RitualScanHarness):
    """Undo or Reset map drops unsaved Ritual pages, and says so."""

    def test_undo_drops_a_held_page_and_says_so(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.capture(2)
        self.finish(1, self.reading("Exalted Orb", 5, needs_review=True))
        self.window.undo_map()
        self.app.processEvents()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.pages(), [])
        self.assertIn("1 unsaved Ritual page was not saved because the saved logs changed; scan again.",
                      self.message())

    def test_undo_drops_reading_and_waiting_pages_and_ignores_the_late_reading(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.capture(2)
        self.capture(3)
        self.window.undo_map()
        self.app.processEvents()
        self.assertIn("2 unsaved Ritual pages were not saved because the saved logs changed; scan again.",
                      self.message())
        self.finish(1, self.reading("Exalted Orb", 5))
        self.poll()
        self.assertEqual(len(self.futures), 2)
        self.assertEqual(self.pages(), [])
        self.assertEqual(self.window._ritual_waiting, [])

    def test_undo_with_nothing_unsaved_says_nothing_extra(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6))
        self.window.undo_map()
        self.app.processEvents()
        self.assertEqual(self.message(), "Ritual approval undone. Saved totals and chain state restored.")

    def test_reset_map_drops_a_held_page_and_says_so(self):
        self.capture(1)
        self.finish(0, self.reading("Divine Orb", 6, needs_review=True))
        with patch.object(ui.QMessageBox, "question", return_value=ui.QMessageBox.StandardButton.Yes):
            self.window.reset_current_map()
        self.app.processEvents()
        self.assertEqual(self.message(), "M0001 reset. Undo restores its saved logs. 1 unsaved Ritual page was "
                                         "not saved because the saved logs changed; scan again.")
        self.assertIsNone(self.window.pending_review_kind)


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
