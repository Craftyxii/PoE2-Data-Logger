"""The roll count is the Ritual tracker: a scan with the same rolls left as the map's last saved
page repeats it and is dropped with a confirmation. The rule resets on every map."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class RitualRepeatPageTests(unittest.TestCase):
    """Only a rerolled page is new; a repeat is confirmed and needs no input."""

    @classmethod
    def setUpClass(cls):
        """Reuse or create the QApplication required by the desktop window."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated window that saves clear Ritual pages automatically."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ritual-repeat-")
        self.original_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        # Keep the real scan wrapper so failures reach the review as the user sees them.
        self.window._submit = lambda label, function, done=None: self.jobs.append(done)
        self.window.set_auto_all(True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close desktop workers, restore logger storage and remove temporary data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.original_data_dir
        logger._READY = False
        self.tmp.cleanup()

    def capture(self):
        """Capture a page as the hotkey does; return the callback that delivers its reading."""
        self.window._ritual_captured(Image.new("RGB", (64, 64), (len(self.jobs), 0, 0)), live=True)
        return self.jobs[-1]

    def reading(self, rerolls, clear=True):
        """Build a recognized page with one reward and the given rolls left."""
        reward = ({"category": "Omen", "name": "Omen of Whittling", "quantity": 1, "tribute": 300,
                   "source": "Ritual offer", "deferred": False, "score": .99, "name_match": 1} if clear else
                  {"category": "Item", "name": "", "quantity": 1, "tribute": 0, "deferred": False,
                   "unresolved": True, "needs_review": True, "score": 0})
        return {"items": [reward], "raw_text": "Ritual evidence", "unmatched": [], "reward_count": 1,
                "tribute_available": 5430, "rerolls_remaining": rerolls}

    def scan(self, rerolls, *, clear=True):
        """Capture and read a page; return the saved commit count."""
        self.capture()(self.reading(rerolls, clear))
        self.app.processEvents()
        return logger.get_state()["scan_commit_count"]

    def new_map(self):
        """Press + New map."""
        self.window.finish_map()
        self.app.processEvents()

    def windows(self):
        """Return the visible top-level windows, to prove the drop opens none."""
        return {widget for widget in QApplication.topLevelWidgets() if widget.isVisible()}

    def messages(self):
        """Return the review panel, Ritual and status-bar messages."""
        return (self.window.review_summary.text(), self.window.ritual_status.text(),
                self.window.statusBar().currentMessage())

    def test_unchanged_roll_count_drops_the_repeat_and_confirms_it_without_review_or_window(self):
        self.assertEqual(self.scan(9), 1)
        windows = self.windows()
        self.assertEqual(self.scan(9), 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertFalse(self.window.ritual_review_group.isVisible())
        confirmation = "✓ Already saved: M0001 page 1 had 9 rolls left (Scan Commit #1)."
        self.assertEqual(self.messages(), (confirmation,) * 3)
        self.assertEqual(self.window.review_summary.property("role"), "success")
        self.assertEqual(self.windows(), windows)
        # A reroll leaves one roll fewer: that page is new and saves itself.
        self.assertEqual(self.scan(8), 2)
        self.assertEqual(self.scan(8), 2)
        self.assertEqual(self.window.review_summary.text(),
                         "✓ Already saved: M0001 page 2 had 8 rolls left (Scan Commit #2).")
        self.assertEqual([page["page_number"] for page in logger.ritual_pages_for_map("M0001")], [1, 2])
        self.assertEqual(self.scan(1), 3)
        self.assertEqual(self.scan(1), 3)
        self.assertIn("had 1 roll left", self.window.review_summary.text())

    def test_new_map_resets_the_rule_and_locks_the_previous_map(self):
        self.assertEqual(self.scan(9), 1)
        before = logger.ritual_pages_for_map("M0001")
        self.new_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertIsNone(logger.last_ritual_rerolls("M0002"))
        saved = logger.get_state()["scan_commit_count"]
        # The previous map's last page also had 9 rolls left; this map has none saved yet.
        self.assertEqual(self.scan(9), saved + 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0002")), 1)
        self.assertEqual(logger.ritual_pages_for_map("M0001"), before)
        self.assertEqual(logger.last_ritual_page("M0001")["scan_commit_number"], 1)
        # Within the new map the rule still holds.
        self.assertEqual(self.scan(9), saved + 1)
        self.assertIn("M0002 page 1 had 9 rolls left", self.window.review_summary.text())
        with self.assertRaisesRegex(ValueError, "belongs to M0001"):
            logger.save_ritual_page([], tribute_available=1, rerolls_remaining=3, expected_map_id="M0001")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), before)

    def test_a_scan_captured_before_new_map_is_never_saved_to_either_map(self):
        self.assertEqual(self.scan(9), 1)
        deliver = self.capture()
        self.new_map()
        commits = logger.get_state()["scan_commit_count"]
        deliver(self.reading(8))
        self.app.processEvents()
        self.assertEqual(logger.get_state()["scan_commit_count"], commits)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])

    def test_a_map_change_during_the_scan_asks_to_scan_again(self):
        self.assertEqual(self.scan(9), 1)
        deliver = self.capture()
        logger.finish_map(None, None, None)
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "Scan the current map again"):
            deliver(self.reading(9))
        self.app.processEvents()
        self.assertIn("Scan again", self.window.review_summary.text())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])

    def test_a_finished_map_is_never_compared_or_saved_until_new_map(self):
        self.assertEqual(self.scan(9), 1)
        logger.finish_map(None, None, None)
        self.window.refresh()
        commits = logger.get_state()["scan_commit_count"]
        deliver = self.capture()
        # The same rolls as the finished map's page must not be dropped as its repeat.
        with self.assertRaisesRegex(ValueError, r"M0001 is finished; press \+ New map"):
            deliver(self.reading(9))
        self.app.processEvents()
        self.assertIn("+ New map", self.window.review_summary.text())
        self.assertNotIn("Already saved", self.window.review_summary.text())
        self.assertEqual(logger.get_state()["scan_commit_count"], commits)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.window.reject_review()
        self.new_map()
        saved = logger.get_state()["scan_commit_count"]
        self.assertEqual(self.scan(9), saved + 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0002")), 1)

    def test_an_unread_roll_count_never_drops_a_page(self):
        self.assertEqual(self.scan(9), 1)
        self.assertEqual(self.scan(None), 2)
        # The previous page's count is unknown, so nothing can be shown to repeat it.
        self.assertEqual(self.scan(9), 3)

    def test_only_saved_pages_count_and_undo_reopens_the_count(self):
        self.assertEqual(self.scan(9, clear=False), 0)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        # The held page was never saved, so scanning it again replaces the review.
        self.assertEqual(self.scan(9, clear=False), 0)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.window.reject_review()
        self.assertEqual(self.scan(9), 1)
        self.assertEqual(self.scan(9), 1)
        logger.undo_last_action()
        self.window.refresh()
        self.assertIsNone(logger.last_ritual_rerolls("M0001"))
        self.assertEqual(self.scan(9), 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)


if __name__ == "__main__":
    unittest.main()
