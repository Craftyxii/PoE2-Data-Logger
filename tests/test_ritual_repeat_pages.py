"""A Ritual scan with the same roll count as the map's previous saved page repeats it and is dropped."""

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
    """Only a rerolled page is new; the drop is silent and needs no input."""

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
        self.window._submit_scan = lambda label, request, done, *args: self.jobs.append(done)
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

    def scan(self, rerolls, *, clear=True):
        """Capture a page as the hotkey does and deliver its reading; return the saved commit count."""
        self.window._ritual_captured(Image.new("RGB", (64, 64), (len(self.jobs), 0, 0)), live=True)
        reward = ({"category": "Omen", "name": "Omen of Whittling", "quantity": 1, "tribute": 300,
                   "source": "Ritual offer", "deferred": False, "score": .99, "name_match": 1} if clear else
                  {"category": "Item", "name": "", "quantity": 1, "tribute": 0, "deferred": False,
                   "unresolved": True, "needs_review": True, "score": 0})
        self.jobs[-1]({"items": [reward], "raw_text": "Ritual evidence", "unmatched": [], "reward_count": 1,
                       "tribute_available": 5430, "rerolls_remaining": rerolls})
        self.app.processEvents()
        return logger.get_state()["scan_commit_count"]

    def windows(self):
        """Return the visible top-level windows, to prove the drop opens none."""
        return {widget for widget in QApplication.topLevelWidgets() if widget.isVisible()}

    def test_unchanged_roll_count_drops_the_repeat_without_review_message_or_window(self):
        self.assertEqual(self.scan(9), 1)
        windows = self.windows()
        self.assertEqual(self.scan(9), 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertFalse(self.window.ritual_review_group.isVisible())
        self.assertEqual((self.window.review_summary.text(), self.window.ritual_status.text()), ("", ""))
        self.assertEqual(self.windows(), windows)
        # A reroll leaves one roll fewer: that page is new and saves itself.
        self.assertEqual(self.scan(8), 2)
        self.assertEqual(self.scan(8), 2)
        self.assertEqual([page["page_number"] for page in logger.ritual_pages_for_map("M0001")], [1, 2])

    def test_each_map_compares_only_with_its_own_previous_page(self):
        self.assertEqual(self.scan(9), 1)
        self.window.finish_map()
        self.app.processEvents()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertIsNone(logger.last_ritual_rerolls("M0002"))
        saved = logger.get_state()["scan_commit_count"]
        self.assertEqual(self.scan(9), saved + 1)
        self.assertEqual(len(logger.ritual_pages_for_map("M0002")), 1)
        self.assertEqual(logger.last_ritual_rerolls("M0001"), 9)

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
