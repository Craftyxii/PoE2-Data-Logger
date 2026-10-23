"""Route general-key captures: Ritual windows, Rite maps and Tablets mentioning Favours or Tribute."""

import json
from pathlib import Path
import unittest

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.ocr.item_ocr import is_ritual_page
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from tests import test_hotkey_presentation_retry as fixtures


FIXTURES = Path(__file__).parent / "fixtures"


def rows(recorded):
    """Rebuild OCR rows from recorded text and confidence pairs."""
    return [{"text": text, "score": score} for text, score in recorded]


class RitualWindowDetectionTests(unittest.TestCase):
    """Detect the Ritual window by its own rows, not by modifier sentences."""

    def test_every_recorded_ritual_window_is_a_ritual_page(self):
        """Counters, standalone titles and Defer/Offer controls identify all 34 windows."""
        windows = json.loads((FIXTURES / "ritual_windows" / "ocr_rows.json").read_text(encoding="utf-8"))["windows"]
        self.assertEqual(len(windows), 34)
        for name, recorded in windows.items():
            with self.subTest(window=name):
                self.assertTrue(is_ritual_page(rows(recorded)))

    def test_modifier_sentences_and_tooltip_anchors_are_not_ritual_pages(self):
        """Rite, Tablet and waystone wording about Favours and Tribute stays an item or map tooltip."""
        for recorded in (
                [("RAVINE", 1), ("BIOME: MOUNTAIN", 1),
                 ("RIROLLING FAVOURS AT RITUAL ALTARS IN AREA COSTS 20% REDUCED TRIBUTE", .99)],
                [("RITUAL TABLET", 1), ("5 USES REMAINING", 1),
                 ("FAVOURS AT RITUAL ALTARS IN AREA COSTS 11% INCREASED TRIBUTE", .98)],
                [("WAYSTONE (TIER 15)", 1), ("FAVOURS", 1), ("2,000 TRIBUTE", 1)],
                [("FAVOURS DEFERRED AT RITUAL ALTARS IN MAP REAPPEAR 20% SOONER", 1), ("TRIBUTE", 1)]):
            with self.subTest(rows=recorded[0][0]):
                self.assertFalse(is_ritual_page(rows(recorded)))
        # Weak counters need a confirming title or control.
        self.assertFalse(is_ritual_page(rows([("1,162 TRIBUTE", .62)])))
        self.assertTrue(is_ritual_page(rows([("1,162 TRIBUTE", .62), ("DEFER ITEM", 1)])))


class GeneralKeyRoutingTests(unittest.TestCase):
    """Send real hovered tooltips through the general key's capture and reading path."""

    @classmethod
    def setUpClass(cls):
        """Share the presentation fixture's Qt application."""
        fixtures.HotkeyPresentationRetryTests.setUpClass()

    def setUp(self):
        """Use an isolated profile with a started map."""
        self.fixture = fixtures.HotkeyPresentationRetryTests(
            "test_opened_result_and_png_stay_owned_until_writer_releases")
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def hover(self, path):
        """Press the general key over one captured tooltip and return the published event."""
        with Image.open(path) as source:
            tooltip = source.convert("RGB")
        # A stand-in remnant reader skips the Expedition panel scan of the same pixels.
        manager = HotkeyManager(supported=True, readers={"opened": lambda *_, **__: {}},
                                hover_reader=lambda: None, focused=lambda: True)
        # Read the captured pixels directly: game-window focus and bounds are
        # Windows capture concerns that a test runner without the game cannot meet.
        manager._capture_lock.acquire()
        manager._capture_revision = manager._capture_owner = 1
        manager._read_capture("default", "opened", None, tooltip, tooltip, revision=1, strictness_values={})
        return manager.status()["latest"]

    def test_hovered_rite_map_reads_as_a_rite_not_a_ritual_page(self):
        """A Rite map's "Rerolling Favours … Tribute" modifier no longer opens a Ritual review."""
        logger.save_settings({"activity_type": "Ritual"})
        event = self.hover(FIXTURES / "rite_tooltips" / "ravine.png")
        self.assertEqual(event["mode"], "rite", event.get("error"))
        self.assertEqual(len(event["result"]["affixes"]), 2)

    def test_hovered_unique_ritual_tablet_reads_as_a_tablet(self):
        """Freedom of Faith's Tribute modifier keeps the general key on the Tablet reader."""
        event = self.hover(FIXTURES / "unique_tablets" / "freedom_of_faith.png")
        self.assertEqual((event["mode"], event["result"]["kind"]), ("item", "tablet"), event.get("error"))
        self.assertEqual(len(event["result"]["mods"]), 2)


if __name__ == "__main__":
    unittest.main()
