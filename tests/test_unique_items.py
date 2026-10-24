"""Exercise sourced artwork controls and conservative catalog-only matching.

Rendered source controls prove matching/invariance, not accuracy on game captures.
Synthetic artwork below tests ambiguity/cache mechanics only.
"""

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from PoE2_Data_Logger.ocr import unique_items as unique


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "PoE2_Data_Logger/unique_item_assets"


def render(artwork, shape, scale=54):
    """Composite art at its own aspect onto a complete inventory footprint."""
    size = (shape[0] * scale, shape[1] * scale)
    fitted = ImageOps.contain(artwork.convert("RGBA"), size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (15, 14, 32, 255))
    canvas.alpha_composite(fitted, ((size[0] - fitted.width) // 2,
                                   (size[1] - fitted.height) // 2))
    return canvas.convert("RGB")


def texture(channel=0, size=(80, 80)):
    """Synthetic equal-luminance color siblings for mechanics, not real accuracy."""
    yy, xx = np.indices((size[1], size[0]))
    values = (70 + (xx * 7 + yy * 11) % 180).astype(np.uint8)
    rgba = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    rgba[:, :, 3] = ((xx > 8) & (xx < size[0] - 9) &
                        (yy > 8) & (yy < size[1] - 9)).astype(np.uint8) * 255
    rgba[:, :, channel] = (values if channel == 0 else
                           np.round(values.astype(float) * .299 / .587).astype(np.uint8))
    return Image.fromarray(rgba, "RGBA")


@contextmanager
def temporary_catalog(entries):
    """Explicitly pin each test-only catalog; production always uses release pins."""
    with tempfile.TemporaryDirectory(prefix="poe2-unique-matcher-") as directory:
        root = Path(directory)
        (root / "icons").mkdir()
        rows = []
        for index, (name, artwork, shape, shared) in enumerate(entries):
            output = io.BytesIO()
            artwork.save(output, format="PNG")
            raw = output.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            icon = "icons/" + digest + ".png"
            (root / icon).write_bytes(raw)
            rows.append({"id": str(index), "name": name, "columns": shape[0],
                         "rows": shape[1], "icon": icon, "image_sha256": digest,
                         "shared_names": shared})
        manifest = {"schema_version": 1, "source": {"revision": "1" * 40}, "items": rows}
        raw = json.dumps(manifest).encode("utf-8")
        (root / "manifest.json").write_bytes(raw)
        with mock.patch.object(unique, "ASSET_ROOT", root), \
                mock.patch.object(unique, "_REVISION", "1" * 40), \
                mock.patch.object(unique, "_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest()):
            unique.clear_caches()
            try:
                yield root, manifest
            finally:
                unique.clear_caches()


class SourcedUniqueArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((CATALOG_ROOT / "manifest.json").read_text(encoding="utf-8"))["items"]

    def setUp(self):
        unique.clear_caches()

    def tearDown(self):
        unique.clear_caches()

    def source_image(self, name):
        row = next(row for row in self.rows if row["name"] == name)
        with Image.open(CATALOG_ROOT / row["icon"]) as artwork:
            return row, render(artwork, (row["columns"], row["rows"]))

    def test_manifest_pin_and_all_original_png_hashes(self):
        raw = (CATALOG_ROOT / "manifest.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), unique._MANIFEST_SHA256)
        self.assertEqual(len(self.rows), 449)
        self.assertEqual(unique._catalog().revision, unique._REVISION)
        for row in self.rows:
            with self.subTest(name=row["name"], id=row["id"]):
                raw = (CATALOG_ROOT / row["icon"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row["image_sha256"])
                with Image.open(io.BytesIO(raw)) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.size, (row["image_width"], row["image_height"]))

    def test_complete_grayscale_source_controls_keep_correct_candidate_without_wrong_names(self):
        for row in self.rows:
            shape = (row["columns"], row["rows"])
            with Image.open(CATALOG_ROOT / row["icon"]) as artwork:
                image = ImageOps.grayscale(render(artwork, shape)).convert("RGB")
            with self.subTest(name=row["name"], id=row["id"]):
                result = unique.match(image, shape)
                self.assertIsNotNone(result)
                self.assertIn(row["name"], result["candidate_names"])
                self.assertIn(result["name"], ("", row["name"]))
                self.assertEqual(result["category"], "Item")
                self.assertTrue(result["catalog_unique"])
                self.assertEqual(result["catalog_revision"], unique._REVISION)
        self.assertLessEqual(unique._RETAINED_BYTES, unique._BANK_BYTES)
        self.assertLessEqual(len(unique._QUERIES), unique._QUERY_LIMIT)

    def test_colored_and_dimmed_source_controls_across_every_footprint(self):
        selected = ("Bramblejack", "Corpsewade", "Dionadair", "Astramentis", "Headhunter",
                    "Sanguine Diviner", "Olroth's Resolve", "Splinter of Lorrata")
        shapes = set()
        for name in selected:
            row, image = self.source_image(name)
            shape = (row["columns"], row["rows"])
            shapes.add(shape)
            for label, query in (("color", image),
                                 ("dim", ImageEnhance.Brightness(image).enhance(.4))):
                with self.subTest(name=name, mode=label):
                    result = unique.match(query, shape)
                    self.assertIsNotNone(result)
                    self.assertIn(name, result["candidate_names"])
                    self.assertIn(result["name"], ("", name))
        self.assertEqual(len(shapes), 8)

    def test_flasks_drawn_empty_on_ritual_pages_match_their_full_source_art(self):
        """Ritual pages draw a unique flask without its liquid; the source art shows it full."""
        pages = ROOT / "tests/fixtures/ritual_pages_hover"
        for page, box, name in (("123830.png", (6, 164, 59, 270), "Blood of the Warrior"),
                                ("130520.png", (6, 6, 59, 112), "Lavianga's Spirits")):
            with self.subTest(name=name), Image.open(pages / page) as source:
                result = unique.match(source.convert("RGB").crop(box), (1, 2))
                self.assertEqual((result["name"], result["candidate_names"]), (name, [name]))
        for row in self.rows:
            if row["item_class"] == "Flask":
                with self.subTest(name=row["name"], look="full"):
                    _, image = self.source_image(row["name"])
                    self.assertEqual(unique.match(image, (1, 2))["name"], row["name"])

    def test_source_sprite_aspect_is_retained_inside_larger_footprint(self):
        row, image = self.source_image("Rise of the Phoenix")
        self.assertEqual((row["columns"], row["rows"]), (2, 4))
        self.assertEqual((row["image_width"], row["image_height"]), (212, 316))
        result = unique.match(image, (2, 4))
        self.assertIsNotNone(result)
        self.assertIn("Rise of the Phoenix", result["candidate_names"])
        self.assertEqual(result["name"], "")  # Exact art is also used by ordinary bases.
        bank = unique._bank((2, 4))
        index = next(i for i, labels in enumerate(bank.names) if "Rise of the Phoenix" in labels)
        occupied = bank.weights[index].reshape(128, 64).sum(axis=1) > 0
        self.assertGreater(np.flatnonzero(occupied)[0], 8)
        self.assertLess(np.flatnonzero(occupied)[-1], 119)

    def test_source_shared_unique_and_base_art_remain_choices(self):
        expected = {"Guiding Palm of the Heart": "Guiding Palm", "Loreweave": "Ancient Mail",
                    "Kalandra's Touch": "Ring"}
        for name, other in expected.items():
            row, image = self.source_image(name)
            for level in (0, 50, 100):
                with self.subTest(name=name, strictness=level):
                    result = unique.match(image, (row["columns"], row["rows"]), strictness=level)
                    self.assertIsNotNone(result)
                    self.assertEqual(result["name"], "")
                    self.assertIn(name, result["candidate_names"])
                    self.assertIn(other, result["candidate_names"])

    def test_all_ordinary_currency_families_are_negative_controls(self):
        rows = json.loads((ROOT / "PoE2_Data_Logger/third_party/currency_overlay/inventory-icons.json")
                          .read_text(encoding="utf-8"))["icons"]
        checked = 0
        for row in rows:
            if row.get("ignored"):
                continue
            rgba = np.asarray(row["rgba"], dtype=np.uint8).reshape(40, 40, 4)
            background = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
            background.alpha_composite(Image.fromarray(rgba, "RGBA"))
            for label, image in (("color", background.convert("RGB")),
                                 ("gray", ImageOps.grayscale(background).convert("RGB"))):
                with self.subTest(currency=row["members"], mode=label):
                    self.assertIsNone(unique.match(image, (1, 1)))
            checked += 1
        self.assertEqual(checked, 278)  # Includes the appended Sacred Bloom catalog entry.

    def test_empty_noise_partial_item_and_wrong_shape_do_not_acquire_unique_names(self):
        rng = np.random.default_rng(20261020)
        for columns in (1, 2):
            for rows in (1, 2, 3, 4):
                shape = (columns, rows)
                size = (columns * 54, rows * 54)
                for image in (Image.new("RGB", size, (15, 14, 32)),
                              Image.new("RGB", size, "white"),
                              Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3),
                                                           dtype=np.uint8), "RGB")):
                    with self.subTest(shape=shape):
                        self.assertIsNone(unique.match(image, shape))
        row, body = self.source_image("Bramblejack")
        self.assertIsNone(unique.match(body.crop((0, 0, 54, 54)), (1, 1)))
        self.assertIsNone(unique.match(body, (1, 1)))

    def test_removed_shared_art_metadata_fails_pinned_manifest_integrity(self):
        data = json.loads((CATALOG_ROOT / "manifest.json").read_bytes())
        row = next(row for row in data["items"] if row["name"] == "Rise of the Phoenix")
        row.pop("shared_names")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(unique, "ASSET_ROOT", path):
                self.assertEqual(unique.names(), ())
                self.assertIsNone(unique.match(Image.new("RGB", (108, 216)), (2, 4)))

    def test_name_catalog_is_case_insensitive_and_does_not_include_shared_bases(self):
        self.assertIn("Astramentis", unique.names())
        self.assertTrue(unique.is_unique_name("aStRaMeNtIs"))
        self.assertFalse(unique.is_unique_name("Ancient Mail"))
        self.assertFalse(unique.is_unique_name(None))


class UniqueMatcherMechanicsTests(unittest.TestCase):
    def tearDown(self):
        unique.clear_caches()

    def test_grayscale_color_siblings_are_ambiguous_and_manual_learning_has_no_input(self):
        red, green = texture(0), texture(1)
        entries = [("Red unique", red, (1, 1), []), ("Green unique", green, (1, 1), [])]
        with temporary_catalog(entries):
            for level in (0, 50):
                for name, artwork in (("Red unique", red), ("Green unique", green)):
                    image = render(artwork, (1, 1), scale=80)
                    with self.subTest(name=name, strictness=level):
                        self.assertEqual(unique.match(image, (1, 1), strictness=level)["name"], name)
                        gray = ImageOps.grayscale(image).convert("RGB")
                        first = unique.match(gray, (1, 1), strictness=level)
                        self.assertEqual(first["name"], "")
                        self.assertEqual(set(first["candidate_names"]), {"Red unique", "Green unique"})
                        # Looser acceptance cannot distinguish grayscale siblings,
                        # nor can a caller mutate cached evidence into approval.
                        first["name"] = name
                        first["candidate_names"].clear()
                        repeated = unique.match(gray, (1, 1), strictness=level)
                        self.assertEqual(repeated["name"], "")
                        self.assertEqual(set(repeated["candidate_names"]), {"Red unique", "Green unique"})

    def test_shared_png_names_and_base_names_never_become_clear(self):
        artwork = texture()
        with temporary_catalog([("First unique", artwork, (1, 1), ["Ordinary base"]),
                                ("Second unique", artwork, (1, 1), [])]):
            for level in (0, 50, 100):
                with self.subTest(strictness=level):
                    result = unique.match(render(artwork, (1, 1), scale=80), (1, 1), strictness=level)
                    self.assertEqual(result["name"], "")
                    self.assertEqual(set(result["candidate_names"]),
                                     {"First unique", "Second unique", "Ordinary base"})

    def test_multiple_art_variants_of_same_name_are_not_competing_names(self):
        artwork = texture()
        variant = ImageOps.mirror(artwork)
        with temporary_catalog([("One unique", artwork, (1, 1), []),
                                ("One unique", variant, (1, 1), [])]):
            result = unique.match(render(artwork, (1, 1), scale=80), (1, 1))
            self.assertEqual(result["name"], "One unique")
            self.assertEqual(result["candidate_names"], ["One unique"])

    def test_corrupt_or_missing_rival_fails_entire_footprint_closed(self):
        artwork = texture()
        for missing in (False, True):
            with temporary_catalog([("One", artwork, (1, 1), []),
                                    ("Rival", ImageOps.mirror(artwork), (1, 1), [])]) as (root, data):
                image = render(artwork, (1, 1), scale=80)
                self.assertEqual(unique.match(image, (1, 1))["name"], "One")
                rival = root / data["items"][1]["icon"]
                if missing:
                    rival.unlink()
                else:
                    rival.write_bytes(b"not a PNG and not the pinned bytes")
                self.assertIsNone(unique.match(image, (1, 1)))

    def test_manifest_revision_and_paths_are_validated_even_with_test_digest(self):
        for change in ("revision", "path", "duplicate-id", "wrong-shape"):
            with temporary_catalog([("One", texture(), (1, 1), [])]) as (root, data):
                if change == "revision":
                    data["source"]["revision"] = "2" * 40
                elif change == "path":
                    data["items"][0]["icon"] = "../outside.png"
                elif change == "duplicate-id":
                    data["items"].append(dict(data["items"][0]))
                else:
                    data["items"][0]["columns"] = True
                raw = json.dumps(data).encode()
                (root / "manifest.json").write_bytes(raw)
                with mock.patch.object(unique, "_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest()):
                    self.assertEqual(unique.names(), ())

    def test_exact_cache_reuses_only_evidence_and_invalidates_after_catalog_replacement(self):
        artwork = texture()
        with temporary_catalog([("One", artwork, (1, 1), [])]) as (root, data):
            image = render(artwork, (1, 1), scale=80)
            with mock.patch.object(unique, "_rank", wraps=unique._rank) as rank:
                self.assertEqual(unique.match(image, (1, 1))["name"], "One")
                result = unique.match(image.copy(), (1, 1))
                self.assertEqual(rank.call_count, 1)
                result["candidate_names"].append("caller mutation")
                self.assertNotIn("caller mutation", unique.match(image, (1, 1))["candidate_names"])
                data["items"][0]["name"] = "Replacement name"
                raw = json.dumps(data).encode()
                (root / "manifest.json").write_bytes(raw)
                with mock.patch.object(unique, "_MANIFEST_SHA256", hashlib.sha256(raw).hexdigest()):
                    self.assertEqual(unique.match(image, (1, 1))["name"], "Replacement name")
                    self.assertEqual(rank.call_count, 2)

    def test_prepared_banks_and_query_entries_are_bounded(self):
        artwork = texture()
        entries = [("Small", artwork, (1, 1), []),
                   ("Tall", artwork.resize((80, 160)), (1, 2), [])]
        with temporary_catalog(entries), mock.patch.object(unique, "_BANK_BYTES", 30_000), \
                mock.patch.object(unique, "_QUERY_LIMIT", 2):
            self.assertIsNotNone(unique.match(render(artwork, (1, 1), scale=80), (1, 1)))
            self.assertIsNotNone(unique.match(render(artwork.resize((80, 160)), (1, 2), scale=80), (1, 2)))
            self.assertEqual(len(unique._BANKS), 1)
            self.assertLessEqual(unique._RETAINED_BYTES, 30_000)
            for color in ((10, 10, 10), (20, 20, 20), (30, 30, 30)):
                unique.match(Image.new("RGB", (80, 160), color), (1, 2))
            self.assertEqual(len(unique._QUERIES), 2)

    def test_invalid_inputs_never_prepare_artwork(self):
        with mock.patch.object(unique, "_bank") as prepare:
            for image, shape in ((None, (1, 1)), (Image.new("RGB", (4, 4)), (1, 1)),
                                 (Image.new("RGB", (80, 80)), [1, 1]),
                                 (Image.new("RGB", (80, 80)), (True, 1)),
                                 (Image.new("RGB", (80, 80)), (3, 1))):
                self.assertIsNone(unique.match(image, shape))
            prepare.assert_not_called()
        with self.assertRaises(ValueError):
            unique.match(Image.new("RGB", (80, 80)), (1, 1), strictness=True)

    def test_strictness_one_hundred_never_supplies_a_clear_name(self):
        artwork = texture()
        with temporary_catalog([("One", artwork, (1, 1), [])]):
            result = unique.match(render(artwork, (1, 1), scale=80), (1, 1), strictness=100)
            # Very strict evidence may be rejected, but never silently approved.
            self.assertTrue(result is None or result["name"] == "")

    def test_optional_catalog_missing_does_not_break_existing_scanners(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(unique, "ASSET_ROOT", Path(directory)):
            self.assertIsNone(unique.match(Image.new("RGB", (80, 80)), (1, 1)))
            self.assertEqual(unique.names(), ())


if __name__ == "__main__":
    unittest.main()
