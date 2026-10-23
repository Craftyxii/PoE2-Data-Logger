"""Real 2026-10-23 Ritual pages: framed deferred Omens and partly covered item corners.

Each crop is scanned once at native scale. Names asserted here were checked
against catalog artwork and, for Omen colour variants, their glow hue.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import item_ocr


FIXTURES = Path(__file__).parent / "fixtures" / "ritual_pages"

# Gold- and red-framed deferred Omens, and items whose footprints had been split.
NAMED = {
    "094308.png": {1: ("Omen", "Omen of Whittling"), 25: ("Omen", "Omen of Whittling"),
                   37: ("Omen", "Omen of Resurgence")},
    "094531.png": {2: ("Item", "The Everlasting Gaze"), 13: ("Omen", "Omen of Resurgence"),
                   25: ("Item", "Valako's Vice")},
    "094703.png": {49: ("Item", "Blackgleam")},
    "095316.png": {1: ("Omen", "Omen of Sinistral Annulment"), 13: ("Omen", "Omen of Sinistral Erasure"),
                   97: ("Item", "Elevore")},
}
# Gray deferred Omens whose art several Omens share stay Omens awaiting a name.
GRAY_OMENS = {"094308.png": (13,), "094531.png": (1,), "094703.png": (1, 13, 25)}


class RitualFramedPagesTests(unittest.TestCase):
    """Check complete footprints, deferral and verified names on four real pages."""

    @classmethod
    def setUpClass(cls):
        """Scan every page once in a disposable profile."""
        cls.cases = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
        cls.results = {}
        with tempfile.TemporaryDirectory(prefix="poe2-ritual-pages-") as folder, \
                patch.object(store, "DATA_DIR", Path(folder)), patch.object(logger, "_READY", False):
            for case in cls.cases:
                with Image.open(FIXTURES / case["file"]) as source:
                    cls.results[case["file"]] = item_ocr.scan_ritual_page(source.convert("RGB"), OMEN_NAMES)

    def rows(self, filename):
        """Index one page's rewards by their first grid slot."""
        return {row["grid_slots"][0]: row for row in self.results[filename]["items"]}

    def test_footprints_and_deferred_anchors_match_the_pages(self):
        """Split gauntlets, a red-highlighted gray robe and a 2x2 idol each stay one reward."""
        self.assertEqual(len(self.cases), 4)
        for case in self.cases:
            with self.subTest(page=case["file"]):
                result = self.results[case["file"]]
                self.assertTrue(result["grid_detected"])
                self.assertFalse(result["coverage_uncertain"])
                self.assertEqual([row["grid_slots"] for row in result["items"]], case["footprints"])
                self.assertEqual([row["grid_slots"][0] for row in result["items"] if row["deferred"]],
                                 case["deferred_anchors"])

    def test_framed_omens_and_regrouped_items_are_named(self):
        """Deferred frames and covered corners no longer hide identities the art establishes."""
        for filename, expected in NAMED.items():
            rows = self.rows(filename)
            for slot, (category, name) in expected.items():
                with self.subTest(page=filename, slot=slot):
                    self.assertEqual((rows[slot]["category"], rows[slot]["name"]), (category, name))
                    self.assertFalse(rows[slot]["name_needs_review"])

    def test_gray_shared_art_omens_still_wait_for_a_name(self):
        """Gray Omen art shared by several Omens keeps its manual name review."""
        for filename, slots in GRAY_OMENS.items():
            rows = self.rows(filename)
            for slot in slots:
                with self.subTest(page=filename, slot=slot):
                    self.assertEqual((rows[slot]["category"], rows[slot]["name"]), ("Omen", ""))
                    self.assertTrue(rows[slot]["name_needs_review"])
                    self.assertTrue(rows[slot]["deferred"])


if __name__ == "__main__":
    unittest.main()
