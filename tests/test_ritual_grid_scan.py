"""Ritual image enumeration checks keeping occupied unknown items visible with explicit uncertain fields."""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid


def reward_grid(footprints):
    rewards = []
    for slots in footprints:
        columns = [(slot - 1) % 12 for slot in slots]
        rows = [(slot - 1) // 12 for slot in slots]
        rewards.append({"slots": slots,
                        "box": (min(columns) * 40, min(rows) * 40,
                                (max(columns) + 1) * 40, (max(rows) + 1) * 40)})
    return {"bounds": (0, 0, 480, 400), "columns": 12, "rows": 10, "pitch": 40,
            "confidence": 1, "rewards": rewards,
            "evidence": {"complete": True, "horizontal_hits": 11, "vertical_hits": 13}}


class RitualGridScanTests(unittest.TestCase):
    def setUp(self):
        self.page = Image.new("RGB", (480, 400), (26, 26, 40))
        self.reader = SimpleNamespace(
            icon=lambda cell, count_digits=None: {"family": "omen", "members": ["Omen of Whittling"],
                                                   "score": .99, "method": "inventory"})

    def scan(self, grid, rows=(), markers=(), labels=None, references=()):
        if labels is None:
            labels = {index: {"count": 1, "count_verified": True}
                      for index, reward in enumerate(grid["rewards"] if grid else []) if len(reward["slots"]) == 1}
        with patch.object(item_ocr, "ocr_lines", return_value=list(rows)), \
                patch.object(item_ocr, "ritual_totals", return_value={"tribute_available": 5430, "rerolls_remaining": 2}), \
                patch.object(item_ocr, "deferred_markers", return_value=list(markers)), \
                patch.object(ritual_grid, "detect_reward_grid", return_value=grid), \
                patch.object(item_ocr, "_ritual_cell_labels", return_value=labels), \
                patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(currency_ocr, "omen_references", return_value=()):
            return item_ocr.scan_ritual_page(self.page, OMEN_NAMES, references)

    def test_ordinary_rewards_are_enumerated_without_names_or_deferred_markers(self):
        grid = reward_grid([[1], [2], [13], [14]])
        result = self.scan(grid)
        self.assertEqual(result["grid_reward_count"], 4)
        self.assertEqual(result["reward_count"], 4)
        self.assertEqual([item["grid_slots"] for item in result["items"]], [[1], [2], [13], [14]])
        self.assertEqual([item["name"] for item in result["items"]], ["Omen of Whittling"] * 4)
        self.assertTrue(all(item["needs_review"] for item in result["items"]))
        self.assertTrue(all(item["tribute"] is None for item in result["items"]))
        self.assertEqual((result["tribute_available"], result["rerolls_remaining"]), (5430, 2))

    def test_unknown_single_cell_is_visible_with_blank_name_and_uncertain_count(self):
        self.reader.icon = lambda cell, count_digits=None: {"family": None, "score": .2, "all": []}
        result = self.scan(reward_grid([[1]]), labels={0: {"count_present": True, "count_verified": False}})
        self.assertEqual(result["reward_count"], 1)
        self.assertEqual(result["unresolved_count"], 1)
        item = result["items"][0]
        self.assertEqual((item["name"], item["category"]), ("", "Item"))
        self.assertTrue(item["name_needs_review"])
        self.assertTrue(item["count_needs_review"])
        self.assertFalse(item["category_verified"])
        self.assertTrue(item["unresolved"])
        self.assertEqual(item["box"], (0, 0, 40, 40))

    def test_multi_cell_equipment_is_one_item_and_deferred_does_not_make_it_omen(self):
        result = self.scan(reward_grid([[1, 2, 13, 14], [25, 37, 49]]),
                           markers=[{"x": 10, "y": 10, "score": .99}])
        self.assertEqual(result["grid_reward_count"], 2)
        self.assertEqual(len(result["items"]), 2)
        first = result["items"][0]
        self.assertEqual((first["category"], first["name"], first["quantity"]), ("Item", "", 1))
        self.assertTrue(first["deferred"])
        self.assertTrue(first["category_verified"])
        self.assertFalse(first["count_needs_review"])
        self.assertFalse(result["items"][1]["deferred"])

    def test_marker_near_shared_border_is_assigned_to_only_one_adjacent_reward(self):
        for scale in (1, .5):
            grid = reward_grid([[1], [2]])
            for reward in grid["rewards"]:
                reward["box"] = tuple(value * scale for value in reward["box"])
            pitch = 40 * scale
            for x in (pitch - 1, pitch):
                with self.subTest(scale=scale, x=x):
                    result = self.scan(grid, markers=[{"x": x, "y": pitch / 2, "score": .99}])
                    self.assertEqual([item["deferred"] for item in result["items"]], [True, False])
                    self.assertEqual(result["deferred_count"], 1)

    def test_outside_grid_ocr_prose_cannot_add_or_duplicate_reward_rows(self):
        rows = [{"text": "Omen of Resurgence 4,000", "score": .999,
                 "x": 200, "right": 470, "y": 250, "bottom": 270}]
        result = self.scan(reward_grid([[1]]), rows=rows)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "Omen of Whittling")
        self.assertIsNone(result["items"][0]["tribute"])

    def test_complete_grid_enumerates_all_120_occupied_rewards(self):
        result = self.scan(reward_grid([[slot] for slot in range(1, 121)]))
        self.assertEqual(result["grid_reward_count"], 120)
        self.assertEqual(result["reward_count"], 120)
        self.assertEqual(result["items"][-1]["grid_slots"], [120])

    def test_tablets_excluded_from_currency_inventory_are_valid_ritual_rewards(self):
        self.reader.icon = lambda cell, count_digits=None: {"ignored": True, "score": 0}
        self.reader.inventory_ranked = lambda cell, **kwargs: [
            {"name": "tablet", "score": -2100}, {"name": "other", "score": -3000}]
        self.reader.inventory_members = {"tablet": ["Irradiated Tablet"], "other": ["Other"]}
        result = self.scan(reward_grid([[1]]))
        self.assertEqual(result["items"][0]["name"], "Irradiated Tablet")
        self.assertEqual(result["items"][0]["category"], "Item")
        self.assertFalse(result["items"][0]["name_needs_review"])

    def test_ambiguous_local_icon_references_remain_unnamed(self):
        self.reader.icon = lambda cell, count_digits=None: {"family": None, "score": .1, "all": []}
        self.reader.examples = lambda cell, examples: [
            {"name": "Omen of Bartering", "score": -100}, {"name": "Omen of Recombination", "score": -100}]
        image = Image.new("RGB", (40, 40), (70, 80, 160))
        raw = io.BytesIO(); image.save(raw, format="PNG")
        references = [{"name": name, "image": raw.getvalue()} for name in ("Omen of Bartering", "Omen of Recombination")]
        result = self.scan(reward_grid([[1]]), references=references)
        item = result["items"][0]
        self.assertEqual(item["name"], "")
        self.assertTrue(item["unresolved"])
        self.assertEqual(item["candidate_names"], ["Omen of Bartering", "Omen of Recombination"])

    def test_real_local_reference_reader_resolves_unique_art_but_holds_shared_art(self):
        pixels = np.random.default_rng(16).integers(45, 220, (39, 39, 3), dtype=np.uint8)
        art = Image.fromarray(pixels)
        self.page.paste(art, (1, 1))
        raw = io.BytesIO(); art.save(raw, format="PNG")
        self.reader = currency_ocr.get_reader()
        unique = [{"name": "Omen of Bartering", "image": raw.getvalue()}]
        result = self.scan(reward_grid([[1]]), references=unique)
        self.assertEqual(result["items"][0]["name"], "Omen of Bartering")
        self.assertFalse(result["items"][0]["unresolved"])
        shared = unique + [{"name": "Omen of Recombination", "image": raw.getvalue()}]
        result = self.scan(reward_grid([[1]]), references=shared)
        self.assertEqual(result["items"][0]["name"], "")
        self.assertTrue(result["items"][0]["unresolved"])
        self.assertEqual(result["items"][0]["candidate_names"], ["Omen of Bartering", "Omen of Recombination"])

    def test_clipped_or_unsupported_ritual_grid_holds_page_and_keeps_deferred_unknown(self):
        rows = [{"text": "Favours", "score": .999, "x": 10, "right": 80, "y": 10, "bottom": 30}]
        result = self.scan(None, rows=rows, markers=[{"x": 70, "y": 80, "score": .99, "box": (40, 40, 100, 100)}])
        self.assertFalse(result["grid_detected"])
        self.assertTrue(result["coverage_uncertain"])
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual((item["category"], item["name"]), ("Item", ""))
        self.assertTrue(item["unresolved"])
        self.assertTrue(item["count_needs_review"])

    def test_headerless_partial_grid_cannot_be_treated_as_complete_text_only_page(self):
        self.page = Image.new("RGB", (300, 260), (4, 4, 4))
        draw = ImageDraw.Draw(self.page)
        for index in range(6):
            draw.line((20 + index * 40, 20, 20 + index * 40, 220), fill=(70, 56, 30))
            draw.line((20, 20 + index * 40, 220, 20 + index * 40), fill=(70, 56, 30))
        result = self.scan(None, rows=[{"text": "Omen of Whittling 4,000", "score": .999}])
        self.assertTrue(result["coverage_uncertain"])
        self.assertFalse(result["grid_detected"])
        self.assertEqual(result["items"][0]["name"], "Omen of Whittling")

    def test_count_crops_must_agree_before_stack_quantity_is_verified(self):
        crop = Image.new("RGB", (10, 10), "white")
        cell = Image.new("RGB", (40, 40), "navy")
        from PoE2_Data_Logger.ocr import inventory_labels
        for texts, verified in ((["11", "11"], True), (["1", "11"], False)):
            with self.subTest(texts=texts), patch.object(inventory_labels, "count_crops", return_value=([crop, crop], True)), \
                    patch.object(inventory_labels, "tier_crops", return_value=([], False)), \
                    patch.object(item_ocr, "_engine", return_value=SimpleNamespace(text_rec=lambda value: SimpleNamespace(txts=texts, scores=[.999, .999]))):
                labels = item_ocr._ritual_cell_labels({0: cell})
                self.assertEqual(labels[0]["count_verified"], verified)
                if verified:
                    self.assertEqual(labels[0]["count"], 11)
                else:
                    self.assertNotIn("count", labels[0])

    def test_text_parser_capacity_matches_full_grid_without_metadata_overflow(self):
        lines = [{"text": "Omen of Whittling 4,000", "score": .999} for _ in range(120)]
        result = item_ocr.parse_ritual(lines, OMEN_NAMES)
        self.assertEqual(len(result["items"]), 120)
        self.assertEqual(result["unmatched"], [])
        lines.append({"text": "Omen of Whittling 4,000", "score": .999})
        result = item_ocr.parse_ritual(lines, OMEN_NAMES)
        self.assertEqual(len(result["items"]), 120)
        self.assertEqual(result["unmatched"], ["Omen of Whittling 4,000"])


if __name__ == "__main__":
    unittest.main()
