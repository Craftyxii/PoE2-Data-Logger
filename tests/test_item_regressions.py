import io
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, looks_like_modifier
from PoE2_Data_Logger.ui.native_desktop import clear_ritual_read


class ItemRegressionTests(unittest.TestCase):
    def setUp(self):
        self.affixes = [entry["name"] for entry in affix_catalog()]
        self.flags = [entry["name"] for entry in affix_catalog()
                      if entry["unit"] == "flag" and "tablet" in entry["kinds"]]

    def row(self, text, x=30, y=40, right=None):
        return {"text": text, "score": .99, "x": x, "y": y,
                "right": x + 220 if right is None else right, "bottom": y + 20}

    def test_every_tablet_flag_survives_copy_and_screen_parsing(self):
        self.assertEqual(len(self.flags), 4)
        for flag in self.flags:
            with self.subTest(flag=flag):
                copied = item_text.parse_item_text("\n".join([
                    "Item Class: Tablets", "Rarity: Magic", "Precursor Tablet", "--------",
                    "Item Level: 82", "--------", flag, "10% increased Pack Size in Map",
                    "--------", "Can be used at a completed Tower"]), self.affixes)
                screen = item_text.parse_screen_tooltip([
                    self.row("Precursor Tablet"), self.row("Uses Remaining: 10", y=65),
                    self.row(flag, y=90), self.row("10% increased Pack Size in Map", y=115),
                    self.row("Can be used at a completed Tower", y=140)], self.affixes)
                for result in (copied, screen):
                    self.assertIn(flag, result["mods"])
                    self.assertEqual([(entry["affix"], entry["value"], entry["unit"])
                                      for entry in result["matches"]],
                                     [(flag, 1, "flag"), ("increased Pack Size in Map", 10, "%")])
                    self.assertEqual(result["uncertain"], [])
                self.assertEqual(screen["status"], "ready")

    def test_unresolved_modifier_prose_requires_review_and_headers_do_not(self):
        prose = "Expeditions contain mysterious Relics in Map"
        result = item_ocr.parse_tablet([
            self.row("10% increased Pack Size in Map"), self.row(prose),
            self.row("Expedition Precursor Tablet"), self.row("Uses Remaining: 10"),
            self.row("Right click to use")], self.affixes)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["uncertain"], [prose])
        copied = item_text.parse_item_text("\n".join([
            "Item Class: Tablets", "Rarity: Magic", "Expedition Precursor Tablet",
            "Item Level: 82", prose, "10% increased Pack Size in Map"]), self.affixes)
        self.assertEqual(copied["uncertain"], [prose])
        self.assertFalse(looks_like_modifier("Expedition Precursor Tablet"))

    def omen_page(self, positions, rows=(), markers=(), references=None, icon_size=40):
        name = "Omen of Whittling"
        rng = np.random.default_rng(7)
        icon = Image.fromarray(rng.integers(40, 230, (40, 40, 3), dtype=np.uint8))
        output = io.BytesIO()
        icon.save(output, format="PNG")
        if icon_size != 40:
            icon = Image.fromarray(item_ocr.cv2.resize(np.asarray(icon), (icon_size, icon_size),
                                                      interpolation=item_ocr.cv2.INTER_AREA))
        page = Image.new("RGB", (300, 220), (26, 26, 40))
        for position in positions:
            page.paste(icon, position)
        references = references if references is not None else [{"name": name, "image": output.getvalue()}]
        with patch.object(item_ocr, "ocr_lines", return_value=list(rows)), patch.object(
                item_ocr, "ritual_totals", return_value={}), patch.object(
                item_ocr, "deferred_markers", return_value=list(markers)), patch.object(
                currency_ocr, "omen_references", return_value=()):
            return item_ocr.scan_ritual_page(page, [name], references)

    def test_repeated_omen_icons_preserve_each_occurrence_and_deferred_state(self):
        result = self.omen_page([(40, 80), (160, 80)],
                                markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([entry["name"] for entry in result["items"]], ["Omen of Whittling"] * 2)
        self.assertEqual([entry["quantity"] for entry in result["items"]], [1, 1])
        self.assertEqual([entry["deferred"] for entry in result["items"]], [False, True])
        self.assertTrue(all(entry["needs_review"] for entry in result["items"]))

    def test_named_omen_is_not_duplicated_when_second_icon_is_visible(self):
        result = self.omen_page([(40, 80), (160, 80)], rows=[
            self.row("Omen of Whittling 4,000", x=30, y=125, right=100)],
            markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([entry["deferred"] for entry in result["items"]], [False, True])
        self.assertEqual(result["items"][0]["tribute"], 4000)
        self.assertNotIn("needs_review", result["items"][0])
        self.assertTrue(result["items"][1]["needs_review"])

    def test_two_named_omens_keep_their_prices_and_can_save_automatically(self):
        result = self.omen_page([(40, 80), (160, 80)], rows=[
            self.row("Omen of Whittling 4,000", x=30, y=125, right=100),
            self.row("Omen of Whittling 2,000", x=150, y=125, right=220)],
            markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([(entry["tribute"], entry["deferred"]) for entry in result["items"]],
                         [(4000, False), (2000, True)])
        self.assertTrue(clear_ritual_read(result, ["Omen of Whittling"]))

    def test_multiple_reference_scales_do_not_duplicate_a_single_icon(self):
        result = self.omen_page([(40, 80)])
        self.assertEqual(len(result["items"]), 1)

    def test_smaller_and_larger_icons_still_preserve_repeated_occurrences(self):
        for size in (32, 40, 50):
            with self.subTest(size=size):
                result = self.omen_page([(40, 80), (160, 80)], icon_size=size)
                self.assertEqual(len(result["items"]), 2)

    def test_neighboring_icons_do_not_share_one_deferred_marker(self):
        result = self.omen_page([(40, 80), (80, 80)],
                                markers=[{"x": 70, "y": 110, "score": .99}])
        self.assertEqual([entry["deferred"] for entry in result["items"]], [True, False])

    def test_icon_match_keeps_an_already_identified_deferred_omen(self):
        parsed = {"items": [{"category": "Omen", "name": "Omen of Whittling", "quantity": 1,
                             "tribute": 4000, "source": "Omen of Whittling 4,000",
                             "score": .99, "name_match": 1, "deferred": True}],
                  "unmatched": [], "raw_text": "Omen of Whittling 4,000", "status": "review"}
        with patch.object(item_ocr, "parse_ritual", return_value=parsed):
            result = self.omen_page([(40, 80)])
        self.assertTrue(result["items"][0]["deferred"])
        self.assertTrue(clear_ritual_read(result, ["Omen of Whittling"]))

    def test_repeated_page_reuses_icon_matches_and_reads_new_ocr_values(self):
        item_ocr._RITUAL_MATCH_CACHE.clear()
        first = self.omen_page([(40, 80)], rows=[self.row("Omen of Whittling 4,000")])
        with patch.object(item_ocr.cv2, "matchTemplate", side_effect=AssertionError("matching repeated")):
            repeated = self.omen_page([(40, 80)], rows=[self.row("Omen of Whittling 2,000")])
        self.assertEqual(first["items"][0]["tribute"], 4000)
        self.assertEqual(repeated["items"][0]["tribute"], 2000)
        self.assertTrue(clear_ritual_read(repeated, ["Omen of Whittling"]))

    def test_page_and_reference_changes_do_not_reuse_old_icon_matches(self):
        item_ocr._RITUAL_MATCH_CACHE.clear()
        self.omen_page([(40, 80)])
        changed_page = self.omen_page([(40, 80), (160, 80)])
        self.assertEqual(len(changed_page["items"]), 2)
        icon = Image.fromarray(np.random.default_rng(8).integers(40, 230, (40, 40, 3), dtype=np.uint8))
        output = io.BytesIO()
        icon.save(output, format="PNG")
        changed_reference = self.omen_page([(40, 80)], references=[
            {"name": "Omen of Whittling", "image": output.getvalue()}])
        self.assertEqual(changed_reference["items"], [])

    def test_bad_omen_references_are_skipped(self):
        result = self.omen_page([], references=[{"name": "Omen of Whittling", "image": b"invalid"}])
        self.assertEqual(result["items"], [])

    def test_flat_color_reference_cannot_invent_omen_occurrences(self):
        output = io.BytesIO()
        Image.new("RGB", (40, 40), (240, 30, 30)).save(output, format="PNG")
        result = self.omen_page([(40, 80)], references=[
            {"name": "Omen of Whittling", "image": output.getvalue()}])
        self.assertEqual(result["items"], [])

    def test_reference_dimensions_are_checked_before_decode(self):
        output = io.BytesIO()
        Image.new("RGB", (8193, 1)).save(output, format="PNG")
        with patch.object(Image.Image, "load", side_effect=AssertionError("oversized image decoded")):
            self.assertIsNone(item_ocr._reference_image(output.getvalue(), 96))

    def test_old_large_references_are_bounded_without_changing_original(self):
        source = Image.new("RGB", (600, 300), (40, 80, 120))
        inventory = item_ocr._reference_image(source, 96)
        omen = item_ocr._reference_image(source, 512)
        self.assertEqual(source.size, (600, 300))
        self.assertEqual(inventory.size, (96, 48))
        self.assertEqual(omen.size, (512, 256))


if __name__ == "__main__":
    unittest.main()
