"""Same-shape catalog items that differ only in colour are never named by shape alone."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid


RITUAL_PAGE = Path(__file__).parent / "fixtures" / "ritual_pages_hover" / "124949.png"


class LookAlikeColourTests(unittest.TestCase):
    """Place catalog art on a real empty Ritual cell, as a Ritual page shows it."""

    @classmethod
    def setUpClass(cls):
        """Crop one empty Ritual cell and load the catalog art."""
        cls.folder = tempfile.TemporaryDirectory(prefix="poe2-look-alikes-")
        cls.patches = (patch.object(store, "DATA_DIR", Path(cls.folder.name)), patch.object(logger, "_READY", False))
        for active in cls.patches:
            active.start()
        with Image.open(RITUAL_PAGE) as source:
            page = source.convert("RGB")
        grid = ritual_grid.detect_reward_grid(page)
        used = {slot for reward in grid["rewards"] for slot in reward["slots"]}
        slot = next(slot for slot in range(61, 121) if slot not in used)
        left, top = grid["bounds"][:2]
        pitch = grid["pitch"]
        column, row = (slot - 1) % 12, (slot - 1) // 12
        cls.tile = page.crop((round(left + column * pitch), round(top + row * pitch),
                              round(left + (column + 1) * pitch), round(top + (row + 1) * pitch)))
        catalog = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text(encoding="utf-8"))
        cls.art = {icon["members"][0]: icon["rgba"] for icon in catalog["icons"]}
        cls.reader = currency_ocr.get_reader()

    @classmethod
    def tearDownClass(cls):
        """Restore the caller's profile."""
        for active in reversed(cls.patches):
            active.stop()
        cls.folder.cleanup()

    def cell(self, name):
        """Render a catalog item on the Ritual cell, cropped as the Ritual reader crops it."""
        size = 53
        art = Image.fromarray(np.array(self.art[name], dtype=np.uint8).reshape(40, 40, 4), "RGBA")
        art = art.resize((size, size), Image.Resampling.LANCZOS)
        cell = self.tile.resize((size, size)).copy()
        cell.paste(art, (0, 0), art)
        return cell.crop((1, 1, size, size))

    def test_liquid_emotions_drawn_alike_are_never_named_as_each_other(self):
        """Ferocity and Contempt share one flask; each was named as the other on a Ritual cell."""
        for name in ("Potent Liquid Ferocity", "Potent Liquid Contempt"):
            cell = self.cell(name)
            for look, shown in (("plain", cell), ("unframed", item_ocr._unframed(cell))):
                with self.subTest(name=name, look=look):
                    icon = self.reader.icon(shown)
                    self.assertIn(icon.get("members") or [name], ([name],))
        held = self.reader.icon(self.cell("Potent Liquid Contempt"))
        self.assertFalse(held.get("family"))
        self.assertIn("Potent Liquid Contempt", held["look_alikes"])

    def test_held_look_alikes_reach_ritual_review_as_separate_choices(self):
        """The Ritual reader offers each look-alike as its own suggestion."""
        cell = self.cell("Potent Liquid Contempt")
        name, _score, candidates = item_ocr._ritual_reward_icon(self.reader, cell, {}, ())
        self.assertIsNone(name)
        self.assertIn("Potent Liquid Contempt", candidates)
        self.assertTrue(all(" / " not in candidate for candidate in candidates))

    def test_items_without_a_same_shape_sibling_are_still_named(self):
        """A distinct shape needs no colour check."""
        for name in ("Chaos Orb", "Divine Orb", "Orb of Annulment"):
            with self.subTest(name=name):
                self.assertIn(name, self.reader.icon(self.cell(name)).get("members") or [])

    def test_same_shape_follows_the_matchers_shift_allowance(self):
        """Liquid emotions share a shape a pixel apart; different orbs do not."""
        families = {members[0]: family for family, members in self.reader.inventory_members.items()}
        rgba = self.reader.calibrated_rgba
        self.assertTrue(currency_ocr._same_shape(rgba[families["Potent Liquid Ferocity"]],
                                                 rgba[families["Potent Liquid Contempt"]]))
        self.assertFalse(currency_ocr._same_shape(rgba[families["Chaos Orb"]], rgba[families["Exalted Orb"]]))


if __name__ == "__main__":
    unittest.main()
