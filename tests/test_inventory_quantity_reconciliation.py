"""Keep wider retry artwork from overriding corroborated inventory count digits.

The rendered Prismatic Alloy regression uses bundled artwork and captured digit
templates. It reproduces the production failure but is not the user's screenshot
or an independent capture-accuracy measurement.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageFilter

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr


class InventoryQuantityReconciliationTests(unittest.TestCase):
    """Exercise real conflicting crop pixels and preserve genuine trailing digits."""

    @classmethod
    def setUpClass(cls):
        """Load the existing captured glyphs and bundled artwork without altering either bank."""
        cls.glyphs = json.loads((currency_ocr.ROOT / "digit-templates.json").read_text())["templates"]
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
        cls.artwork = {entry["family"]: Image.fromarray(
            np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4), "RGBA")
            for entry in entries if entry["family"] in ("divine-orb", "prismatic-alloy")}

    def setUp(self):
        """Isolate catalog/settings reads and restore the profile even after a failure."""
        temporary = tempfile.TemporaryDirectory(prefix="poe2-count-artwork-")
        self.addCleanup(temporary.cleanup)
        previous = store.DATA_DIR
        self.addCleanup(self.restore_profile, previous)
        store.DATA_DIR = Path(temporary.name)
        logger._READY = False

    @staticmethod
    def restore_profile(previous):
        """Restore the caller's profile and initialize flag after each regression."""
        store.DATA_DIR = previous
        logger._READY = False

    def cell(self, family, number, *, y=2):
        """Render captured glyphs over existing artwork, including the normal black outline."""
        cell = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        cell.alpha_composite(self.artwork[family])
        mask = Image.new("L", cell.size)
        x, baseline = 2, 13 + y
        for digit in str(number):
            glyph = self.glyphs[digit]
            ink = np.asarray(glyph["data"], dtype=np.uint8).reshape(glyph["h"], glyph["w"])
            mask.paste(Image.fromarray(ink * 255), (x, baseline - glyph["h"]))
            x += glyph["w"] + 1
        cell.paste((0, 0, 0), mask=mask.filter(ImageFilter.MaxFilter(3)))
        cell.paste((255, 255, 255), mask=mask)
        return cell.convert("RGB").resize((54, 54), Image.Resampling.LANCZOS)

    @staticmethod
    def grid(cells):
        """Place rendered cells in an already aligned grid to isolate quantity recognition."""
        grid = Image.new("RGB", (54 * 12, 54 * 5), (26, 26, 40))
        grid.info["poe2_inventory_aligned"] = True
        for index, cell in enumerate(cells):
            grid.paste(cell, ((index % 12) * 54, (index // 12) * 54))
        return grid

    def test_rendered_one_with_raw_artwork_four_keeps_supported_quantity_pending(self):
        """Read the production 1/14 crop conflict through real RapidOCR, glyphs and icon matching."""
        cell = self.cell("prismatic-alloy", 1)
        grid = self.grid([cell])
        labels = item_ocr._inventory_labels(grid)
        self.assertEqual(labels[1]["count_candidate"], 14)
        self.assertEqual(labels[1]["complete_count"], 1)
        self.assertEqual(currency_ocr.get_reader().count(cell), 1)
        result = item_ocr.scan_inventory_grid(grid)
        self.assertEqual(result["unknown"], [])
        self.assertEqual(len(result["items"]), 1)
        row = result["items"][0]
        self.assertEqual(row["name"], "Prismatic Alloy")
        self.assertEqual(row["quantity"], 1)
        self.assertTrue(row["count_needs_review"])

    def test_real_rendered_multi_digit_counts_remain_complete(self):
        """Keep correctly placed 10, 14, 74 and 292 captured glyphs through both count readers."""
        numbers = (10, 14, 74, 292)
        cells = []
        for number in numbers:
            cell = Image.new("RGB", (54, 54), (26, 26, 40))
            x = 3
            for digit in str(number):
                glyph = self.glyphs[digit]
                ink = np.asarray(glyph["data"], dtype=np.uint8).reshape(glyph["h"], glyph["w"])
                cell.paste(Image.fromarray(ink * 229 + 26).convert("RGB"), (x, 15 - glyph["h"]))
                x += glyph["w"] + 2
            cells.append(cell)
        # This case isolates count processing; identity recognition runs without
        # replacement in the separate Prismatic artwork regression above.
        reader = currency_ocr.get_reader()
        with patch.object(reader, "icon", return_value={
                "family": "prismatic-alloy", "members": ["Prismatic Alloy"], "score": .99}):
            result = item_ocr.scan_inventory_grid(self.grid(cells))
        rows = {row["slot"]: row for row in result["items"]}
        self.assertEqual(result["unknown"], [])
        self.assertEqual({slot: row["quantity"] for slot, row in rows.items()},
                         dict(enumerate(numbers, 1)))

    def test_rendered_divine_seven_remains_seven(self):
        """Keep the available Divine artwork's seven unchanged without claiming unseen-capture coverage."""
        result = item_ocr.scan_inventory_grid(self.grid([self.cell("divine-orb", 7)]))
        self.assertEqual(result["unknown"], [])
        self.assertEqual([(row["name"], row["quantity"]) for row in result["items"]],
                         [("Divine Orb", 7)])

    def test_fallback_may_keep_a_real_trailing_digit_when_native_does_not_confirm_complete_prefix(self):
        """Preserve 14 or 11 when the native reader confirms the wider crop or cannot arbitrate."""
        for candidate, native in ((14, 14), (11, 11), (14, None)):
            with self.subTest(candidate=candidate, native=native):
                labels = {slot: {"count_present": False, "count": 1, "tier_present": False}
                          for slot in range(1, 61)}
                labels[1] = {"count_present": True, "count_candidate": candidate,
                             "complete_count": 1, "tier_present": False}
                reader = SimpleNamespace(icon=lambda cell, **kwargs: {
                    "family": "prismatic-alloy", "members": ["Prismatic Alloy"], "score": .99},
                    count=lambda cell, value=native: value)
                with patch.object(item_ocr, "_inventory_labels", return_value=labels), \
                        patch.object(currency_ocr, "get_reader", return_value=reader):
                    result = item_ocr.scan_inventory_grid(self.grid([self.cell("prismatic-alloy", candidate)]))
                self.assertEqual(result["items"][0]["quantity"], candidate)
                self.assertTrue(result["items"][0]["count_needs_review"])

    def test_independent_complete_one_wins_raw_retry_artwork_for_any_extra_digit(self):
        """Require agreeing complete and native evidence without hardcoding the spurious digit four."""
        for candidate in (12, 14, 17, 19):
            with self.subTest(candidate=candidate):
                labels = {slot: {"count_present": False, "count": 1, "tier_present": False}
                          for slot in range(1, 61)}
                labels[1] = {"count_present": True, "count_candidate": candidate,
                             "complete_count": 1, "tier_present": False}
                reader = SimpleNamespace(icon=lambda cell, **kwargs: {
                    "family": "prismatic-alloy", "members": ["Prismatic Alloy"], "score": .99},
                    count=lambda cell: 1)
                with patch.object(item_ocr, "_inventory_labels", return_value=labels), \
                        patch.object(currency_ocr, "get_reader", return_value=reader):
                    result = item_ocr.scan_inventory_grid(self.grid([self.cell("prismatic-alloy", 1)]))
                self.assertEqual(result["items"][0]["quantity"], 1)
                self.assertTrue(result["items"][0]["count_needs_review"])

    def test_existing_long_native_count_recovery_is_retained(self):
        """Retain the existing prefix safeguard when native evidence sees a real third digit."""
        labels = {slot: {"count_present": False, "count": 1, "tier_present": False}
                  for slot in range(1, 61)}
        labels[1] = {"count_present": True, "count": 29, "tier_present": False}
        reader = SimpleNamespace(icon=lambda cell, **kwargs: {
            "family": "splinter", "members": ["Simulacrum Splinter"], "score": .99},
            count=lambda cell: 292)
        with patch.object(item_ocr, "_inventory_labels", return_value=labels), \
                patch.object(currency_ocr, "get_reader", return_value=reader):
            result = item_ocr.scan_inventory_grid(self.grid([self.cell("prismatic-alloy", 292)]))
        self.assertEqual(result["items"][0]["quantity"], 292)
        self.assertTrue(result["items"][0]["count_needs_review"])


if __name__ == "__main__":
    unittest.main()
