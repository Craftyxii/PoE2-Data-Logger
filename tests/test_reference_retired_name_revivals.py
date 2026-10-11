"""Reference packs preserve explicit retired-name choices without reviving old bundled defaults."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class ReferenceRetiredNameRevivalTests(unittest.TestCase):
    """Exercise fresh-profile sharing, legacy compatibility, validation and atomic revival writes."""

    def setUp(self):
        """Select an isolated profile and keep all probe artifacts outside the active log directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-reference-revivals-")
        self.previous = store.DATA_DIR
        self.profile("source")

    def tearDown(self):
        """Restore the selected application profile and remove disposable archives/databases."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def profile(self, name):
        """Initialize an independent profile with empty logging state for cross-profile round trips."""
        store.DATA_DIR = Path(self.temporary.name) / name
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def data(self):
        """Build a minimal supported manifest, allowing optional revival metadata to be varied independently."""
        return {key: [] for key in ("families", "recipes", "aliases", "seed_states", "affixes",
                "master_perks", "currency_names", "omen_names", "item_names", "glyphs", "icons",
                "scans", "review_icon_examples")}

    def pack(self, data, version=3):
        """Write a valid ZIP envelope around the supplied catalog and revival metadata."""
        path = Path(self.temporary.name) / "references.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": reference_pack.FORMAT,
                                                          "version": version, "data": data}))
        return path

    def revival_metadata(self):
        """Read only the explicit revival choices stored in the application's metadata."""
        with logger._connect() as db:
            return logger._meta(db, "active_retired_currency_names", [])

    def test_names_without_icons_round_trip_but_unmarked_old_defaults_stay_hidden(self):
        """Restore user choices on a populated target while retaining its activity and local overrides."""
        logger.add_currency_item("Sun Artifact")
        logger.add_ritual_name("Omen of Corruption")
        with logger._connect() as db:
            # Older profiles can retain obsolete default rows without explicit local registration.
            db.execute("INSERT INTO currency_items(name) VALUES('Exotic Coinage')")
        contents = reference_pack.export_pack()
        with ZipFile(io.BytesIO(contents)) as archive:
            data = json.loads(archive.read("manifest.json"))["data"]
        self.assertEqual(data["active_retired_currency_names"], ["omen of corruption", "sun artifact"])
        self.assertEqual(data["icons"], [])
        self.assertEqual(data["review_icon_examples"], [])
        path = Path(self.temporary.name) / "roundtrip.zip"
        path.write_bytes(contents)
        self.profile("target")
        logger.add_currency_item("Black Scythe Artifact")
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 7}])
        with logger._connect() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                      for table in ("maps", "commits", "currency_snapshots", "analysis_context")}
        reference_pack.import_pack(path)
        self.assertIn("Sun Artifact", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.ritual_names())
        self.assertIn("Black Scythe Artifact", logger.currency_names())
        self.assertNotIn("Exotic Coinage", logger.currency_names())
        self.assertEqual(self.revival_metadata(), ["black scythe artifact", "omen of corruption", "sun artifact"])
        with logger._connect() as db:
            for table, rows in before.items():
                self.assertEqual([tuple(row) for row in db.execute(f"SELECT * FROM {table}")], rows, table)
        self.assertEqual(logger.undo_action(), "Import OCR references")
        self.assertTrue(all(count == 0 for count in reference_pack.import_pack(path).values()))
        logger._READY = False
        logger.initialize()
        self.assertIn("Sun Artifact", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.ritual_names())
        self.assertNotIn("Exotic Coinage", logger.currency_names())
        with ZipFile(io.BytesIO(reference_pack.export_pack())) as archive:
            restored = json.loads(archive.read("manifest.json"))["data"]["active_retired_currency_names"]
        self.assertEqual(restored, self.revival_metadata())

    def test_existing_pack_versions_can_omit_metadata_and_do_not_erase_local_choices(self):
        """Keep pre-extension packs readable while distinguishing their legacy rows from local overrides."""
        for version in (1, 2, 3):
            with self.subTest(version=version):
                self.profile(f"legacy-{version}")
                data = self.data()
                data["currency_names"] = [{"name": "Sun Artifact"}]
                data["omen_names"] = [{"name": "Omen of Corruption"}]
                reference_pack.import_pack(self.pack(data, version))
                self.assertNotIn("Sun Artifact", logger.currency_names())
                self.assertNotIn("Omen of Corruption", logger.ritual_names())
                self.assertEqual(self.revival_metadata(), [])
                logger.add_currency_item("Sun Artifact")
                reference_pack.import_pack(self.pack(data, version), replace_existing=True)
                self.assertIn("Sun Artifact", logger.currency_names())
                self.assertEqual(self.revival_metadata(), ["sun artifact"])

    def test_revival_metadata_requires_bounded_unique_registered_retired_labels(self):
        """Reject malformed overrides before catalog mutation or screenshot-directory creation."""
        invalid = ("sun artifact", None, [True], ["Chaos Orb"], ["Exotic Coinage"],
                   ["Sun Artifact", " SUN ARTIFACT "], ["Sun\nArtifact"], ["Sun Artifact"] * 20)
        for value in invalid:
            with self.subTest(metadata=value):
                data = self.data()
                data["currency_names"] = [{"name": "Sun Artifact"}]
                data["affixes"] = [{"name": "Audit revival rollback affix"}]
                data["active_retired_currency_names"] = value
                with self.assertRaises(ValueError):
                    reference_pack.import_pack(self.pack(data))
                self.assertEqual(self.revival_metadata(), [])
                with logger._connect() as db:
                    self.assertIsNone(db.execute("SELECT 1 FROM currency_items WHERE name='Sun Artifact'").fetchone())
                    self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name='Audit revival rollback affix'")
                                      .fetchone())
                self.assertFalse((store.DATA_DIR / "images").exists())

    def test_case_apostrophe_and_whitespace_normalization_preserves_catalog_spelling(self):
        """Match canonical retired keys without rewriting the imported visible name's spelling."""
        data = self.data()
        data["currency_names"] = [{"name": "ALDUR’S SAGA"}]
        data["active_retired_currency_names"] = ["  Aldur's Saga  "]
        reference_pack.import_pack(self.pack(data))
        self.assertIn("ALDUR’S SAGA", logger.currency_names())
        self.assertEqual(self.revival_metadata(), ["aldur's saga"])

    def test_later_import_failure_rolls_back_revivals_and_catalog_writes_together(self):
        """An invalid later glyph cannot leave a revived name or any earlier imported record behind."""
        data = self.data()
        data["currency_names"] = [{"name": "Sun Artifact"}]
        data["active_retired_currency_names"] = ["sun artifact"]
        data["affixes"] = [{"name": "Audit revival rollback affix"}]
        data["glyphs"] = [{"sha256": "invalid", "rune": "Rage", "vector": ""}]
        with self.assertRaisesRegex(ValueError, "Invalid reviewed rune hash"):
            reference_pack.import_pack(self.pack(data))
        self.assertEqual(self.revival_metadata(), [])
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM currency_items WHERE name='Sun Artifact'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM affixes WHERE name='Audit revival rollback affix'").fetchone())

    def test_reference_reset_removes_imported_explicit_revivals(self):
        """Resetting learned references clears imported revival choices along with user-added names."""
        data = self.data()
        data["currency_names"] = [{"name": "Sun Artifact"}]
        data["active_retired_currency_names"] = ["sun artifact"]
        reference_pack.import_pack(self.pack(data))
        self.assertIn("Sun Artifact", logger.currency_names())
        reference_pack.reset_to_defaults()
        self.assertNotIn("Sun Artifact", logger.currency_names())
        self.assertEqual(self.revival_metadata(), [])


if __name__ == "__main__":
    unittest.main()
