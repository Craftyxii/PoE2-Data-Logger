"""Keep uncertain item identity separate from independently verified stack counts.

The Idol control renders the shipped source PNG and exercises real recognition,
geometry and Qt review policy; it establishes this catalog-delivery regression,
not accuracy on an unseen game capture. Count-gate negatives deliberately replace
identity/count evidence to isolate the conditions that must remain under review.
"""

import copy
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from openpyxl import load_workbook
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, unique_items
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_currency_read, clear_currency_stack


ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "tests/fixtures/currency_training"


class InventoryIdentityConflictTests(unittest.TestCase):
    """Use real catalog/capture regressions and isolated uncertainty-gate controls."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application and cache only deterministic real scan results."""
        cls.app = QApplication.instance() or QApplication([])
        cls._captures = {}
        cls._idol = None

    def setUp(self):
        """Open an isolated logger profile without touching a user's saved data."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-identity-conflict-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.directory.name)
        logger._READY = False
        logger.initialize()
        self.window = None

    def tearDown(self):
        """Retire any real Qt window and restore the caller's storage directory."""
        if self.window is not None:
            self.window.close()
            self.window.pool.shutdown(wait=True, cancel_futures=True)
            self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.directory.cleanup()

    @staticmethod
    def idol_image():
        """Render the exact isolated color control at source-art inventory slot 57."""
        pitch, margin = 54, 8
        image = Image.new("RGB", (12 * pitch + 2 * margin + 1, 5 * pitch + 2 * margin + 1), (4, 4, 4))
        draw = ImageDraw.Draw(image)
        for column in range(13):
            x = margin + column * pitch
            draw.line((x, margin, x, margin + 5 * pitch), fill=(70, 56, 30))
        for row in range(6):
            y = margin + row * pitch
            draw.line((margin, y, margin + 12 * pitch, y), fill=(70, 56, 30))
        catalog = ROOT / "PoE2_Data_Logger/unique_item_assets"
        entry = next(row for row in json.loads((catalog / "manifest.json").read_text())["items"]
                     if row["id"] == "141")
        assert entry["name"] == "Idol of Uldurn"
        cell = Image.new("RGBA", (pitch - 1, pitch - 1), (15, 14, 32, 255))
        with Image.open(catalog / entry["icon"]) as source:
            art = ImageOps.contain(source.convert("RGBA"), cell.size, Image.Resampling.LANCZOS)
        cell.alpha_composite(art, ((cell.width - art.width) // 2, (cell.height - art.height) // 2))
        image.paste(cell.convert("RGB"), (margin + 8 * pitch + 1, margin + 4 * pitch + 1))
        return image

    def idol_result(self):
        """Run unmodified production geometry, labels and identity readers once per class."""
        if type(self)._idol is None:
            type(self)._idol = item_ocr.scan_inventory_grid(self.idol_image())
        return copy.deepcopy(type(self)._idol)

    def capture_result(self, name):
        """Read an original supplied inventory through the actual production scanner."""
        if name not in type(self)._captures:
            with Image.open(CAPTURES / name) as source:
                type(self)._captures[name] = item_ocr.scan_inventory_grid(source.convert("RGB"))
        return copy.deepcopy(type(self)._captures[name])

    @staticmethod
    def row_for(result, slot):
        """Find one emitted slot without depending on recognized-versus-unknown routing."""
        return next(row for row in result["items"] + result["unknown"]
                    if row.get("slot") == slot or slot in row.get("grid_slots", []))

    @staticmethod
    def candidates(row):
        """Read both candidate encodings used by the existing inventory review."""
        return set(row.get("candidate_names") or str(row.get("candidate") or "").split(" / ")) - {""}

    def review(self, result):
        """Deliver actual or explicitly injected evidence to the real autosave/review policy."""
        if self.window is None:
            self.window = LoggerWindow()
            self.window._poll.stop()
        self.window.state["settings"]["ocr_auto_commit"] = True
        self.window._inventory_read(copy.deepcopy(result), live=True)
        self.app.processEvents()
        return self.window.inventory_table

    def save_idol_choice(self, name):
        """Select and approve a real captured catalog conflict through existing Qt controls."""
        self.window = LoggerWindow()
        self.window._poll.stop()
        jobs = []
        self.window._submit = lambda label, work, done=None: jobs.append(done)
        self.window._inventory_captured(self.idol_image(), live=False)
        jobs[-1](self.idol_result())
        table = self.window.inventory_table
        row = self.table_row(table, 57)
        table.cellWidget(row, 1).setEditText(name)
        controls = table.cellWidget(row, 3)
        controls.findChild(QPushButton, "approveCurrency").click()
        self.assertEqual(controls.property("reviewStatus"), "approved")
        self.window.approve_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"], 1)

    @staticmethod
    def table_row(table, slot):
        """Locate the row by captured slot identity after the review has sorted it."""
        return next(row for row in range(table.rowCount())
                    if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == slot)

    def uncertain_scan(self, *, native=None, marker=False, clipped=False, geometry=False, read=None):
        """Isolate unknown identity with controlled count evidence over occupied image pixels."""
        image = Image.new("RGB", (648, 270), (26, 26, 40))
        pixels = np.random.default_rng(371).integers(35, 180, (54, 54, 3), dtype=np.uint8)
        image.paste(Image.fromarray(pixels), (0, 0))
        image.info["poe2_inventory_aligned"] = True
        if clipped:
            image.info["poe2_inventory_clipped_count_slots"] = [1]
        if geometry:
            image.info["poe2_inventory_geometry_uncertain"] = True
        labels = {slot: {"count": 1, "count_present": False, "tier_present": False,
                         "count_clipped": False, "count_geometry_uncertain": False,
                         "count_glyphs": None} for slot in range(1, 61)}
        labels[1].update(count_present=marker or clipped or geometry,
                         count_clipped=clipped, count_geometry_uncertain=geometry)
        reader = currency_ocr.get_reader()
        icon = {"family": None, "score": .8, "uncertain": True,
                "all": [{"name": "Unrecognized item"}]}
        with patch.object(item_ocr, "_inventory_labels", return_value=labels), \
                patch.object(reader, "icon", return_value=icon), \
                patch.object(reader, "count", return_value=native), \
                patch.object(unique_items, "match", return_value=None):
            return item_ocr.scan_inventory_grid(image, read=read)

    def assert_count_confirmation(self, result):
        """An unknown count must remain blank and manual without being autosaved."""
        row = self.row_for(result, 1)
        self.assertIsNot(row.get("count_verified"), True)
        table = self.review(result)
        index = self.table_row(table, 1)
        self.assertEqual(table.item(index, 2).text(), "")
        self.assertTrue(table.cellWidget(index, 3).property("reviewCountRequired"))
        self.assertFalse(table.isRowHidden(index))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.window.reject_review()

    def test_color_idol_keeps_item_identity_among_candidates_without_wrong_clear_currency(self):
        """Competing one-cell catalogs cannot automatically rename the actual Idol source sprite."""
        result = self.idol_result()
        row = self.row_for(result, 57)
        self.assertTrue(row.get("name") == "Idol of Uldurn" or "Idol of Uldurn" in self.candidates(row))
        self.assertFalse(row.get("name") == "Vaal Armourer's Infuser" and clear_currency_stack(row))
        self.assertFalse(clear_currency_read(result))

    def test_color_idol_remains_visible_and_cannot_autosave_an_incorrect_hidden_row(self):
        """The real desktop must hold the actual conflicting source result with autosave enabled."""
        table = self.review(self.idol_result())
        index = self.table_row(table, 57)
        self.assertFalse(table.isRowHidden(index))
        self.assertTrue(table.cellWidget(index, 3).property("reviewNameRequired"))
        self.assertEqual(self.window.pending_review_kind, "currency")
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_approved_idol_choice_registers_learns_and_exports_as_item(self):
        """A selected Item candidate retains its own category after actual row and scan approval."""
        self.assertNotIn("Idol of Uldurn", logger.item_names())
        self.assertNotIn("Idol of Uldurn", logger.currency_names())
        self.save_idol_choice("Idol of Uldurn")
        self.assertIn("Idol of Uldurn", logger.item_names())
        self.assertNotIn("Idol of Uldurn", logger.currency_names())
        self.assertEqual(logger.currency_for_map("M0001")["start"], {"Idol of Uldurn": 1})
        self.assertEqual([(row["name"], row["kind"]) for row in logger.review_icons()],
                         [("Idol of Uldurn", "item")])
        rows = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertTrue(any(row.get("Item: Idol of Uldurn") == "1" for row in rows))
        self.assertFalse(any("Currency: Idol of Uldurn" in row for row in rows))
        workbook = load_workbook(io.BytesIO(workbook_export.export_xlsx()), read_only=True)
        try:
            history = list(workbook["Scan History"].values)
            column = history[0].index("Item: Idol of Uldurn")
            self.assertIn(1, [row[column] for row in history[1:]])
        finally:
            workbook.close()

    def test_approved_currency_alternative_does_not_inherit_item_candidate_category(self):
        """Choosing the competing currency preserves Currency registration and learned artwork kind."""
        self.save_idol_choice("Vaal Armourer's Infuser")
        self.assertIn("Vaal Armourer's Infuser", logger.currency_names())
        self.assertNotIn("Vaal Armourer's Infuser", logger.item_names())
        self.assertEqual([(row["name"], row["kind"]) for row in logger.review_icons()],
                         [("Vaal Armourer's Infuser", "currency")])

    def test_registered_category_wins_over_the_same_item_candidate_hint(self):
        """A user's already registered Currency name is not silently migrated by catalog proposals."""
        logger.add_currency_item("Idol of Uldurn")
        self.save_idol_choice("Idol of Uldurn")
        self.assertIn("Idol of Uldurn", logger.currency_names())
        self.assertNotIn("Idol of Uldurn", logger.item_names())

    def test_manually_typed_other_name_does_not_inherit_item_candidate_category(self):
        """Candidate-specific evidence cannot assign Item to a different manually supplied name."""
        self.save_idol_choice("Manual unrelated token")
        self.assertIn("Manual unrelated token", logger.currency_names())
        self.assertNotIn("Manual unrelated token", logger.item_names())

    def test_actual_unmarked_unknown_capture_cells_retain_verified_one(self):
        """Source captures with clear empty count corners retain count evidence despite unknown names."""
        for filename in ("inventory_121050.png", "inventory_121548.png"):
            result = self.capture_result(filename)
            for slot in (2, 49):
                with self.subTest(capture=filename, slot=slot):
                    row = self.row_for(result, slot)
                    self.assertEqual(row.get("quantity"), 1)
                    self.assertIs(row.get("count_verified"), True)
                    self.assertIs(row.get("count_needs_review"), False)

    def test_actual_unmarked_unknown_cells_request_only_name_in_review(self):
        """Real recognition preserves one in Qt while keeping both unknown identities reviewable."""
        table = self.review(self.capture_result("inventory_121050.png"))
        for slot in (2, 49):
            index = self.table_row(table, slot)
            self.assertFalse(table.isRowHidden(index))
            self.assertEqual(table.item(index, 1).text(), "")
            self.assertEqual(table.item(index, 2).text(), "1")
            controls = table.cellWidget(index, 3)
            self.assertTrue(controls.property("reviewNameRequired"))
            self.assertFalse(controls.property("reviewCountRequired"))
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)

    def test_missed_marker_cannot_default_unknown_native_292_to_one(self):
        """A contradictory independent native count keeps even an absent-marker label unverified."""
        self.assert_count_confirmation(self.uncertain_scan(native=292))

    def test_clipped_uncertain_or_present_count_evidence_stays_manual(self):
        """Count-corner and grid uncertainty or a present unreadable marker cannot prove single quantity."""
        for flags in ({"clipped": True}, {"geometry": True}, {"marker": True}):
            with self.subTest(flags=flags):
                self.assert_count_confirmation(self.uncertain_scan(**flags))

    def test_custom_text_reader_cannot_supply_absent_unknown_count_proof(self):
        """An injected text callback bypasses label evidence and cannot make unknown count one trusted."""
        self.assert_count_confirmation(self.uncertain_scan(read=lambda image: []))

    def test_unknown_payload_without_explicit_count_trust_does_not_prefill_quantity(self):
        """A raw quantity or boolean cannot masquerade as independently verified unknown-count evidence."""
        for change in ({}, {"count_verified": False}, {"count_verified": "yes"},
                       {"quantity": True, "count_verified": True},
                       {"count_needs_review": True, "count_verified": True}):
            with self.subTest(change=change):
                result = {"items": [], "unknown": [{"slot": 1, "quantity": 1,
                          "count_needs_review": False, **change}]}
                table = self.review(result)
                index = self.table_row(table, 1)
                self.assertEqual(table.item(index, 2).text(), "")
                self.assertTrue(table.cellWidget(index, 3).property("reviewCountRequired"))
                self.window.reject_review()


if __name__ == "__main__":
    unittest.main()
