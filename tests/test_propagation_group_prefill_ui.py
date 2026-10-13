"""Shared propagation evidence fills existing recipe selectors without logging automatically."""

import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from PoE2_Data_Logger.ocr import propagation_scan
from tests import test_propagation_ocr as fixtures


class PropagationGroupPrefillUITests(unittest.TestCase):
    """Exercise crown recovery through all existing inline controls and explicit row approval."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application while isolating each logger profile."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create a temporary profile and stop polling before rendering controlled family panels."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-group-prefill-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.fixture = fixtures.PropagationOCRTests()
        self.fixture.glyph = Image.new("RGB", (29, 29), "black")

    def tearDown(self):
        """Close workers and callbacks before restoring the previous profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def verify_all_recipe_selectors(self, image, rows, sockets, *, approve_row=3):
        """Pass real crown geometry into Qt, then verify menus and an explicit noncursor approval."""
        before = logger.get_state()
        reading = {**self.fixture.scan(image, rows), **logger.scan_context()}
        self.assertEqual(reading["candidates"], [17], reading)
        self.assertEqual(len(reading["choices"]), 5)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        self.window._propagation_read(reading, encoded.getvalue())
        self.assertEqual(logger.get_state(), before)
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertFalse(self.window.preview.isHidden())
        table = self.window.propagation_recipe_table
        self.assertEqual(table.columnCount(), 3)
        self.assertEqual(table.rowCount(), 5)
        self.assertEqual([table.horizontalHeaderItem(index).text() for index in range(3)],
                         ["Recipe", "Marked runes · left to right", "Review"])
        names_by_row = []
        with logger._connect() as db:
            for row, choice in enumerate(reading["choices"]):
                self.assertTrue(choice["can_use"], choice)
                self.assertEqual(choice["positions"], sockets, choice)
                names = [rune.strip() for rune in db.execute(
                    "SELECT combo FROM recipes WHERE name=?", (choice["selected_recipe"],)
                ).fetchone()[0].split("+")]
                names_by_row.append(names)
                fields = self.window._propagation_row_inputs[row]
                self.assertEqual(len(fields), 2)
                self.assertEqual(table.cellWidget(row, 1).findChildren(QComboBox), list(fields))
                for field in fields:
                    self.assertFalse(field.isEditable())
                    self.assertTrue(field.isEnabled())
                    self.assertEqual([field.itemText(index) for index in range(field.count())],
                                     ["", *names])
                    self.assertEqual([field.itemData(index) for index in range(field.count())],
                                     [None, *range(len(names))])
                expected_data = [socket - 1 for socket in sockets]
                expected_names = [names[socket - 1] for socket in sockets]
                if len(sockets) == 1:
                    expected_data.append(None)
                    expected_names.append("")
                self.assertEqual([field.currentData() for field in fields], expected_data)
                self.assertEqual([field.currentText() for field in fields], expected_names)
                approve = table.cellWidget(row, 2).findChild(QPushButton, "approvePropagationRecipe")
                self.assertEqual(approve.text(), "Approve")
                self.assertTrue(approve.isEnabled())
                # Suggestions leave the existing recipe menus available for manual correction.
                for index, field in enumerate(fields):
                    alternate = 0 if index == 0 else len(names) - 1
                    field.setCurrentIndex(alternate + 1)
                    self.assertEqual(field.currentData(), alternate)
                    self.assertEqual(field.currentText(), names[alternate])
                    field.setCurrentIndex(0 if expected_data[index] is None else
                                          expected_data[index] + 1)
                self.assertEqual([field.currentData() for field in fields], expected_data)
                self.assertEqual([field.currentText() for field in fields], expected_names)
        self.assertEqual(logger.get_state(), before)
        # No reviewed recipe is logged until the end user clicks its existing Approve button.
        expected = [names_by_row[approve_row][socket - 1] for socket in sockets]
        selected = reading["choices"][approve_row]["selected_recipe"]
        table.cellWidget(approve_row, 2).findChild(QPushButton, "approvePropagationRecipe").click()
        saved = logger.get_state()
        self.assertEqual(saved["chain"], [{"step": 1, "rune1": expected[0],
                                         "rune2": expected[1] if len(expected) == 2 else ""}])
        self.assertEqual(saved["detonated"], (before["detonated"] or 0) + 1)
        self.assertEqual(saved["chain_provenance"][0]["recipe"], selected)
        self.assertEqual(saved["chain_provenance"][0]["family"], 17)
        self.assertEqual(saved["chain_provenance"][0]["family_candidates"], [17])
        self.assertEqual(len(saved["chain_provenance"]), 1)
        self.app.processEvents()
        self.assertEqual(logger.get_state(), saved)
        return reading

    def panel_with_unmarked_receivers(self, marks, *, weak_text=False, missing_tiles=False):
        """Leave recipes two and five unmarked, optionally weakening text or erasing their rune bars."""
        image, rows = self.fixture.family_panel(marks)
        for receiver in (1, 4):
            if weak_text:
                rows[receiver]["score"] = .84
            if missing_tiles:
                y = 86 + receiver * 81
                ImageDraw.Draw(image).rectangle((50, y - 24, image.width - 1, y + 19),
                                               fill=(176, 161, 130))
        return image, rows

    def test_single_crown_fills_every_existing_family_selector(self):
        """A crown on recipe one suggests its slot for every row, each with its own rune name."""
        image, rows = self.fixture.family_panel({0: [2]})
        self.verify_all_recipe_selectors(image, rows, [2])

    def test_crowns_on_different_recipes_fill_both_slots_on_every_row(self):
        """Independent crowns in slots two and five supply the ordered pair for all five recipes."""
        image, rows = self.fixture.family_panel({0: [2], 4: [5]})
        self.verify_all_recipe_selectors(image, rows, [2, 5])

    def test_two_fragment_rows_fill_all_selectors_without_autoapproval(self):
        """Two matching partial marks suggest one shared slot while leaving logging to Approve."""
        image, rows = self.fixture.family_panel({})
        self.fixture.fragment(image, 1, 2)
        self.fixture.fragment(image, 4, 2)
        reading = self.verify_all_recipe_selectors(image, rows, [2])
        self.assertTrue(all(choice["position_source"] == "fragment_consensus"
                            for choice in reading["choices"]))

    def test_unmarked_receiver_with_missing_first_contour_uses_verified_family_slots(self):
        """A damaged unmarked receiver borrows proven family slots without changing its menus."""
        image, rows = self.fixture.family_panel({0: [2]})
        top = 86 + 4 * 81 - 18
        ImageDraw.Draw(image).rectangle((50, top, 90, top + 37), fill=(176, 161, 130))
        self.verify_all_recipe_selectors(image, rows, [2], approve_row=4)

    def test_three_crowned_recipes_prefill_lower_confidence_unmarked_receivers(self):
        """Crowns on recipes one, three and four also fill recipes two and five at OCR score .84."""
        image, rows = self.panel_with_unmarked_receivers(
            {0: [2], 2: [2], 3: [2]}, weak_text=True)
        self.verify_all_recipe_selectors(image, rows, [2], approve_row=1)

    def test_three_crowned_recipes_prefill_receivers_without_local_rune_bars(self):
        """Unmarked recipes two and five use the proven family socket after their rune bars disappear."""
        image, rows = self.panel_with_unmarked_receivers(
            {0: [2], 2: [2], 3: [2]}, missing_tiles=True)
        self.verify_all_recipe_selectors(image, rows, [2], approve_row=4)

    def test_three_crowned_recipes_prefill_weak_receivers_without_local_rune_bars(self):
        """The formerly blank receivers fill despite both .84 OCR scores and absent local geometry."""
        image, rows = self.panel_with_unmarked_receivers(
            {0: [2], 2: [2], 3: [2]}, weak_text=True, missing_tiles=True)
        self.verify_all_recipe_selectors(image, rows, [2], approve_row=4)

    def test_separate_source_sockets_prefill_both_slots_for_weak_unmarked_receivers(self):
        """Independent source recipes prove both slots while weak receivers have no local rune bars."""
        image, rows = self.panel_with_unmarked_receivers(
            {0: [2], 2: [5]}, weak_text=True, missing_tiles=True)
        self.verify_all_recipe_selectors(image, rows, [2, 5], approve_row=1)

    def test_fragment_consensus_prefills_weak_receivers_without_local_rune_bars(self):
        """Two independent partial source marks fill weak receivers for manual review and approval."""
        image, rows = self.panel_with_unmarked_receivers({}, weak_text=True, missing_tiles=True)
        self.fixture.fragment(image, 0, 2)
        self.fixture.fragment(image, 2, 2)
        reading = self.verify_all_recipe_selectors(image, rows, [2], approve_row=4)
        self.assertTrue(all(choice["position_source"] == "fragment_consensus"
                            for choice in reading["choices"]))

    def test_original_family57_capture_fills_existing_controls_before_manual_approval(self):
        """The actual formerly blank capture reaches all six unchanged dropdown rows and saves only on click."""
        path = Path(__file__).resolve().parent / "fixtures/propagation_prefill/family57-opened.jpg"
        reading = {**propagation_scan.scan_propagation(path), **logger.scan_context()}
        before = logger.get_state()
        self.window._propagation_read(reading, path.read_bytes())
        self.assertEqual(logger.get_state(), before)
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertFalse(self.window.preview.isHidden())
        table = self.window.propagation_recipe_table
        self.assertEqual(table.rowCount(), 6)
        with logger._connect() as db:
            for index, choice in enumerate(reading["choices"]):
                names = [rune.strip() for rune in db.execute("SELECT combo FROM recipes WHERE name=?",
                         (choice["selected_recipe"],)).fetchone()[0].split("+")]
                first, second = self.window._propagation_row_inputs[index]
                self.assertEqual((first.currentData(), first.currentText()), (3, names[3]))
                self.assertEqual((second.currentData(), second.currentText()), (None, ""))
                self.assertEqual([first.itemText(item) for item in range(first.count())], ["", *names])
                self.assertTrue(table.cellWidget(index, 2).findChild(
                    QPushButton, "approvePropagationRecipe").isEnabled())
        table.cellWidget(5, 2).findChild(QPushButton, "approvePropagationRecipe").click()
        saved = logger.get_state()
        self.assertEqual(saved["chain_provenance"][0]["recipe"], "Bitter Dead (Level 20)")
        self.assertEqual(saved["chain_provenance"][0]["family"], 57)
        self.assertEqual(len(saved["chain"]), 1)


if __name__ == "__main__":
    unittest.main()
