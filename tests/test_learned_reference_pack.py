"""Reference ZIP checks preserving learned item evidence and rejecting malformed or unsafe imports."""

import hashlib
import io
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, review_learning, store


class LearnedReferencePackTests(unittest.TestCase):
    """Exercise learned icon ZIP round trips, merge policies, validation and atomic import rejection."""
    def setUp(self):
        """Create an isolated source reference database."""
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name) / "source"
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the logger data directory and remove temporary reference databases."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def artwork(self, columns=1, rows=1):
        """Draw distinctive artwork with the requested inventory footprint."""
        image = Image.new("RGB", (96 * columns, 96 * rows), (15, 20, 45))
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, image.width - 10, image.height - 10), fill=(200, 80, 50))
        draw.line((0, image.height, image.width, 0), fill=(10, 210, 190), width=8)
        return image

    def learned(self, name, kind, columns=1, rows=1, file="learned/0000.png"):
        """Encode sample artwork into learned-icon metadata and PNG bytes with a matching hash."""
        image = self.artwork(columns, rows)
        raw, columns, rows = review_learning.encode_example(
            {"image": image, "columns": columns, "rows": rows})
        return {"name": name, "kind": kind, "columns": columns, "rows": rows,
                "file": file, "sha256": hashlib.sha256(raw).hexdigest()}, raw

    def save(self, name, kind, columns=1, rows=1, *, mirror=False):
        """Register a canonical label and save its sample artwork, optionally mirrored."""
        image = self.artwork(columns, rows)
        if mirror:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        with logger._connect() as db:
            canonical, resolved = logger._canonical_registered_name(db, name, kind.title())
            logger._save_review_examples(db, [{"name": canonical, "category": kind.title(),
                                               "image": image,
                                               "columns": columns, "rows": rows}],
                                        {canonical.casefold(): (resolved, 1)})

    def data(self):
        """Build an empty reference-pack database payload containing all supported record sections."""
        return {key: [] for key in (
            "families", "recipes", "aliases", "seed_states", "affixes", "master_perks",
            "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans",
            "review_icon_examples")}

    def pack(self, data, assets=None, version=3):
        """Write a reference ZIP with the requested manifest version and image assets."""
        path = Path(self.tmp.name) / "references.zip"
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "format": reference_pack.FORMAT, "version": version, "data": data}))
            for name, raw in (assets or {}).items():
                archive.writestr(name, raw)
        return path

    def switch_database(self, name):
        """Select and initialize another isolated reference database."""
        store.DATA_DIR = Path(self.tmp.name) / name
        logger._READY = False
        logger.initialize()

    def test_learned_currency_omen_and_full_gear_round_trip_into_fresh_database(self):
        """Verify learned currency, omen and gear evidence survives backup and ZIP import into a fresh database."""
        self.save("Example local currency", "currency", mirror=True)
        self.save("Omen of Local Testing", "omen")
        self.save("Example tall armour", "item", 2, 3)
        with logger._connect() as db:
            before = [tuple(row) for row in db.execute(
                "SELECT name,kind,columns,rows,image_png,image_sha256 FROM review_icon_examples ORDER BY id")]
        contents = reference_pack.export_pack()
        with ZipFile(io.BytesIO(contents)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], 3)
            self.assertEqual(len(manifest["data"]["review_icon_examples"]), 3)
        backup_path = Path(self.tmp.name) / "backup.sqlite3"
        backup_path.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup_path)) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM review_icon_examples").fetchone()[0], 3)
        path = Path(self.tmp.name) / "roundtrip.zip"
        path.write_bytes(contents)
        self.switch_database("target")
        self.assertEqual(reference_pack.import_pack(path)["review_icon_examples"], 3)
        with logger._connect() as db:
            after = [tuple(row) for row in db.execute(
                "SELECT name,kind,columns,rows,image_png,image_sha256 FROM review_icon_examples ORDER BY id")]
        self.assertEqual(after, before)
        self.assertIn("Omen of Local Testing", logger.currency_names())
        self.assertEqual(len(logger.review_icons()), 3)
        self.assertTrue(all(value == 0 for value in reference_pack.import_pack(path).values()))

    def test_legacy_version_two_pack_without_learned_records_remains_supported(self):
        """Verify version-two packs may omit learned records while version-three packs require the section."""
        data = self.data()
        del data["review_icon_examples"]
        self.assertTrue(all(value == 0 for value in reference_pack.import_pack(self.pack(data, version=2)).values()))
        with self.assertRaisesRegex(ValueError, "invalid database records"):
            reference_pack.import_pack(self.pack(data))

    def test_long_omen_name_round_trip_preserves_both_name_catalogs(self):
        """Verify a long learned omen name survives export and import with its currency catalog entry."""
        name = "Omen of " + "Long" * 33
        self.save(name, "omen")
        path = Path(self.tmp.name) / "long-omen.zip"
        path.write_bytes(reference_pack.export_pack())
        self.switch_database("long-omen-target")
        self.assertEqual(reference_pack.import_pack(path)["review_icon_examples"], 1)
        self.assertIn(name, logger.currency_names())
        self.assertEqual(logger.review_icons()[0]["name"], name)

    def test_merge_retains_existing_correction_and_explicit_replace_relabels_once(self):
        """Verify merge keeps an existing label and explicit replacement relabels identical artwork once."""
        self.save("Current armour name", "item", 2, 3)
        entry, raw = self.learned("Imported armour name", "item", 2, 3)
        data = self.data()
        data["item_names"] = [{"name": entry["name"]}]
        data["review_icon_examples"] = [entry]
        path = self.pack(data, {entry["file"]: raw})
        self.assertEqual(reference_pack.import_pack(path)["review_icon_examples"], 0)
        self.assertEqual(logger.review_icons()[0]["name"], "Current armour name")
        self.assertEqual(reference_pack.import_pack(path, replace_existing=True)["review_icon_examples"], 1)
        self.assertEqual(logger.review_icons()[0]["name"], "Imported armour name")
        self.assertEqual(reference_pack.import_pack(path, replace_existing=True)["review_icon_examples"], 0)
        self.assertEqual(len(logger.review_icons()), 1)

    def test_casefold_names_keep_the_registered_spelling_without_duplicate_columns(self):
        """Verify casefold-equivalent learned labels retain canonical spelling without duplicate names."""
        self.save("Straße Token", "currency")
        entry, raw = self.learned("STRASSE TOKEN", "currency")
        data = self.data()
        data["currency_names"] = [{"name": entry["name"]}]
        data["review_icon_examples"] = [entry]
        self.assertEqual(reference_pack.import_pack(self.pack(data, {entry["file"]: raw}))["currency_names"], 0)
        self.assertEqual(logger.review_icons()[0]["name"], "Straße Token")
        self.assertNotIn("STRASSE TOKEN", logger.currency_names())

    def test_duplicate_same_label_artwork_deduplicates_and_conflicting_labels_reject(self):
        """Verify duplicate artwork with one label deduplicates and conflicting labels reject import."""
        entry, raw = self.learned("Example armour", "item", 2, 3)
        second = dict(entry, file="learned/0001.png")
        data = self.data()
        data["item_names"] = [{"name": entry["name"]}]
        data["review_icon_examples"] = [entry, second]
        assets = {entry["file"]: raw, second["file"]: raw}
        self.assertEqual(reference_pack.import_pack(self.pack(data, assets))["review_icon_examples"], 1)
        data["review_icon_examples"][1] = dict(second, name="Conflicting armour")
        with self.assertRaisesRegex(ValueError, "conflicting learned icon labels"):
            reference_pack.import_pack(self.pack(data, assets), replace_existing=True)
        self.assertEqual(logger.review_icons()[0]["name"], "Example armour")

    def test_invalid_learned_metadata_rejects_before_any_import_writes(self):
        """Verify invalid learned metadata rejects the import before unrelated database writes."""
        entry, raw = self.learned("Example armour", "item", 2, 3)
        changes = [{"file": "../outside.png"}, {"file": "learned/../../outside.png"},
                   {"sha256": []}, {"columns": True}, {"columns": 3}, {"rows": 5},
                   {"name": ""}, {"name": "Bad\x7fname"}, {"kind": "unknown"}, {"kind": "omen"}]
        for change in changes:
            with self.subTest(change=change):
                data = self.data()
                data["affixes"] = [{"name": "Uncommitted learned affix"}]
                data["review_icon_examples"] = [dict(entry, **change)]
                with self.assertRaises(ValueError):
                    reference_pack.import_pack(self.pack(data, {entry["file"]: raw}))
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name='Uncommitted learned affix'").fetchone())
        self.assertEqual(logger.review_icons(), [])

    def test_wrong_hash_empty_artwork_and_wrong_dimensions_are_rejected(self):
        """Verify mismatched hashes, empty artwork, wrong dimensions and grayscale assets reject import."""
        entry, valid = self.learned("Example armour", "item", 2, 3)
        def png(image):
            """Encode an image as PNG bytes for malformed-asset import cases."""
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
        for raw, hash_value in ((valid, "0" * 64),
                                (png(Image.new("RGB", (192, 288))), None),
                                (png(self.artwork()), None),
                                (png(self.artwork(2, 3).convert("L")), None)):
            with self.subTest(length=len(raw), hash_value=hash_value):
                data = self.data()
                data["review_icon_examples"] = [dict(entry, sha256=hash_value or hashlib.sha256(raw).hexdigest())]
                with self.assertRaises(ValueError):
                    reference_pack.import_pack(self.pack(data, {entry["file"]: raw}))
        self.assertEqual(logger.review_icons(), [])

    def test_missing_or_wrong_catalog_category_rolls_back_other_reference_updates(self):
        """Verify absent or mismatched catalog categories roll back other reference additions."""
        for name, kind in (("Unregistered example", "item"), ("Chaos Orb", "item")):
            with self.subTest(name=name):
                entry, raw = self.learned(name, kind)
                data = self.data()
                data["affixes"] = [{"name": "Uncommitted catalog affix"}]
                data["review_icon_examples"] = [entry]
                with self.assertRaisesRegex(ValueError, "missing.*name database|wrong category"):
                    reference_pack.import_pack(self.pack(data, {entry["file"]: raw}))
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name='Uncommitted catalog affix'").fetchone())

    def test_learned_assets_share_total_archive_entry_limit(self):
        """Verify learned images count toward the shared archive-entry limit on export and import."""
        self.save("Example armour", "item", 2, 3)
        with patch.object(reference_pack, "MAX_ENTRIES", 1):
            with self.assertRaisesRegex(ValueError, "too many images"):
                reference_pack.export_pack()
        entry, raw = self.learned("Example armour", "item", 2, 3)
        data = self.data()
        data["review_icon_examples"] = [entry]
        path = self.pack(data, {entry["file"]: raw})
        with patch.object(reference_pack, "MAX_ENTRIES", 1):
            with self.assertRaisesRegex(ValueError, "too many"):
                reference_pack.import_pack(path)

    def test_saved_damage_cannot_be_exported_as_a_valid_learned_reference(self):
        """Verify damaged saved icon hashes cannot be exported as valid learned references."""
        self.save("Example armour", "item", 2, 3)
        with logger._connect() as db:
            db.execute("UPDATE review_icon_examples SET image_sha256=?", ("0" * 64,))
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            reference_pack.export_pack()


if __name__ == "__main__":
    unittest.main()
