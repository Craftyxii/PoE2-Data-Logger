"""Run original family captures through production OCR without annotated geometry or mocked text."""

from pathlib import Path
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import propagation_scan


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "propagation_prefill"


class PropagationCapturePrefillTests(unittest.TestCase):
    """Prevent full-panel geometry and family recovery from blanking correctly recognized recipes."""

    def setUp(self):
        """Use an isolated reference catalog and preserve the caller's logger profile."""
        self.temporary = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the original profile after removing all temporary scan data."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def assert_capture(self, filename, family, recipes, positions):
        """Check exact row identities and each recipe's own slot names without changing saved state."""
        before = logger.get_state()
        result = propagation_scan.scan_propagation(FIXTURES / filename)
        self.assertEqual(result["candidates"], [family], result)
        self.assertEqual([row["selected_recipe"] for row in result["choices"]], recipes)
        with logger._connect() as db:
            for choice, expected in zip(result["choices"], positions):
                combo = db.execute("SELECT combo FROM recipes WHERE name=?",
                                  (choice["selected_recipe"],)).fetchone()[0].split("+")
                self.assertTrue(choice["can_use"], choice)
                self.assertEqual(choice["positions"], expected, choice)
                self.assertEqual(choice["runes"], [combo[index - 1].strip() for index in expected])
        self.assertEqual(logger.get_state(), before)

    def test_family57_glyph_glint_cannot_erase_six_valid_crown_selections(self):
        """A false raw slot-five partial inside the glyph cannot block the actual slot-four crowns."""
        self.assert_capture("family57-opened.jpg", 57,
                            ["Thaumaturgic Flux (Level 19)", "Verisium Manifestations (Level 20)",
                             "Powered by Verisium (Level 20)", "Remnants of Kalguur (Level 20)",
                             "Grim Pillars (Level 20)", "Bitter Dead (Level 20)"], [[4]] * 6)

    def test_family52_duplicate_anchor_cannot_erase_six_valid_crown_selections(self):
        """An off-lattice duplicate and interrupted endpoint contours cannot blank socket three."""
        self.assert_capture("family52-opened.png", 52,
                            ["Greater Jeweller's Orb", "Gemcutter's Prism x3", "Glassblower's Bauble x3",
                             "Gemcutter's Prism x2", "Glassblower's Bauble x2", "Lesser Jeweller's Orb"],
                            [[3]] * 6)

    def test_family30_short_recipes_keep_the_common_socket_without_inventing_socket_five(self):
        """The long recipe's pair and the short recipes' single crown all survive group inference."""
        self.assert_capture("family30-opened.png", 30,
                            ["Chilling Flux", "Lesser Adept Rune", "Lesser Glacial Rune", "Armourer's Scrap x2"],
                            [[1, 5], [1], [1], [1]])

    def test_real_catalog_socket_contradiction_still_requires_manual_entry(self):
        """An actual eight-tile capture cannot silently use the catalog's seven-rune recipe order."""
        before = logger.get_state()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?",
                                        ("The Runefather's Alloy",)).fetchone()[0], 7)
        result = propagation_scan.scan_propagation(FIXTURES / "family14-catalog-mismatch.png")
        self.assertEqual(result["candidates"], [14])
        self.assertEqual(len(result["choices"]), 2)
        self.assertTrue(all(not choice["can_use"] and not choice["positions"]
                            for choice in result["choices"]), result)
        self.assertEqual(logger.get_state(), before)


if __name__ == "__main__":
    unittest.main()
