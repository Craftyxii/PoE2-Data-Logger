"""Qt Atlas graph interaction checks against a small fixture catalog: allocation, choice selection and preserved drafts."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip
from PySide6.QtGui import QTextDocument, QValidator

from PoE2_Data_Logger.core.atlas_catalog import catalog as bundled_catalog
from PoE2_Data_Logger.ui.atlas_settings import AtlasSettingsPage


def fixture_catalog():
    """Build a small Atlas graph with choices, activities and nonallocatable nodes."""
    def node(node_id, x, y, activity="Main Atlas", kind="small", choices=None):
        """Construct a fixture node with effects and allocation rules for its kind."""
        return {"id": node_id, "hash": node_id, "name": node_id.title(),
                "activity": activity, "x": x, "y": y, "kind": kind,
                "allocatable": kind not in ("root", "decorative"),
                "effects": [f"Effect for {node_id}"] if kind not in ("root", "decorative") else [],
                "stats": {},
                "choices": choices or []}
    choices = [{"id": "desert", "name": "Desert", "effects": ["10% rarity in Desert"], "stats": {}},
               {"id": "swamp", "name": "Swamp", "effects": ["10% rarity in Swamp"], "stats": {}}]
    return {"version": "test-1", "activities": ["Main Atlas", "Expedition"],
            "nodes": {"root": node("root", 0, 0, kind="root"),
                      "rarity": node("rarity", 180, 0),
                      "choice": node("choice", 360, 0, kind="notable", choices=choices),
                      "maps": node("maps", 180, 180),
                      "expedition": node("expedition", 900, 0, activity="Expedition"),
                      "decoration": node("decoration", 0, 260, kind="decorative")},
            "edges": [["root", "rarity"], ["rarity", "choice"], ["rarity", "maps"]]}


class AtlasEditorTests(unittest.TestCase):
    """Exercise Atlas allocation, choices, draft persistence and hover interactions."""
    @classmethod
    def setUpClass(cls):
        """Create the shared Qt application for Atlas widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an Atlas editor backed by the small fixture catalog."""
        self.catalog_patch = patch("PoE2_Data_Logger.ui.atlas_settings.catalog", return_value=fixture_catalog())
        self.catalog_patch.start()
        self.page = AtlasSettingsPage()
        self.page.resize(1200, 760)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        """Dismiss popups, dispose of the editor and restore the catalog provider."""
        QToolTip.hideText()
        self.page.choice_combo.hidePopup()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.catalog_patch.stop()

    def click_node(self, node_id):
        """Click the requested scene node and process its Qt events."""
        item = self.page.node_items[node_id]
        point = self.page.view.mapFromScene(item.pos())
        QTest.mouseClick(self.page.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()

    def hover_node(self, node_id):
        """Hover a scene node and return its visible tooltip as plain text."""
        viewport = self.page.view.viewport()
        QTest.mouseMove(viewport, QPoint(5, 5))
        self.app.processEvents()
        point = self.page.view.mapFromScene(self.page.node_items[node_id].pos())
        QTest.mouseMove(viewport, point)
        self.app.processEvents()
        self.assertTrue(QToolTip.isVisible(), "Node hover should display the effects popup immediately")
        document = QTextDocument()
        document.setHtml(QToolTip.text())
        return document.toPlainText()

    def test_click_allocates_lights_node_and_click_again_removes(self):
        """Verify node clicks toggle allocation and its connected edge color."""
        self.click_node("rarity")
        self.assertEqual(self.page.settings()["allocated"], ["rarity"])
        self.assertTrue(self.page.node_items["rarity"].allocated)
        root_edge = next(item for left, right, item in self.page.edge_items if left == "root")
        self.assertEqual(root_edge.pen().color().name(), "#e5b660")
        self.click_node("rarity")
        self.assertEqual(self.page.settings()["allocated"], [])
        self.assertFalse(self.page.node_items["rarity"].allocated)
        self.assertEqual(root_edge.pen().color().name(), "#4a4030")

    def test_choice_node_opens_dropdown_and_selection_survives_toggle(self):
        """Verify a selected choice survives turning its node off."""
        self.click_node("choice")
        self.assertTrue(self.page.choice_combo.view().isVisible())
        self.assertIn("awaiting a selection", self.page.summary.text())
        index = self.page.choice_combo.model().index(2, 0)
        point = self.page.choice_combo.view().visualRect(index).center()
        QTest.mouseClick(self.page.choice_combo.view().viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertEqual(self.page.settings()["choices"], {"choice": "swamp"})
        self.assertIn("10% rarity in Swamp", self.page.node_effects.toPlainText())
        self.click_node("choice")
        self.assertNotIn("choice", self.page.settings()["allocated"])
        self.assertFalse(self.page.choice_combo.view().isVisible())
        self.assertEqual(self.page.settings()["choices"], {"choice": "swamp"})

    def test_autofill_allocates_all_activities_preserving_choices_and_gear(self):
        """Verify autofill and clear retain saved choices and gear rarity."""
        self.page.set_settings({"allocated": [], "choices": {"choice": "desert"}, "gear_item_rarity": 187.25})
        self.page.autofill_button.click()
        self.assertEqual(self.page.settings()["allocated"], ["choice", "expedition", "maps", "rarity"])
        self.assertEqual(self.page.settings()["choices"], {"choice": "desert"})
        self.assertEqual(self.page.settings()["gear_item_rarity"], 187.25)
        self.assertNotIn("awaiting", self.page.summary.text())
        self.page.clear_button.click()
        self.assertEqual(self.page.settings()["allocated"], [])
        self.assertEqual(self.page.settings()["choices"], {"choice": "desert"})

    def test_repeated_choice_node_click_closes_dropdown_and_turns_node_off(self):
        """Verify a second choice-node click closes the popup and removes allocation."""
        self.click_node("choice")
        self.assertTrue(self.page.choice_combo.view().isVisible())
        self.click_node("choice")
        self.assertFalse(self.page.choice_combo.view().isVisible())
        self.assertEqual(self.page.settings()["allocated"], [])

    def test_autofill_does_not_invent_a_choice_or_allocate_root_decoration(self):
        """Verify autofill leaves choices unset and skips nonallocatable artwork."""
        self.page.autofill_button.click()
        self.assertEqual(self.page.settings()["choices"], {})
        self.assertNotIn("root", self.page.settings()["allocated"])
        self.assertNotIn("decoration", self.page.settings()["allocated"])
        self.assertIn("1 allocated choice node awaiting a selection", self.page.summary.text())

    def test_filters_and_zoom_keep_model_and_can_click_other_activity(self):
        """Verify activity filtering and zoom preserve allocation across activities."""
        self.click_node("rarity")
        before = self.page.view.transform().m11()
        self.page.zoom_in_button.click()
        self.assertGreater(self.page.view.transform().m11(), before)
        self.page.activity_filter.setCurrentIndex(self.page.activity_filter.findData("Expedition"))
        self.assertFalse(self.page.node_items["rarity"].isVisible())
        self.assertTrue(self.page.node_items["expedition"].isVisible())
        self.click_node("expedition")
        self.assertEqual(self.page.settings()["allocated"], ["expedition", "rarity"])
        self.page.activity_filter.setCurrentIndex(0)
        self.assertTrue(self.page.node_items["rarity"].isVisible())
        self.assertTrue(self.page.node_items["expedition"].isVisible())

    def test_background_refresh_preserves_draft_and_explicit_reload_resets(self):
        """Verify ordinary refresh protects a dirty draft while forced reload replaces it."""
        self.click_node("rarity")
        self.page.gear_rarity.setValue(75)
        self.assertTrue(self.page.dirty)
        self.assertFalse(self.page.set_settings({}, "Applies to next map"))
        self.assertEqual(self.page.settings()["allocated"], ["rarity"])
        self.assertEqual(self.page.settings()["gear_item_rarity"], 75)
        self.assertEqual(self.page.status.text(), "Applies to next map")
        self.assertTrue(self.page.set_settings({}, force=True))
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.settings()["allocated"], [])
        self.assertIsNone(self.page.settings()["gear_item_rarity"])

    def test_save_emits_complete_payload_and_keeps_failed_save_draft(self):
        """Verify save emits all settings and clears dirty state only after reload."""
        saved = []
        self.page.saved.connect(saved.append)
        self.click_node("rarity")
        self.page.gear_rarity.setValue(0)
        self.page.save_button.click()
        self.assertEqual(saved, [{"catalog_version": "test-1", "allocated": ["rarity"],
                                 "choices": {}, "gear_item_rarity": 0}])
        self.assertTrue(self.page.dirty)
        self.page.set_settings(saved[0], "Saved", force=True)
        self.assertFalse(self.page.dirty)
        self.assertEqual(self.page.settings()["gear_item_rarity"], 0)

    def test_root_is_inspectable_but_cannot_change_allocation(self):
        """Verify inspecting a root neither allocates it nor dirties settings."""
        self.click_node("root")
        self.assertEqual(self.page.node_title.text(), "Root")
        self.assertEqual(self.page.settings()["allocated"], [])
        self.assertFalse(self.page.dirty)

    def test_scene_is_lazy_until_page_opens(self):
        """Verify lazy tree creation applies settings loaded before the scene exists."""
        page = AtlasSettingsPage()
        try:
            self.assertEqual(page.node_items, {})
            page.set_settings({"allocated": ["rarity"], "gear_item_rarity": 25})
            page.ensure_tree()
            self.assertTrue(page.node_items["rarity"].allocated)
            self.assertEqual(page.settings()["gear_item_rarity"], 25)
        finally:
            page.deleteLater()

    def test_gear_validation_matches_backend_range_and_rejects_negative_stat(self):
        """Verify gear rarity limits reject negatives and allow explicit zero."""
        self.assertEqual(self.page.gear_rarity.maximum(), 9999)
        self.assertEqual(self.page.gear_rarity.validate("-0.50 %", 0)[0], QValidator.State.Invalid)
        self.page.gear_rarity.stepUp()
        self.assertEqual(self.page.settings()["gear_item_rarity"], 0)

    def test_gear_rarity_accepts_typing_after_clicking_unknown_value(self):
        """Verify typing into either rarity control accepts zero and decimal values."""
        field = self.page.gear_rarity
        for target in (field, field.lineEdit()):
            for text, expected in (("125.5", 125.5), ("0", 0), (".75", .75)):
                with self.subTest(target=type(target).__name__, text=text):
                    self.page.set_settings({}, force=True)
                    QTest.mouseClick(field.lineEdit(), Qt.MouseButton.LeftButton)
                    QTest.keyClicks(target, text)
                    QTest.keyClick(target, Qt.Key.Key_Tab)
                    self.assertEqual(self.page.settings()["gear_item_rarity"], expected)
                    self.assertTrue(self.page.dirty)

    def test_gear_rarity_paste_and_edit_keep_the_typed_number(self):
        """Verify pasted rarity remains editable and saves the edited number."""
        field = self.page.gear_rarity
        self.app.clipboard().setText("187.25")
        QTest.mouseClick(field.lineEdit(), Qt.MouseButton.LeftButton)
        QTest.keyClick(field.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.page.settings()["gear_item_rarity"], 187.25)
        field.lineEdit().setCursorPosition(1)
        QTest.keyClick(field.lineEdit(), Qt.Key.Key_Delete)
        self.assertEqual(self.page.settings()["gear_item_rarity"], 17.25)
        saved = []
        self.page.saved.connect(saved.append)
        self.page.save_button.click()
        self.assertEqual(saved[0]["gear_item_rarity"], 17.25)

    def test_bundled_main_tree_art_and_all_activity_filters_load(self):
        """Verify the bundled graph and artwork load for every activity filter."""
        data = bundled_catalog()
        with patch("PoE2_Data_Logger.ui.atlas_settings.catalog", return_value=data):
            page = AtlasSettingsPage()
        try:
            page.resize(1200, 760)
            page.show()
            self.app.processEvents()
            self.assertEqual(page.activity_filter.currentData(), "Main Atlas")
            self.assertTrue(page._background_items[0].isVisible())
            self.assertEqual(len(page.node_items), len(data["nodes"]))
            for activity in data["activities"]:
                page.activity_filter.setCurrentIndex(page.activity_filter.findData(activity))
                visible = [item.node["activity"] for item in page.node_items.values() if item.isVisible()]
                self.assertTrue(visible)
                self.assertEqual(set(visible), {activity})
            self.assertFalse(page.dirty)
        finally:
            page.close()
            page.deleteLater()

    def test_hover_shows_effects_without_allocating_or_selecting_node(self):
        """Verify hover shows node effects without changing allocation or selection."""
        before = self.page.settings()
        text = self.hover_node("maps")
        self.assertIn("Maps", text)
        self.assertIn("Main Atlas", text)
        self.assertIn("Not allocated", text)
        self.assertIn("Effect for maps", text)
        self.assertEqual(self.page.settings(), before)
        self.assertIsNone(self.page._selected_node)
        self.assertEqual(self.page.node_title.text(), "Select an Atlas node")
        self.assertFalse(self.page.dirty)

    def test_hover_does_not_replace_selection_or_existing_unsaved_draft(self):
        """Verify hover preserves the selected node and unsaved rarity draft."""
        self.click_node("rarity")
        self.page.gear_rarity.setValue(150)
        before = self.page.settings()
        self.assertTrue(self.page.dirty)
        self.hover_node("maps")
        self.assertEqual(self.page.settings(), before)
        self.assertEqual(self.page._selected_node, "rarity")
        self.assertEqual(self.page.node_title.text(), "Rarity")
        self.assertTrue(self.page.dirty)

    def test_unset_choice_hover_lists_all_available_effects(self):
        """Verify an unset choice tooltip lists every option without opening selection."""
        text = self.hover_node("choice")
        self.assertIn("Effect for choice", text)
        self.assertIn("Choose one effect", text)
        self.assertIn("Desert", text)
        self.assertIn("10% rarity in Desert", text)
        self.assertIn("Swamp", text)
        self.assertIn("10% rarity in Swamp", text)
        self.assertEqual(self.page.settings()["choices"], {})
        self.assertFalse(self.page.choice_combo.isVisible())

    def test_selected_choice_hover_shows_only_selected_effect(self):
        """Verify an allocated choice tooltip shows only its selected option."""
        self.page.set_settings({"allocated": ["choice"], "choices": {"choice": "swamp"}})
        text = self.hover_node("choice")
        self.assertIn("Allocated", text)
        self.assertIn("Effect for choice", text)
        self.assertIn("Swamp", text)
        self.assertIn("10% rarity in Swamp", text)
        self.assertNotIn("10% rarity in Desert", text)
        self.assertEqual(self.page.settings()["choices"], {"choice": "swamp"})
        self.assertFalse(self.page.dirty)

    def test_off_node_hover_labels_saved_choice_as_inactive(self):
        """Verify an unallocated node labels its retained choice as inactive."""
        self.page.set_settings({"allocated": [], "choices": {"choice": "swamp"}})
        text = self.hover_node("choice")
        self.assertIn("Saved choice (inactive)", text)
        self.assertIn("10% rarity in Swamp", text)
        self.assertNotIn("Selected effect", text)
        self.assertEqual(self.page.settings()["allocated"], [])

    def test_hover_popup_disappears_when_mouse_leaves_or_tree_hides(self):
        """Verify leaving or hiding the tree dismisses its hover popup."""
        self.hover_node("maps")
        QTest.mouseMove(self.page.view.viewport(), QPoint(5, 5))
        QTest.qWait(350)
        self.assertFalse(QToolTip.isVisible())
        self.hover_node("maps")
        self.page.view.hide()
        QTest.qWait(350)
        self.assertFalse(QToolTip.isVisible())

    def test_hover_renders_multiline_effect_safely(self):
        """Verify multiline effects preserve text while escaping tooltip markup."""
        self.page.node_items["maps"].node["effects"] = ["First line\nSecond line <tag> & details"]
        self.page._update_visuals()
        text = self.hover_node("maps")
        self.assertIn("First line\nSecond line <tag> & details", text)
        self.assertIn("&lt;tag&gt;", QToolTip.text())

    def test_clicking_node_dismisses_effects_popup(self):
        """Verify allocation clicks dismiss the node's hover popup."""
        self.hover_node("maps")
        self.click_node("maps")
        QTest.qWait(350)
        self.assertFalse(QToolTip.isVisible())
        self.assertEqual(self.page.settings()["allocated"], ["maps"])

    def test_root_hover_identifies_starting_point_without_allocation_label(self):
        """Verify root tooltips describe a starting point with no allocation status."""
        text = self.hover_node("root")
        self.assertIn("Activity starting point", text)
        self.assertIn("No Atlas bonuses", text)
        self.assertNotIn("Not allocated", text)
        self.assertEqual(self.page.settings()["allocated"], [])

    def test_decoration_hover_identifies_nonallocatable_art(self):
        """Verify decoration tooltips identify artwork with no Atlas bonuses."""
        text = self.hover_node("decoration")
        self.assertIn("Atlas decoration", text)
        self.assertIn("No Atlas bonuses", text)
        self.assertNotIn("Not allocated", text)
        self.assertEqual(self.page.settings()["allocated"], [])


if __name__ == "__main__":
    unittest.main()
