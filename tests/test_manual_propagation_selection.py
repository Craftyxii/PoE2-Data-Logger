"""Keep manual fallback attached to the unresolved reward that the user approves."""

import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ManualPropagationSelectionTests(unittest.TestCase):
    """Reproduce two known seven-rune rewards followed by an unreadable quantity reward."""

    @classmethod
    def setUpClass(cls):
        """Reuse Qt while each case owns an isolated profile."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Use the shipped sibling recipes without changing their rune combinations."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-manual-propagation-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        image = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(image, format="PNG")
        self.raw = image.getvalue()

    def tearDown(self):
        """Close deferred UI work before restoring the previous profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def capture(self, *, family=None, candidates=None, extra=None):
        """Deliver the reported OCR shape, which has no top-level selected recipe."""
        unresolved = {"selected_recipe": None, "reward_text": "10x Chaos Orb",
                      "runes": [], "can_use": False,
                      "status": "Reward was unclear — enter its propagated runes manually.",
                      "family": family, "candidates": candidates or []}
        choices = [
            {"selected_recipe": "Cadigan's Epiphany", "runes": ["Wisdom", "Sky"],
             "positions": [1, 2], "can_use": True, "family": "Family 19", "candidates": [19]},
            {"selected_recipe": "Astrid's Creativity", "runes": ["Wisdom", "Arcane"],
             "positions": [1, 2], "can_use": True, "family": "Family 19", "candidates": [19]},
            unresolved]
        if extra:
            choices.append({**unresolved, "reward_text": extra})
        reading = {"mode": "propagation", "selected_recipe": None, "can_use": False,
                   "runes": [], "choices": choices, "family": "Family 19", "candidates": [19],
                   "status": "Choose a recipe and check its marked runes.", **logger.scan_context()}
        self.window._propagation_read(reading, self.raw)
        return reading

    def enter_runes(self):
        """Fill the reported first and optional second rune without approving anything."""
        for field, rune in zip(self.window.propagation_rune_inputs, ("Wisdom", "Vision")):
            field.setEditText(rune)

    def approve(self, row):
        """Return the row's actual Approve button."""
        return self.window.propagation_recipe_table.cellWidget(row, 2).findChild(
            QPushButton, "approvePropagationRecipe")

    def register_quantity_reward(self):
        """Represent a user-added quantity recipe using the public reference editors."""
        # The bundled catalog has x1/x2/x3 Chaos rewards; the screenshot's x10
        # is deliberately added rather than borrowing a different recipe's ID.
        logger.save_recipe({"name": "Chaos Orb x10", "sockets": 3,
                            "combo": "Wisdom + Vision + Time"})
        logger.save_family({"family": 8001, "top_socket": 3, "recipes": ["Chaos Orb x10"]})

    def test_manual_save_selects_sole_unknown_reward_and_counts_one_approval(self):
        """Opening fallback chooses the only unresolved row before any rune editing."""
        reading = self.capture()
        table = self.window.propagation_recipe_table
        self.assertEqual(table.currentRow(), -1)
        before = logger.get_state()
        self.window.manual_propagation_button.click()
        self.assertEqual(table.currentRow(), 2)
        self.assertIn("10x Chaos Orb", self.window.propagation_manual_label.text())
        self.enter_runes()
        self.assertTrue(self.window.propagation_add_button.isEnabled())
        self.assertTrue(self.approve(2).isEnabled())
        self.assertEqual(logger.get_state(), before)
        self.window.propagation_add_button.click()
        saved = logger.get_state()
        self.assertEqual(saved["chain"], [{"step": 1, "rune1": "Wisdom", "rune2": "Vision"}])
        self.assertEqual(saved["chain_provenance"][0]["recipe"], "")
        self.assertEqual(saved["detonated"], 1)
        self.assertEqual(saved["scan_commit_count"], before["scan_commit_count"] + 2)
        self.window.propagation_add_button.click()
        self.window._propagation_read(reading, self.raw)
        self.assertEqual(logger.get_state(), saved)
        self.assertIsNone(self.window._manual_propagation_context)

    def test_both_manual_actions_keep_exact_quantity_recipe_and_its_family(self):
        """Save and row Approve use exact quantity normalization and selected-row provenance."""
        self.register_quantity_reward()
        for index, action in enumerate(("save", "approve"), 1):
            with self.subTest(action=action):
                reading = self.capture(family="Family 8001", candidates=[8001])
                self.window.manual_propagation_button.click()
                self.enter_runes()
                button = self.window.propagation_add_button if action == "save" else self.approve(2)
                self.assertTrue(button.isEnabled())
                button.click()
                saved = logger.get_state()
                origin = saved["chain_provenance"][-1]
                self.assertEqual((origin["recipe"], origin["family"], origin["family_candidates"]),
                                 ("Chaos Orb x10", 8001, [8001]))
                self.assertEqual(origin["family_source"], "supplied")
                self.assertTrue(origin["recipe_id"])
                self.assertEqual((len(saved["chain"]), saved["detonated"]), (index, index))
                self.window._propagation_read(reading, self.raw)
                self.assertEqual(logger.get_state(), saved)

    def test_manual_save_preserves_held_remnant_and_typed_reward(self):
        """Independent fallback cannot consume the remnant that is still being corrected."""
        self.window.manual_remnant_button.click()
        self.window.first_recipe.setText("Reward being corrected")
        pending = logger.get_state()["ocr_pending"]
        self.capture()
        self.window.manual_propagation_button.click()
        self.enter_runes()
        self.window.propagation_add_button.click()
        self.assertEqual(self.window.pending_review_kind, "remnant")
        self.assertEqual(self.window.first_recipe.text(), "Reward being corrected")
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["detonated"], 1)

    def test_opening_manual_fields_preserves_an_explicit_selection_during_edits(self):
        """The fallback shortcut never switches an already selected reward."""
        self.capture()
        table = self.window.propagation_recipe_table
        table.setCurrentCell(0, 0)
        self.window.manual_propagation_button.click()
        self.assertEqual(table.currentRow(), 0)
        self.enter_runes()
        self.assertEqual(table.currentRow(), 0)
        table.setCurrentCell(2, 0)
        self.assertTrue(all(not field.currentText() for field in self.window.propagation_rune_inputs))
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.assertIsNone(logger.get_state()["detonated"])

    def test_multiple_unknown_rewards_require_an_explicit_row_and_denials_stay_blocked(self):
        """Ambiguous fallback cannot attach runes to an arbitrary or denied reward."""
        self.capture(extra="11x Chaos Orb")
        table = self.window.propagation_recipe_table
        self.window.manual_propagation_button.click()
        self.enter_runes()
        self.assertEqual(table.currentRow(), -1)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "Select a waiting propagation recipe"):
            self.window.add_manual_propagation()
        table.setCurrentCell(2, 0)
        self.enter_runes()
        self.assertTrue(self.window.propagation_add_button.isEnabled())
        self.window.deny_propagation_recipe(2)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "Select a waiting propagation recipe"):
            self.window.add_manual_propagation()
        for row in (0, 1, 3):
            self.window.deny_propagation_recipe(row)
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertFalse(self.window.propagation_add_button.isEnabled())
        self.assertIsNone(logger.get_state()["detonated"])

    def test_changed_expedition_rejects_a_previous_manual_capture(self):
        """The newly available save path retains captured expedition validation."""
        reading = self.capture()
        self.window.manual_propagation_button.click()
        self.enter_runes()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertIsNone(self.window._manual_propagation_context)
        self.window._manual_propagation_context = dict(reading)
        self.enter_runes()
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            self.window.add_manual_propagation()
        self.assertIsNone(logger.get_state()["detonated"])


if __name__ == "__main__":
    unittest.main()
