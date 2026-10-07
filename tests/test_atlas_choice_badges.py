import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from PoE2_Data_Logger.core.atlas_catalog import catalog as bundled_catalog
from PoE2_Data_Logger.ui.atlas_settings import AtlasSettingsPage


CHOICE_ID = "AtlasMonsterPackSizeSelector1"


def choice_catalog():
    # Use the actual game's options and stable IDs, with compact test geometry.
    choice = copy.deepcopy(bundled_catalog()["nodes"][CHOICE_ID])
    choice.update(x=200, y=0)
    ordinary = {"id": "ordinary", "name": "Ordinary passive", "kind": "small",
                "activity": "Main Atlas", "x": 0, "y": 0, "allocatable": True,
                "effects": ["1% increased Rarity of Items found"], "choices": [], "stats": {}}
    return {"version": "test-choice-badges", "activities": ["Main Atlas"],
            "nodes": {CHOICE_ID: choice, "ordinary": ordinary},
            "edges": [["ordinary", CHOICE_ID]]}


class RecordingPainter(QPainter):
    """Observe text sent to the real raster paint path."""

    def __init__(self, image):
        super().__init__(image)
        self.labels = []

    def drawText(self, *args):
        self.labels.append(str(args[-1]))
        return super().drawText(*args)


class AtlasChoiceBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = choice_catalog()
        self.catalog_patch = patch("PoE2_Data_Logger.ui.atlas_settings.catalog", return_value=self.data)
        self.catalog_patch.start()
        self.page = AtlasSettingsPage()
        self.page.resize(1200, 760)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        QToolTip.hideText()
        self.page.choice_combo.hidePopup()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.catalog_patch.stop()

    def painted_labels(self, node_id=CHOICE_ID):
        image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = RecordingPainter(image)
        try:
            painter.translate(100, 100)
            self.page.node_items[node_id].paint(painter, None)
            return painter.labels
        finally:
            painter.end()

    def tooltip_text(self):
        document = QTextDocument()
        document.setHtml(self.page.node_items[CHOICE_ID].toolTip())
        return document.toPlainText()

    def click_choice_face(self):
        item = self.page.node_items[CHOICE_ID]
        # Click where the number is painted, not just the icon's center.
        face = item.mapToScene(QPointF(0, item.radius * .4))
        point = self.page.view.mapFromScene(face)
        QTest.mouseClick(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()

    def set_choice(self, number, allocated=True):
        choice_id = self.data["nodes"][CHOICE_ID]["choices"][number - 1]["id"]
        self.page.set_settings({"allocated": [CHOICE_ID] if allocated else [],
                                "choices": {CHOICE_ID: choice_id}}, force=True)

    def test_game_option_numbers_match_dropdown_selection_paint_and_hover(self):
        self.click_choice_face()
        combo = self.page.choice_combo
        self.assertTrue(combo.view().isVisible())
        self.assertEqual([combo.itemText(index) for index in range(1, combo.count())],
                         ["1. Pack Size", "2. Effectiveness", "3. Rarity"])
        self.assertEqual([combo.itemData(index) for index in range(1, combo.count())],
                         [option["id"] for option in self.data["nodes"][CHOICE_ID]["choices"]])
        index = combo.model().index(2, 0)
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=combo.view().visualRect(index).center())
        self.app.processEvents()
        self.assertEqual(self.painted_labels(), ["2"])
        self.assertEqual(self.page.settings()["choices"], {CHOICE_ID: "AtlasMonsterPackSizeSelector1b"})
        self.assertIn("2. Effectiveness", self.page.node_effects.toPlainText())
        tooltip = self.tooltip_text()
        self.assertIn("Selected effect: 2. Effectiveness", tooltip)
        self.assertIn("15% increased Effectiveness", tooltip)
        self.assertNotIn("Selected effect: 1.", tooltip)

    def test_saved_options_paint_their_number_including_third_option(self):
        for number, name in enumerate(("Pack Size", "Effectiveness", "Rarity"), 1):
            with self.subTest(number=number):
                self.set_choice(number)
                self.assertEqual(self.painted_labels(), [str(number)])
                self.assertIn(f"Selected effect: {number}. {name}", self.tooltip_text())

    def test_clicking_badge_turns_node_off_without_losing_saved_choice(self):
        self.set_choice(3)
        self.click_choice_face()
        self.assertNotIn(CHOICE_ID, self.page.settings()["allocated"])
        self.assertEqual(self.painted_labels(), [])
        self.assertIn("Saved choice (inactive): 3. Rarity", self.tooltip_text())
        self.assertNotIn("Selected effect", self.tooltip_text())
        self.click_choice_face()
        self.assertIn(CHOICE_ID, self.page.settings()["allocated"])
        self.assertEqual(self.painted_labels(), ["3"])
        self.assertEqual(self.page.settings()["choices"][CHOICE_ID], "AtlasMonsterPackSizeSelector1c")

    def test_autofill_and_clear_preserve_choice_without_painting_inactive_badge(self):
        self.set_choice(2, allocated=False)
        self.assertEqual(self.painted_labels(), [])
        self.page.autofill_button.click()
        self.assertEqual(self.painted_labels(), ["2"])
        self.assertEqual(self.painted_labels("ordinary"), [])
        self.page.clear_button.click()
        self.assertEqual(self.painted_labels(), [])
        self.assertEqual(self.page.settings()["choices"][CHOICE_ID], "AtlasMonsterPackSizeSelector1b")
        self.page.autofill_button.click()
        self.assertEqual(self.painted_labels(), ["2"])

    def test_unset_choice_has_no_option_number_or_invented_default(self):
        self.page.autofill_button.click()
        self.assertEqual(self.painted_labels(), [])
        self.assertIn("awaiting a selection", self.page.summary.text())
        self.assertIn("1. Pack Size", self.tooltip_text())
        self.set_choice(1)
        self.page._selected_node = CHOICE_ID
        self.page._show_node_details()
        self.page.choice_combo.setCurrentIndex(0)
        self.assertNotIn(CHOICE_ID, self.page.settings()["choices"])
        self.assertEqual(self.painted_labels(), [])
        self.assertIn("awaiting a selection", self.page.summary.text())

    def test_number_comes_from_catalog_order_instead_of_variant_id_suffix(self):
        node = self.data["nodes"][CHOICE_ID]
        node["choices"][:] = [node["choices"][2], node["choices"][0], node["choices"][1]]
        self.page.set_settings({"allocated": [CHOICE_ID],
                                "choices": {CHOICE_ID: "AtlasMonsterPackSizeSelector1a"}}, force=True)
        self.assertEqual(self.painted_labels(), ["2"])
        self.assertIn("Selected effect: 2. Pack Size", self.tooltip_text())


if __name__ == "__main__":
    unittest.main()
