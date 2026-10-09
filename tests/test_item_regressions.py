"""Item text/OCR regressions for currency quantities, waystone/tablet modifiers and Ritual reading fields."""

import io
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, item_text
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog, looks_like_modifier
from PoE2_Data_Logger.ui.native_desktop import clear_currency_read, clear_ritual_read


class ItemRegressionTests(unittest.TestCase):
    """Check modifier parsing, repeated Ritual icons and custom inventory confidence."""
    def setUp(self):
        """Load affix names and the tablet flag modifiers used by parsing fixtures."""
        self.affixes = [entry["name"] for entry in affix_catalog()]
        self.flags = [entry["name"] for entry in affix_catalog()
                      if entry["unit"] == "flag" and "tablet" in entry["kinds"]]

    def row(self, text, x=30, y=40, right=None):
        """Construct a high-confidence OCR row with configurable text bounds."""
        return {"text": text, "score": .99, "x": x, "y": y,
                "right": x + 220 if right is None else right, "bottom": y + 20}

    def test_every_tablet_flag_survives_copy_and_screen_parsing(self):
        """Verify all tablet flags parse identically from copied text and screen OCR."""
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
        """Verify unknown modifier prose needs review while tablet headers are ignored."""
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
        """Scan a synthetic Ritual page with controlled icons, OCR and deferred markers."""
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
        """Verify repeated unnamed Omen icons retain separate quantities and deferred states."""
        result = self.omen_page([(40, 80), (160, 80)],
                                markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([entry["name"] for entry in result["items"]], ["Omen of Whittling"] * 2)
        self.assertEqual([entry["quantity"] for entry in result["items"]], [1, 1])
        self.assertEqual([entry["deferred"] for entry in result["items"]], [False, True])
        self.assertTrue(all(entry["needs_review"] for entry in result["items"]))

    def test_named_omen_is_not_duplicated_when_second_icon_is_visible(self):
        """Verify one named Omen and another visible icon produce exactly two occurrences."""
        result = self.omen_page([(40, 80), (160, 80)], rows=[
            self.row("Omen of Whittling 4,000", x=30, y=125, right=100)],
            markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([entry["deferred"] for entry in result["items"]], [False, True])
        self.assertEqual(result["items"][0]["tribute"], 4000)
        self.assertNotIn("needs_review", result["items"][0])
        self.assertTrue(result["items"][1]["needs_review"])

    def test_two_named_omens_keep_their_prices_and_can_save_automatically(self):
        """Verify separately named Omen occurrences keep prices and qualify for automatic save."""
        result = self.omen_page([(40, 80), (160, 80)], rows=[
            self.row("Omen of Whittling 4,000", x=30, y=125, right=100),
            self.row("Omen of Whittling 2,000", x=150, y=125, right=220)],
            markers=[{"x": 190, "y": 110, "score": .99}])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual([(entry["tribute"], entry["deferred"]) for entry in result["items"]],
                         [(4000, False), (2000, True)])
        self.assertTrue(clear_ritual_read(result, ["Omen of Whittling"]))

    def test_multiple_reference_scales_do_not_duplicate_a_single_icon(self):
        """Verify multiscale reference matching reports one occurrence per icon."""
        result = self.omen_page([(40, 80)])
        self.assertEqual(len(result["items"]), 1)

    def test_smaller_and_larger_icons_still_preserve_repeated_occurrences(self):
        """Verify repeated Omen icons remain distinct at different rendered sizes."""
        for size in (32, 40, 50):
            with self.subTest(size=size):
                result = self.omen_page([(40, 80), (160, 80)], icon_size=size)
                self.assertEqual(len(result["items"]), 2)

    def test_neighboring_icons_do_not_share_one_deferred_marker(self):
        """Verify a deferred marker attaches only to its nearest neighboring icon."""
        result = self.omen_page([(40, 80), (80, 80)],
                                markers=[{"x": 70, "y": 110, "score": .99}])
        self.assertEqual([entry["deferred"] for entry in result["items"]], [True, False])

    def test_icon_match_keeps_an_already_identified_deferred_omen(self):
        """Verify icon matching preserves deferred state already established by parsed text."""
        parsed = {"items": [{"category": "Omen", "name": "Omen of Whittling", "quantity": 1,
                             "tribute": 4000, "source": "Omen of Whittling 4,000",
                             "score": .99, "name_match": 1, "deferred": True}],
                  "unmatched": [], "raw_text": "Omen of Whittling 4,000", "status": "review"}
        with patch.object(item_ocr, "parse_ritual", return_value=parsed):
            result = self.omen_page([(40, 80)])
        self.assertTrue(result["items"][0]["deferred"])
        self.assertTrue(clear_ritual_read(result, ["Omen of Whittling"]))

    def test_repeated_page_reuses_icon_matches_and_reads_new_ocr_values(self):
        """Verify cached icon positions still merge freshly read tribute values."""
        item_ocr._RITUAL_MATCH_CACHE.clear()
        first = self.omen_page([(40, 80)], rows=[self.row("Omen of Whittling 4,000")])
        with patch.object(item_ocr.cv2, "matchTemplate", side_effect=AssertionError("matching repeated")):
            repeated = self.omen_page([(40, 80)], rows=[self.row("Omen of Whittling 2,000")])
        self.assertEqual(first["items"][0]["tribute"], 4000)
        self.assertEqual(repeated["items"][0]["tribute"], 2000)
        self.assertTrue(clear_ritual_read(repeated, ["Omen of Whittling"]))

    def test_page_and_reference_changes_do_not_reuse_old_icon_matches(self):
        """Verify changed page pixels or references invalidate cached Omen matches."""
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
        """Verify invalid reference image bytes are ignored during Ritual scanning."""
        result = self.omen_page([], references=[{"name": "Omen of Whittling", "image": b"invalid"}])
        self.assertEqual(result["items"], [])

    def test_flat_color_reference_cannot_invent_omen_occurrences(self):
        """Verify featureless references cannot create false Omen matches."""
        output = io.BytesIO()
        Image.new("RGB", (40, 40), (240, 30, 30)).save(output, format="PNG")
        result = self.omen_page([(40, 80)], references=[
            {"name": "Omen of Whittling", "image": output.getvalue()}])
        self.assertEqual(result["items"], [])

    def test_reference_dimensions_are_checked_before_decode(self):
        """Verify oversized reference dimensions are rejected before pixel decoding."""
        output = io.BytesIO()
        Image.new("RGB", (8193, 1)).save(output, format="PNG")
        with patch.object(Image.Image, "load", side_effect=AssertionError("oversized image decoded")):
            self.assertIsNone(item_ocr._reference_image(output.getvalue(), 96))

    def test_old_large_references_are_bounded_without_changing_original(self):
        """Verify reference resizing respects limits without mutating the source image."""
        source = Image.new("RGB", (600, 300), (40, 80, 120))
        inventory = item_ocr._reference_image(source, 96)
        omen = item_ocr._reference_image(source, 512)
        self.assertEqual(source.size, (600, 300))
        self.assertEqual(inventory.size, (96, 48))
        self.assertEqual(omen.size, (512, 256))

    def inventory_custom_read(self, names, include_builtin=False):
        """Scan synthetic inventory icons with configurable competing custom labels."""
        icon = Image.fromarray(np.random.default_rng(51).integers(40, 230, (40, 40, 3), dtype=np.uint8))
        grid = Image.new("RGB", (480, 200), (26, 26, 40))
        grid.paste(icon, (0, 0))
        references = [{"name": name, "image": icon} for name in names]
        reader = currency_ocr.CurrencyReader()
        labels = {slot: {"count_present": False, "count": 1, "tier_present": False}
                  for slot in range(1, 61)}
        readings = [{"family": None, "all": [], "score": 0}]
        if include_builtin:
            grid.paste(icon, (40, 0))
            readings.append({"family": "ExaltedOrb", "members": ["Exalted Orb"], "score": .99})
        with patch.object(item_ocr, "_inventory_labels", return_value=labels), patch.object(
                item_ocr, "inventory_grid", side_effect=lambda image: image), patch.object(
                currency_ocr, "get_reader", return_value=reader), patch.object(
                reader, "icon", side_effect=readings):
            return item_ocr.scan_inventory_grid(grid, references)

    def test_ambiguous_custom_slot_remains_reviewable_beside_a_clear_builtin_item(self):
        """Verify an ambiguous custom slot remains unknown beside a recognized builtin item."""
        result = self.inventory_custom_read(["Chaos Orb"] * 3 + ["Exalted Orb"], include_builtin=True)
        self.assertEqual([(entry["slot"], entry["name"]) for entry in result["items"]], [(2, "Exalted Orb")])
        self.assertEqual(len(result["unknown"]), 1)
        self.assertEqual(result["unknown"][0]["slot"], 1)
        self.assertEqual(set(result["unknown"][0]["candidate"].split(" / ")), {"Chaos Orb", "Exalted Orb"})
        self.assertFalse(clear_currency_read(result))

    def test_unique_custom_item_still_allows_automatic_approval(self):
        """Verify duplicate references for one label still yield an automatically clear read."""
        result = self.inventory_custom_read(["Chaos Orb"] * 3)
        self.assertEqual([(entry["slot"], entry["name"]) for entry in result["items"]], [(1, "Chaos Orb")])
        self.assertEqual(result["unknown"], [])
        self.assertTrue(clear_currency_read(result))


if __name__ == "__main__":
    unittest.main()
