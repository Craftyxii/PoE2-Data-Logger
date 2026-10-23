"""Ritual image enumeration checks keeping occupied unknown items visible with explicit uncertain fields."""

import io
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, ritual_grid


def reward_grid(footprints):
    """Construct complete Ritual grid evidence from reward slot footprints."""
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
    """Check grid reward enumeration, unresolved fields and deferred marker assignment."""
    def setUp(self):
        """Create a synthetic reward page and a controlled Omen icon reader."""
        self.page = Image.new("RGB", (480, 400), (26, 26, 40))
        self.reader = SimpleNamespace(
            icon=lambda cell, count_digits=None, **_options: {"family": "omen", "members": ["Omen of Whittling"],
                                                   "score": .99, "method": "inventory"})

    def scan(self, grid, rows=(), markers=(), labels=None, references=()):
        """Scan the fixture page with controlled grid, OCR, markers and count labels."""
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
        """Verify every occupied grid reward stays visible with uncertain tribute values."""
        grid = reward_grid([[1], [2], [13], [14]])
        result = self.scan(grid)
        self.assertEqual(result["grid_reward_count"], 4)
        self.assertEqual(result["reward_count"], 4)
        self.assertEqual([item["grid_slots"] for item in result["items"]], [[1], [2], [13], [14]])
        self.assertEqual([item["name"] for item in result["items"]], ["Omen of Whittling"] * 4)
        self.assertTrue(all(not item["needs_review"] for item in result["items"]))
        self.assertTrue(all(item["tribute"] is None for item in result["items"]))
        self.assertEqual((result["tribute_available"], result["rerolls_remaining"]), (5430, 2))

    def test_unknown_single_cell_is_visible_with_blank_name_and_uncertain_count(self):
        """Verify an unknown occupied cell retains bounds and explicit name/count uncertainty."""
        self.reader.icon = lambda cell, count_digits=None, **_options: {"family": None, "score": .2, "all": []}
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
        """Verify equipment footprints form single rewards and retain category when deferred."""
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
        """Verify a border marker belongs to exactly one adjacent reward at each scale."""
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
        """Verify unrelated OCR outside reward bounds cannot invent reward rows or prices."""
        rows = [{"text": "Omen of Resurgence 4,000", "score": .999,
                 "x": 200, "right": 470, "y": 250, "bottom": 270}]
        result = self.scan(reward_grid([[1]]), rows=rows)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["name"], "Omen of Whittling")
        self.assertIsNone(result["items"][0]["tribute"])

    def test_complete_grid_enumerates_all_120_occupied_rewards(self):
        """Verify a complete Ritual grid supports all 120 occupied rewards."""
        result = self.scan(reward_grid([[slot] for slot in range(1, 121)]))
        self.assertEqual(result["grid_reward_count"], 120)
        self.assertEqual(result["reward_count"], 120)
        self.assertEqual(result["items"][-1]["grid_slots"], [120])

    def test_tablets_excluded_from_currency_inventory_are_valid_ritual_rewards(self):
        """Verify tablets ignored by inventory recognition remain valid Ritual items."""
        self.reader.icon = lambda cell, count_digits=None, **_options: {"ignored": True, "score": 0}
        self.reader.inventory_ranked = lambda cell, **kwargs: [
            {"name": "tablet", "score": -2100}, {"name": "other", "score": -3000}]
        self.reader.inventory_members = {"tablet": ["Irradiated Tablet"], "other": ["Other"]}
        result = self.scan(reward_grid([[1]]))
        self.assertEqual(result["items"][0]["name"], "Irradiated Tablet")
        self.assertEqual(result["items"][0]["category"], "Item")
        self.assertFalse(result["items"][0]["name_needs_review"])

    def test_ambiguous_local_icon_references_remain_unnamed(self):
        """Verify tied local references leave the reward unnamed with candidate labels."""
        self.reader.icon = lambda cell, count_digits=None, **_options: {"family": None, "score": .1, "all": []}
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
        """Verify real local matching resolves unique artwork and holds shared labels for review."""
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
        """Verify unsupported grids preserve deferred unknown rewards and flag incomplete coverage."""
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
        """Verify a partial grid without a heading retains coverage uncertainty despite parsed text."""
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
        """Verify independent count crops must agree before quantity is marked verified."""
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
        """Verify text parsing accepts 120 rewards and reports overflow as unmatched text."""
        lines = [{"text": "Omen of Whittling 4,000", "score": .999} for _ in range(120)]
        result = item_ocr.parse_ritual(lines, OMEN_NAMES)
        self.assertEqual(len(result["items"]), 120)
        self.assertEqual(result["unmatched"], [])
        lines.append({"text": "Omen of Whittling 4,000", "score": .999})
        result = item_ocr.parse_ritual(lines, OMEN_NAMES)
        self.assertEqual(len(result["items"]), 120)
        self.assertEqual(result["unmatched"], ["Omen of Whittling 4,000"])


    def test_accepted_weighted_identity_does_not_get_a_second_score_gate(self):
        """A calibrated family match retains its identity proof despite a different score scale."""
        self.reader.icon = lambda cell, count_digits=None, **_options: {
            "family": "omen", "members": ["Omen of Whittling"], "score": .82, "method": "inventory"}
        item = self.scan(reward_grid([[1]]))["items"][0]
        self.assertEqual(item["name"], "Omen of Whittling")
        self.assertFalse(item["needs_review"])
        self.assertTrue(item.get("identity_verified", False))
        self.assertFalse(item["count_needs_review"])

    def test_candidate_score_does_not_verify_an_unresolved_identity(self):
        """Even a high suggestion score cannot supply a name without matcher acceptance."""
        self.reader.icon = lambda cell, count_digits=None, **_options: {
            "family": None, "score": .999,
            "all": [{"name": "Omen of Whittling", "score": .999}]}
        item = self.scan(reward_grid([[1]]))["items"][0]
        self.assertEqual(item["name"], "")
        self.assertFalse(item.get("identity_verified", False))
        self.assertTrue(item["needs_review"])

    def test_real_refreshment_match_is_verified_without_a_second_raw_score_gate(self):
        """The supplied pixels establish the accepted Omen while count and identity stay separate."""
        with Image.open(Path(__file__).parent / "fixtures/ritual_rewards/02.png") as source:
            result = item_ocr.scan_ritual_page(source.convert("RGB"), OMEN_NAMES)
        item = next(item for item in result["items"] if item["name"] == "Omen of Refreshment")
        self.assertLess(item["score"], .94)
        self.assertEqual(item["quantity"], 1)
        self.assertFalse(item["count_needs_review"])
        self.assertFalse(item["needs_review"])
        self.assertTrue(item.get("identity_verified", False))

    def test_competing_accepted_item_and_currency_identities_remain_unverified(self):
        """Independent accepted identities cannot override each other through score scales."""
        from PoE2_Data_Logger.ocr import unique_items
        self.reader.icon = lambda cell, count_digits=None, **_options: {
            "family": "infuser", "members": ["Vaal Armourer's Infuser"],
            "score": .99, "method": "inventory"}
        match = {"name": "Idol of Uldurn", "score": .99,
                 "candidate_names": ["Idol of Uldurn"],
                 "catalog_revision": "test-controlled-conflict", "source": "Controlled item evidence"}
        with patch.object(unique_items, "match", return_value=match):
            item = self.scan(reward_grid([[1]]))["items"][0]
        self.assertEqual(item["name"], "")
        self.assertFalse(item.get("identity_verified", False))
        self.assertFalse(item["category_verified"])
        self.assertEqual(set(item["candidate_names"]), {"Idol of Uldurn", "Vaal Armourer's Infuser"})
        self.assertTrue(item["needs_review"])

    def test_reliable_artwork_with_unreadable_count_still_needs_confirmation(self):
        """A known icon cannot bypass an actual uncertain quantity marker."""
        item = self.scan(reward_grid([[1]]), labels={
            0: {"count_present": True, "count_verified": False, "count_candidate": 11}})["items"][0]
        self.assertEqual((item["name"], item["quantity"]), ("Omen of Whittling", 11))
        self.assertTrue(item["needs_review"])
        self.assertTrue(item["count_needs_review"])

    def test_default_quantity_does_not_change_artwork_count_mask(self):
        """An implicit one must keep the original unlabelled-artwork matching mask."""
        for present, expected_hint in ((False, None), (True, 1)):
            with self.subTest(marker_present=present):
                hints = []
                def icon(cell, count_digits=None, **_options):
                    """Record the numeral mask used by direct and full-grid matching."""
                    hints.append(count_digits)
                    return {"family": "omen", "members": ["Omen of Whittling"], "score": .99}
                self.reader.icon = icon
                label = {"count": 1, "count_verified": True, "count_present": present}
                item_ocr._ritual_reward_icon(self.reader, self.page.crop((0, 0, 40, 40)), label, [])
                item = self.scan(reward_grid([[1]]), labels={0: label})["items"][0]
                self.assertEqual(hints, [expected_hint, expected_hint])
                self.assertEqual((item["name"], item["quantity"]), ("Omen of Whittling", 1))
                self.assertFalse(item["count_needs_review"])

    def test_absent_marker_means_one_but_unreadable_marker_stays_pending(self):
        """Distinguish an unmarked single item from a clipped or unreadable label."""
        from PoE2_Data_Logger.ocr import inventory_labels
        cell = Image.new("RGB", (40, 40), "navy")
        for present in (False, True):
            with self.subTest(present=present), \
                    patch.object(inventory_labels, "count_crops", return_value=([], present)), \
                    patch.object(inventory_labels, "tier_crops", return_value=([], False)), \
                    patch.object(item_ocr, "_engine") as engine:
                label = item_ocr._ritual_cell_labels({0: cell})[0]
                self.assertEqual(label["count_verified"], not present)
                if present:
                    self.assertNotIn("count", label)
                else:
                    self.assertEqual(label["count"], 1)
                engine.assert_not_called()

    def test_captured_sacred_bloom_without_marker_has_verified_quantity_one(self):
        """The supplied Sacred Bloom artwork must not ask for a nonexistent stack label."""
        path = Path(__file__).parent / "fixtures/ritual_rewards/10.png"
        with Image.open(path) as source:
            original = source.convert("RGB")
        for scale in (1, 2):
            with self.subTest(scale=scale):
                page = original.resize((original.width * scale, original.height * scale),
                                       Image.Resampling.LANCZOS)
                grid = ritual_grid.detect_reward_grid(page)
                self.assertIsNotNone(grid)
                inset = max(1, round(grid["pitch"] * .02))
                cells = {}
                for reward in grid["rewards"]:
                    if reward["slots"] in ([37], [49]):
                        left, top, right, bottom = reward["box"]
                        cells[reward["slots"][0]] = page.crop((left + inset, top + inset, right, bottom))
                self.assertEqual(set(cells), {37, 49})
                labels = item_ocr._ritual_cell_labels(cells)
                for slot in cells:
                    self.assertFalse(labels[slot]["count_present"])
                    self.assertTrue(labels[slot]["count_verified"])
                    self.assertEqual(labels[slot]["count"], 1)

if __name__ == "__main__":
    unittest.main()
