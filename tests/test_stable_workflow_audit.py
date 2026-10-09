"""Release audit of combined workflows using visible Qt controls and real captures.

Only scanner completion is delivered directly; seed/opened outputs come from
the checked-in game images. Tablet text is an explicitly synthetic clipboard
reading. Database calls below inspect outcomes and do not perform user actions.
"""
import copy
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QScrollArea

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ocr import opened_scan, scan
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


ROOT = Path(__file__).resolve().parents[1]


class StableWorkflowAudit(unittest.TestCase):
    """Exercise mode changes, replacement map IDs and tablet capacity recovery."""

    @classmethod
    def setUpClass(cls):
        """Read the two real game captures once against an untrained catalog."""
        cls.app = QApplication.instance() or QApplication([])
        cls.fixtures = tempfile.TemporaryDirectory(prefix="poe2-workflow-fixtures-")
        previous = store.DATA_DIR
        try:
            store.DATA_DIR = Path(cls.fixtures.name)
            logger._READY = False
            logger.initialize()
            cls.seed_path = ROOT / "PoE2_Data_Logger/region_examples/seed.jpg"
            cls.opened_path = ROOT / "PoE2_Data_Logger/region_examples/opened.jpg"
            cls.seed = scan.scan(cls.seed_path)
            with Image.open(cls.opened_path) as image:
                cls.opened = opened_scan.scan_opened(image.convert("RGB"))
        finally:
            store.DATA_DIR = previous
            logger._READY = False

    @classmethod
    def tearDownClass(cls):
        """Remove the temporary untrained recognition database."""
        cls.fixtures.cleanup()

    def setUp(self):
        """Open an isolated shown window without background polling."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-stable-workflow-")
        self.previous = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.w = LoggerWindow()
        self.w._poll.stop()
        self.w.resize(1600, 1000)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        """Close worker threads and restore shared logger and hotkey state."""
        self.w.close()
        self.w.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def reach(self, widget):
        """Navigate to and scroll a real control into an unobstructed hit target."""
        for index in range(self.w.tabs.count()):
            page = self.w.tabs.widget(index)
            if page.isAncestorOf(widget):
                QTest.mouseClick(next(button for position, button in enumerate(self.w.nav_buttons)
                                  if (position if button.property("page_index") is None else button.property("page_index")) == index), Qt.LeftButton)
                self.app.processEvents()
                if isinstance(page, QScrollArea):
                    page.ensureWidgetVisible(widget, 20, 20)
                break
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(widget, 20, 20)
                self.app.processEvents()
            ancestor = ancestor.parentWidget()
        self.app.processEvents()
        self.assertTrue(widget.isVisible())
        hit = self.w.childAt(widget.mapTo(self.w, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit), widget.objectName())

    def click(self, widget):
        """Activate an enabled exposed control using a mouse event."""
        self.reach(widget)
        self.assertTrue(widget.isEnabled())
        QTest.mouseClick(widget, Qt.LeftButton)
        self.app.processEvents()

    def named(self, text):
        """Find an action by its label, preferring the currently visible duplicate."""
        matches = [b for b in self.w.findChildren(QPushButton) if b.text() == text]
        if len(matches) > 1:
            matches = [b for b in matches if b.isVisible()]
        self.assertEqual(len(matches), 1, text)
        return matches[0]

    def choose(self, widget, wanted):
        """Choose combo data through keyboard input rather than signal shortcuts."""
        self.click(widget)
        QTest.keyClick(widget, Qt.Key_Escape)
        index = widget.findData(wanted)
        self.assertGreaterEqual(index, 0, wanted)
        QTest.keyClick(widget, Qt.Key_Home)
        for _ in range(index):
            QTest.keyClick(widget, Qt.Key_Down)
        QTest.keyClick(widget, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(widget.currentData(), wanted)

    def edit(self, widget, text):
        """Replace a field using keyboard input that emits textEdited."""
        self.click(widget)
        QTest.keyClick(widget, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(widget, Qt.Key_Backspace)
        QTest.keyClicks(widget, text)
        self.app.processEvents()
        self.assertEqual(widget.text(), text)

    def deliver(self, mode):
        """Deliver real OCR output at scanner completion with its current context."""
        result = copy.deepcopy(self.seed if mode == "seed" else self.opened)
        result.update(logger.scan_context())
        result["mode"] = mode
        path = self.seed_path if mode == "seed" else self.opened_path
        self.w._scan_done(mode, result, path.read_bytes())
        self.app.processEvents()

    def seed_row(self, row):
        """Select a seed by clicking its visible table cell."""
        table = self.w.seed_table
        self.reach(table)
        item = table.item(row, 1)
        table.scrollToItem(item)
        self.app.processEvents()
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(item).center())
        self.app.processEvents()
        self.assertEqual(table.currentRow(), row)

    def test_real_seed_opened_and_both_modes_use_separate_remnant_ids(self):
        """Three modes share actual Family 49 artwork while Both saves one linked ID."""
        self.choose(self.w.mode_select, "seed")
        self.deliver("seed")
        self.seed_row(2)  # Actual Arcane/P2 three-socket seed.
        self.click(self.w.approve_scan_button)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        self.assertFalse(self.w.approve_scan_button.isEnabled())
        for row in (0, 1, 3):
            self.seed_row(row)
            self.click(self.w.reject_scan_button)
        self.assertIsNone(logger.get_state()["ocr_pending"])

        self.choose(self.w.mode_select, "opened")
        self.deliver("opened")
        self.click(self.w.approve_scan_button)
        self.choose(self.w.mode_select, "both")
        self.deliver("seed")
        self.seed_row(2)
        self.deliver("opened")
        self.click(self.w.approve_scan_button)
        self.assertFalse(self.w.approve_scan_button.isEnabled())
        with logger._connect() as db:
            commits = [dict(r) for r in db.execute("SELECT * FROM commits ORDER BY number")]
        self.assertEqual([r["reference"] for r in commits], ["R0001", "R0002", "R0003"])
        self.assertEqual([r["expedition_id"] for r in commits], ["M0001-E01"] * 3)
        self.assertEqual([json.loads(r["details_json"])["family"] for r in commits], [49] * 3)
        self.assertEqual(json.loads(commits[-1]["details_json"])["visible_seed"],
                         {"sockets": 3, "slot": "P2", "rune": "Arcane", "mode": "both"})
        self.assertEqual(logger.get_state()["scan_commit_count"], 3)

    def test_one_tablet_capacity_recovers_four_slots_and_clear_resets_sequence(self):
        """Synthetic clipboard scans expand capacity and never overwrite a fifth slot."""
        self.choose(self.w.tablets_used, 1)
        self.click(self.named("Save tablet config"))
        for number in range(1, 5):
            self.w._hover_item_read({"kind": "tablet", "source": "clipboard",
                "mods": [f"{number * 10}% increased Pack Size in Map"],
                "matches": [], "uncertain": [], **logger.scan_context()})
            self.app.processEvents()
            self.assertEqual(self.w.tablets_used.currentData(), number)
            self.click(self.w.approve_scan_button)
            self.assertEqual(logger.tablet_next_slot(), number + 1)
        saved = copy.deepcopy(logger.get_state()["settings"])
        self.w._hover_item_read({"kind": "tablet", "source": "clipboard",
            "mods": ["99% increased Pack Size in Map"], "matches": [], "uncertain": [],
            **logger.scan_context()})
        self.app.processEvents()
        self.assertFalse(self.w.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["settings"], saved)
        self.click(self.named("Clear tablet config"))
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertIsNone(self.w.pending_review_kind)
        self.assertTrue(all(not item["affix"] for item in logger.get_state()["settings"]["tablet_affixes"]))

    def test_new_map_and_undo_restore_saved_kills_and_retire_unsaved_scan(self):
        """Header map transitions preserve prior kills and clear a held real seed review."""
        for widget, text in ((self.w.normal, "123"), (self.w.magic, "9"),
                             (self.w.rare, "2"), (self.w.unique, "1")):
            self.edit(widget, text)
        self.choose(self.w.mode_select, "seed")
        self.deliver("seed")
        self.click(self.named("+ New map"))
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual([w.text() for w in (self.w.normal, self.w.magic, self.w.rare, self.w.unique)], [""] * 4)
        self.assertIsNone(self.w.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(self.w.seed_table.rowCount(), 0)
        self.click(self.named("Undo new map"))
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual([w.text() for w in (self.w.normal, self.w.magic, self.w.rare, self.w.unique)],
                         ["123", "9", "2", "1"])
        self.assertIsNone(self.w.pending_review_kind)
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        totals = [r for r in rows if r.get("Type") == "Map totals"]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["Normal Kills (Map)"], "123")
        self.assertEqual(totals[0]["Unique Kills (Map)"], "1")

    def test_delayed_file_result_cannot_attach_to_recreated_empty_map_id(self):
        """A retired file callback stays rejected even when undo reuses its captured map ID."""
        self.click(self.named("+ New map"))
        jobs = []
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.seed_path), "Images")), \
                patch.object(self.w, "_submit", side_effect=lambda label, work, done: jobs.append((work, done))):
            self.click(self.named("Add remnant seed screenshot…"))
        self.assertEqual(len(jobs), 1)
        result = copy.deepcopy(self.seed)
        result.update(logger.scan_context())
        result["mode"] = "seed"
        self.click(self.named("Undo new map"))
        self.click(self.named("+ New map"))
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        count = logger.get_state()["scan_commit_count"]
        jobs[0][1](result)
        self.app.processEvents()
        self.assertEqual(logger.get_state()["scan_commit_count"], count)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertIsNone(self.w.pending_review_kind)
        self.assertFalse(self.w.approve_scan_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
