"""Keep Ritual row decisions local until the existing whole-page transaction.

Recognition evidence is injected only to isolate review policy. These tests do
not measure OCR accuracy; real-capture recognition has separate regressions.
"""

import copy
import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_ritual_reward


class RitualVerifiedIdentityTests(unittest.TestCase):
    """Accepted matcher evidence cannot override a different unresolved field."""

    def reward(self, **changes):
        """Model an independently accepted identity with a weighted-art score."""
        return {"category": "Item", "name": "Chaos Orb", "quantity": 2,
                "tribute": None, "deferred": False, "score": .82,
                "identity_verified": True, **changes}

    def test_verified_item_and_registered_omen_do_not_repeat_the_raw_score_gate(self):
        """A verified weighted icon need not also pass an unrelated .94 floor."""
        self.assertTrue(clear_ritual_reward(self.reward(), []))
        omen = "Omen of Refreshment"
        self.assertTrue(clear_ritual_reward(self.reward(category="Omen", name=omen,
                                                      name_match=1), [omen]))
        for marker in (None, False, 1, "true"):
            self.assertFalse(clear_ritual_reward(self.reward(identity_verified=marker), []))

    def test_verified_identity_preserves_other_review_and_value_guards(self):
        """Naming evidence never approves counts, deferred state or invalid data."""
        changes = ({"name": ""}, {"name": "Unidentified reward"},
                   {"quantity": 0}, {"quantity": True}, {"quantity": 1.5},
                   {"tribute": -1}, {"deferred": None}, {"deferred": "false"},
                   {"needs_review": True}, {"unresolved": True},
                   {"name_needs_review": True}, {"count_needs_review": True},
                   {"score": float("nan")}, {"score": float("inf")},
                   {"score": -.01}, {"score": 1.01}, {"score": "invalid"},
                   {"category": "Currency"}, {"source": "Legacy x2 reading"})
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(clear_ritual_reward(self.reward(**change), []))
        self.assertFalse(clear_ritual_reward(self.reward(), [], 100))
        self.assertFalse(clear_ritual_reward(self.reward(category="Omen", name="Unknown Omen",
                                                       name_match=1), []))
        self.assertFalse(clear_ritual_reward(self.reward(category="Omen", name="Known Omen",
                                                       name_match=.5), ["Known Omen"]))


