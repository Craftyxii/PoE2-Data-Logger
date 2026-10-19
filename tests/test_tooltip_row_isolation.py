"""Preserve visual OCR rows while excluding nearby non-tooltip text.

The recorded boxes below came from real RapidOCR on a rendered review image,
not a captured game tooltip. They retain the detector's right-fragment-first
ordering that previously made one modifier become two saved modifiers.
"""

import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_waystone_read


RECORDED_ROWS = [
    {"text": "Waystone (Tier 15)", "score": .99704,
     "x": 398., "y": 20., "right": 591., "bottom": 45., "parts": [
         {"text": "Waystone (Tier 15)", "score": .99704,
          "x": 398., "y": 20., "right": 591., "bottom": 45.}]},
    {"text": "Waystone Drop Chance: +215%", "score": .99053,
     "x": 296., "y": 68., "right": 623., "bottom": 95., "parts": [
         {"text": "Waystone Drop Chance: +215%", "score": .99053,
          "x": 296., "y": 68., "right": 623., "bottom": 95.}]},
    {"text": "Monsters have 50% increased Damage", "score": .99877,
     "x": 268., "y": 118., "right": 759., "bottom": 146., "parts": [
         {"text": "50% increased Damage", "score": .99877,
          "x": 514., "y": 118., "right": 759., "bottom": 146.},
         {"text": "Monsters have", "score": .99994,
          "x": 268., "y": 120., "right": 420., "bottom": 143.}]},
    {"text": "Can be used in a Map Device", "score": .99897,
     "x": 297., "y": 170., "right": 594., "bottom": 195., "parts": [
         {"text": "Can be used in a Map Device", "score": .99897,
          "x": 297., "y": 170., "right": 594., "bottom": 195.}]},
]


