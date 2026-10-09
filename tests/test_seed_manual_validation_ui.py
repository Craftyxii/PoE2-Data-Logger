"""QTest corrections must keep both-view seed matching and saved audits consistent."""

import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class SeedManualValidationUITests(unittest.TestCase):
    """Exercise visible correction fields and Approve against isolated SQLite state."""

    @classmethod
    def setUpClass(cls):
        """Create the shared offscreen application used by actual Qt input events."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Start a shown logger with automatic saves disabled and a fresh map."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-seed-manual-validation-")
        self.previous_mode = service.HOTKEY.status()["mode"]
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.save_settings({"auto_commit": False, "ocr_auto_commit": False})
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1366, 900)
        self.window.show()
        self.app.processEvents()
        self.choose_mode("both")
        output = io.BytesIO()
        Image.new("RGB", (600, 400), "gray").save(output, format="PNG")
        self.image = output.getvalue()

    def tearDown(self):
        """Close the window and restore the caller's scan mode and data directory."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def stage(self, family):
        """Read a genuine three-socket catalog stage for scanner-boundary fixtures."""
        with logger._connect() as db:
            row = db.execute("SELECT * FROM seed_states WHERE family=? AND sockets=3",
                             (family,)).fetchone()
        return {"sockets": row["sockets"], "seed_slot": row["seed_slot"],
                "seed_rune": row["seed_rune"], "family": f"Family {family}",
                "candidates": [family], "can_commit": True,
                "rewards": json.loads(row["rewards_json"])}

    def opened(self, family=49):
        """Build an opened completion from that stage's ordered catalog rewards."""
        stage_rewards = self.stage(family)["rewards"]
        with logger._connect() as db:
            family_rewards = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=?",
                                                  (family,)).fetchone()[0])
        rewards = [reward for reward in family_rewards if reward in stage_rewards]
        return {"mode": "opened", "family": f"Family {family}", "candidates": [family],
                "sockets": 3, "can_use": True, "status": "Review opened rewards.",
                "first_recipe": rewards[0], "next_recipe": rewards[1] if len(rewards) > 1 else None,
                "opened_recipes": [{"recipe": reward} for reward in rewards]}

    def deliver(self, mode, result):
        """Inject only scanner completion; review and persistence run normally."""
        self.window._scan_done(mode, {**copy.deepcopy(result), **logger.scan_context(), "mode": mode},
                               self.image)
        self.app.processEvents()

    def reach(self, widget):
        """Use the owning sidebar page button and expose the actual correction field."""
        for index in range(self.window.tabs.count()):
            page = self.window.tabs.widget(index)
            if page.isAncestorOf(widget):
                nav = next(button for position, button in enumerate(self.window.nav_buttons)
                                  if (position if button.property("page_index") is None else button.property("page_index")) == index)
                self.assertTrue(nav.isVisible())
                QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                if isinstance(page, QScrollArea):
                    page.ensureWidgetVisible(widget, 20, 20)
                break
        self.app.processEvents()
        self.assertTrue(widget.isVisible())
        hit = self.window.childAt(widget.mapTo(self.window, widget.rect().center()))
        self.assertTrue(hit is widget or widget.isAncestorOf(hit))

    def choose_mode(self, mode):
        """Choose a remnant mode with keyboard events on the visible combo box."""
        widget = self.window.mode_select
        self.reach(widget)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        QTest.keyClick(widget, Qt.Key.Key_Home)
        for _ in range(widget.findData(mode)):
            QTest.keyClick(widget, Qt.Key.Key_Down)
        self.app.processEvents()
        self.assertEqual(widget.currentData(), mode)

    def edit(self, widget, text):
        """Replace a visible field with keyboard input that emits textEdited."""
        self.reach(widget)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(widget, text)
        self.app.processEvents()
        self.assertEqual(widget.text(), text)

    def approve(self):
        """Try the visible approval control twice to expose duplicate-save errors."""
        self.reach(self.window.approve_scan_button)
        for _ in range(2):
            QTest.mouseClick(self.window.approve_scan_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def records(self):
        """Snapshot persistence tables to detect any partial or incorrect write."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits", "scan_links")}

    def seed_review(self):
        """Load a single Family 49 seed into the actual visible review fields."""
        self.deliver("seed", {"status": "Visible seed", "remnants": [self.stage(49)]})

    def test_invalid_slot_correction_cannot_link_or_commit_opened_family(self):
        """A typed P9 in a three-socket seed must block linking and all writes."""
        self.seed_review()
        self.edit(self.window.seed_slot, "P9")
        self.deliver("opened", self.opened())
        before = self.records()
        self.approve()
        self.assertEqual(self.records(), before)
        self.assertEqual(self.window._seed_readings[0]["candidates"], [])
        self.assertIsNone(self.window._both_link)

    def test_different_family_correction_cannot_use_original_candidates(self):
        """Changing Arcane/P2 to Earth/P1 must stop matching old Family 49."""
        self.seed_review()
        other = self.stage(61)
        self.edit(self.window.seed_slot, other["seed_slot"])
        self.edit(self.window.seed_rune, other["seed_rune"])
        self.deliver("opened", self.opened())
        before = self.records()
        self.approve()
        self.assertEqual(self.records(), before)
        self.assertEqual(self.window._seed_readings[0]["candidates"], [61])
        self.assertIsNone(self.window._both_link)

    def test_corrected_socket_count_cannot_match_original_opened_count(self):
        """Changing a seed to four sockets must block its three-socket opened view."""
        self.seed_review()
        self.edit(self.window.seed_sockets, "4")
        self.deliver("opened", self.opened())
        before = self.records()
        self.approve()
        self.assertEqual(self.records(), before)
        self.assertIsNone(self.window._both_link)

    def test_correction_after_reopening_seed_review_invalidates_link(self):
        """Reopening and correcting a matched seed must disable opened approval."""
        self.seed_review()
        self.deliver("opened", self.opened())
        self.assertEqual(self.window._both_link, 0)
        self.choose_mode("seed")
        self.edit(self.window.seed_slot, "P9")
        self.choose_mode("both")
        self.assertIsNone(self.window._both_link)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        before = self.records()
        self.approve()
        self.assertEqual(self.records(), before)

    def test_valid_correction_can_commit_matching_new_family_once(self):
        """Corrected Earth/P1 can save Family 61 with matching audit identity."""
        self.seed_review()
        other = self.stage(61)
        self.edit(self.window.seed_slot, other["seed_slot"])
        self.edit(self.window.seed_rune, other["seed_rune"])
        self.deliver("opened", self.opened(61))
        self.approve()
        with logger._connect() as db:
            commits = db.execute("SELECT * FROM commits WHERE kind='Remnant'").fetchall()
            exports = db.execute("SELECT row_json FROM new_export WHERE remnant_id='R0001'").fetchall()
        self.assertEqual(len(commits), 1)
        self.assertEqual((commits[0]["reference"], commits[0]["map_id"], commits[0]["expedition_id"]),
                         ("R0001", "M0001", "M0001-E01"))
        details = json.loads(commits[0]["details_json"])
        self.assertEqual(details["family"], 61)
        self.assertEqual(details["visible_seed"],
                         {"sockets": 3, "slot": other["seed_slot"], "rune": other["seed_rune"], "mode": "both"})
        self.assertEqual([json.loads(row[0])[1] for row in exports],
                         [row["recipe"] for row in details["recipes"]])
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_persistence_rejects_invalid_linked_seed_atomically(self):
        """Reject invalid slots and conflicting families before any transactional writes."""
        opened = self.opened()
        for slot, rune in (("P9", "Arcane"), ("P1", "Earth")):
            with self.subTest(slot=slot, rune=rune):
                before = self.records()
                with self.assertRaisesRegex(ValueError, "seed|socket"):
                    logger.commit_remnant(opened["first_recipe"], opened["next_recipe"], 49,
                                          visible_seed={"sockets": 3, "slot": slot, "rune": rune, "mode": "both"})
                self.assertEqual(self.records(), before)


if __name__ == "__main__":
    unittest.main()