class RitualRowActionTests(unittest.TestCase):
    """Exercise existing Review-column actions without partial logging or learning."""

    @classmethod
    def setUpClass(cls):
        """Reuse Qt while each test owns an isolated database and window."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Paint distinct captured cells so accepted corrections can teach real crops."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-ritual-row-actions-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.image = Image.new("RGB", (500, 250), (15, 14, 32))
        draw = ImageDraw.Draw(self.image)
        for index in range(5):
            left = 30 + index * 65
            draw.rectangle((left, 40, left + 39, 79), fill=(180, 25 + index * 20, 50))
            draw.ellipse((left + 5, 44, left + 30, 74), fill=(40, 160, 210))

    def tearDown(self):
        """Stop workers before restoring storage and removing the disposable profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.directory.cleanup()

    def reward(self, index=0, **changes):
        """Retain original slot/crop evidence on each controlled reward."""
        left = 30 + index * 65
        return {"category": "Item", "name": "Chaos Orb", "quantity": 1,
                "tribute": None, "deferred": False, "score": 1,
                "source": "Captured reward", "grid_slots": [index + 1],
                "box": (left, 40, left + 40, 80), **changes}

    def read(self, items, strictness=50, *, include_strictness=True, live=False):
        """Deliver a captured result through the normal pending-review callback."""
        self.window._ritual_captured(self.image.copy(), live=live,
                                     expected_strictness=strictness)
        # Each page saved on the map took one reroll, so a new page has one fewer.
        rerolls = 3 - len(logger.ritual_pages_for_map(logger.get_state()["current_map_id"]))
        result = {"items": copy.deepcopy(items), "raw_text": "Ritual evidence",
                  "unmatched": [], "tribute_available": 5430, "rerolls_remaining": rerolls,
                  "grid_detected": True, "grid_reward_count": len(items)}
        if strictness != 50 and include_strictness:
            result["_ocr_strictness"] = strictness
        self.jobs[-1][1](result)

    def controls(self, row):
        """Require actions to stay inside the existing seventh Review column."""
        self.assertEqual(self.window.ritual_table.columnCount(), 7)
        controls = self.window.ritual_table.cellWidget(row, 6)
        self.assertIsNotNone(controls)
        return controls

    def approve(self, row):
        """Use the same row action a user clicks and verify its accepted state."""
        controls = self.controls(row)
        controls.findChild(QPushButton, "approveRitual").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")

    def test_mixed_page_commits_known_and_approved_only_and_learns_after_final_save(self):
        """Pending/rejected guesses never register names or teach captured artwork."""
        self.read([self.reward(), self.reward(1, name="", name_needs_review=True,
                                             unresolved=True, needs_review=True),
                   self.reward(2, name="Rejected Ritual gear", needs_review=True),
                   self.reward(3, name="Pending Ritual gear", needs_review=True)])
        table = self.window.ritual_table
        self.assertTrue(table.isRowHidden(0))
        self.assertTrue(all(not table.isRowHidden(row) for row in (1, 2, 3)))
        table.item(1, 1).setText("Reviewed Ritual armour")
        table.item(1, 2).setText("3")
        self.approve(1)
        self.controls(2).findChild(QPushButton, "rejectRitual").click()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.review_icons(), [])
        self.assertNotIn("Reviewed Ritual armour", logger.item_names())
        self.assertFalse(table.isRowHidden(1))
        self.window.approve_review()
        items = logger.ritual_pages_for_map("M0001")[0]["items"]
        self.assertEqual([(item["name"], item["quantity"]) for item in items],
                         [("Chaos Orb", 1), ("Reviewed Ritual armour", 3)])
        self.assertEqual([(ref["name"], ref["kind"]) for ref in logger.review_icons()],
                         [("Reviewed Ritual armour", "item")])
        self.assertNotIn("Rejected Ritual gear", logger.item_names())
        self.assertNotIn("Pending Ritual gear", logger.item_names())
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)
        for row in range(4):
            self.assertFalse(self.controls(row).isEnabled())
        self.assertIn("Saved", table.item(1, 6).text())
        self.assertIn("Rejected", table.item(2, 6).text())
        self.assertIn("Rejected", table.item(3, 6).text())

    def test_row_approval_validates_required_values_without_writing(self):
        """Invalid fields stay pending; approving a blank name mirrors Currency rejection."""
        changes = ((0, "Currency"), (1, "Unidentified reward"), (1, "Bad\x01name"),
                   (2, ""), (2, "0"), (2, "1.5"), (2, "1000001"),
                   (3, "-1"), (5, Qt.CheckState.PartiallyChecked))
        for column, invalid in changes:
            with self.subTest(column=column, invalid=invalid):
                self.read([self.reward(needs_review=True)])
                cell = self.window.ritual_table.item(0, column)
                cell.setCheckState(invalid) if column == 5 else cell.setText(invalid)
                controls = self.controls(0)
                with self.assertRaises(ValueError):
                    self.window.review_ritual_row(controls, True)
                self.assertEqual(controls.property("reviewStatus"), "pending")
                self.assertEqual(logger.get_state()["scan_commit_count"], 0)
                self.assertEqual(logger.review_icons(), [])
                self.window.reject_review()
        self.read([self.reward(name="", needs_review=True)])
        self.controls(0).findChild(QPushButton, "approveRitual").click()
        self.assertEqual(self.controls(0).property("reviewStatus"), "rejected")
        self.assertIn("no name", self.window.ritual_table.item(0, 6).text())

    def test_every_reward_edit_requires_renewed_approval(self):
        """Changing even Deferred or evidence cannot retain stale row acceptance."""
        changes = ((0, "Omen"), (1, "Another Ritual token"), (2, "2"),
                   (3, "40"), (4, "Corrected evidence"), (5, Qt.CheckState.Checked))
        for column, changed in changes:
            with self.subTest(column=column):
                self.read([self.reward(needs_review=True)])
                self.approve(0)
                cell = self.window.ritual_table.item(0, column)
                cell.setCheckState(changed) if column == 5 else cell.setText(changed)
                self.assertEqual(self.controls(0).property("reviewStatus"), "pending")
                self.assertTrue(self.controls(0).findChild(QPushButton, "approveRitual").isEnabled())
                self.window.reject_review()

    def test_frozen_loose_scan_acceptance_matches_hidden_rows_and_maximum_stays_pending(self):
        """Hidden level0 rewards must remain accepted for the eventual page save."""
        item = self.reward(score=.85)
        self.read([item], strictness=0)
        self.assertTrue(self.window.ritual_table.isRowHidden(0))
        self.assertEqual(self.controls(0).property("reviewStatus"), "approved")
        self.window.approve_review()
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["name"], "Chaos Orb")
        self.read([self.reward()], strictness=100)
        self.assertFalse(self.window.ritual_table.isRowHidden(0))
        self.assertEqual(self.controls(0).property("reviewStatus"), "pending")
        self.approve(0)

    def test_capture_policy_governs_auto_save_even_without_a_result_policy_marker(self):
        """A missing worker marker cannot change held/hidden rows at the final auto-save gate."""
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.read([self.reward()], strictness=100, include_strictness=False, live=True)
        self.assertEqual(self.controls(0).property("reviewStatus"), "pending")
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])
        self.window.reject_review()
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.read([self.reward(score=.85)], strictness=0, include_strictness=False, live=True)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.ritual_pages_for_map("M0001")[0]["items"][0]["name"], "Chaos Orb")

    def test_removed_and_superseded_controls_do_not_approve_a_different_reward(self):
        """Widget identity keeps row actions attached through removal and rescans."""
        self.read([self.reward(needs_review=True), self.reward(1, name="Second token", needs_review=True)])
        first, second = self.controls(0), self.controls(1)
        self.window.ritual_table.setCurrentCell(0, 1)
        self.window.remove_ritual_row()
        self.window.review_ritual_row(first, True)
        self.assertEqual(self.controls(0).property("reviewStatus"), "pending")
        self.window.review_ritual_row(second, True)
        self.assertEqual(self.controls(0).property("reviewStatus"), "approved")
        # A held page is never replaced by a rescan; reject it, then read the next page.
        self.window.reject_review()
        self.read([self.reward(needs_review=True)])
        self.window.review_ritual_row(second, True)
        self.assertEqual(self.controls(0).property("reviewStatus"), "pending")

    def test_deep_row_actions_preserve_scroll_selection_and_queued_focus(self):
        """Currency-style row actions retain the same reward after focus/layout delivery."""
        self.window.resize(840, 650)
        self.window.show()
        self.read([self.reward(index, needs_review=True) for index in range(30)])
        table = self.window.ritual_table
        page = self.window.tabs.widget(0)
        table.setColumnWidth(2, 900)
        self.app.processEvents()
        row = 25
        table.setCurrentCell(row, 2)
        table.scrollToItem(table.item(row, 2))
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        page.ensureWidgetVisible(self.controls(row), 0, 12)
        self.app.processEvents()
        bars = [table.verticalScrollBar(), table.horizontalScrollBar(),
                page.verticalScrollBar(), page.horizontalScrollBar()]
        self.assertTrue(all(bar.value() > 0 for bar in bars[:3]))
        positions = [bar.value() for bar in bars]
        slots = [table.item(index, 0).data(Qt.ItemDataRole.UserRole)["grid_slots"]
                 for index in range(table.rowCount())]
        controls = self.controls(row)
        for action_name, state in (("approveRitual", "approved"), ("rejectRitual", "rejected")):
            action = controls.findChild(QPushButton, action_name)
            action.setFocus()
            for bar, position in zip(bars, positions):
                bar.setValue(position)
            QTest.mouseClick(action, Qt.MouseButton.LeftButton)
            self.assertEqual(controls.property("reviewStatus"), state)
            self.assertEqual([bar.value() for bar in bars], positions)
            self.app.processEvents()
            QTest.qWait(20)
            self.app.processEvents()
            self.assertEqual([bar.value() for bar in bars], positions)
            self.assertEqual(table.currentRow(), row)
            self.assertEqual({index.row() for index in table.selectedIndexes()}, {row})
            self.assertIs(self.app.focusWidget(), controls)
        self.assertEqual([table.item(index, 0).data(Qt.ItemDataRole.UserRole)["grid_slots"]
                          for index in range(table.rowCount())], slots)

    def test_new_map_keeps_row_actions_on_the_map_the_page_was_captured_on(self):
        """A reward decision made after + New map belongs to the page's own map, never the new one."""
        self.read([self.reward(needs_review=True)])
        controls = self.controls(0)
        self.window.finish_map()
        self.assertEqual(self.window.ritual_table.rowCount(), 1)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.window.review_ritual_row(controls, True)
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.window.approve_review()
        self.assertEqual([page["items"][0]["name"] for page in logger.ritual_pages_for_map("M0001")], ["Chaos Orb"])
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])

    def test_context_or_transaction_failure_cannot_learn_or_mark_rows_saved(self):
        """Local approval still relies on the final existing atomic save boundary."""
        self.read([self.reward(name="", needs_review=True, unresolved=True)])
        self.window.ritual_table.item(0, 1).setText("Atomic Ritual reward")
        self.approve(0)
        with patch.object(logger, "save_ritual_page", side_effect=ValueError("transaction failed")):
            with self.assertRaisesRegex(ValueError, "transaction failed"):
                self.window.save_ritual()
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.review_icons(), [])
        self.assertNotIn("Atomic Ritual reward", logger.item_names())
        self.assertNotIn("Saved", self.window.ritual_table.item(0, 6).text())
        with patch.object(logger, "ritual_capture_map", side_effect=ValueError("map changed")):
            with self.assertRaisesRegex(ValueError, "map changed"):
                self.window.review_ritual_row(self.controls(0), True)
            with self.assertRaisesRegex(ValueError, "map changed"):
                self.window.save_ritual()
        self.assertEqual(logger.ritual_pages_for_map("M0001"), [])

    def test_deferred_offers_are_never_logged_and_repeated_pages_stay_separate(self):
        """Row decisions do not merge different pages; a deferred offer is never reviewed or logged.

        It was logged on the page that first offered it, so its page keeps only its counters.
        """
        for deferred in (False, True, False):
            self.image.putpixel((0, 0), (int(deferred), 0, len(logger.ritual_pages_for_map("M0001"))))
            self.read([self.reward(name="Repeated Ritual item", quantity=3,
                                   deferred=deferred, needs_review=not deferred)])
            if deferred:
                self.assertEqual(self.window.ritual_table.rowCount(), 0)
            else:
                self.approve(0)
            self.window.approve_review()
        pages = logger.ritual_pages_for_map("M0001")
        self.assertEqual([[item["quantity"] for item in page["items"]] for page in pages], [[3], [], [3]])
        rows = list(csv.DictReader(io.StringIO(logger.export_ritual_csv().decode("utf-8-sig"))))
        self.assertEqual([(row["Name"], row["New Find Quantity"]) for row in rows],
                         [("Repeated Ritual item", "3"), ("", ""), ("Repeated Ritual item", "3")])


if __name__ == "__main__":
    unittest.main()