class TooltipRowIsolationTests(unittest.TestCase):
    """Keep source-row boundaries, retained confidence and local crop geometry."""

    def read(self, rows):
        return item_text.read_screen_tooltip(Image.new("RGB", (1400, 400)), ocr_rows=rows)

    def test_recorded_fragmented_modifier_remains_one_clear_visual_row(self):
        result = self.read(copy.deepcopy(RECORDED_ROWS))
        self.assertEqual(result["mods"], ["Monsters have 50% increased Damage"])
        self.assertEqual(result["fields"]["map_mods"], 1)
        self.assertEqual(result["uncertain"], [])
        self.assertTrue(clear_waystone_read(result))

    def test_same_row_external_ui_is_removed_before_rebuilding_tooltip_text(self):
        rows = copy.deepcopy(RECORDED_ROWS)
        for row, text in ((rows[0], "Inventory"), (rows[2], "999% increased UI Damage")):
            row["parts"].append({"text": text, "score": .1,
                                 "x": 1150., "y": row["y"], "right": 1380.,
                                 "bottom": row["bottom"]})
            row.update(text=row["text"] + " " + text, score=.1, right=1380.)
        result = self.read(rows)
        self.assertEqual(result["mods"], ["Monsters have 50% increased Damage"])
        self.assertTrue(clear_waystone_read(result))
        modifier = result["ocr_rows"][2]
        self.assertEqual((modifier["x"], modifier["y"], modifier["right"], modifier["bottom"]),
                         (268., 118., 759., 146.))
        self.assertEqual(modifier["score"], .99877)
        self.assertEqual([part["text"] for part in modifier["parts"]],
                         ["Monsters have", "50% increased Damage"])
        self.assertNotIn("Inventory", " ".join(row["text"] for row in result["ocr_rows"]))

    def test_retained_weak_fragment_still_blocks_automatic_save(self):
        rows = copy.deepcopy(RECORDED_ROWS)
        rows[2]["parts"][0]["score"] = .7
        result = self.read(rows)
        self.assertEqual(result["mods"], ["Monsters have 50% increased Damage"])
        self.assertEqual(result["ocr_rows"][2]["score"], .7)
        self.assertFalse(clear_waystone_read(result))

    def test_separate_vertical_modifiers_are_never_joined(self):
        rows = copy.deepcopy(RECORDED_ROWS)
        second = {"text": "Monsters have 20% increased Attack Speed", "score": .99,
                  "x": 280., "y": 150., "right": 740., "bottom": 170.}
        rows.insert(3, {**second, "parts": [dict(second)]})
        rows[-1]["y"] = rows[-1]["parts"][0]["y"] = 200.
        rows[-1]["bottom"] = rows[-1]["parts"][0]["bottom"] = 225.
        result = self.read(rows)
        self.assertEqual(result["mods"], ["Monsters have 50% increased Damage",
                                         "Monsters have 20% increased Attack Speed"])
        self.assertEqual(result["fields"]["map_mods"], 2)
        self.assertTrue(clear_waystone_read(result))

    def test_filtering_does_not_mutate_supplied_rows_or_parts(self):
        rows = copy.deepcopy(RECORDED_ROWS)
        original = copy.deepcopy(rows)
        result = self.read(rows)
        result["ocr_rows"][2]["parts"][0]["text"] = "changed after recognition"
        self.assertEqual(rows, original)

    def test_coordinate_free_rows_keep_the_existing_parser_contract(self):
        rows = [{"text": text, "score": .99} for text in (
            "Waystone (Tier 15)", "Waystone Drop Chance: +215%",
            "Monsters have 50% increased Damage", "Can be used in a Map Device")]
        result = self.read(rows)
        self.assertEqual(result["mods"], ["Monsters have 50% increased Damage"])
        self.assertEqual(result["ocr_rows"], rows)

    def test_tablet_value_stays_with_its_affix_and_other_vertical_affix_stays_separate(self):
        rows = [
            {"text": "Precursor Tablet", "score": .99, "x": 400., "y": 20.,
             "right": 600., "bottom": 45.},
            {"text": "Uses Remaining: 10", "score": .99, "x": 400., "y": 70.,
             "right": 600., "bottom": 95.},
            {"text": "25% increased Pack Size in Map", "score": .99,
             "x": 300., "y": 118., "right": 700., "bottom": 145., "parts": [
                 {"text": "increased Pack Size in Map", "score": .99,
                  "x": 360., "y": 118., "right": 700., "bottom": 145.},
                 {"text": "25%", "score": .999, "x": 300., "y": 120.,
                  "right": 340., "bottom": 143.}]},
            {"text": "40% increased Rarity of Items found in Map", "score": .99,
             "x": 300., "y": 160., "right": 700., "bottom": 185.},
            {"text": "Can be used at a completed Tower", "score": .99,
             "x": 300., "y": 210., "right": 700., "bottom": 235.},
        ]
        result = item_text.read_screen_tooltip(Image.new("RGB", (1100, 400)),
            [entry["name"] for entry in affix_catalog()], ocr_rows=rows)
        self.assertEqual(result["kind"], "tablet")
        self.assertEqual([(entry["raw"], entry["value"]) for entry in result["matches"]], [
            ("25% increased Pack Size in Map", 25),
            ("40% increased Rarity of Items found in Map", 40)])
        self.assertEqual(result["uncertain"], [])
        self.assertEqual(result["status"], "ready")


class TooltipRowAutosaveUITests(unittest.TestCase):
    """Check the real enabled-auto-save path persists one modifier and its raw text."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-tooltip-row-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.set_auto_all(True)

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_fragmented_visual_modifier_auto_saves_once_with_count_one(self):
        image = Image.new("RGB", (1100, 400), (10, 10, 15))
        memory = io.BytesIO()
        image.save(memory, format="PNG")
        result = item_text.read_screen_tooltip(image, self.window.state["affixes"],
                                             ocr_rows=copy.deepcopy(RECORDED_ROWS))
        result.update(logger.scan_context())
        self.window._hover_item_read(result, memory.getvalue())
        self.app.processEvents()
        state = logger.get_state()
        self.assertEqual(state["settings"]["map_mods"], 1)
        self.assertEqual([text for text in state["settings"]["waystone_mods"] if text],
                         ["Monsters have 50% increased Damage"])
        self.assertEqual(state["scan_commit_count"], 1)
        self.assertIsNone(self.window.pending_review_kind)
        with logger._connect() as db:
            saved = json.loads(db.execute("SELECT snapshot_json FROM commits").fetchone()[0])
        self.assertEqual((saved["base_map_mods"], saved["total_mods"]), (1, 1))
        self.assertEqual([text for text in saved["waystone_mods"] if text],
                         ["Monsters have 50% increased Damage"])


if __name__ == "__main__":
    unittest.main()
