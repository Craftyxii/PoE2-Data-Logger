"""Qt scan-conflict checks preserving a pending remnant review across other captured activity and delayed worker results."""

import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidgetItem

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.ui import native_desktop
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, select


class UIScanConflictTests(unittest.TestCase):
    """Check unrelated activity and delayed scan results preserve pending remnant and seed reviews."""
    incoming = ("currency_capture", "currency_read", "ritual_capture", "ritual_read",
                "waystone", "hover_tablet", "tablet_read")

    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger with queued workers, deterministic inventory cropping and unsaved rune inputs."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ui-scan-conflicts-")
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
        self.window.auto_tablet_checkbox.setChecked(False)
        self.window.rune_inputs[0].setText("Death")
        self.window.rune_inputs[1].setText("Power")

    def tearDown(self):
        """Restore the grid patch, close the window and workers and remove isolated logger data."""
        self.grid_patch.stop()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def image_bytes(self, color=(120, 130, 140)):
        """Encode a solid-color screenshot as PNG bytes for preview-preservation checks."""
        output = io.BytesIO()
        Image.new("RGB", (600, 400), color).save(output, format="PNG")
        return output.getvalue()

    def opened_result(self, status="The first reward needs review."):
        """Build an unclear opened-remnant reading bound to the current scan context."""
        return {"mode": "opened", "status": status, "can_use": False, "first_recipe": None,
                "opened_recipes": [{"raw": "Unreadable reward", "recipe": None}],
                **logger.scan_context()}

    def prepare_pending(self, kind):
        """Prepare an opened, manual or seed review and assert its review controls and binding."""
        if kind == "opened":
            self.window.show_result("opened", self.opened_result(), self.image_bytes())
            self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], "R0001")
        elif kind == "manual":
            self.window._show_image(self.image_bytes())
            self.window.manual_remnant_button.click()
            self.assertEqual(logger.get_state()["ocr_pending"]["expedition_id"], "M0001-E01")
        else:
            with logger._connect() as db:
                stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 LIMIT 1").fetchone())
            result = {"mode": "seed", "status": "Visible remnant needs review.",
                      "remnants": [{"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                                    "seed_rune": stage["seed_rune"], "candidates": [3],
                                    "can_commit": False}], **logger.scan_context()}
            self.window.show_result("seed", result, self.image_bytes())
            self.assertIsNotNone(logger.get_state()["ocr_pending"])
        if kind != "seed":
            self.window.first_recipe.setText("Perfect Chaos Orb x3")
            self.window.next_recipe.setText("Perfect Exalted Orb x3")
            self.window.resolve_recipe(preserve_review=True)
            self.assertGreater(self.window.recipe_table.rowCount(), 0)
        self.assertEqual(self.window.pending_review_kind, "seed" if kind == "seed" else "remnant")
        self.assertFalse(self.window.approve_scan_button.isHidden())
        self.assertFalse(self.window.reject_scan_button.isHidden())

    def table_rows(self, table):
        """Snapshot table cell text and check state, preserving missing cells."""
        return [[(table.item(row, column).text(), table.item(row, column).checkState())
                 if table.item(row, column) else None for column in range(table.columnCount())]
                for row in range(table.rowCount())]

    def review_state(self):
        """Snapshot review data, drafts, controls, context targets and worker identities for preservation checks."""
        window = self.window
        return {
            "pending": logger.get_state()["ocr_pending"],
            "commit_count": logger.get_state()["scan_commit_count"],
            "kind": window.pending_review_kind,
            "heading": window.review_kind.text(),
            "summary": window.review_summary.text(),
            "tab": window.tabs.currentIndex(),
            "mode": (window.mode, window.mode_select.currentData()),
            "current_file": dict(window.current_file),
            "preview": (window.preview.pixmap().cacheKey(), window.preview.isHidden()),
            "controls": [(widget.isHidden(), widget.isEnabled()) for widget in
                         (window.approve_scan_button, window.reject_scan_button,
                          window.remnant_log_group, window.review_group, window.recipe_table,
                          window.seed_fields_widget, window.seed_table)],
            "recipe_draft": (window.first_recipe.text(), window.next_recipe.text(),
                             window.recipe_family.currentData(), self.table_rows(window.recipe_table)),
            "seed_draft": (window.seed_sockets.text(), window.seed_slot.text(), window.seed_rune.text(),
                           window.seed_family.currentData(), self.table_rows(window.seed_table),
                           copy.deepcopy(window._seed_readings)),
            "results": copy.deepcopy(window.results),
            "images": dict(window.images),
            "resolved": copy.deepcopy(window.resolved),
            "chain": [(field.text(), field.property("chainPart"), field.property("chainRecipe"))
                      for field in window.rune_inputs],
            "chain_rows": self.table_rows(window.chain_review_table),
            "waystone": [(field.text()) for field in (window.waystone, window.map_mods,
                                                      window.item_rarity, window.waystone_name)],
            "tablets": [(field.currentData(), amount.text())
                        for field, amount in zip(window.tablet_affixes, window.tablet_values)],
            "tablet_raw": copy.deepcopy(window.tablet_raw_mods),
            "inventory": self.table_rows(window.inventory_table),
            "ritual": self.table_rows(window.ritual_table),
            "ritual_fields": (window.ritual_tribute.text(), window.ritual_rerolls.text(),
                              window.ritual_raw.toPlainText()),
            "targets": (window._pending_currency_map, window._pending_currency_phase,
                        window._pending_ritual_map, window._pending_tablet_slot),
            "reading_ids": tuple(id(reading) for reading in
                                 (window._remnant_reading, window._inventory_reading, window._ritual_reading)),
            "jobs": len(self.jobs),
            "overlay_token": window._overlay_review_token,
        }

    def inventory_result(self):
        """Build a reviewable seven-Chaos-Orb inventory reading."""
        return {"items": [{"slot": 1, "name": "Chaos Orb", "quantity": 7}], "unknown": []}

    def ritual_result(self):
        """Build a reviewable one-item Ritual reward reading with tribute and raw text."""
        return {"items": [{"category": "Item", "name": "Reward", "quantity": 1,
                           "tribute": 10, "source": "Reward", "score": 1}], "raw_text": "Reward"}

    def tablet_result(self):
        """Build a clipboard tablet reading bound to the current scan context."""
        return {"kind": "tablet", "mods": ["10% increased Pack Size in Map"],
                "matches": [], "uncertain": [], "source": "clipboard", **logger.scan_context()}

    def invoke(self, kind):
        """Deliver the requested inventory, Ritual, waystone or tablet capture/read path."""
        if kind == "currency_capture":
            return self.window._inventory_captured(Image.new("RGB", (480, 200), "blue"), live=False)
        if kind == "currency_read":
            return self.window._inventory_read(self.inventory_result(), live=False)
        if kind == "ritual_capture":
            return self.window._ritual_captured(Image.new("RGB", (200, 100), "blue"), live=False)
        if kind == "ritual_read":
            return self.window._ritual_read(self.ritual_result(), live=False)
        if kind == "waystone":
            result = parse_item_text("Item Class: Waystones\nRarity: Rare\nStorm Peak\n"
                                     "Waystone (Tier 16)\n--------\nWaystone Drop Chance: +87%\n"
                                     "--------\nItem Level: 82\n--------\n"
                                     "30% increased Rarity of Items found in this Area", self.window.state["affixes"])
            result.update(logger.scan_context())
            return self.window._hover_item_read(result, self.image_bytes((10, 20, 30)))
        if kind == "hover_tablet":
            return self.window._hover_item_read(self.tablet_result(), self.image_bytes((10, 20, 30)))
        return self.window._tablet_read(1, self.tablet_result())

    def assert_incoming_scans_preserve_pending(self, pending_kind):
        """Assert every unrelated scan path is rejected without changing the prepared pending review."""
        self.prepare_pending(pending_kind)
        before = self.review_state()
        for incoming in self.incoming:
            with self.subTest(incoming=incoming):
                with self.assertRaisesRegex(ValueError, r"(?i)save.*reject"):
                    self.invoke(incoming)
                self.assertEqual(self.review_state(), before)

    def test_nonremnant_scans_preserve_opened_remnant_token_and_recipe_draft(self):
        """Verify nonremnant scans preserve opened remnant token and recipe draft."""
        self.assert_incoming_scans_preserve_pending("opened")

    def test_other_activity_scans_preserve_manual_remnant_binding(self):
        """Verify other activity scans preserve manual remnant binding."""
        self.assert_incoming_scans_preserve_pending("manual")

    def test_nonremnant_scans_preserve_visible_seed_review(self):
        """Verify nonremnant scans preserve visible seed review."""
        self.assert_incoming_scans_preserve_pending("seed")

    def test_nonremnant_scans_preserve_orphan_database_remnant_token(self):
        """Verify nonremnant scans preserve orphan database remnant token."""
        logger.assign_ocr_id("opened")
        self.assertIsNone(self.window.pending_review_kind)
        before = self.review_state()
        for incoming in self.incoming:
            with self.subTest(incoming=incoming):
                with self.assertRaisesRegex(ValueError, r"(?i)save.*reject"):
                    self.invoke(incoming)
                self.assertEqual(self.review_state(), before)

    def test_rejecting_remnant_allows_each_normal_scan_path(self):
        """Verify rejecting remnant allows each normal scan path."""
        for incoming in self.incoming:
            with self.subTest(incoming=incoming):
                self.prepare_pending("opened")
                self.window.reject_scan_button.click()
                self.assertIsNone(logger.get_state()["ocr_pending"])
                self.assertIsNone(self.window.pending_review_kind)
                self.invoke(incoming)
                expected = "currency" if incoming.startswith("currency") else "ritual" if incoming.startswith("ritual") \
                    else "waystone" if incoming == "waystone" else "tablet"
                self.assertEqual(self.window.pending_review_kind, expected)
                self.assertFalse(self.window.reject_scan_button.isHidden())
                self.assertIsNone(logger.get_state()["ocr_pending"])
                self.window.reject_review()

    def test_delayed_inventory_and_ritual_results_cannot_replace_new_remnant_review(self):
        """Verify delayed inventory and ritual results cannot replace new remnant review."""
        for incoming, result in (("currency_capture", self.inventory_result),
                                 ("ritual_capture", self.ritual_result)):
            with self.subTest(incoming=incoming):
                self.invoke(incoming)
                work, callback = self.jobs[-1]
                self.prepare_pending("opened")
                before = self.review_state()
                self.assertIsNone(work())
                callback(result())
                self.assertEqual(self.review_state(), before)
                self.window.reject_review()

    def capture_file(self):
        """Queue an opened-remnant file scan and return its completion callback."""
        self.window.mode = "opened"
        select(self.window.mode_select, "opened")
        path = Path(self.tmp.name) / "remnant.png"
        path.write_bytes(self.image_bytes((30, 40, 50)))
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            self.window.scan_file()
        return self.jobs[-1][1]

    def test_delayed_file_result_cannot_replace_newer_held_remnant(self):
        """Verify delayed file result cannot replace newer held remnant."""
        old = self.capture_file()
        latest = self.capture_file()
        latest(self.opened_result("New remnant needs review."))
        self.window.first_recipe.setText("Perfect Chaos Orb x3")
        self.window.next_recipe.setText("Perfect Exalted Orb x3")
        self.window.resolve_recipe(preserve_review=True)
        before = self.review_state()

        old(self.opened_result("Obsolete remnant result."))

        self.assertEqual(self.review_state(), before)
        self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], "R0001")

    def saved_history_item(self):
        """Create a saved-seed history item and matching preview image."""
        item = QListWidgetItem("Saved seed reference #17")
        item.setData(Qt.ItemDataRole.UserRole, 17)
        path = Path(self.tmp.name) / "saved-seed.png"
        path.write_bytes(self.image_bytes((10, 20, 30)))
        return item, path

    def test_saved_seed_history_preserves_pending_remnant_mode_image_and_recipe(self):
        """Verify saved seed history preserves pending remnant mode image and recipe."""
        self.prepare_pending("opened")
        item, path = self.saved_history_item()
        before = self.review_state()

        with patch.object(store, "image_for", return_value=path) as image_for:
            with self.assertRaisesRegex(ValueError, r"(?i)save.*reject"):
                self.window.load_saved_scan(item)
            image_for.assert_not_called()

        self.assertEqual(self.review_state(), before)

    def test_saved_seed_history_preserves_orphan_database_remnant_token(self):
        """Verify saved seed history preserves orphan database remnant token."""
        logger.assign_ocr_id("opened")
        self.window._show_image(self.image_bytes())
        self.assertIsNone(self.window.pending_review_kind)
        item, path = self.saved_history_item()
        before = self.review_state()

        with patch.object(store, "image_for", return_value=path) as image_for:
            with self.assertRaisesRegex(ValueError, r"(?i)save.*reject"):
                self.window.load_saved_scan(item)
            image_for.assert_not_called()

        self.assertEqual(self.review_state(), before)

    def test_saved_seed_history_loads_normally_without_pending_review(self):
        """Verify saved seed history loads normally without pending review."""
        service.HOTKEY.set_mode("opened")
        self.window.mode = "opened"
        select(self.window.mode_select, "opened")
        self.window.tabs.setCurrentIndex(2)
        item, path = self.saved_history_item()

        with patch.object(store, "image_for", return_value=path) as image_for:
            self.window.load_saved_scan(item)
            image_for.assert_called_once_with(17)

        self.assertEqual(self.window.mode, "seed")
        self.assertEqual(self.window.mode_select.currentData(), "seed")
        self.assertEqual(service.HOTKEY.status()["mode"], "seed")
        self.assertEqual(self.window.current_file["seed"], path.name)
        self.assertEqual(self.window.images["seed"], path.read_bytes())
        self.assertFalse(self.window.preview.isHidden())
        self.assertEqual(self.window.preview.pixmap().toImage().pixelColor(0, 0).getRgb()[:3], (10, 20, 30))
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_normal_tab_navigation_preserves_pending_remnant_review(self):
        """Verify normal tab navigation preserves pending remnant review."""
        self.prepare_pending("opened")
        before = self.review_state()
        before.pop("overlay_token")
        for index in (1, 2, 3, 4, 5, 6, 0):
            with self.subTest(tab=index):
                self.window.nav_buttons[index].click()
                self.assertEqual(self.window.tabs.currentIndex(), index)
                after = self.review_state()
                # Leaving Review cancels a deferred overlay reveal; the review
                # itself must remain available when the user returns.
                after.pop("overlay_token")
                self.assertEqual(after, {**before, "tab": index})


if __name__ == "__main__":
    unittest.main()
