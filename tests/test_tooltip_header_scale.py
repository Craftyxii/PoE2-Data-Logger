"""Keep supported Normal header evidence at scale without borrowing nearby UI."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import item_ocr, item_text
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow, clear_waystone_read


def normal_rows(scale=1):
    """Scale the observed layout of the rendered-image review reproduction."""
    rows = []
    for text, x, y, right, bottom in (
        ("Rarity: Normal", 375, 20, 525, 45),
        ("Waystone (Tier 16)", 352, 70, 546, 95),
        ("Revives Available: 6", 347, 120, 552, 143),
        ("Can be used in a Map Device", 301, 179, 598, 205),
    ):
        part = {"text": text, "score": .99, "x": x * scale, "y": y * scale,
                "right": right * scale, "bottom": bottom * scale}
        rows.append({**part, "parts": [dict(part)]})
    return rows


def intervening_header_rows(split, scale=2):
    """Represent the same unrelated item name as one box or a split visual row."""
    rows = normal_rows(scale)
    other = {"text": "Pearlescent Amulet", "score": .99, "x": 345 * scale,
             "y": 48 * scale, "right": 555 * scale, "bottom": 68 * scale}
    if split:
        other["parts"] = [
            {"text": "Pearlescent", "score": .99, "x": 345 * scale,
             "y": 48 * scale, "right": 440 * scale, "bottom": 68 * scale},
            {"text": "Amulet", "score": .99, "x": 458 * scale,
             "y": 48 * scale, "right": 555 * scale, "bottom": 68 * scale},
        ]
    rows.insert(1, other)
    return rows


class TooltipHeaderScaleTests(unittest.TestCase):
    """Only coherent title-adjacent rarity may extend the established pixel margin."""

    def read(self, rows, scale=1, strictness=50):
        return item_text.read_screen_tooltip(Image.new("RGB", (900 * scale, 400 * scale)),
                                             ocr_rows=rows, strictness=strictness)

    def assert_held(self, result):
        if result is not None:
            self.assertFalse(result.get("_normal_unmodified", False))
            self.assertFalse(clear_waystone_read(result))

    def test_complete_normal_header_scales_at_one_two_and_four_times(self):
        for scale in (1, 2, 4):
            with self.subTest(scale=scale):
                result = self.read(normal_rows(scale), scale)
                self.assertIsNotNone(result)
                self.assertEqual((result["fields"]["tier"], result["fields"]["waystone"],
                                  result["fields"]["map_mods"], result["mods"]), (16, 0, 0, []))
                self.assertTrue(clear_waystone_read(result))

    def test_missing_rarity_or_usage_never_proves_zero(self):
        for scale in (1, 2, 4):
            rows = normal_rows(scale)
            for incomplete in (rows[1:], rows[:-1]):
                with self.subTest(scale=scale, texts=[row["text"] for row in incomplete]):
                    self.assert_held(self.read(incomplete, scale))

    def test_unrelated_normal_ui_cannot_supply_a_missing_header(self):
        for scale in (2, 4):
            for offset in (-350, 120):
                rows = normal_rows(scale)
                rows[0]["x"] += offset * scale
                rows[0]["right"] += offset * scale
                rows[0]["parts"] = [{key: value for key, value in rows[0].items() if key != "parts"}]
                with self.subTest(scale=scale, offset=offset):
                    self.assert_held(self.read(rows, scale))

    def test_other_item_header_between_rarity_and_title_blocks_extension(self):
        for scale in (2, 4):
            rows = normal_rows(scale)
            rows.insert(1, {"text": "Gold Ring", "score": .99, "x": 400 * scale,
                            "y": 48 * scale, "right": 500 * scale, "bottom": 68 * scale})
            self.assert_held(self.read(rows, scale))

    def test_split_and_one_box_intervening_item_both_block_normal_recovery(self):
        for scale in (2, 4):
            for split in (False, True):
                with self.subTest(scale=scale, split=split):
                    self.assert_held(self.read(intervening_header_rows(split, scale), scale))

    def test_split_rarity_recovers_its_value_and_preserves_weak_value_review(self):
        for scale in (2, 4):
            for value_score in (.99, .5):
                rows = normal_rows(scale)
                rows[0]["parts"] = [
                    {"text": "Normal", "score": value_score, "x": 450 * scale,
                     "y": 20 * scale, "right": 525 * scale, "bottom": 45 * scale},
                    {"text": "Rarity:", "score": .99, "x": 375 * scale,
                     "y": 20 * scale, "right": 440 * scale, "bottom": 45 * scale},
                ]
                result = self.read(rows, scale)
                with self.subTest(scale=scale, value_score=value_score):
                    self.assertIsNotNone(result)
                    self.assertEqual(result["fields"]["map_mods"], 0)
                    self.assertEqual(result["ocr_rows"][0]["text"], "Rarity: Normal")
                    self.assertEqual(result["ocr_rows"][0]["score"], value_score)
                    self.assertEqual(clear_waystone_read(result), value_score >= .9)

    def test_nearby_other_item_prefix_does_not_expand_normal_proof(self):
        rows = [
            {"text": "Rarity: Normal", "score": .999, "x": 375, "y": 20, "right": 525, "bottom": 35},
            {"text": "Gold Ring", "score": .999, "x": 400, "y": 36, "right": 500, "bottom": 46},
            {"text": "Requires Level 12", "score": .999, "x": 350, "y": 52, "right": 550, "bottom": 62},
            {"text": "Waystone (Tier 16)", "score": .999, "x": 350, "y": 70, "right": 550, "bottom": 90},
            {"text": "Revives Available: 6", "score": .999, "x": 350, "y": 110, "right": 550, "bottom": 130},
            {"text": "Can be used in a Map Device", "score": .999, "x": 300, "y": 150, "right": 600, "bottom": 170},
        ]
        self.assertIsNone(self.read(rows))
        self.assertIsNone(item_text.parse_screen_tooltip(rows))

    def test_conflicting_or_duplicate_rarity_is_not_hidden_by_a_separator(self):
        for scale in (1, 2, 4):
            for first in ("Magic", "Normal"):
                rows = normal_rows(scale)
                for row in rows[1:]:
                    row["y"] += 20 * scale
                    row["bottom"] += 20 * scale
                    row["parts"][0]["y"] += 20 * scale
                    row["parts"][0]["bottom"] += 20 * scale
                rows[0]["text"] = rows[0]["parts"][0]["text"] = "Rarity: " + first
                rows[1:1] = [
                    {"text": "Rarity: Normal", "score": .99, "x": 375 * scale,
                     "y": 45 * scale, "right": 525 * scale, "bottom": 70 * scale},
                    {"text": "--------", "score": .99, "x": 375 * scale,
                     "y": 80 * scale, "right": 525 * scale, "bottom": 82 * scale},
                ]
                with self.subTest(scale=scale, first=first):
                    self.assert_held(self.read(rows, scale))

    def test_header_distance_and_font_height_remain_bounded(self):
        scale = 2
        for header_y, height, accepted in ((25, 25, True), (24, 25, False),
                                           (50, 8, False), (20, 50, False)):
            rows = normal_rows(scale)
            for row in rows[1:]:
                row["y"] += 30 * scale
                row["bottom"] += 30 * scale
                row["parts"][0]["y"] += 30 * scale
                row["parts"][0]["bottom"] += 30 * scale
            rows[0]["y"] = header_y * scale
            rows[0]["bottom"] = (header_y + height) * scale
            rows[0]["parts"] = [{key: value for key, value in rows[0].items() if key != "parts"}]
            result = self.read(rows, scale)
            with self.subTest(header_y=header_y, height=height):
                if accepted:
                    self.assertIsNotNone(result)
                    self.assertTrue(clear_waystone_read(result))
                else:
                    self.assert_held(result)

    def test_unavailable_title_height_keeps_the_existing_minimum_margin(self):
        rows = normal_rows(2)
        del rows[1]["bottom"]
        del rows[1]["parts"][0]["bottom"]
        self.assert_held(self.read(rows, 2))
        rows = normal_rows()
        del rows[1]["bottom"]
        del rows[1]["parts"][0]["bottom"]
        self.assertTrue(clear_waystone_read(self.read(rows)))

    def test_recovered_rarity_confidence_and_strictness_still_require_review(self):
        for scale in (2, 4):
            for score, strictness in ((.5, 50), (.99, 100)):
                rows = normal_rows(scale)
                rows[0]["score"] = rows[0]["parts"][0]["score"] = score
                original = copy.deepcopy(rows)
                result = self.read(rows, scale, strictness)
                with self.subTest(scale=scale, score=score, strictness=strictness):
                    self.assertIsNotNone(result)
                    self.assertEqual(result["fields"]["map_mods"], 0)
                    self.assertFalse(clear_waystone_read(result))
                    self.assertEqual(rows, original)


class TooltipHeaderAutosaveUITests(unittest.TestCase):
    """Exercise production tooltip routing before the enabled-auto-save UI boundary."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-tooltip-header-")
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

    def test_intervening_item_cannot_auto_save_zeroes_for_either_box_layout(self):
        before = logger.get_state()
        image = Image.new("RGB", (1800, 800))
        context = logger.scan_context()
        for split in (False, True):
            manager = HotkeyManager(supported=False)
            manager._capture_lock.acquire()
            with patch.object(item_ocr, "ocr_lines", return_value=intervening_header_rows(split)), \
                    patch.object(manager, "_ocr_capture_call", side_effect=lambda revision, fn, *args, **kw: fn(*args, **kw)):
                manager._read_capture("waystone", "waystone", None, None, image,
                    capture_map=context["_capture_map_id"], generation=context["_scan_generation"],
                    expedition=context["_capture_expedition"], pending_map=context["_capture_map_pending"])
            event = manager.status()["latest"]
            self.window._deliver_hotkey_event(event, manager.image(event["id"]))
            self.app.processEvents()
            after = logger.get_state()
            with self.subTest(split=split):
                self.assertEqual(after["scan_commit_count"], before["scan_commit_count"])
                self.assertEqual(after["settings"], before["settings"])
                self.assertIsNone(event["result"])
                self.assertIn("No waystone tooltip", event["error"])


if __name__ == "__main__":
    unittest.main()
