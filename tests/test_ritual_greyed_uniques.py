"""A unique drawn gray because its price is above the available Tribute keeps its name.

A thin gray sprite (the Shyaba sceptre) leaves the blue cell backing visible inside its
outline. Color loss is judged on the item's own lit pixels, so the gray shape is compared
as gray instead of being marked down for missing color.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import item_ocr, unique_items

PAGE = Path(__file__).parent / "fixtures" / "ritual_greyed" / "094703.png"
SHYABA = (184, 126, 290, 337)


class RitualGreyedUniqueTests(unittest.TestCase):
    """Check a real page whose 2x4 unique is gray, and its neighbours."""

    @classmethod
    def setUpClass(cls):
        """Scan the page once in a disposable profile."""
        cls.folder = tempfile.TemporaryDirectory(prefix="poe2-ritual-greyed-")
        cls.addClassCleanup(cls.folder.cleanup)
        for active in (patch.object(store, "DATA_DIR", Path(cls.folder.name)), patch.object(logger, "_READY", False)):
            active.start()
            # Cleanups run even when the scan below fails, so the profile is always restored.
            cls.addClassCleanup(active.stop)
        with Image.open(PAGE) as source:
            cls.image = source.convert("RGB")
        cls.result = item_ocr.scan_ritual_page(cls.image, OMEN_NAMES)

    def test_gray_unique_is_named_and_its_neighbours_are_unchanged(self):
        names = {row["grid_slots"][0]: row["name"] for row in self.result["items"]}
        self.assertEqual({slot: name for slot, name in names.items() if name},
                         {2: "Arvil's Wheel", 4: "Shyaba", 37: "Surefooted Sigil", 39: "Tyranny's Grip",
                          49: "Blackgleam", 85: "Radiant Grief", 87: "Wandering Reliquary"})
        shyaba = next(row for row in self.result["items"] if row["grid_slots"][0] == 4)
        self.assertEqual(shyaba["grid_slots"], [4, 5, 16, 17, 28, 29, 40, 41])
        self.assertFalse(shyaba["deferred"])
        self.assertFalse(shyaba["unresolved"])

    def test_gray_sprite_is_compared_as_gray_with_no_rival(self):
        for inset in (0, 1):
            with self.subTest(inset=inset):
                left, top, right, bottom = SHYABA
                crop = self.image.crop((left + inset, top + inset, right, bottom))
                match = unique_items.match(crop, (2, 4))
                self.assertEqual((match["name"], match["candidate_names"]), ("Shyaba", ["Shyaba"]))


if __name__ == "__main__":
    unittest.main()
