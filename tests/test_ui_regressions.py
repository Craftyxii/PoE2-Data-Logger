"""Qt desktop regressions for review ownership, asynchronous callbacks, header synchronization and compact layouts."""

import io
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class UIRegressionTests(unittest.TestCase):
    """Check desktop review callbacks, header synchronization, reference ownership, and failures."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication needed by these widget tests."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create an isolated logger window and retain submitted OCR jobs without running them."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ui-regression-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        self.grid_patch = patch.object(native_desktop.item_ocr, "inventory_grid", side_effect=lambda image: image)
        self.grid_patch.start()

    def tearDown(self):
        """Stop the grid patch, close the window/workers, restore storage, and remove test data."""
        self.grid_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def image_bytes(self, color=(120, 130, 140)):
        """Encode a solid 600-by-400 RGB screenshot as PNG bytes."""
        raw = io.BytesIO()
        Image.new("RGB", (600, 400), color).save(raw, format="PNG")
        return raw.getvalue()

    def test_reference_pack_folder_button_preserves_earlier_reference_exports(self):
        """Save twice through the real button, keeping the older pack and each snapshot's names."""
        from zipfile import ZipFile
        import json
        logger.add_item_name("First shared custom item")
        folder = Path(self.tmp.name) / "reference-exports"
        folder.mkdir()
        older = folder / "PoE2_OCR_References.zip"
        older.write_bytes(b"An earlier reference pack must remain untouched")
        self.window.reference_folder.setText(str(folder))
        self.window.show()
        self.window.tabs.setCurrentIndex(8)
        self.app.processEvents()
        action = next(widget for widget in self.window.findChildren(native_desktop.QPushButton)
                      if widget.text() == "Save reference pack to folder")
        self.window.tabs.currentWidget().ensureWidgetVisible(action)
        self.app.processEvents()
        exports = []
        for index in range(2):
            if index:
                logger.add_item_name("Second shared custom item")
            QTest.mouseClick(action, Qt.MouseButton.LeftButton)
            work, done = self.jobs.pop()
            result = work()
            done(result)
            destination = Path(result["path"])
            self.assertNotIn(destination, exports)
            exports.append(destination)
            with ZipFile(destination) as pack:
                names = [entry["name"] for entry in json.loads(pack.read("manifest.json"))["data"]["item_names"]]
            self.assertIn("First shared custom item", names)
            self.assertEqual("Second shared custom item" in names, bool(index))
            self.assertEqual(older.read_bytes(), b"An earlier reference pack must remain untouched")
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["PoE2_OCR_References.zip", "PoE2_OCR_References_2.zip", "PoE2_OCR_References_3.zip"])

    def test_inventory_and_ritual_review_compact_preview_without_affecting_other_scans(self):
        """Show both item reviews at compact/wide sizes while keeping recipe previews full-size."""
        self.window.show()
        raw = self.image_bytes()
        for width in (900, 1920):
            self.window.resize(width, 1000)
            for kind in ("currency", "ritual", "waystone"):
                self.window._review_pending(kind, "Review scanned entries")
                self.window._show_image(raw)
                self.app.processEvents()
                compact = kind in ("currency", "ritual")
                self.assertEqual(self.window.preview.maximumHeight(), 182 if compact else 260)
                self.assertLessEqual(self.window.preview.pixmap().height(), 182 if compact else 260)
                self.assertEqual(self.window._review_page_layout.contentsMargins().top(), 9)
                self.assertEqual(self.window._review_latest_layout.spacing(), 5 if compact else 6)

    def test_expedition_selector_keeps_pending_snapshot_approvable(self):
        """Refuse context changes through both selectors instead of stranding a pending inventory."""
        self.window.show()
        self.window._inventory_read({"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7}],
                                     "unknown": []}, live=False)
        for control in (self.window.expedition, self.window.header_expedition):
            select(control, 2)
            with self.assertRaisesRegex(ValueError, "before changing expedition"):
                self.window.set_expedition(control)
            self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
            self.assertEqual(self.window.expedition.currentData(), 1)
            self.assertEqual(self.window.header_expedition.currentData(), 1)
        self.window.approve_scan_button.click()
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})

    def test_reference_reset_button_removes_custom_examples_but_retains_logs_and_drafts(self):
        """Use the visible reset button to restore references without resetting saved or typed map data."""
        name = logger.add_item_name("Custom reset test item")
        logger.save_item_icon(name, Image.new("RGB", (64, 64), (120, 80, 60)))
        logger.save_settings({"biome": "Desert"})
        before = logger.get_state()
        self.window.refresh()
        select(self.window.reference_kind, "item")
        self.window.refresh_reference_names()
        self.window.refresh_reference_examples()
        self.assertGreaterEqual(self.window.reference_name.findText(name), 0)
        self.assertEqual(self.window.reference_list.count(), 1)
        self.window.reference_kind.setCurrentIndex(self.window.reference_kind.findData("omen"))
        self.window.normal.setText("123")
        self.window.resize(900, 768)
        self.window.show()
        self.window.tabs.setCurrentIndex(8)
        self.app.processEvents()
        self.window.tabs.currentWidget().ensureWidgetVisible(self.window.reference_reset_button)
        self.app.processEvents()
        self.assertEqual(self.window.tabs.currentWidget().horizontalScrollBar().maximum(), 0)
        with patch.object(native_desktop.QMessageBox, "question",
                          return_value=native_desktop.QMessageBox.StandardButton.Yes):
            QTest.mouseClick(self.window.reference_reset_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertNotIn(name, logger.item_names())
        self.assertEqual(self.window.reference_kind.currentData(), "omen")
        self.window.reference_kind.setCurrentIndex(self.window.reference_kind.findData("item"))
        self.assertEqual(self.window.reference_name.findText(name), -1)
        self.assertEqual(self.window.reference_list.count(), 0)
        self.assertIn("Default OCR references restored", self.window.reference_status.text())
        self.assertEqual(self.window.normal.text(), "123")
        after = logger.get_state()
        for key in ("current_map_id", "current_remnant_id", "current_expedition_id", "scan_commit_count", "settings"):
            self.assertEqual(after[key], before[key], key)

    def test_reference_reset_cancellation_and_busy_workers_do_not_change_references(self):
        """Keep references on Cancel and block reset if a worker starts before or during confirmation."""
        name = logger.add_item_name("Retained reset test item")
        with patch.object(native_desktop.QMessageBox, "question",
                          return_value=native_desktop.QMessageBox.StandardButton.Cancel), \
                patch.object(native_desktop.reference_pack, "reset_to_defaults") as reset:
            self.window.reference_reset_button.click()
            reset.assert_not_called()
        self.assertIn(name, logger.item_names())
        for phase in ("before", "during"):
            with self.subTest(phase=phase):
                def confirm(*args):
                    """Simulate a queued worker arriving while the confirmation dialog is open."""
                    self.window._pending_tasks["test"] = (None, logger.session_generation())
                    return native_desktop.QMessageBox.StandardButton.Yes
                if phase == "before":
                    self.window._pending_tasks["test"] = (None, logger.session_generation())
                try:
                    with patch.object(native_desktop.QMessageBox, "question", side_effect=confirm), \
                            patch.object(native_desktop.reference_pack, "reset_to_defaults") as reset:
                        with self.assertRaisesRegex(ValueError, "Wait for the current scan"):
                            self.window.reset_reference_database()
                        reset.assert_not_called()
                    self.assertIn(name, logger.item_names())
                finally:
                    self.window._pending_tasks.clear()

    def test_raw_database_export_button_uses_selected_folder_and_keeps_saved_log(self):
        """Click the raw export control, execute its queued write and reopen the saved SQLite log."""
        directory = Path(self.tmp.name) / "shared"
        directory.mkdir()
        self.window.export_folder.setText(str(directory))
        before = logger.get_state()
        self.window.resize(900, 768)
        self.window.show()
        self.window.tabs.setCurrentIndex(5)
        self.app.processEvents()
        self.window.tabs.currentWidget().ensureWidgetVisible(self.window.raw_database_export_button)
        self.app.processEvents()
        self.assertEqual(self.window.tabs.currentWidget().horizontalScrollBar().maximum(), 0)
        QTest.mouseClick(self.window.raw_database_export_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.jobs), 1)
        work, done = self.jobs.pop()
        result = work()
        done(result)
        path = Path(result["path"])
        # The export canonicalizes paths, including Windows temporary 8.3 aliases.
        self.assertEqual(path.parent.resolve(), directory.resolve())
        self.assertEqual(path.suffix, ".sqlite3")
        self.assertTrue(path.read_bytes().startswith(b"SQLite format 3\x00"))
        with closing(sqlite3.connect(path)) as backup:
            self.assertEqual(backup.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertIsNotNone(backup.execute("SELECT map_id FROM maps WHERE map_id=?",
                                                (before["current_map_id"],)).fetchone())
            with logger._connect() as source:
                tables = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for table in tables:
                    self.assertEqual(backup.execute(f'SELECT * FROM "{table}"').fetchall(),
                                     [tuple(row) for row in source.execute(f'SELECT * FROM "{table}"')], table)
        self.assertIn(str(path), self.window.statusBar().currentMessage())

    def test_chain_and_atlas_controls_fit_small_screen_after_completion(self):
        """Keep chain, Atlas and scan-region actions inside a 1366-by-720 desktop window."""
        self.window.show()
        self.window.rune_inputs[0].setText("Death")
        self.window.commit_chain()
        self.window.complete_chain()
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.window.resize(1366, 720)
        self.app.processEvents()
        self.assertEqual(self.window.size().toTuple(), (1366, 720))
        page = self.window.tabs.widget(1)
        self.window.tabs.setCurrentIndex(1)
        self.app.processEvents()
        page.ensureWidgetVisible(self.window.expedition_complete_chain_button, 20, 20)
        self.app.processEvents()
        targets = [self.window.expedition_complete_chain_button]
        for target in targets:
            rect = QRect(target.mapTo(self.window, QPoint()), target.size())
            self.assertTrue(target.isVisible())
            self.assertTrue(self.window.rect().contains(rect))
        self.window.tabs.setCurrentIndex(self.window.tabs.indexOf(self.window.atlas_settings_page))
        self.app.processEvents()
        for target in (self.window.atlas_settings_page.gear_rarity, self.window.atlas_settings_page.save_button):
            rect = QRect(target.mapTo(self.window, QPoint()), target.size())
            self.assertTrue(target.isVisible())
            self.assertTrue(self.window.rect().contains(rect))
        self.window.tabs.setCurrentIndex(self.window.tabs.indexOf(self.window.scan_regions))
        self.app.processEvents()
        save_regions = next(item for item in self.window.scan_regions.findChildren(native_desktop.QPushButton)
                            if item.text() == "Save regions")
        for target in (self.window.scan_regions.game_resolution, self.window.scan_regions.canvas, save_regions):
            rect = QRect(target.mapTo(self.window, QPoint()), target.size())
            self.assertTrue(target.isVisible())
            self.assertTrue(self.window.rect().contains(rect),
                            f"{type(target).__name__} {target.accessibleName() or getattr(target, 'text', lambda: '')()}: {rect}")
        self.assertEqual(self.window.height(), 720)

    def opened_result(self):
        """Build a confident partial opened-reward result bound to the current map."""
        return {"mode": "opened", "status": "Review opened rewards.", "can_use": True,
                "family": "Family 3", "candidates": [3], "sockets": 10, "recipe_sockets": 10,
                "socket_source": "opened icons", "first_line_gap": 60, "list_complete": False,
                "first_recipe": "Perfect Chaos Orb x3", "next_recipe": "Perfect Exalted Orb x3",
                "opened_recipes": [{"recipe": name, "ocr_score": .99, "match_score": 1}
                                   for name in ("Perfect Chaos Orb x3", "Perfect Exalted Orb x3")],
                "_target_map_id": logger.get_state()["current_map_id"], **logger.scan_context()}

    def capture_file(self, mode="opened"):
        """Select a temporary PNG in the requested scan mode and return its callback."""
        self.window.mode = mode
        select(self.window.mode_select, mode)
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(self.image_bytes())
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.scan_file()
        return self.jobs[-1][1]

    def capture_inventory(self, color=(120, 130, 140), live=False):
        """Queue a synthetic inventory capture and return its deferred result callback."""
        self.window._inventory_captured(Image.new("RGB", (480, 200), color), live=live)
        return self.jobs[-1][1]

    def inventory_result(self, quantity=7):
        """Build a confident single-stack Chaos Orb inventory result."""
        return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": quantity}], "unknown": []}

    def capture_ritual(self):
        """Queue a synthetic manual Ritual capture and return its result callback."""
        self.window._ritual_captured(Image.new("RGB", (200, 100)), live=False)
        return self.jobs[-1][1]

    def task_error(self, callback, text="Recognition unavailable"):
        """Deliver a recognition exception through the window task-completion handler."""
        self.window._pending_tasks["failed"] = (callback, logger.session_generation())
        self.window._task_done("failed", None, RuntimeError(text))

    def seed_result(self, color=(120, 130, 140)):
        """Show a confident family-3 seed reading with current context and a synthetic screenshot."""
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
        result = {"mode": "seed", "remnants": [{"sockets": stage["sockets"],
                  "seed_slot": stage["seed_slot"], "seed_rune": stage["seed_rune"],
                  "family": "Family 3", "candidates": [3], "can_commit": True}], **logger.scan_context()}
        self.window.show_result("seed", result, self.image_bytes(color))

    def reference_job(self):
        """Queue saving the displayed seed reference and return its worker/callback pair."""
        self.seed_result()
        self.window.save_seed_scan()
        return self.jobs[-1]

    def two_seed_reference_job(self):
        """Queue the first of two identically labeled seeds with visibly distinct bar crops."""
        self.seed_result()
        reading = dict(self.window._seed_readings[0])
        result = {**self.window.results["seed"], "remnants": [
            {**reading, "scan_index": index + 1, "bar_bounds": bounds}
            for index, bounds in enumerate((
                {"x": 80, "y": 60, "width": 180, "height": 20},
                {"x": 320, "y": 200, "width": 180, "height": 20}))]}
        image = Image.new("RGB", (600, 400), (120, 130, 140))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 60, 259, 79), fill=(180, 50, 40))
        draw.rectangle((320, 200, 499, 219), fill=(40, 50, 180))
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        self.window.show_result("seed", result, raw.getvalue())
        self.window.seed_table.setCurrentCell(0, 2)
        self.window.save_seed_scan()
        return self.jobs[-1]

    def persist_reference(self, work):
        """Run a reference-save worker with reviewed-vector extraction disabled."""
        with patch.object(store, "_reviewed_vector", return_value=None):
            return work()

    def test_header_map_flags_sync_without_changing_area(self):
        """Verify header map flags sync without changing area."""
        area = self.window.stat_values[2].text()
        self.window.header_deli.setChecked(True)
        self.window.header_wisp.setChecked(True)
        self.assertTrue(self.window.deli.isChecked())
        self.assertTrue(self.window.wisp.isChecked())
        self.assertTrue(logger.get_state()["settings"]["deli"])
        self.assertTrue(logger.get_state()["settings"]["wisp"])
        self.assertEqual(self.window.stat_values[2].text(), area)
        self.window.deli.setChecked(False)
        self.window.wisp.setChecked(False)
        self.assertFalse(self.window.header_deli.isChecked())
        self.assertFalse(self.window.header_wisp.isChecked())
        self.assertFalse(logger.get_state()["settings"]["deli"])
        self.assertFalse(logger.get_state()["settings"]["wisp"])
        self.assertEqual(self.window.stat_values[2].text(), area)

    def test_header_expedition_controls_existing_chain_selection(self):
        """Verify header expedition controls existing chain selection."""
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.window.expedition.currentData(), 2)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertIn("M0001-E02", self.window.header_expedition.currentText())
        self.assertIn("M0001-E02", self.window.chain_note.text())
        self.window.rune_inputs[0].setText("Rage")
        self.window.rune_inputs[1].setText("Time")
        self.window.commit_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual([row["rune1"] for row in logger.get_state()["chain"]], ["Rage", "Time"])
        self.window.complete_chain()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E03")
        self.assertEqual(self.window.header_expedition.currentData(), 3)
        self.assertEqual(logger.get_state()["chain"], [])
        self.window.expedition.setCurrentIndex(self.window.expedition.findData(1))
        self.assertEqual(self.window.header_expedition.currentData(), 1)
        self.assertIn("M0001-E01", self.window.header_expedition.currentText())
        self.assertEqual(logger.get_state()["chain"], [])
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual([row["rune1"] for row in logger.get_state()["chain"]], ["Rage", "Time"])

    def test_header_remnant_id_follows_review_save_and_map_transition(self):
        """Verify header remnant ID follows review save and map transition."""
        self.assertEqual(self.window.header_map_id.text(), "#1")
        self.assertEqual(self.window.header_remnant_id.text(), "—")
        self.window.show_result("opened", self.opened_result())
        self.assertEqual(self.window.header_remnant_id.text(), "#1")
        self.window.discard_scan()
        self.assertEqual(self.window.header_remnant_id.text(), "—")
        self.window.show_result("opened", self.opened_result())
        self.window.approve_remnant_scan()
        self.assertEqual(self.window.header_remnant_id.text(), "#1")
        self.window.finish_map()
        self.assertEqual(self.window.header_map_id.text(), "#2")
        self.assertEqual(self.window.header_remnant_id.text(), "—")
        self.assertEqual(self.window.header_expedition.currentData(), 1)
        self.assertIn("M0002-E01", self.window.header_expedition.currentText())

    def test_expedition_change_preserves_pending_remnant_original_binding(self):
        """Verify expedition change preserves pending remnant original binding."""
        self.window.show_result("opened", self.opened_result())
        pending = logger.get_state()["ocr_pending"]
        select(self.window.header_expedition, 2)
        self.window.set_expedition(self.window.header_expedition)
        self.assertEqual(self.window.header_expedition.currentData(), 2)
        self.assertEqual(self.window.expedition.currentData(), 2)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(pending["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["settings"]["expedition"], 2)
        self.assertEqual(self.window.pending_review_kind, "remnant")

    def test_unclear_or_unsupported_waystone_tier_cannot_reuse_previous_tier(self):
        """Verify unclear or unsupported Waystone tier cannot reuse previous tier."""
        for tier in (None, 14, 17):
            with self.subTest(tier=tier):
                select(self.window.tier, 16)
                result = parse_item_text("Item Class: Waystones\nRarity: Rare\nStorm Peak\n"
                                        "Waystone (Tier 15)\n--------\nWaystone Drop Chance: +85%\n"
                                        "--------\nItem Level: 79\n--------\n"
                                        "30% increased Rarity of Items found in this Area", self.window.state["affixes"])
                result["fields"]["tier"] = tier
                self.window._hover_item_read(result)
                self.assertEqual(self.window.tier.currentData(), "")
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertEqual(self.window.stat_values[2].text(), "—")
                before = logger.get_state()["scan_commit_count"]
                with self.assertRaises(ValueError):
                    self.window.approve_review()
                self.assertEqual(logger.get_state()["scan_commit_count"], before)
                self.window.tier.setCurrentIndex(self.window.tier.findData(15))
                self.assertTrue(self.window.approve_scan_button.isEnabled())
                self.window.approve_review()
                self.assertEqual(logger.get_state()["settings"]["tier"], 15)
                self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)

    def test_discard_cancels_delayed_remnant_and_autosave(self):
        """Verify discard cancels delayed remnant and autosave."""
        self.window.set_auto_commit(True)
        self.window.show_result("opened", self.opened_result())
        callback = self.capture_file()
        result = self.opened_result()
        self.window.discard_scan()
        callback(result)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertFalse(self.window.approve_scan_button.isEnabled())

    def test_undo_clears_waystone_draft_and_preserves_saved_history(self):
        """Verify undo clears Waystone draft and preserves saved history."""
        self.window.waystone.setText("50")
        self.window.save_map_settings()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 7}])
        logger.save_ritual_page([{"category": "Item", "name": "Reward", "quantity": 2}])
        self.window.finish_map()
        before = logger.export_all_csv()
        with logger._connect() as db:
            history = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
        result = parse_item_text("Item Class: Waystones\nRarity: Rare\nStorm Peak\nWaystone (Tier 16)\n--------\nWaystone Drop Chance: +87%\n--------\nItem Level: 82\n--------\n30% increased Rarity of Items found in this Area", self.window.state["affixes"])
        result.update(logger.scan_context())
        self.window._hover_item_read(result)
        self.window.undo_map()
        self.assertEqual(self.window.state["current_map_id"], "M0001")
        self.assertEqual(self.window.waystone.text(), "50")
        self.assertIsNone(self.window.pending_review_kind)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 7})
        self.assertEqual(len(logger.ritual_pages_for_map("M0001")), 1)
        self.assertEqual(logger.export_all_csv(), before)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")], history)

    def test_failed_undo_preserves_pending_draft(self):
        """Verify failed undo preserves pending draft."""
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 7}])
        self.window._review_pending("waystone", "Review waystone")
        self.window.waystone.setText("87")
        with self.assertRaisesRegex(ValueError, "saved activity"):
            self.window.undo_map()
        self.assertEqual(self.window.pending_review_kind, "waystone")
        self.assertEqual(self.window.waystone.text(), "87")

    def test_reference_save_finishes_without_linking_to_later_map(self):
        """Verify reference save finishes without linking to later map."""
        work, callback = self.reference_job()
        self.window.finish_map()
        self.window.show_result("opened", self.opened_result())
        saved = self.persist_reference(work)
        callback(saved)
        self.assertIsNone(self.window.saved_scan)
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scans WHERE id=?", (saved["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)

    def test_reference_save_stays_associated_after_propagation_advances(self):
        """Verify reference save stays associated after propagation advances."""
        work, callback = self.reference_job()
        context = logger.scan_context()
        logger.commit_chain_draft([{"rune1": "Rage"}], context)
        logger.complete_chain(context)
        self.window.refresh()
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])
        self.assertEqual(self.window._saved_scan_capture["context"]["_capture_expedition"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_reference_save_does_not_attach_to_replacement_capture(self):
        """Verify reference save does not attach to replacement capture."""
        work, callback = self.reference_job()
        self.seed_result((140, 130, 120))
        callback(self.persist_reference(work))
        self.assertIsNone(self.window.saved_scan)

    def test_reference_callback_distinguishes_identical_seed_labels(self):
        """Verify reference callback distinguishes identical seed labels."""
        work, callback = self.two_seed_reference_job()
        original = self.window._seed_readings[0]
        self.window.seed_table.setCurrentCell(1, 2)
        saved = self.persist_reference(work)
        callback(saved)
        self.assertIsNot(self.window._seed_readings[self.window.seed_table.currentRow()], original)
        self.assertIsNone(self.window.saved_scan)
        with Image.open(store.image_for(saved["id"])) as screenshot:
            self.assertEqual(screenshot.getpixel((75, 70)), (180, 50, 40))

    def test_selecting_another_identical_seed_releases_saved_reference(self):
        """Verify selecting another identical seed releases saved reference."""
        work, callback = self.two_seed_reference_job()
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])
        self.window.seed_table.setCurrentCell(1, 2)
        self.assertIsNone(self.window.saved_scan)
        self.window.show_result("opened", self.opened_result())
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_links").fetchone()[0], 0)

    def test_current_seed_reference_remains_associated(self):
        """Verify current seed reference remains associated."""
        work, callback = self.reference_job()
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])

    def test_reference_completion_can_link_original_seed_and_opened_view(self):
        """Verify reference completion can link original seed and opened view."""
        work, callback = self.reference_job()
        self.window.show_result("opened", self.opened_result())
        saved = self.persist_reference(work)
        callback(saved)
        self.assertEqual(self.window.saved_scan["id"], saved["id"])
        self.window.approve_remnant_scan()
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT remnant_id,scan_id FROM scan_links")], [("R0001", saved["id"])])

    def test_clear_tablets_drops_inflight_autosave_result(self):
        """Verify clear tablets drops inflight autosave result."""
        self.window.set_auto_tablets(True)
        revision = service.HOTKEY._capture_revision
        event = {"mode": "item", "error": "", "result": {"kind": "tablet",
                 "mods": ["30% increased Pack Size"], "matches": [], "uncertain": [],
                 "source": "clipboard", **logger.scan_context()}}
        service.HOTKEY._capture_lock.acquire()
        try:
            self.window.clear_tablets()
        finally:
            service.HOTKEY._finish_capture(event, None, revision)
        self.window.poll()
        self.assertIsNone(service.HOTKEY.status()["latest"])
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertFalse(any(row["affix"] for row in logger.get_state()["settings"]["tablet_affixes"]))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_scan_failures_release_reading_state_and_allow_rejection(self):
        """Verify scan failures release reading state and allow rejection."""
        for kind, capture, field, save in (
                ("currency", self.capture_inventory, "_inventory_reading", self.window.save_inventory),
                ("ritual", self.capture_ritual, "_ritual_reading", self.window.save_ritual),
                ("remnant", self.capture_file, "_remnant_reading", self.window.commit_remnant),
                ("seed", lambda: self.capture_file("seed"), "_remnant_reading", self.window.commit_review)):
            with self.subTest(kind=kind):
                callback = capture()
                self.task_error(callback)
                self.assertIsNone(getattr(self.window, field))
                self.assertFalse(self.window.approve_scan_button.isEnabled())
                self.assertIn("Scan failed", self.window.review_summary.text())
                with self.assertRaisesRegex(ValueError, "scan failed"):
                    save()
                self.window.reject_review()
                self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_executor_failure_reaches_failed_review(self):
        """Verify executor failure reaches failed review."""
        self.window._submit = LoggerWindow._submit.__get__(self.window, LoggerWindow)
        with patch.object(native_desktop.item_ocr, "scan_inventory_grid", side_effect=RuntimeError("Recognition unavailable")):
            self.window._inventory_captured(Image.new("RGB", (480, 200)), live=False)
            deadline = time.monotonic() + 3
            while self.window._pending_tasks and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertFalse(self.window._pending_tasks)
        self.assertIsNone(self.window._inventory_reading)
        self.assertIn("Recognition unavailable", self.window.review_summary.text())
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_callback_context_failure_reports_finished_scan(self):
        """Verify callback context failure reports finished scan."""
        callback = self.capture_file()
        result = self.opened_result()
        logger.save_settings({"expedition": 2})
        self.window._pending_tasks["result"] = (callback, logger.session_generation())
        self.window._task_done("result", result, None)
        self.assertIsNone(self.window._remnant_reading)
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertIn("expedition changed", self.window.review_summary.text())
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_failed_inventory_retry_preserves_clear_autosave(self):
        """Verify failed inventory retry preserves clear autosave."""
        self.window.set_auto_all(True)
        self.task_error(self.capture_inventory(live=True))
        callback = self.capture_inventory((140, 130, 120), live=True)
        callback(self.inventory_result(9))
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Chaos Orb": 9})
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    def test_stale_failure_cannot_clear_or_replace_newer_review(self):
        """Verify stale failure cannot clear or replace newer review."""
        older = self.capture_inventory()
        latest = self.capture_inventory((140, 130, 120))
        self.task_error(older, "Old recognition failed")
        self.assertIsNotNone(self.window._inventory_reading)
        latest(self.inventory_result(9))
        summary = self.window.review_summary.text()
        self.task_error(older, "Old recognition failed again")
        self.assertEqual(self.window.review_summary.text(), summary)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertEqual(self.window.save_inventory()["start"], {"Chaos Orb": 9})


if __name__ == "__main__":
    unittest.main()
