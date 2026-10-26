"""Startup fills the scanners' production reference banks without scan evidence."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import artwork_cache, currency_ocr, item_ocr, ritual_grid, unique_items
from tests.test_unique_items import render, temporary_catalog, texture


class StartupArtworkCacheTests(unittest.TestCase):
    """Use real bundled/captured artwork and keep OCR-model inference out of startup."""

    def setUp(self):
        artwork_cache._CACHE.clear()
        unique_items.clear_caches()
        item_ocr._RITUAL_MATCH_CACHE.clear()
        if hasattr(item_ocr._RITUAL_OMEN_CACHE, "ranks"):
            item_ocr._RITUAL_OMEN_CACHE.ranks.clear()
        self.reader = currency_ocr.CurrencyReader()
        self.cell = Image.fromarray(np.asarray(
            self.reader.calibrated_candidates[50][1][4:44, 4:44], dtype=np.uint8), "RGB")
        output = io.BytesIO()
        self.cell.save(output, format="PNG")
        self.inventory = [
            {"id": 1, "name": "Local reviewed icon", "kind": "currency", "reviewed": True,
             "columns": 1, "rows": 1, "image": output.getvalue()},
            {"id": 2, "name": "Local fallback icon", "kind": "item", "reviewed": False,
             "columns": 1, "rows": 1, "image": output.getvalue()},
            {"id": 3, "name": "Local equipment", "kind": "item", "reviewed": True,
             "columns": 2, "rows": 3, "image": output.getvalue()},
        ]
        # Ritual's entire source signature differs even when selected learned
        # pixels coincide, as it does for the logger's two reference queries.
        self.ritual = [{"name": "Omen of Whittling", "kind": "omen",
                        "image": output.getvalue()}] + [dict(row) for row in self.inventory]

    def tearDown(self):
        artwork_cache._CACHE.clear()
        unique_items.clear_caches()

    def prepare(self, inventory=None, ritual=None):
        with patch.object(currency_ocr, "get_reader", return_value=self.reader):
            item_ocr.prepare_artwork(self.inventory if inventory is None else inventory,
                                     self.ritual if ritual is None else ritual)

    def test_startup_populates_only_production_banks_and_keeps_all_footprints(self):
        """No synthetic query, OCR engine or recognition result is needed to warm art."""
        with patch.object(item_ocr, "_engine", side_effect=AssertionError("Startup loaded OCR")), \
                patch.object(item_ocr, "ocr_lines", side_effect=AssertionError("Startup read text")), \
                patch.object(artwork_cache.cv2, "matchTemplate",
                             side_effect=AssertionError("Startup matched a fake query")):
            self.prepare()
        self.assertFalse(self.reader._rank_cache or self.reader._example_rank_cache or
                         item_ocr._RITUAL_MATCH_CACHE or unique_items._QUERIES)
        self.assertEqual(set(self.reader._scaled_candidates), set(self.reader.calibrated_rgba))
        self.assertEqual(len(artwork_cache._CACHE), 2)
        self.assertTrue(all(bank is not None and not bank.verified
                            for _, bank in artwork_cache._CACHE.values()))
        self.assertEqual({bank.shape for bank in unique_items._BANKS.values()},
                         {(columns, rows) for columns in (1, 2) for rows in (1, 2, 3, 4)})
        self.assertGreater(unique_items._RETAINED_BYTES, 32 * 1024 * 1024)
        self.assertLessEqual(unique_items._RETAINED_BYTES, 64 * 1024 * 1024)
        self.assertLessEqual(self.reader._learned_reference_bytes, 16 * 1024 * 1024)
        for references in (self.inventory, self.ritual):
            signature = item_ocr._reference_snapshot(references).signature
            actual = {key[1:] for key in self.reader._learned_reference_banks
                      if key[0] == signature}
            self.assertEqual(actual, {((1, 1), False), ((1, 1), True), ((2, 3), True)})

        # Calling startup again is an actual cache hit in every production
        # builder; a fresh result cache would not satisfy these identity checks.
        identities = {key: value[0] for key, value in self.reader._learned_reference_banks.items()}
        atlases = dict(self.reader._example_atlases)
        banks = dict(unique_items._BANKS)
        transforms = {key: value[1] for key, value in artwork_cache._CACHE.items()}
        with patch.object(item_ocr, "_reference_image", side_effect=AssertionError("Decoded again")), \
                patch.object(currency_ocr, "_inventory_candidate", side_effect=AssertionError("Scaled again")), \
                patch.object(artwork_cache._Correlation, "__init__",
                             side_effect=AssertionError("Built reference FFT again")), \
                patch.object(unique_items.Image, "open", side_effect=AssertionError("Decoded unique again")):
            self.prepare()
            for references in (self.inventory, self.ritual):
                for shape, reviewed in (((1, 1), False), ((1, 1), True), ((2, 3), True)):
                    bank = item_ocr._prepared_reference_bank(self.reader, references, shape, reviewed)
                    self.reader.examples(self.cell, bank[0])
            for shape in {(c, r) for c in (1, 2) for r in (1, 2, 3, 4)}:
                self.assertIs(unique_items._bank(shape), next(bank for bank in banks.values()
                                                             if bank.shape == shape))
        for key, value in identities.items():
            self.assertIs(self.reader._learned_reference_banks[key][0], value)
        for key, value in atlases.items():
            self.assertIs(self.reader._example_atlases[key], value)
        for key, value in transforms.items():
            self.assertIs(artwork_cache._CACHE[key][1], value)

    @patch.object(ocr_runtime, "_FAST", False)
    def test_inventory_and_captured_gray_scan_match_cold_outputs_without_preparing_again(self):
        """Real scanner matching reuses warmed instances and preserves complete results."""
        image = Image.new("RGB", (480, 200), (26, 26, 40))
        image.paste(self.cell, (0, 0))
        with Image.open(Path(__file__).parent / "fixtures/ritual_grayscale/264_tribute.png") as source:
            ritual = source.convert("RGB")
        grid = ritual_grid.detect_reward_grid(ritual)
        reward = next(reward for reward in grid["rewards"] if reward["slots"] == [1])
        left, top, right, bottom = reward["box"]
        inset = max(1, round(grid["pitch"] * .02))
        gray_cell = ritual.crop((left + inset, top + inset, right, bottom))
        cold = currency_ocr.CurrencyReader()
        with patch.object(currency_ocr, "get_reader", return_value=cold):
            expected_inventory = item_ocr.scan_inventory_grid(image, self.inventory, read=lambda cell: [])
        with patch.object(artwork_cache, "match", side_effect=cv2.matchTemplate):
            expected_gray = item_ocr._ritual_gray_omen_icon(gray_cell)
        self.assertIsNotNone(expected_gray)
        item_ocr._RITUAL_OMEN_CACHE.ranks.clear()
        artwork_cache._CACHE.clear()
        self.prepare()
        transforms = {key: value[1] for key, value in artwork_cache._CACHE.items()}
        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "_reference_image", side_effect=AssertionError("Scan decoded reference")), \
                patch.object(currency_ocr, "_inventory_candidate", side_effect=AssertionError("Scan scaled reference")), \
                patch.object(artwork_cache._Correlation, "__init__",
                             side_effect=AssertionError("Scan built reference FFT")):
            actual_inventory = item_ocr.scan_inventory_grid(image, self.inventory, read=lambda cell: [])
            actual_gray = item_ocr._ritual_gray_omen_icon(gray_cell)
        self.assertEqual(actual_inventory, expected_inventory)
        self.assertEqual(actual_gray, expected_gray)
        for key, value in transforms.items():
            self.assertIs(artwork_cache._CACHE[key][1], value)
            self.assertTrue(value.verified)

    def test_reference_edits_rebuild_only_the_changed_full_source_bank(self):
        """Metadata, dimensions and replacement pixels retain normal content invalidation."""
        self.prepare()
        original = item_ocr._reviewed_bank(self.reader, self.inventory, (2, 3))
        ritual_bank = item_ocr._reviewed_bank(self.reader, self.ritual, (2, 3))
        updated = [dict(row) for row in self.inventory]
        updated[-1].update(name="Corrected equipment", kind="omen", rows=4,
                           image=self.cell.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
        with patch.object(item_ocr, "_reference_image", wraps=item_ocr._reference_image) as decode:
            self.prepare(inventory=updated)
        self.assertEqual(decode.call_count, 3)
        replacement = item_ocr._reviewed_bank(self.reader, updated, (2, 4))
        self.assertIsNot(replacement, original)
        self.assertEqual((set(replacement[0].order), dict(replacement[1]), replacement[2]),
                         ({"Corrected equipment"}, {"Corrected equipment": "omen"}, (2, 4)))
        self.assertIs(item_ocr._reviewed_bank(self.reader, self.ritual, (2, 3)), ritual_bank)
        fresh = currency_ocr.CurrencyReader()
        fresh_bank = item_ocr._reviewed_bank(fresh, updated, (2, 4))
        self.assertEqual(self.reader.examples(self.cell, replacement[0]),
                         fresh.examples(self.cell, fresh_bank[0]))

    def test_every_unique_footprint_matches_cold_without_redecoding_source_art(self):
        """All eight production matcher paths consume their retained startup bank."""
        controls = {}
        for row in unique_items._catalog().items:
            shape = row["columns"], row["rows"]
            if shape not in controls:
                with Image.open(unique_items.ASSET_ROOT / row["icon"]) as image:
                    controls[shape] = render(image, shape)
        expected = {shape: unique_items.match(image, shape) for shape, image in controls.items()}
        self.assertEqual(len(expected), 8)
        self.assertTrue(all(expected.values()))
        unique_items.clear_caches()
        self.prepare()
        banks = {bank.shape: bank for bank in unique_items._BANKS.values()}
        with patch.object(unique_items.Image, "open", side_effect=AssertionError("Unique scan decoded again")):
            for shape, image in controls.items():
                self.assertEqual(unique_items.match(image, shape), expected[shape])
                self.assertIs(unique_items._bank(shape), banks[shape])
        self.assertEqual(len(unique_items._BANKS), 8)

    def test_fallback_scanner_uses_the_prepared_original_icons(self):
        """The legacy no-grid Ritual matcher receives the same cached source arrays."""
        self.prepare()
        combined = item_ocr._ritual_fallback_references(self.ritual)
        icons = item_ocr._ritual_fallback_bank(self.reader, combined)
        image = Image.new("RGB", (300, 200), (26, 26, 40))
        image.paste(self.cell, (50, 50))
        seen = []

        def matching(shown, icon, scale, page_key):
            seen.append(icon)
            return ()

        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(item_ocr, "ocr_lines", return_value=[]), \
                patch.object(item_ocr, "ritual_totals", return_value={}), \
                patch.object(item_ocr, "deferred_markers", return_value=[]), \
                patch.object(ritual_grid, "detect_reward_grid", return_value=None), \
                patch.object(item_ocr, "_reference_image", side_effect=AssertionError("Fallback decoded again")), \
                patch.object(item_ocr, "_ritual_icon_matches", side_effect=matching):
            item_ocr.scan_ritual_page(image, OMEN_NAMES, self.ritual)
        expected = [icon for name, icon in icons if name in OMEN_NAMES]
        self.assertEqual(len(seen), len(expected))
        self.assertTrue(all(actual is wanted for actual, wanted in zip(seen, expected)))

    def test_catalog_edits_invalidate_bundled_omen_and_fallback_pixels(self):
        """Replaced catalog bytes get a new source signature in both Omen paths."""
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
        omen = next(entry for entry in entries if entry["members"][0].startswith("Omen of "))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory-icons.json"
            path.write_text(json.dumps({"icons": [omen]}))
            with patch.object(currency_ocr, "ROOT", Path(directory)):
                before = currency_ocr.omen_references()
                bank = item_ocr._ritual_fallback_bank(self.reader, before)
                old_stat = path.stat()
                changed = dict(omen, rgba=list(omen["rgba"]))
                changed["rgba"][400:800] = [255] * 400
                path.write_text(json.dumps({"icons": [changed]}))
                os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))
                after = currency_ocr.omen_references()
                replacement = item_ocr._ritual_fallback_bank(self.reader, after)
                self.assertNotEqual(before[0]["image"], after[0]["image"])
                self.assertIsNot(bank, replacement)

    def test_corrupt_unique_source_keeps_integrity_fallback_after_preparation(self):
        """A stale prepared bank cannot bypass a changed source file's checksum."""
        artwork = texture()
        entries = [("Source control", artwork, (1, 1), [])]
        with temporary_catalog(entries) as (root, manifest):
            self.assertEqual(unique_items.prepare_artwork(), 1)
            query = render(artwork, (1, 1))
            self.assertIsNotNone(unique_items.match(query, (1, 1)))
            icon = root / manifest["items"][0]["icon"]
            icon.write_bytes(b"corrupt replacement")
            self.assertEqual(unique_items.prepare_artwork(), 0)
            self.assertIsNone(unique_items.match(query, (1, 1)))


if __name__ == "__main__":
    unittest.main()
