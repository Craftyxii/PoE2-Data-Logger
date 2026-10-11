"""Keep Developer Mode catalog edits owned by their selected family across unrelated refreshes."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog
from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import IconCropCanvas, LoggerWindow, select, value


class CatalogEditorDraftTests(unittest.TestCase):
    """Exercise real setting saves and export completion without silently replacing catalog drafts."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application required for worker completion and editor signals."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated visible Developer Mode profile with automatic polling stopped."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-catalog-editor-drafts-")
        self.previous = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.developer_mode.setChecked(True)
        self.window.tabs.setCurrentIndex(5)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close this window's workers and restore shared storage and hotkey mode ownership."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def enter_family_draft(self):
        """Enter distinguishable edits in every family field using the existing New family selection."""
        self.window.family_id.setText("901")
        self.window.family_top.setText("9")
        self.window.family_active.setChecked(False)
        self.window.family_recipes.setPlainText("Chaos Orb x3\nRegal Orb x3")
        return self.window._family_form()

    def test_new_family_draft_survives_unrelated_setting_save(self):
        """Save an Atlas Master through the real action while retaining the whole family draft."""
        draft = self.enter_family_draft()
        self.window.save_active_master()
        self.assertEqual(self.window._family_form(), draft)
        self.assertEqual(value(self.window.family_pick), "")
        self.window.refresh()
        self.assertEqual(self.window._family_form(), draft)

    def test_add_reference_crop_is_one_undo_action_including_its_new_name(self):
        """Use the real crop dialog then reverse one Add screenshot command's new label and artwork together."""
        image = Image.new("RGB", (96, 96), (15, 20, 45))
        ImageDraw.Draw(image).ellipse((12, 12, 84, 84), fill=(200, 100, 50))
        path = Path(self.tmp.name) / "reference.png"
        image.save(path)
        for kind, names, icons, name in (
                ("currency", logger.currency_names, logger.currency_icons, "Undo Crop Currency"),
                ("item", logger.item_names, logger.item_icons, "Undo Crop Item"),
                ("omen", logger.ritual_names, logger.omen_icons, "Omen of Undo Crop")):
            with self.subTest(kind=kind):
                select(self.window.reference_kind, kind)
                self.window.reference_name.setEditText(name)
                original_action = logger.undo_action()
                with logger._connect() as db:
                    before = db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0]
                errors = []

                def crop_and_save():
                    """Drag a crop and click Save through the live modal dialog's event loop."""
                    dialog = self.app.activeModalWidget()
                    try:
                        self.assertIsInstance(dialog, QDialog)
                        canvas = dialog.findChild(IconCropCanvas)
                        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(0, 0))
                        QTest.mouseMove(canvas, QPoint(95, 95))
                        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(95, 95))
                        buttons = dialog.findChild(QDialogButtonBox)
                        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
                    except Exception as error:
                        errors.append(error)
                        if dialog:
                            dialog.reject()

                QTimer.singleShot(0, crop_and_save)
                with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "Images")):
                    self.window.add_reference_screenshot()
                self.assertFalse(errors, errors)
                self.assertIn(name, names())
                self.assertTrue(any(icon["name"] == name for icon in icons()))
                with logger._connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], before + 1)
                self.window.undo_map()
                self.assertNotIn(name, names())
                self.assertFalse(any(icon["name"] == name for icon in icons()))
                self.assertEqual(logger.undo_action(), original_action)
    def test_existing_family_draft_survives_real_export_completion(self):
        """Pump completion events while yielding to the real CSV worker, then verify retained family edits."""
        self.window.family_pick.setCurrentIndex(self.window.family_pick.findData(26))
        draft = self.enter_family_draft()
        self.window.export_folder.setText(self.tmp.name)
        self.window.write_export("csv")
        deadline = time.monotonic() + 10
        while self.window._pending_tasks and time.monotonic() < deadline:
            # Drain queued UI completion and release the GIL for the Python
            # export worker; repeated native qWait calls can starve that worker.
            self.app.processEvents()
            time.sleep(.01)
        self.assertFalse(self.window._pending_tasks, "The actual export must complete during this test.")
        self.assertIn("Export saved:", self.window.statusBar().currentMessage())
        self.assertTrue(list(Path(self.tmp.name).glob("*.csv")))
        self.assertEqual(value(self.window.family_pick), 26)
        self.assertEqual(self.window._family_form(), draft)

    def test_seed_mapping_target_and_fields_survive_unrelated_save(self):
        """Keep the selected seed family attached to its edited mapping across refresh."""
        select(self.window.edit_seed_family, 26)
        self.window.edit_seed_sockets.setText("3")
        self.window.edit_seed_slot.setText("P1")
        self.window.edit_seed_rune.setText("Death")
        self.window.edit_seed_rewards.setPlainText("Chaos Orb x3")
        self.window.save_active_master()
        self.assertEqual(value(self.window.edit_seed_family), 26)
        self.assertEqual(self.window.edit_seed_sockets.text(), "3")
        self.assertEqual(self.window.edit_seed_slot.text(), "P1")
        self.assertEqual(self.window.edit_seed_rune.text(), "Death")
        self.assertEqual(self.window.edit_seed_rewards.toPlainText(), "Chaos Orb x3")

    def test_explicit_family_selection_loads_the_selected_saved_record(self):
        """Changing families deliberately replaces the previous draft and establishes a new clean baseline."""
        self.enter_family_draft()
        self.window.save_active_master()
        self.window.family_pick.setCurrentIndex(self.window.family_pick.findData(26))
        family = next(entry for entry in self.window.state["families"] if entry["id"] == 26)
        expected = ("26", str(family["top_socket"]), family["valid"], "\n".join(family["recipes"]))
        self.assertEqual(self.window._family_form(), expected)
        self.window.refresh()
        self.assertEqual(self.window._family_form(), expected)
        self.window.family_pick.setCurrentIndex(0)
        self.assertEqual(self.window.family_recipes.toPlainText(), "")
        self.assertNotEqual(self.window.family_id.text(), "901")

    def test_family_search_rows_keep_their_editor_after_selector_changes(self):
        """Double-click a displayed Family result after choosing Recipe DB for the next search."""
        self.window.search_catalog()
        row = next(index for index, entry in enumerate(self.window._catalog_rows)
                   if entry["family"] == 26)
        self.window.catalog_kind.setCurrentIndex(self.window.catalog_kind.findData("recipes"))
        self.assertEqual(self.window.catalog_table.horizontalHeaderItem(0).text(), "Family")
        self.window.catalog_table.cellDoubleClicked.emit(row, 0)
        self.assertEqual(value(self.window.family_pick), 26)
        self.assertEqual(self.window.family_id.text(), "26")
        self.assertEqual(value(self.window.catalog_kind), "recipes")

    def test_recipe_search_rows_keep_their_editor_after_selector_changes(self):
        """Double-click a displayed Recipe result after choosing Family DB for the next search."""
        self.window.catalog_kind.setCurrentIndex(self.window.catalog_kind.findData("recipes"))
        self.window.catalog_query.setText("Regal Orb x3")
        self.window.search_catalog()
        row = next(index for index, entry in enumerate(self.window._catalog_rows)
                   if entry["name"] == "Regal Orb x3")
        result = self.window._catalog_rows[row]
        self.window.catalog_kind.setCurrentIndex(self.window.catalog_kind.findData("families"))
        self.assertEqual(self.window.catalog_table.horizontalHeaderItem(0).text(), "Name")
        self.window.catalog_table.cellDoubleClicked.emit(row, 0)
        self.assertEqual(self.window.edit_recipe_name.text(), result["name"])
        self.assertEqual(self.window.edit_recipe_sockets.text(), str(result["sockets"]))
        self.assertEqual(value(self.window.catalog_kind), "families")


if __name__ == "__main__":
    unittest.main()
