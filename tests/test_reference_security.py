"""Reference import validation checks for bounded archives, safe paths and rejected malformed image/catalog data."""

import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile
import zlib

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, service, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, scan


class ReferenceSecurityTests(unittest.TestCase):
    """Check reference-pack atomic validation, image normalization, and safe rune vectors."""
    def setUp(self):
        """Initialize temporary logger storage for reference import and image validation."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the original data directory and remove the isolated logger database."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def data(self):
        """Build an empty reference manifest containing every supported data collection."""
        return {key: [] for key in (
            "families", "recipes", "aliases", "seed_states", "affixes", "master_perks",
            "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans")}

    def pack(self, data, assets=None):
        """Write a version-2 reference ZIP containing supplied manifest data and asset bytes."""
        path = Path(self.tmp.name) / "references.zip"
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "format": reference_pack.FORMAT, "version": 2, "data": data}))
            for name, raw in (assets or {}).items():
                archive.writestr(name, raw)
        return path

    def png(self, size, color=(30, 80, 120)):
        """Encode a solid RGB image of the requested size as PNG bytes."""
        output = io.BytesIO()
        with Image.new("RGB", size, color) as image:
            image.save(output, format="PNG")
        return output.getvalue()

    def broken_png(self, size=(96, 96)):
        """Build a structurally valid PNG whose compressed pixel stream is empty."""
        def chunk(name, raw):
            """Encode one PNG chunk with its byte length and correct CRC."""
            return (struct.pack(">I", len(raw)) + name + raw +
                    struct.pack(">I", zlib.crc32(name + raw) & 0xffffffff))
        return (b"\x89PNG\r\n\x1a\n" +
                chunk(b"IHDR", struct.pack(">IIBBBBB", *size, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))

    def icon(self, name, kind, file, raw):
        """Build a named icon manifest entry with kind, asset path, and SHA-256 hash."""
        return {"name": name, "kind": kind, "file": file,
                "sha256": hashlib.sha256(raw).hexdigest()}

    def test_damaged_pixel_data_is_rejected_before_import_mutations(self):
        """Verify damaged pixel data is rejected before import mutations."""
        raw = self.broken_png()
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        data = self.data()
        data["affixes"] = [{"name": "Uncommitted reference affix"}]
        data["icons"] = [self.icon("Chaos Orb", "currency", "icons/0000.png", raw)]
        with self.assertRaisesRegex(ValueError, "damaged"):
            reference_pack.import_pack(self.pack(data, {"icons/0000.png": raw}))
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name=?",
                                         ("Uncommitted reference affix",)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM currency_icons").fetchone()[0], 0)
        self.assertFalse((store.DATA_DIR / "images").exists())

    def test_screenshot_upload_rejects_damaged_pixel_data(self):
        """Verify screenshot upload rejects damaged pixel data."""
        with self.assertRaisesRegex(ValueError, "damaged"):
            service._image(base64.b64encode(self.broken_png((200, 100))).decode())

    def test_oversized_icons_are_normalized_and_reimport_deduplicates_stored_bytes(self):
        """Verify oversized icons are normalized and reimport deduplicates stored bytes."""
        data = self.data()
        raw = self.png((4000, 3000))
        data["item_names"] = [{"name": "Example inventory item"}]
        data["icons"] = [self.icon(name, kind, f"icons/{index:04d}.png", raw)
                         for index, (name, kind) in enumerate((
                             ("Chaos Orb", "currency"), ("Example inventory item", "item"),
                             ("Omen of Whittling", "omen")))]
        assets = {entry["file"]: raw for entry in data["icons"]}
        path = self.pack(data, assets)
        self.assertLess(path.stat().st_size, 5000)
        counts = reference_pack.import_pack(path)
        self.assertEqual([counts[key] for key in ("currency_icons", "item_icons", "omen_icons")], [1, 1, 1])
        for entry in logger.currency_icons() + logger.item_icons():
            with Image.open(io.BytesIO(entry["image"])) as image:
                self.assertEqual(image.size, (96, 96))
                self.assertEqual(image.mode, "RGB")
                image.load()
        with Image.open(io.BytesIO(logger.omen_icons()[0]["image"])) as image:
            self.assertEqual(image.size, (512, 384))
            image.load()
        self.assertTrue(all(value == 0 for value in reference_pack.import_pack(path).values()))

    def test_valid_local_icons_round_trip_without_duplicate_records(self):
        """Verify valid local icons round trip without duplicate records."""
        logger.add_item_name("Example inventory item")
        with Image.new("RGB", (96, 96), (30, 80, 120)) as image:
            logger.save_currency_icon("Chaos Orb", image)
            logger.save_item_icon("Example inventory item", image)
        with Image.new("RGB", (80, 40), (60, 30, 90)) as image:
            logger.save_omen_icon("Omen of Whittling", image)
        path = Path(self.tmp.name) / "local.zip"
        path.write_bytes(reference_pack.export_pack())
        self.assertTrue(all(value == 0 for value in reference_pack.import_pack(path).values()))

    def test_valid_icon_encoding_is_preserved_during_round_trip(self):
        """Verify valid icon encoding is preserved during round trip."""
        output = io.BytesIO()
        with Image.new("RGB", (96, 96), (30, 80, 120)) as image:
            image.save(output, format="PNG", compress_level=0)
        raw = output.getvalue()
        with logger._connect() as db:
            db.execute("INSERT INTO currency_icons(name,image_png,recorded_at) VALUES(?,?,?)",
                       ("Chaos Orb", raw, logger._now()))
        path = Path(self.tmp.name) / "legacy-icons.zip"
        path.write_bytes(reference_pack.export_pack())
        self.assertEqual(reference_pack.import_pack(path)["currency_icons"], 0)
        self.assertEqual(logger.currency_icons()[0]["image"], raw)

    def test_duplicate_icon_path_cannot_reuse_omen_size_for_currency(self):
        """Verify duplicate icon path cannot reuse omen size for currency."""
        raw = self.png((512, 512))
        data = self.data()
        data["icons"] = [self.icon("Chaos Orb", "currency", "icons/0000.png", raw),
                         self.icon("Omen of Whittling", "omen", "icons/0000.png", raw)]
        with self.assertRaisesRegex(ValueError, "duplicate icon"):
            reference_pack.import_pack(self.pack(data, {"icons/0000.png": raw}))

    def test_screenshot_metadata_cannot_trigger_icon_normalization(self):
        """Verify screenshot metadata cannot trigger icon normalization."""
        raw = self.png((40, 40))
        sha = hashlib.sha256(raw).hexdigest()
        file = f"screens/{sha}.png"
        data = self.data()
        data["scans"] = [{"file_name": "reference.png", "file": file, "sha256": sha,
                          "sockets": 3, "slot": "P1", "rune": "Sun", "family": "",
                          "rewards_json": "[]", "status": "local", "kind": "currency"}]
        self.assertEqual(reference_pack.import_pack(self.pack(data, {file: raw}))["scans"], 1)
        self.assertEqual((store.DATA_DIR / "images" / f"{sha}.png").read_bytes(), raw)

    def test_inventory_scan_skips_old_damage_and_retains_small_reference_images(self):
        """Verify inventory scan skips old damage and retains small reference images."""
        with logger._connect() as db:
            db.executemany("INSERT INTO currency_icons(name,image_png,recorded_at) VALUES(?,?,?)", [
                ("Chaos Orb", self.broken_png(), logger._now()),
                ("Chaos Orb", self.png((4000, 3000)), logger._now())])
        retained = []

        class Reader:
            """Stub inventory classification while recording normalized local-reference dimensions."""
            def icon(self, cell, count_digits=None):
                """Return no bundled icon candidates for each inventory cell."""
                return {"family": None, "all": []}

            def examples(self, cell, examples):
                """Record the sizes of supplied reference images and return no matches."""
                retained.extend(example["image"].size for example in examples)
                return []

        image = Image.fromarray(np.random.default_rng(8).integers(
            0, 256, (250, 600, 3), dtype=np.uint8))
        with patch.object(currency_ocr, "get_reader", return_value=Reader()):
            result = item_ocr.scan_inventory_grid(image, logger.inventory_icons(), read=lambda image: [])
        self.assertEqual(result["items"], [])
        self.assertEqual(len(retained), 60)
        self.assertTrue(all(max(size) <= 96 for size in retained))

    def test_case_only_recipe_and_all_mapping_references_use_existing_spelling(self):
        """Verify case only recipe and all mapping references use existing spelling."""
        logger.save_recipe({"name": "Case Reward", "sockets": 3, "combo": "Sun + Moon + Ward"})
        data = self.data()
        with logger._connect() as db:
            recipe = dict(db.execute("SELECT * FROM recipes WHERE name='Case Reward'").fetchone())
        recipe["name"] = "case reward"
        data["recipes"] = [recipe]
        data["families"] = [{"id": 9999, "top_socket": 3, "valid": 1,
                             "recipes_json": '["CASE REWARD"]'}]
        data["aliases"] = [{"alias": "case shortcut", "target": "case reward"}]
        data["seed_states"] = [{"family": 9999, "sockets": 3, "seed_slot": "P1",
                                "seed_rune": "Sun", "rewards_json": '["case REWARD"]',
                                "status": "local"}]
        counts = reference_pack.import_pack(self.pack(data))
        self.assertEqual(counts["recipes"], 0)
        self.assertEqual(logger.resolve("case reward", family=9999)["family"], 9999)
        self.assertEqual(logger.resolve("case shortcut", family=9999)["family"], 9999)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recipes WHERE name=? COLLATE NOCASE",
                                         ("Case Reward",)).fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute("SELECT recipes_json FROM families WHERE id=9999")
                                        .fetchone()[0]), ["Case Reward"])
            self.assertEqual(json.loads(db.execute("SELECT rewards_json FROM seed_states WHERE family=9999")
                                        .fetchone()[0]), ["Case Reward"])

    def test_conflicting_case_only_recipe_rows_rollback(self):
        """Verify conflicting case only recipe rows rollback."""
        data = self.data()
        data["recipes"] = [{"name": "New Case Reward", "sockets": 3, "combo": "Sun + Moon + Ward"},
                           {"name": "new case reward", "sockets": 3, "combo": "Sun + Moon + Stone"}]
        with self.assertRaisesRegex(ValueError, "conflicting records"):
            reference_pack.import_pack(self.pack(data), replace_existing=True)
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM recipes WHERE name=? COLLATE NOCASE",
                                         ("New Case Reward",)).fetchone())

    def test_explicit_case_only_recipe_replacement_keeps_existing_spelling(self):
        """Verify explicit case only recipe replacement keeps existing spelling."""
        logger.save_recipe({"name": "Case Reward", "sockets": 3, "combo": "Sun + Moon + Ward"})
        data = self.data()
        data["recipes"] = [{"name": "CASE REWARD", "sockets": 4,
                            "combo": "Sun + Moon + Ward + Stone"}]
        self.assertEqual(reference_pack.import_pack(self.pack(data), replace_existing=True)["recipes"], 1)
        with logger._connect() as db:
            row = db.execute("SELECT name,sockets FROM recipes WHERE name=? COLLATE NOCASE",
                             ("Case Reward",)).fetchall()
        self.assertEqual([tuple(value) for value in row], [("Case Reward", 4)])

    def test_case_only_duplicates_in_family_are_rejected(self):
        """Verify case only duplicates in family are rejected."""
        data = self.data()
        data["families"] = [{"id": 9999, "top_socket": 10, "valid": 1,
                             "recipes_json": '["Perfect Chaos Orb x3", "perfect chaos orb x3"]'}]
        with self.assertRaisesRegex(ValueError, "duplicate recipes"):
            reference_pack.import_pack(self.pack(data))

    def test_scaled_imported_glyph_is_rejected_before_committing_records(self):
        """Verify oversized glyph vector norms reject import before any reference rows are saved."""
        data = self.data()
        data["affixes"] = [{"name": "Uncommitted glyph affix"}]
        values = np.ones(1296, dtype=np.float32) * 1e20
        data["glyphs"] = [{"sha256": "0" * 64, "rune": "Sun",
                           "vector": base64.b64encode(values.tobytes()).decode()}]
        with self.assertRaisesRegex(ValueError, "norm"):
            reference_pack.import_pack(self.pack(data))
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name=?",
                                         ("Uncommitted glyph affix",)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reviewed_glyphs").fetchone()[0], 0)

    def test_zero_and_normalized_reviewed_glyphs_round_trip(self):
        """Verify zero and normalized reviewed glyphs round trip."""
        data = self.data()
        rng = np.random.default_rng(7)
        values = rng.normal(size=1296).astype(np.float32)
        values -= values.mean()
        values /= np.linalg.norm(values)
        data["glyphs"] = [{"sha256": str(index) * 64, "rune": rune,
                           "vector": base64.b64encode(vector.tobytes()).decode()}
                          for index, (rune, vector) in enumerate((
                              ("Sun", values), ("Moon", np.zeros(1296, dtype=np.float32))))]
        self.assertEqual(reference_pack.import_pack(self.pack(data))["glyphs"], 2)
        path = Path(self.tmp.name) / "glyphs.zip"
        path.write_bytes(reference_pack.export_pack())
        self.assertEqual(reference_pack.import_pack(path)["glyphs"], 0)
        self.assertEqual(len(store.reviewed_glyphs()), 2)

    def test_old_scaled_glyph_cannot_inflate_confidence_or_change_correct_rune(self):
        """Verify old scaled glyph cannot inflate confidence or change correct rune."""
        rng = np.random.default_rng(5)
        image = Image.fromarray(rng.integers(0, 256, (300, 600, 3), dtype=np.uint8))
        bx, by, sockets = 400, 100, 3
        cx, cy = scan.center_for(bx, by, sockets, 1)
        actual = scan.vector(scan.crop_at(image, cx, cy, 36, 36), "gray")
        noise = rng.normal(size=1296).astype(np.float32)
        noise -= noise.mean()
        noise -= actual * (noise @ actual)
        noise /= np.linalg.norm(noise)
        weak = (.05 * actual + np.sqrt(1 - .05 ** 2) * noise).astype(np.float32)
        scaled = weak * 100
        with logger._connect() as db:
            db.executemany("INSERT INTO reviewed_glyphs VALUES(?,?,?)", [
                ("0" * 64, "Sun", scaled.tobytes()), ("1" * 64, "Moon", actual.tobytes()),
                ("2" * 64, "Invalid", np.full(1296, np.nan, dtype=np.float32).tobytes()),
                ("3" * 64, "Broken", b"bad"),
                ("4" * 64, "Huge", np.full(1296, np.finfo(np.float32).max, dtype=np.float32).tobytes())])
        references = store.reviewed_glyphs()
        self.assertEqual({name for name, _ in references}, {"Sun", "Moon", "Huge"})
        self.assertTrue(all(np.isfinite(values).all() and
                            np.linalg.norm(values.astype(np.float64)) <= 1.000001
                            for _, values in references))
        states = [{"sockets": 3, "seed_slot": "P1", "seed_rune": rune, "family": family,
                   "rewards": [], "status": "local"} for rune, family in (("Sun", 1), ("Moon", 2))]

        class Model:
            """Stub socket probabilities so the regression isolates rune-vector similarity."""
            def predict_proba(self, features):
                """Return equal probabilities for three socket classes for every feature row."""
                return np.ones((len(features), 3))

        with patch.object(scan, "features", return_value=np.zeros(4)), patch.object(
                scan, "decode", return_value=(0, 2, 3)):
            spoofed = scan._scan_bar(image, (bx, by, .99), Model(), np.array(["Moon", "Sun"]),
                                    np.stack([actual, scaled]), states)
            safe = scan._scan_bar(image, (bx, by, .99), Model(),
                                 np.array([name for name, _ in references]),
                                 np.stack([values for _, values in references]), states)
        self.assertEqual(spoofed["seed_rune"], "Sun")
        self.assertGreater(spoofed["rune_similarity"], 1)
        self.assertEqual(safe["seed_rune"], "Moon")
        self.assertLessEqual(safe["rune_similarity"], 1)
        self.assertTrue(safe["can_commit"])


if __name__ == "__main__":
    unittest.main()
