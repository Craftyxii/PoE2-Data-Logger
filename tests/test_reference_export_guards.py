"""Reference sharing preserves Unicode labels and rejects oversized exports during collection."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class ReferenceExportGuardTests(unittest.TestCase):
    """Exercise real reference archives and bounded evidence collection in isolated profiles."""

    def setUp(self):
        """Select a disposable profile without changing the user's active logging directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-reference-export-guards-")
        self.previous = store.DATA_DIR
        self.profile("source")

    def tearDown(self):
        """Restore the selected profile and remove all probe databases, files and archives."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def profile(self, name):
        """Initialize an independent reference database with empty session records."""
        store.DATA_DIR = Path(self.temporary.name) / name
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def image(self, seed=0, size=(180, 180)):
        """Create deterministic textured RGB pixels whose PNG bytes exercise the byte budget."""
        return Image.fromarray(np.random.default_rng(seed).integers(
            0, 256, (size[1], size[0], 3), dtype=np.uint8))

    def screenshots(self, count):
        """Save independent valid screenshot records and content-addressed PNG files."""
        directory = store.DATA_DIR / "images"
        directory.mkdir(parents=True, exist_ok=True)
        sizes = []
        with logger._connect() as db:
            for index in range(count):
                output = io.BytesIO()
                self.image(index).save(output, format="PNG")
                raw = output.getvalue()
                digest = hashlib.sha256(raw).hexdigest()
                filename = digest + ".png"
                (directory / filename).write_bytes(raw)
                db.execute("INSERT INTO scans(recorded_at,file_name,image_name,image_sha256,"
                           "sockets,seed_slot,seed_rune,family,rewards_json,mapping_status) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?)", (logger._now(), f"capture-{index}.png",
                           filename, digest, 3, "P1", "Sun", "", "[]", "local"))
                sizes.append(len(raw))
        return sizes

    def test_currency_omen_and_item_icons_use_existing_unicode_catalog_identity(self):
        """Import actual source packs into case-equivalent catalogs, then deduplicate and undo."""
        cases = (("currency", "STRASSE Currency", "Straße Currency", logger.add_currency_item,
                  logger.save_currency_icon, logger.currency_icons, "currency_items"),
                 ("omen", "Omen of STRASSE", "Omen of Straße", logger.add_ritual_name,
                  logger.save_omen_icon, logger.omen_icons, "ritual_names"),
                 ("item", "STRASSE Item", "Straße Item", logger.add_item_name,
                  logger.save_item_icon, logger.item_icons, "item_names"))
        for kind, incoming, existing, register, save, read, names_table in cases:
            with self.subTest(kind=kind):
                self.profile(f"{kind}-source")
                register(incoming)
                save(incoming, self.image(size=(96, 96)))
                archive = Path(self.temporary.name) / f"{kind}.zip"
                archive.write_bytes(reference_pack.export_pack())
                self.profile(f"{kind}-target")
                register(existing)
                counts = reference_pack.import_pack(archive)
                self.assertEqual(counts[kind + "_icons"], 1)
                self.assertEqual([icon["name"] for icon in read()], [existing])
                with logger._connect() as db:
                    names = [row[0] for row in db.execute(f"SELECT name FROM {names_table}")
                             if row[0].casefold() == incoming.casefold()]
                self.assertEqual(names, [existing])
                self.assertTrue(all(value == 0 for value in reference_pack.import_pack(archive).values()))
                self.assertEqual(logger.undo_last_action()["action"], "Import OCR references")
                self.assertEqual(read(), [])
                with logger._connect() as db:
                    self.assertEqual(logger._catalog_name(db, names_table, incoming), existing)

    def test_screenshot_byte_limit_stops_before_loading_or_decoding_remaining_images(self):
        """Reject a growing collection before its third screenshot exceeds the asset budget."""
        sizes = self.screenshots(8)
        limit = sum(sizes[:2])
        original = Path.read_bytes
        with patch.object(reference_pack, "MAX_PACK", limit), \
                patch.object(Path, "read_bytes", autospec=True, side_effect=original) as loaded, \
                patch.object(reference_pack, "_image", wraps=reference_pack._image) as decoded:
            with self.assertRaisesRegex(ValueError, "exceeds the import size limits"):
                reference_pack.export_pack()
        self.assertEqual(loaded.call_count, 2)
        self.assertEqual(decoded.call_count, 2)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scans").fetchone()[0], 8)
        self.assertEqual(len(list((store.DATA_DIR / "images").glob("*.png"))), 8)

    def test_screenshot_entry_limit_includes_manifest_and_stops_before_the_next_read(self):
        """Count the manifest plus two screenshots and reject the next asset before loading it."""
        self.screenshots(8)
        original = Path.read_bytes
        with patch.object(reference_pack, "MAX_ENTRIES", 3), \
                patch.object(Path, "read_bytes", autospec=True, side_effect=original) as loaded:
            with self.assertRaisesRegex(ValueError, "too many images"):
                reference_pack.export_pack()
        self.assertEqual(loaded.call_count, 2)

    def test_manual_and_learned_icons_share_the_screenshot_asset_budgets(self):
        """Count manual icons, learned examples and captures together for entry and byte limits."""
        logger.save_currency_icon("Chaos Orb", self.image(size=(96, 96)))
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Budget learned icon", "quantity": 1}],
            register_names=True, icon_examples=[{"name": "Budget learned icon", "category": "Currency",
                "image": self.image(1, size=(96, 96)), "columns": 1, "rows": 1}])
        self.screenshots(1)
        original = Path.read_bytes
        with logger._connect() as db:
            icon_bytes = (db.execute("SELECT SUM(length(image_png)) FROM currency_icons").fetchone()[0] +
                          db.execute("SELECT SUM(length(image_png)) FROM review_icon_examples").fetchone()[0])
        for key, limit, message in (("MAX_ENTRIES", 3, "too many images"),
                                    ("MAX_PACK", icon_bytes, "exceeds the import size limits")):
            with self.subTest(budget=key), patch.object(reference_pack, key, limit), \
                    patch.object(Path, "read_bytes", autospec=True, side_effect=original) as loaded:
                with self.assertRaisesRegex(ValueError, message):
                    reference_pack.export_pack()
                self.assertEqual(loaded.call_count, 0)
        self.assertEqual(len(logger.currency_icons()), 1)
        self.assertEqual(len(logger.review_icons()), 1)

    def test_individual_screenshot_limit_is_checked_before_reading_its_bytes(self):
        """Use the saved file length to reject an individually oversized screenshot before allocation."""
        size = self.screenshots(1)[0]
        with patch.object(reference_pack, "MAX_IMAGE", size - 1), \
                patch.object(Path, "read_bytes", autospec=True) as loaded:
            with self.assertRaisesRegex(ValueError, "Reference image is too large"):
                reference_pack.export_pack()
        loaded.assert_not_called()

    def test_valid_archive_at_combined_size_and_entry_limits_round_trips(self):
        """Keep complete valid packs accepted at the exact uncompressed byte and entry boundaries."""
        self.screenshots(2)
        contents = reference_pack.export_pack()
        with ZipFile(io.BytesIO(contents)) as archive:
            limit = sum(entry.file_size for entry in archive.infolist())
        with patch.object(reference_pack, "MAX_PACK", limit), \
                patch.object(reference_pack, "MAX_ENTRIES", 3):
            contents = reference_pack.export_pack()
            path = Path(self.temporary.name) / "boundary.zip"
            path.write_bytes(contents)
            data, assets = reference_pack._inspect(path)
        self.assertEqual(len(assets), 2)
        self.assertEqual(len(data["scans"]), 2)
        with ZipFile(io.BytesIO(contents)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read("manifest.json"))["version"], 3)
        self.profile("restored")
        self.assertEqual(reference_pack.import_pack(path)["scans"], 2)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scans").fetchone()[0], 2)

    def test_manifest_bytes_still_count_toward_the_final_uncompressed_limit(self):
        """Reject a pack whose images fit but whose complete metadata crosses the size boundary."""
        sizes = self.screenshots(1)
        with patch.object(reference_pack, "MAX_PACK", sizes[0]):
            with self.assertRaisesRegex(ValueError, "exceeds the import size limits"):
                reference_pack.export_pack()


if __name__ == "__main__":
    unittest.main()
