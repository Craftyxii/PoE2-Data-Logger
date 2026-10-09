"""Qt coverage for numbered Atlas choice badges, hover text and allocation toggles using a controlled catalog."""

import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from PoE2_Data_Logger.core.atlas_catalog import catalog as bundled_catalog
from PoE2_Data_Logger.ui.atlas_settings import AtlasSettingsPage


CHOICE_ID = "AtlasMonsterPackSizeSelector1"


def choice_catalog():
    # Use the actual game's options and stable IDs, with compact test geometry.
    """Build a compact two-node Atlas fixture retaining the bundled three-choice option order."""
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
        """Initialize raster painting and an ordered list of observed text labels."""
        super().__init__(image)
        self.labels = []

    def drawText(self, *args):
        """Record each text label while forwarding the draw call to the real painter."""
        self.labels.append(str(args[-1]))
        return super().drawText(*args)


class AtlasChoiceBadgeTests(unittest.TestCase):
    """Check choice badge numbering, allocation toggles, hover details and screen-space zoom behavior."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Patch the Atlas catalog and show a settings page with compact choice-node geometry."""
        self.data = choice_catalog()
        self.catalog_patch = patch("PoE2_Data_Logger.ui.atlas_settings.catalog", return_value=self.data)
        self.catalog_patch.start()
        self.page = AtlasSettingsPage()
        self.page.resize(1200, 760)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        """Dismiss tooltips and dropdowns, destroy the settings page and restore the catalog."""
        QToolTip.hideText()
        self.page.choice_combo.hidePopup()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.catalog_patch.stop()

    def painted_labels(self, node_id=CHOICE_ID):
        """Paint a node badge to an image and return the text labels drawn."""
        image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = RecordingPainter(image)
        try:
            painter.translate(100, 100)
            item = self.page.node_items[node_id]
            (item.choice_badge or item).paint(painter, None)
            return painter.labels
        finally:
            painter.end()

    def tooltip_text(self):
        """Convert the choice node tooltip from HTML to plain text for assertions."""
        document = QTextDocument()
        document.setHtml(self.page.node_items[CHOICE_ID].toolTip())
        return document.toPlainText()

    def click_choice_face(self):
        """Click the screen-space badge center and process the resulting Qt events."""
        item = self.page.node_items[CHOICE_ID]
        # Click the screen-fixed number, not just the icon's center.
        point = self.page.view.mapFromScene(item.choice_badge.scenePos())
        QTest.mouseClick(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()

    def set_choice(self, number, allocated=True):
        """Load a numbered catalog option with the requested allocation state."""
        choice_id = self.data["nodes"][CHOICE_ID]["choices"][number - 1]["id"]
        self.page.set_settings({"allocated": [CHOICE_ID] if allocated else [],
                                "choices": {CHOICE_ID: choice_id}}, force=True)

    def test_game_option_numbers_match_dropdown_selection_paint_and_hover(self):
        """Verify game option numbers match dropdown selection paint and hover."""
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
        """Verify saved options paint their number including third option."""
        for number, name in enumerate(("Pack Size", "Effectiveness", "Rarity"), 1):
            with self.subTest(number=number):
                self.set_choice(number)
                self.assertEqual(self.painted_labels(), [str(number)])
                self.assertIn(f"Selected effect: {number}. {name}", self.tooltip_text())

    def test_clicking_badge_turns_node_off_without_losing_saved_choice(self):
        """Verify clicking badge turns node off without losing saved choice."""
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
        """Verify autofill and clear preserve choice without painting inactive badge."""
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
        """Verify unset choice has no option number or invented default."""
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
        """Verify number comes from catalog order instead of variant ID suffix."""
        node = self.data["nodes"][CHOICE_ID]
        node["choices"][:] = [node["choices"][2], node["choices"][0], node["choices"][1]]
        self.page.set_settings({"allocated": [CHOICE_ID],
                                "choices": {CHOICE_ID: "AtlasMonsterPackSizeSelector1a"}}, force=True)
        self.assertEqual(self.painted_labels(), ["2"])
        self.assertIn("Selected effect: 2. Pack Size", self.tooltip_text())

    def test_sidepanel_keeps_actual_seven_and_eight_option_lists(self):
        """Verify sidepanel keeps actual seven and eight option lists."""
        for node_id in ("AtlasEssenceNotable13", "AtlasEssenceNotable11"):
            with self.subTest(node_id=node_id):
                options = copy.deepcopy(bundled_catalog()["nodes"][node_id]["choices"])
                self.data["nodes"][CHOICE_ID]["choices"][:] = options
                self.page.set_settings({}, force=True)
                self.click_choice_face()
                self.page.choice_combo.hidePopup()
                self.page.choice_combo.setCurrentIndex(len(options))
                self.assertEqual(self.painted_labels(), [str(len(options))])
                self.assertEqual(self.page.choice_combo.currentData(), options[-1]["id"])
                text = self.page.node_effects.toPlainText()
                self.assertIn(f"Selected effect: {len(options)}.", text)
                self.assertIn("Available effects", text)
                for number, option in enumerate(options, 1):
                    self.assertIn(f"{number}. {option['name']}", text)
                    for effect in option["effects"]:
                        self.assertIn(effect, text)
                self.click_choice_face()
                self.assertIn(f"Saved choice (inactive): {len(options)}.", self.page.node_effects.toPlainText())
                for number, option in enumerate(options, 1):
                    self.assertIn(f"{number}. {option['name']}", self.page.node_effects.toPlainText())

    def test_hovering_between_badge_and_icon_keeps_node_hovered(self):
        """Verify hovering between badge and icon keeps node hovered."""
        item = self.page.node_items[CHOICE_ID]
        self.page.view.resetTransform()
        self.page.view.centerOn(item)
        viewport = self.page.view.viewport()
        QTest.mouseMove(viewport, QPoint(5, 5))
        self.app.processEvents()
        for scene_point in (item.choice_badge.scenePos(), item.scenePos()):
            QTest.mouseMove(viewport, self.page.view.mapFromScene(scene_point))
            self.app.processEvents()
            self.assertTrue(item.hovered)
            self.assertTrue(QToolTip.isVisible())

    def test_fitted_bundled_tree_badge_is_readable_clickable_and_tracks_zoom(self):
        # Exercise the real 8,000-unit tree where scene-scaled option labels
        # used to shrink below two pixels, and click outside the tiny icon.
        # The offscreen Qt platform routes hover to overlapping top-level
        # windows inconsistently. Keep only the page under test visible,
        # as it is when opened inside the logger's single window.
        """Verify fitted bundled tree badge is readable clickable and tracks zoom."""
        self.page.hide()
        data = bundled_catalog()
        with patch("PoE2_Data_Logger.ui.atlas_settings.catalog", return_value=data):
            page = AtlasSettingsPage()
        try:
            page.resize(1200, 760)
            page.show()
            self.app.processEvents()
            page.fit_button.click()
            item = page.node_items[CHOICE_ID]
            badge = item.choice_badge
            self.assertLess(page.view.transform().m11(), .1)
            original_bounds = item.sceneBoundingRect()
            for zoom in (1, 2.5, .4):
                page.view.zoom(zoom)
                device_bounds = badge.deviceTransform(page.view.viewportTransform()).mapRect(badge.boundingRect())
                self.assertEqual(round(device_bounds.width()), 30)
                self.assertEqual(round(device_bounds.height()), 30)
                self.assertEqual(item.sceneBoundingRect(), original_bounds)
            page.fit_button.click()
            point = page.view.mapFromScene(badge.scenePos()) + QPoint(11, 0)
            self.assertFalse(item.shape().contains(item.mapFromScene(page.view.mapToScene(point))))
            QTest.mouseMove(page.view.viewport(), QPoint(5, 5))
            self.app.processEvents()
            QTest.mouseMove(page.view.viewport(), point)
            self.app.processEvents()
            self.assertTrue(item.hovered)
            self.assertTrue(QToolTip.isVisible())
            self.assertEqual(page.settings()["allocated"], [])
            QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.app.processEvents()
            self.assertEqual(page._selected_node, CHOICE_ID)
            self.assertIn(CHOICE_ID, page.settings()["allocated"])
            self.assertTrue(page.choice_combo.view().isVisible())
            index = page.choice_combo.model().index(3, 0)
            QTest.mouseClick(page.choice_combo.view().viewport(), Qt.MouseButton.LeftButton,
                             pos=page.choice_combo.view().visualRect(index).center())
            self.app.processEvents()
            self.assertEqual(item.choice_number, 3)
            self.assertEqual(page.choice_combo.currentData(), data["nodes"][CHOICE_ID]["choices"][2]["id"])
            self.assertIn("Selected effect: 3.", page.node_effects.toPlainText())
            for passive in page.node_items.values():
                if not passive.node.get("choices"):
                    self.assertIsNone(passive.choice_badge)
            page.resize(1000, 600)
            page.activity_filter.setCurrentIndex(0)
            self.app.processEvents()
            page.fit_button.click()
            viewport = QRectF(page.view.viewport().rect())
            for passive in page.node_items.values():
                if passive.choice_badge is not None:
                    marker = passive.choice_badge
                    device_bounds = marker.deviceTransform(page.view.viewportTransform()).mapRect(marker.boundingRect())
                    self.assertTrue(viewport.contains(device_bounds), passive.node_id)
        finally:
            page.choice_combo.hidePopup()
            page.close()
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
