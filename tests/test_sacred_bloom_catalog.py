"""Exercise Sacred Bloom's captured pixels through the shipped currency catalog.

Slot 37 supplies the documented catalog variant; slot 49 is held out from that
bank but comes from the same page, so this is not independent accuracy evidence.
Repeated-footprint cases copy those real pixels to test enumeration. Mirror and
silver-currency controls render existing source art, not unseen game captures.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, ocr_sensitivity, store
from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid


class SacredBloomCatalogTests(unittest.TestCase):
    """Keep the missing Bloom identity distinct from Mirror and separate stacks."""

    @classmethod
    def setUpClass(cls):
        """Extract unchanged captured rewards using the production grid detector."""
        path = Path(__file__).parent / "fixtures/ritual_rewards/10.png"
        with Image.open(path) as source:
            cls.page = source.convert("RGB")
        cls.grid = ritual_grid.detect_reward_grid(cls.page)
        cls.cells = {}
        inset = max(1, round(cls.grid["pitch"] * .02))
        for reward in cls.grid["rewards"]:
            if reward["slots"] in ([37], [49]):
                left, top, right, bottom = reward["box"]
                cls.cells[reward["slots"][0]] = cls.page.crop(
                    (left + inset, top + inset, right, bottom))

    def setUp(self):
        """Keep production settings/database access inside a temporary profile."""
        self.directory = tempfile.TemporaryDirectory(prefix="poe2-sacred-bloom-")
        self.addCleanup(self.directory.cleanup)
        self.profile = patch.object(store, "DATA_DIR", Path(self.directory.name))
        self.ready = patch.object(logger, "_READY", False)
        self.profile.start()
        self.ready.start()
        self.addCleanup(self.profile.stop)
        self.addCleanup(self.ready.stop)

    def test_original_capture_and_same_page_holdout_keep_verified_single_counts(self):
        """Identify both supplied Blooms without creating nonexistent stack labels."""
        reader = currency_ocr.get_reader()
        labels = item_ocr._ritual_cell_labels(self.cells)
        self.assertEqual(set(self.cells), {37, 49})
        for strictness in (ocr_sensitivity.DEFAULT, 0):
            for slot, cell in self.cells.items():
                with self.subTest(slot=slot, strictness=strictness):
                    identity = reader.icon(cell, strictness=strictness)
                    self.assertEqual(identity["members"], ["Sacred Bloom"])
                    self.assertEqual(identity["family"], "sacred-bloom")
                    self.assertFalse(labels[slot]["count_present"])
                    self.assertTrue(labels[slot]["count_verified"])
                    self.assertEqual(labels[slot]["count"], 1)

    def test_currency_scan_keeps_three_captured_blooms_as_separate_single_stacks(self):
        """Run real identity, count and unique-conflict readers at three grid positions."""
        cell = self.cells[49]
        pitch = cell.width
        image = Image.new("RGB", (12 * pitch, 5 * pitch), (26, 26, 40))
        slots = (1, 17, 60)
        for slot in slots:
            image.paste(cell, (((slot - 1) % 12) * pitch, ((slot - 1) // 12) * pitch))
        image.info["poe2_inventory_aligned"] = True
        for strictness in (ocr_sensitivity.DEFAULT, 0):
            with self.subTest(strictness=strictness):
                result = item_ocr.scan_inventory_grid(image, strictness=strictness)
                self.assertEqual(result["unknown"], [])
                self.assertEqual([row["slot"] for row in result["items"]], list(slots))
                for row in result["items"]:
                    self.assertEqual((row["name"], row["quantity"]), ("Sacred Bloom", 1))
                    self.assertFalse(row["count_needs_review"])
                    self.assertFalse(row.get("name_needs_review", False))

    def test_ritual_scan_keeps_three_blooms_in_distinct_reward_footprints(self):
        """Retain untouched slots 37/49 and a captured-art copy at slot 61."""
        page = self.page.copy()
        target = next(row for row in self.grid["rewards"] if row["slots"] == [61])
        left, top, right, bottom = target["box"]
        inset = max(1, round(self.grid["pitch"] * .02))
        art = self.cells[49].resize((right - left - inset, bottom - top - inset),
                                    Image.Resampling.LANCZOS)
        page.paste(art, (left + inset, top + inset))
        for strictness in (ocr_sensitivity.DEFAULT, 0):
            with self.subTest(strictness=strictness):
                result = item_ocr.scan_ritual_page(page, OMEN_NAMES, strictness=strictness)
                rows = [row for row in result["items"] if row["name"] == "Sacred Bloom"]
                self.assertEqual([row["grid_slots"] for row in rows], [[37], [49], [61]])
                for row in rows:
                    self.assertEqual(row["quantity"], 1)
                    self.assertTrue(row["identity_verified"])
                    self.assertFalse(row["count_needs_review"])
                    self.assertFalse(row["needs_review"])
                    self.assertNotIn("candidate_names", row)

    def test_existing_mirror_and_silver_source_art_keep_their_own_identities(self):
        """Protect existing identities with explicitly catalog-derived controls."""
        bank = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())
        reader = currency_ocr.get_reader()
        for name in ("Mirror of Kalandra", "Orb of Annulment", "Orb of Chance"):
            entry = next(entry for entry in bank["icons"] if name in entry["members"])
            art = Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4))
            background = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
            background.alpha_composite(art)
            for strictness in (ocr_sensitivity.DEFAULT, 0):
                with self.subTest(name=name, strictness=strictness):
                    identity = reader.icon(background.convert("RGB"), strictness=strictness)
                    self.assertEqual(identity["members"], [name])
                    self.assertNotEqual(identity["family"], "sacred-bloom")

    def test_catalog_upgrade_preserves_a_preexisting_item_registration_and_logs(self):
        """A newly bundled currency identity must not conflict with an older user's Item label."""
        logger.initialize()
        # Model the valid preceding catalog, which did not yet seed Sacred Bloom.
        with logger._connect() as db:
            db.execute("DELETE FROM currency_items WHERE name=?", ("Sacred Bloom",))
        logger.add_item_name("Sacred Bloom")
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Sacred Bloom", "quantity": 2,
                                                "category": "Item"}], register_names=True)
        before = logger.export_all_csv()
        with logger._connect() as db:
            logger._set_meta(db, "currency_inventory_catalog_version", "pre-bloom-catalog")
        logger._READY = False
        logger.initialize()
        self.assertIn("Sacred Bloom", logger.item_names())
        self.assertNotIn("Sacred Bloom", logger.currency_names())
        self.assertEqual(logger.export_all_csv(), before)
        saved = logger.save_currency_snapshot("end", [{"name": "Sacred Bloom", "quantity": 3,
                                                       "category": "Item"}], register_names=True)
        self.assertEqual(saved["map_id"], "M0001")


if __name__ == "__main__":
    unittest.main()
