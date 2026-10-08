"""Icon lookup checks for bundled artwork, learned canonical names and reference-cache invalidation."""

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import currency_display


class CurrencyDisplayTests(unittest.TestCase):
    def png(self, size=(96, 96), color=(120, 60, 180, 255)):
        image = Image.new("RGBA", size, color)
        ImageDraw.Draw(image).ellipse((4, 4, size[0] - 4, size[1] - 4), fill=(230, 180, 40, 255))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def image(self, raw):
        with Image.open(io.BytesIO(raw)) as source:
            self.assertEqual(source.format, "PNG")
            return source.copy()

    def test_divine_and_shared_chaos_icons_come_from_bundled_artwork(self):
        divine = currency_display.icon_png("Divine Orb")
        chaos = currency_display.icon_png("Chaos Orb")
        self.assertNotEqual(divine, chaos)
        self.assertEqual(self.image(divine).size, (40, 40))
        self.assertEqual(self.image(chaos).size, (40, 40))
        self.assertEqual(currency_display.icon_png("Perfect Chaos Orb"), chaos)
        self.assertEqual(currency_display.icon_png("  DIVINE ORB  "), divine)
        self.assertGreater(self.image(divine).getchannel("A").getextrema()[1], 0)

    def test_bundled_icons_need_no_database_ocr_engine_or_remote_fetch(self):
        currency_display._bundled_pngs.cache_clear()
        with patch("PoE2_Data_Logger.core.logger_store._connect", side_effect=AssertionError("Database was read")):
            self.assertIsNotNone(currency_display.icon_png("Divine Orb"))
        with patch.object(Path, "read_text", side_effect=AssertionError("Catalog was read repeatedly")):
            self.assertIsNotNone(currency_display.icon_png("Chaos Orb"))
        self.assertEqual(currency_display._bundled_pngs.cache_info().maxsize, 1)

    def test_custom_learned_currency_omen_and_item_labels_share_canonical_lookup(self):
        for kind in ("currency", "omen", "item"):
            with self.subTest(kind=kind):
                reference = {"name": "Custom Straße " + kind, "kind": kind, "reviewed": True,
                             "image": self.png()}
                raw = currency_display.icon_png("CUSTOM STRASSE " + kind.upper(), [reference])
                self.assertEqual(self.image(raw).size, (96, 96))

    def test_full_ritual_gear_footprint_keeps_aspect_ratio(self):
        references = [{"name": "A learned Ritual armour", "kind": "item", "reviewed": True,
                       "columns": 2, "rows": 3, "image": self.png((192, 288))}]
        image = self.image(currency_display.icon_png("A learned Ritual armour", references))
        self.assertEqual(image.size, (64, 96))

    def test_local_artwork_is_not_cached_after_reference_deletion_or_relabel(self):
        reference = {"name": "Local counter token", "reviewed": True, "image": self.png()}
        self.assertIsNotNone(currency_display.icon_png(reference["name"], [reference]))
        self.assertIsNone(currency_display.icon_png(reference["name"], []))
        reference["name"] = "Corrected counter token"
        self.assertIsNone(currency_display.icon_png("Local counter token", [reference]))
        self.assertIsNotNone(currency_display.icon_png(reference["name"], [reference]))

    def test_unknown_invalid_and_path_like_names_use_clear_missing_artwork_result(self):
        for name in ("Unknown counter token", "../../secrets.png", "https://example.com/private.png", "", None, 42):
            with self.subTest(name=name):
                self.assertIsNone(currency_display.icon_png(name))
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Reference path was opened")):
            self.assertIsNone(currency_display.icon_png("Unknown counter token", [
                {"name": "Unknown counter token", "image": "/tmp/private.png"}]))

    def test_damaged_reference_falls_back_to_valid_manual_artwork(self):
        references = [{"name": "Local counter token", "reviewed": True, "image": b"damaged PNG"},
                      {"name": "Local counter token", "kind": "currency", "image": self.png()}]
        self.assertIsNotNone(currency_display.icon_png("Local counter token", references))
        self.assertIsNone(currency_display.icon_png("Local counter token", references[:1]))

    def test_oversized_references_are_rejected_without_decoding_unbounded_pixels(self):
        for image in (b"x" * 500001, Image.new("RGB", (1025, 20)), Image.new("RGB", (1001, 1000))):
            with self.subTest(type=type(image).__name__):
                self.assertIsNone(currency_display.icon_png("Local oversized token", [
                    {"name": "Local oversized token", "image": image}]))


if __name__ == "__main__":
    unittest.main()
