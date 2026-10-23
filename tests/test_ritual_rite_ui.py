"""Optional Rite metadata preserves explicit saves, map ownership and source wording."""

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt

from tests import test_hotkey_presentation_retry as fixtures
from PoE2_Data_Logger.core import logger_store as logger, ritual_rites_catalog
from PoE2_Data_Logger.ui import native_desktop


class RitualRiteUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.HotkeyPresentationRetryTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.HotkeyPresentationRetryTests(
            "test_opened_result_and_png_stay_owned_until_writer_releases")
        self.fixture.setUp()
        self.window = self.fixture.window
        self.catalog = ritual_rites_catalog.affixes()
        # Rites apply only while Ritual is the selected Activity Type.
        logger.save_settings({"activity_type": "Ritual"})
        self.window.refresh()

    def tearDown(self):
        self.fixture.tearDown()

    def new_rite(self, name):
        with patch.object(native_desktop.QInputDialog, "getText", return_value=(name, True)):
            self.window.new_ritual_rite()
        return logger.ritual_rite_state()["selected_rite_id"]

    def select_rite(self, rite_id):
        field = self.window.ritual_rite_select
        field.setCurrentIndex(field.findData(rite_id))

    def set_row(self, number, entry, count="1", kind="local"):
        while len(self.window._ritual_rite_rows) <= number:
            self.window.add_ritual_rite_affix()
        field, amount, selected_kind = self.window._ritual_rite_rows[number]
        if isinstance(entry, dict):
            field.setCurrentIndex(field.findData(entry["id"]))
        else:
            field.setCurrentText(entry)
            selected_kind.setCurrentIndex(selected_kind.findData(kind))
        amount.setText(count)
        return field, amount, selected_kind

    def test_appended_optional_page_preserves_navigation_and_has_all_full_text_choices(self):
        titles = ("Review", "Expedition", "Map / Tablets", "Atlas Masters", "Currency", "Data & export",
                  "Scan settings", "Scan regions", "Database Import/Export", "README", "Licenses", "Disclaimer",
                  "Atlas / Character Settings", "OCR Sensitivity")
        self.assertEqual(tuple(self.window.tabs.tabText(i) for i in range(14)), titles)
        self.assertEqual(self.window.ritual_rite_page_index, 14)
        ritual_nav = next(item for item in self.window.nav_buttons if item.text() == "Ritual")
        ritual_nav.click()
        self.assertEqual(self.window.tabs.currentIndex(), 14)
        self.assertEqual(self.window.page_title.text(), "Ritual")
        self.assertEqual(ritual_nav.property("active"), "true")
        self.assertEqual(self.window.ritual_rite_select.currentText(), "None")
        field, count, kind = self.window._ritual_rite_rows[0]
        self.assertTrue(field.isEditable())
        self.assertEqual(field.count(), 74)
        self.assertEqual([field.itemText(i) for i in range(1, field.count())], [row["text"] for row in self.catalog])
        self.assertEqual(count.text(), "1")
        self.assertTrue(kind.isEnabled())
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
        self.window.nav_buttons[6].click()
        self.assertEqual(self.window.tabs.currentIndex(), 6)
        self.window.nav_buttons[-1].click()
        self.assertEqual(self.window.tabs.currentIndex(), 12)

    def test_local_draft_survives_refresh_none_and_new_rite_until_explicit_save(self):
        known = next(row for row in self.catalog if row["kind"] == "bounty")
        field, _, kind = self.set_row(0, known, "2")
        self.assertEqual(kind.currentData(), "bounty")
        self.assertFalse(kind.isEnabled())
        self.set_row(1, "Future local wording with 17% text", "3")
        draft = self.window._ritual_rite_form()
        first_id = self.new_rite("First Rite")
        second_id = self.new_rite("Second Rite")
        for selected in (None, first_id, second_id):
            self.select_rite(selected)
            self.window.refresh()
            self.assertEqual(self.window._ritual_rite_form(), draft)
            self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
            self.assertIs(self.window._ritual_rite_rows[0][0], field)
        self.window.save_ritual_rite_affixes()
        saved = logger.ritual_rite_state()
        self.assertEqual(saved["selected_rite_id"], second_id)
        self.assertEqual(saved["current_affixes"][0]["id"], known["id"])
        self.assertEqual(saved["current_affixes"][0]["count"], 2)
        self.assertEqual(saved["current_affixes"][1]["text"], "Future local wording with 17% text")
        self.assertTrue(saved["current_affixes"][1]["custom"])

    def test_only_completed_proliferation_rows_appear_with_origin_map_and_rite(self):
        rite_id = self.new_rite("Observed Rite")
        local = next(row for row in self.catalog if row["kind"] == "local")
        bounty = next(row for row in self.catalog if row["kind"] == "bounty")
        proliferation = next(row for row in self.catalog if row["kind"] == "proliferation")
        self.set_row(0, local)
        self.set_row(1, bounty)
        self.set_row(2, proliferation, "2")
        self.set_row(3, proliferation, "3")
        self.window.save_ritual_rite_affixes()
        table = self.window.ritual_rite_inherited_table
        self.assertEqual(table.rowCount(), 0)
        self.window.finish_map()
        self.assertEqual(logger.get_state()["current_map_id"], "M0002")
        self.assertEqual(logger.ritual_rite_state()["selected_rite_id"], rite_id)
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual([table.item(row, 1).text() for row in range(2)], ["2", "3"])
        for row in range(2):
            self.assertEqual(table.item(row, 0).text(), proliferation["inherited_text"])
            self.assertEqual(table.item(row, 2).text(), "M0001")
            self.assertEqual(table.item(row, 3).text(), "Observed Rite")
            self.assertFalse(table.item(row, 0).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertEqual(self.window._ritual_rite_rows[0][0].currentText(), "")
        self.select_rite(None)
        self.assertEqual(table.rowCount(), 0)
        self.select_rite(rite_id)
        self.assertEqual(table.rowCount(), 2)

    def test_custom_proliferation_and_counts_survive_restart_without_changing_wording(self):
        rite_id = self.new_rite("Custom Rite")
        text = "Foretold Proliferation: Preserve this future 17% modifier verbatim"
        _, _, kind = self.set_row(0, text, "4", "proliferation")
        self.assertTrue(kind.isEnabled())
        self.window.save_ritual_rite_affixes()
        original = logger.ritual_rite_state()["current_affixes"][0]
        self.assertEqual(original["inherited_text"], "Preserve this future 17% modifier verbatim")
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        logger._READY = False
        logger.initialize()
        self.fixture.window = self.window = native_desktop.LoggerWindow()
        self.window._poll.stop()
        self.assertEqual(self.window.ritual_rite_select.currentData(), rite_id)
        field, count, kind = self.window._ritual_rite_rows[0]
        self.assertEqual((field.currentText(), count.text(), kind.currentData()), (text, "4", "proliferation"))
        self.window.save_ritual_rite_affixes()
        self.assertEqual(logger.ritual_rite_state()["current_affixes"][0], original)

    def test_stale_map_editor_cannot_save_to_new_map_and_refresh_discards_old_draft(self):
        self.set_row(0, "Old map local draft", "1")
        logger.finish_map(None, None, None)
        logger.start_map()
        with self.assertRaisesRegex(ValueError, "map changed"):
            self.window.save_ritual_rite_affixes()
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
        self.window.refresh()
        self.assertEqual(self.window._ritual_rite_rows[0][0].currentText(), "")
        self.set_row(0, "No Rite selected local observation", "2")
        self.window.save_ritual_rite_affixes()
        state = logger.ritual_rite_state()
        self.assertIsNone(state["selected_rite_id"])
        self.assertEqual(state["current_affixes"][0]["count"], 2)
        self.assertEqual(state["inherited_sources"], [])

    def test_invalid_count_stays_editable_until_corrected_and_save_allows_no_affixes(self):
        self.set_row(0, "Count needs correction", "3.5")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.window.save_ritual_rite_affixes()
        self.window.refresh()
        self.assertEqual(self.window._ritual_rite_rows[0][1].text(), "3.5")
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
        self.window._ritual_rite_rows[0][1].setText("3")
        self.window.save_ritual_rite_affixes()
        self.window.ritual_rite_affix_table.selectRow(0)
        self.window.remove_ritual_rite_affix()
        self.window.save_ritual_rite_affixes()
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])

    def test_reset_to_reused_map_id_rejects_old_form_and_keeps_explicit_rite_selection(self):
        rite_id = self.new_rite("Retained Rite")
        self.set_row(0, "Previous session unsaved observation", "2")
        self.assertEqual(self.window._ritual_rite_form_context[1], "M0001")
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.assertEqual(logger.ritual_rite_state()["map_id"], "M0001")
        with self.assertRaisesRegex(ValueError, "session changed"):
            self.window.save_ritual_rite_affixes()
        self.assertEqual(logger.ritual_rite_state()["current_affixes"], [])
        self.window.refresh()
        self.assertEqual(self.window.ritual_rite_select.currentData(), rite_id)
        self.assertEqual(self.window._ritual_rite_rows[0][0].currentText(), "")


if __name__ == "__main__":
    unittest.main()
