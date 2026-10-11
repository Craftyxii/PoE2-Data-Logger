"""Exercise malformed numeric OCR at the real desktop-to-SQL automatic-save boundary."""

import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_text
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class NumericTokenAutosaveUITests(unittest.TestCase):
    """Show malformed high-confidence readings for review while leaving stored stats intact."""

    bad_rolls = (".5%", ".05%", "-.5%", "−5%", "–5%", "++5%", "—5%",
                 ",5%", "－5%", "＋5%", "- 5%", "5%%", ". 5%", ", 5%")

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application across independently stored test windows."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated visible logger with both existing auto-save settings enabled."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-numeric-autosave-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.set_auto_all(True)
        self.window.show()
        output = io.BytesIO()
        Image.new("RGB", (500, 400), "tan").save(output, format="PNG")
        self.raw = output.getvalue()
        self.app.processEvents()

    def tearDown(self):
        """Close workers before restoring the caller's data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def stored_snapshot(self):
        """Read the actual persisted settings and commit rows, independent of UI drafts."""
        with logger._connect() as db:
            return (db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0],
                    [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")])

    def reading(self, kind, roll, source):
        """Build captured/copied item text through the production parser and freeze its context."""
        if kind == "tablet":
            lines = ["Precursor Tablet", "Uses Remaining: 10", f"{roll} increased Effectiveness"]
            copied = ["Item Class: Tablets", *lines]
        else:
            lines = ["Waystone (Tier 15)", f"Waystone Drop Chance: {roll}",
                     "30% increased Rarity of Items found in this Area"]
            copied = ["Item Class: Waystones", *lines[:2], "Item Level: 82", lines[-1]]
        if source == "screen OCR":
            result = item_text.read_screen_tooltip(
                Image.new("RGB", (500, 400)), self.window.state["affixes"],
                ocr_rows=[{"text": text, "score": 1} for text in lines], strictness=0)
        else:
            result = item_text.parse_item_text("\n".join(copied), self.window.state["affixes"])
            result["source"] = "clipboard"
        return {**result, **logger.scan_context()}

    def test_tablet_malformed_tokens_do_not_persist_or_advance_slot(self):
        """Both capture sources remain reviewable without SQL changes; valid decimals still auto-save."""
        baseline = self.stored_snapshot()
        for source in ("clipboard", "screen OCR"):
            for roll in self.bad_rolls:
                with self.subTest(source=source, roll=roll):
                    self.window._hover_item_read(self.reading("tablet", roll, source), self.raw)
                    self.app.processEvents()
                    self.assertEqual(self.window.pending_review_kind, "tablet")
                    self.assertIn(roll, self.window.tablet_scan_status.text())
                    self.assertEqual(self.window.tablet_values[0].text(), "")
                    self.assertEqual(logger.tablet_next_slot(), 1)
                    self.assertEqual(self.stored_snapshot(), baseline)
                    self.assertFalse(self.window.preview.pixmap().isNull())
        self.window._hover_item_read(self.reading("tablet", "0.5%", "screen OCR"), self.raw)
        self.assertEqual(logger.tablet_next_slot(), 2)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"], .5)
        self.assertEqual(len(self.stored_snapshot()[1]), len(baseline[1]) + 1)

    def test_waystone_malformed_tokens_do_not_persist_despite_auto_commit(self):
        """Malformed drop chances stay blank/reviewable; a valid decimal reaches the map commit."""
        baseline = self.stored_snapshot()
        for source in ("clipboard", "screen OCR"):
            for roll in self.bad_rolls:
                with self.subTest(source=source, roll=roll):
                    self.window._hover_item_read(self.reading("waystone", roll, source), self.raw)
                    self.app.processEvents()
                    self.assertEqual(self.window.pending_review_kind, "waystone")
                    self.assertEqual(self.window.waystone.text(), "")
                    self.assertIn("held", self.window.map_scan_status.text())
                    self.assertEqual(self.stored_snapshot(), baseline)
                    self.assertFalse(self.window.preview.pixmap().isNull())
        self.window._hover_item_read(self.reading("waystone", "10,5%", "screen OCR"), self.raw)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["settings"]["waystone"], 10.5)
        self.assertEqual(len(self.stored_snapshot()[1]), len(baseline[1]) + 1)


if __name__ == "__main__":
    unittest.main()
