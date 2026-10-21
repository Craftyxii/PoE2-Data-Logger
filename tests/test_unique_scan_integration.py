"""Exercise sourced artwork through both scanners without replacing the matcher.

Rendered source sprites establish delivery and footprint behavior, not accuracy
on uncatalogued captures. OCR labels and established Ritual geometry are supplied
independently so these checks do not depend on text-model inference.
"""

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid, unique_items


class UniqueScanIntegrationTests(unittest.TestCase):
    """Keep a whole unique as one Item while retaining established review gates."""

    @classmethod
    def setUpClass(cls):
        cls.reader = currency_ocr.CurrencyReader()
        cls.root = Path(unique_items.__file__).resolve().parents[1] / "unique_item_assets"
        cls.manifest = json.loads((cls.root / "manifest.json").read_text(encoding="utf-8"))
        cls.currency_icons = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]

    def artwork(self, name, pitch=54, *, frame=0):
        """Composite the original transparent source without stretching its aspect."""
        row = next(row for row in self.manifest["items"] if row["name"] == name)
        shape = row["columns"], row["rows"]
        image = Image.new("RGBA", (shape[0] * pitch - frame, shape[1] * pitch - frame),
                          (26, 26, 40, 255))
        with Image.open(self.root / row["icon"]) as source:
            shown = ImageOps.contain(source, image.size, Image.Resampling.LANCZOS)
            image.alpha_composite(shown, ((image.width - shown.width) // 2,
                                         (image.height - shown.height) // 2))
        return image.convert("RGB"), shape

    def grid(self, rows, pitch):
        """Draw distinct empty slots; painting gear removes only its internal dividers."""
        image = Image.new("RGB", (12 * pitch, rows * pitch), (4, 4, 4))
        draw = ImageDraw.Draw(image)
        for column in range(13):
            draw.line((column * pitch, 0, column * pitch, rows * pitch), fill=(70, 56, 30))
        for row in range(rows + 1):
            draw.line((0, row * pitch, 12 * pitch, row * pitch), fill=(70, 56, 30))
        if rows == 5:
            image.info["poe2_inventory_aligned"] = True
        return image

    @staticmethod
    def slots(shape, first=15):
        return [first + row * 12 + column for row in range(shape[1]) for column in range(shape[0])]

    def inventory(self, name, pitch=54, references=()):
        art, shape = self.artwork(name, pitch)
        image = self.grid(5, pitch)
        image.paste(art, (2 * pitch, pitch))
        labels = {slot: {"count": 1, "count_present": False, "tier_present": False}
                  for slot in range(1, 61)}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "_inventory_labels", return_value=labels):
            result = item_ocr.scan_inventory_grid(image, references=references)
        return result, image, art, shape

    def ritual(self, name, pitch=54, gray=False, references=(), deferred=False):
        # The established reward box includes its one-pixel top/left frame;
        # official sprites occupy the inside instead of replacing that frame.
        art, shape = self.artwork(name, pitch, frame=1)
        if gray:
            art = art.convert("L").convert("RGB")
        image = self.grid(10, pitch)
        box = (2 * pitch, pitch, (2 + shape[0]) * pitch, (1 + shape[1]) * pitch)
        image.paste(art, (box[0] + 1, box[1] + 1))
        slots = self.slots(shape)
        grid = {"bounds": (0, 0, image.width, image.height), "columns": 12, "rows": 10,
                "pitch": pitch, "confidence": 1,
                "rewards": [{"slots": slots, "box": box}],
                "evidence": {"complete": True, "horizontal_hits": 11, "vertical_hits": 13}}
        markers = [{"x": box[0] + 5, "y": box[1] + 5, "score": .99}] if deferred else []
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "ocr_lines", return_value=[]), \
                patch.object(item_ocr, "ritual_totals", return_value={"tribute_available": 62,
                                                                   "rerolls_remaining": 3}), \
                patch.object(item_ocr, "deferred_markers", return_value=markers), \
                patch.object(ritual_grid, "detect_reward_grid", return_value=grid), \
                patch.object(item_ocr, "_ritual_cell_labels", return_value={
                    0: {"count": 1, "count_present": False, "count_verified": True}}):
            result = item_ocr.scan_ritual_page(image, OMEN_NAMES, references=references)
        return result, image, art, shape, box

    def assert_identity_suggested(self, item, name):
        names = item.get("candidate_names") or item.get("candidate", "").split(" / ")
        self.assertTrue(item.get("name") == name or name in names, item)

    def test_currency_source_art_keeps_four_footprints_whole_at_three_scales(self):
        for pitch in (40, 54, 80):
            for name in ("Astramentis", "Headhunter", "Bramblejack", "Redblade Banner"):
                with self.subTest(name=name, pitch=pitch):
                    result, image, art, shape = self.inventory(name, pitch)
                    self.assertEqual(result["unknown"], [])
                    self.assertEqual(len(result["items"]), 1, result)
                    item = result["items"][0]
                    self.assert_identity_suggested(item, name)
                    self.assertEqual((item["slot"], item["quantity"], item["category"]), (15, 1, "Item"))
                    self.assertEqual(item["grid_slots"], self.slots(shape))
                    self.assertEqual(item["box"], (2 * pitch, pitch,
                                                  (2 + shape[0]) * pitch, (1 + shape[1]) * pitch))
                    self.assertFalse(item["count_needs_review"])
                    self.assertTrue(item["catalog_unique"])
                    self.assertEqual(item["catalog_revision"], self.manifest["source"]["revision"])

    def test_currency_source_with_blank_interior_cells_still_matches_one_full_item(self):
        """Transparent cells belong to established gear even when they contain no visible artwork."""
        for name in ("Oaksworn", "Brynhand's Mark"):
            with self.subTest(name=name):
                art, shape = self.artwork(name)
                deviations = [float(np.asarray(art.crop((column * 54, row * 54,
                    (column + 1) * 54, (row + 1) * 54))).std(axis=(0, 1)).mean())
                    for row in range(shape[1]) for column in range(shape[0])]
                # These genuine source sprites have at least one cell that
                # the scanner's single-cell occupancy threshold excludes.
                self.assertTrue(any(deviation < 8 for deviation in deviations))
                result, *_ = self.inventory(name)
                self.assertEqual(result["unknown"], [])
                self.assertEqual(len(result["items"]), 1, result)
                item = result["items"][0]
                self.assert_identity_suggested(item, name)
                self.assertEqual((item["quantity"], item["category"]), (1, "Item"))
                self.assertEqual(item["grid_slots"], self.slots(shape))
                self.assertEqual(item["box"], (108, 54, 216, 270))

    def test_same_unique_at_separate_positions_stays_two_items_with_local_deferred_state(self):
        """Whole belts remain distinct; a badge belongs only to its own Ritual footprint."""
        pitch = 54
        art, shape = self.artwork("Headhunter", pitch)
        image = self.grid(5, pitch)
        for column in (2, 6):
            image.paste(art, (column * pitch, pitch))
        labels = {slot: {"count": 1, "count_present": False, "tier_present": False}
                  for slot in range(1, 61)}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "_inventory_labels", return_value=labels):
            result = item_ocr.scan_inventory_grid(image)
        self.assertEqual(result["unknown"], [])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual({item["slot"] for item in result["items"]}, {15, 19})
        for item in result["items"]:
            self.assert_identity_suggested(item, "Headhunter")
            self.assertEqual((item["category"], item["quantity"]), ("Item", 1))

        art, _ = self.artwork("Headhunter", pitch, frame=1)
        image = self.grid(10, pitch)
        rewards = []
        for column, first in ((2, 15), (6, 19)):
            box = (column * pitch, pitch, (column + shape[0]) * pitch, (1 + shape[1]) * pitch)
            image.paste(art, (box[0] + 1, box[1] + 1))
            rewards.append({"slots": self.slots(shape, first=first), "box": box})
        grid = {"bounds": (0, 0, image.width, image.height), "columns": 12, "rows": 10,
                "pitch": pitch, "confidence": 1, "rewards": rewards,
                "evidence": {"complete": True, "horizontal_hits": 11, "vertical_hits": 13}}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "ocr_lines", return_value=[]), \
                patch.object(item_ocr, "ritual_totals", return_value={"tribute_available": 62,
                                                                   "rerolls_remaining": 3}), \
                patch.object(item_ocr, "deferred_markers", return_value=[
                    {"x": rewards[0]["box"][0] + 5, "y": pitch + 5, "score": .99}]), \
                patch.object(ritual_grid, "detect_reward_grid", return_value=grid), \
                patch.object(item_ocr, "_ritual_cell_labels", return_value={
                    index: {"count": 1, "count_present": False, "count_verified": True} for index in (0, 1)}):
            result = item_ocr.scan_ritual_page(image, OMEN_NAMES)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([item["deferred"] for item in result["items"]], [True, False])
        self.assertTrue(set(result["items"][0]["grid_slots"]).isdisjoint(result["items"][1]["grid_slots"]))
        for item in result["items"]:
            self.assert_identity_suggested(item, "Headhunter")
            self.assertEqual((item["category"], item["quantity"]), ("Item", 1))

    def test_ritual_color_and_gray_source_art_preserves_footprints_and_review(self):
        for gray in (False, True):
            for name in ("Astramentis", "Headhunter", "Bramblejack", "Redblade Banner"):
                with self.subTest(name=name, gray=gray):
                    result, image, art, shape, box = self.ritual(name, gray=gray, deferred=True)
                    self.assertEqual(result["reward_count"], 1)
                    self.assertEqual(len(result["items"]), 1)
                    item = result["items"][0]
                    self.assert_identity_suggested(item, name)
                    self.assertEqual((item["quantity"], item["category"]), (1, "Item"))
                    self.assertEqual(item["grid_slots"], self.slots(shape))
                    self.assertEqual(item["box"], box)
                    self.assertFalse(item["count_needs_review"])
                    if not gray:
                        self.assertFalse(item["needs_review"])
                    elif item["name_needs_review"]:
                        self.assertTrue(item["needs_review"])
                    self.assertTrue(item["deferred"])
                    self.assertIsNone(item["tribute"])
                    self.assertEqual((result["tribute_available"], result["rerolls_remaining"]), (62, 3))

    def test_shared_source_art_stays_ambiguous_despite_matching_reviewed_sibling(self):
        for name, alternate in (("Kalandra's Touch", "Ring"), ("Loreweave", "Ancient Mail")):
            art, shape = self.artwork(name)
            references = [{"name": name, "kind": "item", "columns": shape[0], "rows": shape[1],
                           "reviewed": True, "image": art}]
            # This exact learned reference would otherwise override a label.
            bank = item_ocr._reviewed_bank(self.reader, references, shape)
            learned = item_ocr._reviewed_match(self.reader, art, bank)
            self.assertIsNotNone(learned)
            self.assertEqual(learned[0], name)
            with self.subTest(name=name, scan="Currency"):
                result, *_ = self.inventory(name, references=references)
                self.assertEqual(len(result["items"]), 1)
                item = result["items"][0]
                self.assertEqual(item["name"], "")
                self.assertTrue(item["name_needs_review"])
                self.assertIn(name, item["candidate"].split(" / "))
                self.assertIn(alternate, item["candidate"].split(" / "))
                self.assertEqual(item["quantity"], 1)
            with self.subTest(name=name, scan="Ritual"):
                result, *_ = self.ritual(name, references=references)
                item = result["items"][0]
                self.assertEqual(item["name"], "")
                self.assertTrue(item["name_needs_review"])
                self.assertIn(name, item["candidate_names"])
                self.assertIn(alternate, item["candidate_names"])
                self.assertEqual(item["quantity"], 1)

    def test_neighboring_currency_stacks_keep_separate_counts_and_disagreement_hold(self):
        entry = next(row for row in self.currency_icons if "Orb of Chance" in row["members"])
        cell = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        cell.alpha_composite(Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4)))
        image = self.grid(5, 54)
        for slot in (15, 16, 27, 28):
            row, column = divmod(slot - 1, 12)
            image.paste(cell.convert("RGB").resize((54, 54)), (column * 54, row * 54))
        labels = {slot: {"count": 10, "count_present": True, "tier_present": False}
                  for slot in range(1, 61)}
        for native, held in ((10, False), (1, True)):
            with self.subTest(native=native), \
                    patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                    patch.object(item_ocr, "_inventory_labels", return_value=labels), \
                    patch.object(self.reader, "count", return_value=native):
                result = item_ocr.scan_inventory_grid(image)
                self.assertEqual(result["unknown"], [])
                self.assertEqual([(row["slot"], row["name"], row["quantity"])
                                  for row in result["items"]],
                                 [(slot, "Orb of Chance", 10) for slot in (15, 16, 27, 28)])
                self.assertEqual([row["count_needs_review"] for row in result["items"]], [held] * 4)
                self.assertTrue(all(not row.get("catalog_unique") for row in result["items"]))

    def test_mocked_shared_gray_omen_precedes_unique_lookup_without_guessing_identity(self):
        """Contract test with injected Omen evidence; this does not measure Omen accuracy."""
        names = ["Omen of Refreshment", "Omen of Whittling"]
        shared = {"family": None, "members": names, "shared_icon": True, "score": .99,
                  "margin": .3, "method": "Ritual grayscale Omen shape"}
        with patch.object(item_ocr, "_ritual_gray_omen_icon", return_value=shared), \
                patch.object(unique_items, "match", side_effect=AssertionError(
                    "A protected gray Omen must not enter the unique matcher")) as unique:
            result, *_ = self.ritual("Astramentis")
        unique.assert_not_called()
        item = result["items"][0]
        self.assertEqual((item["category"], item["name"]), ("Omen", ""))
        self.assertEqual(item["candidate_names"], names)
        self.assertTrue(item["name_needs_review"])
        self.assertNotIn("catalog_unique", item)

    def test_real_full_item_correction_keeps_local_provenance_in_both_scanners(self):
        """A reviewed custom name overrides nonshared source art without claiming uniqueness."""
        for scan, frame in (("Currency", 0), ("Ritual", 1)):
            with self.subTest(scan=scan):
                art, shape = self.artwork("Bramblejack", frame=frame)
                references = [{"name": "My reviewed armour", "kind": "item", "columns": shape[0],
                               "rows": shape[1], "reviewed": True, "image": art}]
                result, *_ = (self.inventory("Bramblejack", references=references) if scan == "Currency"
                              else self.ritual("Bramblejack", references=references))
                self.assertEqual(len(result["items"]), 1)
                item = result["items"][0]
                self.assertEqual(item["name"], "My reviewed armour")
                self.assertEqual(item["source"], "local reviewed reference")
                self.assertFalse(item["catalog_unique"])
                self.assertEqual(item["category"], "Item")
                self.assertEqual(item["quantity"], 1)
                self.assertEqual(item["grid_slots"], self.slots(shape))

    def test_actual_currency_catalog_result_commits_through_qt_as_item_in_both_exports(self):
        """Deliver sourced recognition, without substituting a hand-authored result."""
        from openpyxl import load_workbook
        from PoE2_Data_Logger.core import logger_store as logger, workbook_export
        from tests.test_unique_item_workflow import UniqueItemWorkflowTests

        result, image, art, shape = self.inventory("Bramblejack")
        self.assertEqual(result["items"][0]["name"], "Bramblejack")
        self.assertFalse(result["items"][0]["name_needs_review"])
        # Reuse the workflow fixture's isolated real Qt/SQLite profile. Its
        # callback scheduler is controlled; row approval and exports are real.
        UniqueItemWorkflowTests.setUpClass()
        fixture = UniqueItemWorkflowTests("runTest")
        fixture.setUp()
        try:
            fixture.window._inventory_captured(image, live=False)
            fixture.jobs[-1](result)
            self.assertNotIn("Bramblejack", logger.item_names())
            fixture.window.approve_review()
            self.assertIn("Bramblejack", logger.item_names())
            self.assertNotIn("Bramblejack", logger.currency_names())
            self.assertTrue(any(row.get("Item: Bramblejack") == "1" for row in fixture.history()))
            self.assertFalse(any("Currency: Bramblejack" in row for row in fixture.history()))
            workbook = load_workbook(io.BytesIO(workbook_export.export_xlsx()),
                                     read_only=True, data_only=True)
            try:
                history = list(workbook["Scan History"].values)
                column = history[0].index("Item: Bramblejack")
                self.assertIn(1, [row[column] for row in history[1:]])
            finally:
                workbook.close()
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
