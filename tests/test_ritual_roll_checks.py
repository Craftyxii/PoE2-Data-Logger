"""Rolls only go down on a map: more rolls ask about a new map, unread rolls warn of a repeat.

Recognition is injected through Futures; these tests check the review decisions only.
"""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from PoE2_Data_Logger.core import logger_store as logger
from tests import test_ritual_sequential_delivery as delivery


class RitualRollCheckTests(delivery.RitualSequentialDeliveryTests):
    """Reuse the delivery fixture: a real window, a real hotkey manager and injected readings."""

    def read(self, index, rerolls, *, quantity=None, needs_review=False):
        """Capture page ``index`` and complete its reading with the given rolls left."""
        self.publish(index)
        self.window.poll()
        self.futures[-1].set_result(self.reading(quantity or index, needs_review=needs_review, rerolls=rerolls))
        self.app.processEvents()
        self.window.poll()

    @staticmethod
    def pages(map_id):
        """Each saved page's first reward quantity and rolls left, from its Scan Commit."""
        saved = {page["page_number"] for page in logger.ritual_pages_for_map(map_id)}
        with logger._connect() as db:
            commits = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind='Ritual' AND map_id=? ORDER BY number", (map_id,))]
        return [(commit["items"][0]["quantity"], commit["rerolls_remaining"]) for commit in commits
                if commit["page"] in saved]

    def test_rolls_went_up_asks_and_yes_starts_the_next_map_with_this_page(self):
        self.read(1, 30)
        self.read(2, 35)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertFalse(self.window.ritual_rolls_question.isHidden())
        self.assertEqual(self.window.ritual_rolls_question_text.text(),
                         "Rolls went up (30 → 35) — did you start a new map?")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        QTest.mouseClick(self.window.ritual_new_map_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(self.pages("M0001"), [(1, 30)])
        self.assertEqual(self.pages("M0002"), [(2, 35)])
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIn("saved to M0002", self.window.statusBar().currentMessage())
        # Undo removes the page first, then the new map.
        self.window.undo_map()
        self.assertEqual((logger.get_state()["current_map_id"], self.pages("M0002")), ("M0002", []))
        self.window.undo_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual(self.pages("M0001"), [(1, 30)])

    def test_no_fix_rolls_lets_the_user_type_the_count_and_approve_checks_it(self):
        self.read(1, 30)
        self.read(2, 35)
        QTest.mouseClick(self.window.ritual_fix_rolls_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.ritual_rolls_question.isHidden())
        self.assertFalse(self.window.ritual_rerolls.isReadOnly())
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        # A higher count still asks; the same count is the page already saved.
        self.window.ritual_rerolls.setText("36")
        self.click_approve()
        self.assertFalse(self.window.ritual_rolls_question.isHidden())
        self.assertEqual(self.pages("M0001"), [(1, 30)])
        QTest.mouseClick(self.window.ritual_fix_rolls_button, Qt.MouseButton.LeftButton)
        self.window.ritual_rerolls.setText("29")
        self.click_approve()
        self.assertEqual(logger.get_state()["current_map_id"], "M0001")
        self.assertEqual(self.pages("M0001"), [(1, 30), (2, 29)])
        self.assertTrue(self.window.ritual_rerolls.isReadOnly())

    def test_fixed_count_equal_to_the_last_page_is_not_saved_again(self):
        self.read(1, 30)
        self.read(2, 35)
        QTest.mouseClick(self.window.ritual_fix_rolls_button, Qt.MouseButton.LeftButton)
        self.window.ritual_rerolls.setText("30")
        self.click_approve()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertTrue(self.window.ritual_status.text().startswith("✓ Already saved: M0001 page 1 had 30 rolls left"))
        self.assertEqual(self.pages("M0001"), [(1, 30)])

    def test_unreadable_rolls_on_a_later_page_are_held_as_a_possible_repeat(self):
        self.read(1, None)
        # The first page of a map saves as before: there is nothing it could repeat.
        self.assertEqual(self.pages("M0001"), [(1, None)])
        self.read(2, 30)
        self.read(3, None)
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(self.window.ritual_status.text(),
                         "Rolls left couldn't be read — this may repeat M0001 page 2 (30 rolls left). "
                         "Approve only if it is a new page.")
        self.assertFalse(self.window.ritual_rerolls.isReadOnly())
        self.click_approve()
        self.assertEqual(self.pages("M0001"), [(1, None), (2, 30), (3, None)])

    def test_typed_rolls_on_an_unread_page_are_checked_like_a_reading(self):
        self.read(1, 30)
        self.read(2, None)
        self.window.ritual_rerolls.setText("30")
        self.click_approve()
        self.assertTrue(self.window.ritual_status.text().startswith("✓ Already saved"))
        self.assertEqual(self.pages("M0001"), [(1, 30)])

    def test_pages_taken_after_the_new_map_page_follow_it(self):
        self.read(1, 30)
        self.publish(2)
        self.window.poll()
        self.publish(3)
        self.publish(4)
        self.window.poll()
        self.window.poll()
        self.assertEqual(len(self.window._ritual_waiting), 2)
        self.futures[-1].set_result(self.reading(2, rerolls=35))
        self.app.processEvents()
        QTest.mouseClick(self.window.ritual_new_map_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        for rerolls in (34, 33):
            self.window.poll()
            self.futures[-1].set_result(self.reading(36 - rerolls + 1, rerolls=rerolls))
            self.app.processEvents()
        self.window.poll()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(self.pages("M0001"), [(1, 30)])
        self.assertEqual(self.pages("M0002"), [(2, 35), (3, 34), (4, 33)])

    def test_pressing_new_map_while_asked_counts_as_yes(self):
        self.read(1, 30)
        self.read(2, 35, needs_review=True)
        self.window.finish_map()
        self.assertTrue(self.window.ritual_rolls_question.isHidden())
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertIn("goes to M0002", self.window.ritual_status.text())
        controls = self.window.ritual_table.cellWidget(0, 6)
        self.window.review_ritual_row(controls, True)
        self.click_approve()
        self.assertEqual(self.pages("M0002"), [(2, 35)])
        self.assertEqual(self.pages("M0001"), [(1, 30)])

    def test_lower_rolls_and_a_new_map_are_unaffected(self):
        self.read(1, 30)
        self.read(2, 28)
        self.window.finish_map()
        self.read(3, 44)
        self.assertEqual(self.pages("M0001"), [(1, 30), (2, 28)])
        self.assertEqual(self.pages("M0002"), [(3, 44)])
        self.assertIsNone(self.window.pending_review_kind)

    def test_added_reward_survives_yes_and_waits_for_explicit_review(self):
        self.read(1, 30)
        self.read(2, 35)
        QTest.mouseClick(self.window.ritual_add_row_button, Qt.MouseButton.LeftButton)
        row = self.window.ritual_table.rowCount() - 1
        self.window.ritual_table.item(row, 1).setText("Added while choosing map")
        self.window.ritual_table.item(row, 2).setText("2")
        QTest.mouseClick(self.window.ritual_new_map_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])
        self.assertEqual(self.window.ritual_table.item(row, 1).text(), "Added while choosing map")
        controls = self.window.ritual_table.cellWidget(row, 6)
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.window.review_ritual_row(controls, True)
        self.click_approve()
        pages = logger.ritual_pages_for_map("M0002")
        self.assertEqual(len(pages), 1)
        self.assertIn({"name": "Added while choosing map", "quantity": 2},
                      [{"name": item["name"], "quantity": item["quantity"]} for item in pages[0]["items"]])

    def test_edited_reward_survives_yes_and_keeps_its_pending_decision(self):
        self.read(1, 30)
        self.read(2, 35)
        self.window.ritual_table.item(0, 2).setText("9")
        QTest.mouseClick(self.window.ritual_new_map_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(self.window.ritual_table.item(0, 2).text(), "9")
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])
        controls = self.window.ritual_table.cellWidget(0, 6)
        self.assertEqual(controls.property("reviewStatus"), "pending")
        self.window.review_ritual_row(controls, True)
        self.click_approve()
        self.assertEqual(self.pages("M0002"), [(9, 35)])

    def test_removed_reward_waits_for_review_after_yes(self):
        self.read(1, 30)
        self.read(2, 35)
        self.window.ritual_table.setCurrentCell(0, 1)
        self.window.remove_ritual_row()
        QTest.mouseClick(self.window.ritual_new_map_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.pending_review_kind, "ritual")
        self.assertEqual(self.window.ritual_table.rowCount(), 0)
        self.assertEqual(logger.ritual_pages_for_map("M0002"), [])


# The inherited delivery tests already run in their own module.
for name in [name for name in dir(delivery.RitualSequentialDeliveryTests) if name.startswith("test_")]:
    if name not in RitualRollCheckTests.__dict__:
        setattr(RitualRollCheckTests, name, None)


if __name__ == "__main__":
    unittest.main()
