"""Keep propagation selectors and approved origins on the same exact recipe identity."""

import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PropagationRecipeIdentityUITests(unittest.TestCase):
    """Exercise supported imported case collisions and Unicode matching through row approval."""

    CASE_NAMES = ("Case Legacy Reward", "CASE Legacy Reward")
    CASE_COMBOS = ("Death + Time + Power", "Rage + Bond + Wisdom")
    UNICODE_NAME = "Äudit Unicode Reward"
    UNICODE_COMBO = "Time + Death + Rebirth"

    @classmethod
    def setUpClass(cls):
        """Reuse the application while each test owns its profiles and reference archive."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Import a real reference pack preserving two distinct legacy recipe spellings."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-propagation-identity-ui-")
        self.previous = store.DATA_DIR
        self.window = None
        self.profile("source")
        # Existing profiles can retain exact case variants with different combos.
        # Current reference packs deliberately preserve their distinct identities.
        with logger._connect() as db:
            for name, combo in zip(self.CASE_NAMES, self.CASE_COMBOS):
                db.execute("INSERT INTO recipes(name,sockets,combo) VALUES(?,?,?)", (name, 3, combo))
            logger._sync_recipe_ids(db)
        logger.save_recipe({"name": self.UNICODE_NAME, "sockets": 3, "combo": self.UNICODE_COMBO})
        for family, name in enumerate((*self.CASE_NAMES, self.UNICODE_NAME), 8001):
            logger.save_family({"family": family, "top_socket": 3, "recipes": [name]})
        expected_catalog = self.catalog()
        archive = Path(self.temporary.name) / "references.zip"
        archive.write_bytes(reference_pack.export_pack())
        self.profile("destination")
        reference_pack.import_pack(archive)
        self.assertEqual(self.catalog(), expected_catalog)
        logger.start_map()
        self.baseline_catalog = self.catalog()
        with logger._connect() as db:
            self.recipe_ids = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids"))
        self.assertNotEqual(self.recipe_ids[self.CASE_NAMES[0]], self.recipe_ids[self.CASE_NAMES[1]])
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        """Close deferred window work before restoring the original logger profile."""
        if self.window is not None:
            self.window.close()
            self.window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def profile(self, name):
        """Initialize an independent source or destination for the export/import boundary."""
        store.DATA_DIR = Path(self.temporary.name) / name
        logger._READY = False
        logger.initialize()

    def catalog(self):
        """Snapshot reference rows and registered identities without matching names again."""
        with logger._connect() as db:
            return {
                "recipes": [tuple(row) for row in db.execute("SELECT * FROM recipes ORDER BY name")],
                "recipe_ids": [tuple(row) for row in db.execute(
                    "SELECT * FROM recipe_ids ORDER BY recipe_name")],
                "families": [tuple(row) for row in db.execute("SELECT * FROM families ORDER BY id")],
            }

    def choice(self, name, rune, family, *, clear=True):
        """Build the scanner's row shape without bypassing mandatory review approval."""
        return {"selected_recipe": name, "runes": [rune] if clear else [],
                "positions": [1] if clear else [], "can_use": clear,
                "family": f"Family {family}" if family is not None else None,
                "candidates": [family] if family is not None else []}

    def capture(self, choices):
        """Present row choices and prove that displaying them does not save any logging records."""
        before = logger.get_state()
        reading = {"mode": "propagation", **choices[0], "choices": choices, **logger.scan_context()}
        self.window._propagation_read(reading)
        self.assertEqual(logger.get_state(), before)
        self.assertEqual(self.catalog(), self.baseline_catalog)
        return reading

    def offered(self, row):
        """Read the actual inline dropdown labels, excluding the unresolved placeholder."""
        fields = self.window._propagation_row_inputs[row]
        self.assertIsNotNone(fields, "A unique recipe identity needs its own rune selectors.")
        return [fields[0].itemText(index) for index in range(1, fields[0].count())]

    def assert_saved_origin(self, name, rune, family):
        """Check the persisted rune, exact recipe ID and family without altering references."""
        state = logger.get_state()
        self.assertEqual(state["chain"], [{"step": 1, "rune1": rune, "rune2": ""}])
        self.assertEqual((state["detonated"], state["scan_commit_count"]), (1, 2))
        origin = state["chain_provenance"][0]
        self.assertEqual((origin["recipe"], origin["recipe_id"], origin["family"]),
                         (name, self.recipe_ids[name], family))
        with logger._connect() as db:
            details = [json.loads(row[0]) for row in db.execute(
                "SELECT details_json FROM commits WHERE kind IN ('Propagation','Chain') ORDER BY number")]
        self.assertEqual(details[0]["origin"]["recipe_id"], self.recipe_ids[name])
        self.assertEqual(details[1]["origins"][0]["recipe_id"], self.recipe_ids[name])
        self.assertEqual(self.catalog(), self.baseline_catalog)

    def test_imported_exact_case_variants_offer_their_own_combinations(self):
        """Exact case spellings must choose their own combos even when SQLite NOCASE matches both."""
        self.capture([self.choice(name, combo.split(" + ")[0], family)
                      for name, combo, family in zip(self.CASE_NAMES, self.CASE_COMBOS, (8001, 8002))])
        for row, combo in enumerate(self.CASE_COMBOS):
            with self.subTest(recipe=self.CASE_NAMES[row]):
                self.assertEqual(self.offered(row), combo.split(" + "))

    def test_explicit_approval_saves_the_selected_exact_recipe_and_its_rune(self):
        """Approving an imported exact identity cannot attribute its sibling's offered rune to it."""
        self.capture([self.choice(self.CASE_NAMES[0], "Death", 8001)])
        # Select the first offered socket as a user correcting the screenshot would.
        self.window._propagation_row_inputs[0][0].setCurrentIndex(1)
        self.window.approve_propagation_recipe(0)
        self.assert_saved_origin(self.CASE_NAMES[0], "Death", 8001)

    def test_unique_unicode_casefold_uses_the_canonical_combo_and_saved_identity(self):
        """A unique non-ASCII name variant retains inline selection and canonical provenance."""
        self.capture([self.choice("äudit unicode reward", "Time", 8003)])
        self.assertEqual(self.offered(0), self.UNICODE_COMBO.split(" + "))
        self.window._propagation_row_inputs[0][0].setCurrentIndex(1)
        self.window.approve_propagation_recipe(0)
        self.assert_saved_origin(self.UNICODE_NAME, "Time", 8003)

    def test_ambiguous_nonexact_name_cannot_save_and_can_be_rejected(self):
        """Do not offer one arbitrary combo for an ambiguous name, even through manual fallback."""
        self.capture([self.choice("case legacy reward", "", None, clear=False)])
        self.assertIsNone(self.window._propagation_row_inputs[0])
        self.window.start_manual_propagation()
        self.window.propagation_rune_inputs[0].setEditText("Death")
        before = logger.get_state()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.window.add_manual_propagation()
        self.assertEqual(logger.get_state(), before)
        self.assertIsNone(self.window._chain_save_pending)
        self.assertEqual(self.window.pending_review_kind, "propagation")
        self.window.reject_review()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._manual_propagation_context)
        self.assertEqual(logger.get_state(), before)
        self.assertEqual(self.catalog(), self.baseline_catalog)


if __name__ == "__main__":
    unittest.main()
