"""Protect exact propagation origins and reference sharing from Unicode casefold ID collisions."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class RecipeUnicodeIdentityTests(unittest.TestCase):
    """Exercise normal canonical names, historical distinct variants and identity tombstones."""

    def setUp(self):
        """Initialize isolated profiles without changing the user's current database."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-unicode-recipes-")
        self.previous = store.DATA_DIR
        self.profile("source")

    def tearDown(self):
        """Restore the original profile and remove disposable test data."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def profile(self, name):
        """Select a fresh initialized logging profile for a real export/import boundary."""
        store.DATA_DIR = Path(self.temporary.name) / name
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def save(self, name, combo="Rage + Time + Bond"):
        """Save a valid three-socket recipe using the public catalog API."""
        return logger.save_recipe({"name": name, "sockets": 3, "combo": combo})

    def legacy(self):
        """Insert the distinct exact rows older ASCII-only lookup allowed, with the old colliding registry."""
        names = ("Straße Legacy Reward", "STRASSE Legacy Reward")
        with logger._connect() as db:
            for name, combo in zip(names, ("Rage + Time + Bond", "Time + Bond + Rage")):
                db.execute("INSERT INTO recipes VALUES(?,?,?,NULL,NULL,NULL)", (name, 3, combo))
            db.execute("INSERT INTO recipe_ids VALUES(?,?)", (names[0], logger._recipe_identifier(names[0])))
        return names

    def pack(self, raw=None):
        """Write actual reference pack bytes to a path accepted by the importer."""
        path = Path(self.temporary.name) / "references.zip"
        path.write_bytes(reference_pack.export_pack() if raw is None else raw)
        return path

    def test_new_unicode_variant_updates_one_canonical_recipe(self):
        """Keep variants of a new Unicode name on one catalog row and stable identity."""
        first = self.save("Äudit Recipe")
        second = self.save("äudit recipe", "Time + Bond + Rage")
        self.assertEqual(second["name"], first["name"])
        self.assertEqual(second["recipe_id"], first["recipe_id"])
        with logger._connect() as db:
            rows = db.execute("SELECT name,combo FROM recipes WHERE name IN (?,?)",
                              (first["name"], "äudit recipe")).fetchall()
            self.assertEqual([tuple(row) for row in rows], [(first["name"], "Time + Bond + Rage")])

    def test_undo_and_reset_tombstones_preserve_readded_variant_identity(self):
        """Retain original recipe identity after removal without restoring it to the active catalog."""
        first = self.save("Äudit Tombstone")
        logger.undo_last_action()
        second = self.save("äudit tombstone")
        self.assertEqual((second["name"], second["recipe_id"]), (first["name"], first["recipe_id"]))
        reference_pack.reset_to_defaults()
        third = self.save("ÄUDIT TOMBSTONE")
        self.assertEqual((third["name"], third["recipe_id"]), (first["name"], first["recipe_id"]))

    def test_legacy_exact_rows_get_distinct_ids_and_ambiguous_input_is_held(self):
        """Preserve different legacy combos and reject nonexact ambiguous name guesses."""
        names = self.legacy()
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            ids = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids WHERE recipe_name IN (?,?)", names))
            self.assertNotEqual(ids[names[0]], ids[names[1]])
            self.assertEqual(logger._canonical(db, names[0]), names[0])
            self.assertEqual(logger._canonical(db, names[1]), names[1])
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                logger._canonical(db, "strasse legacy reward")
        saved = logger.accept_propagation_part(logger.scan_context(), runes=["Time"], recipe=names[1])
        with logger._connect() as db:
            origin = db.execute("SELECT recipe,recipe_id FROM chain_step_provenance").fetchone()
            self.assertEqual(tuple(origin), (names[1], ids[names[1]]))
        self.assertTrue(saved)

    def test_legacy_collision_pack_roundtrips_same_and_fresh_profiles(self):
        """Share migrated exact spellings, combos and distinct IDs without collapsing the exported rows."""
        names = self.legacy()
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            expected = [tuple(row) for row in db.execute(
                "SELECT name,combo,recipe_id FROM recipes JOIN recipe_ids ON recipe_name=name "
                "WHERE name IN (?,?) ORDER BY name", names)]
        path = self.pack()
        reference_pack.import_pack(path, replace_existing=True)
        self.profile("destination")
        reference_pack.import_pack(path)
        with logger._connect() as db:
            actual = [tuple(row) for row in db.execute(
                "SELECT name,combo,recipe_id FROM recipes JOIN recipe_ids ON recipe_name=name "
                "WHERE name IN (?,?) ORDER BY name", names)]
            self.assertEqual(actual, expected)

    def test_distinct_legacy_tombstones_readd_as_distinct_exact_rows(self):
        """Re-add each known legacy spelling after reset without grafting its combo onto the other identity."""
        names = self.legacy()
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            expected = dict(db.execute("SELECT recipe_name,recipe_id FROM recipe_ids WHERE recipe_name IN (?,?)", names))
        reference_pack.reset_to_defaults()
        for name, combo in zip(names, ("Rage + Time + Bond", "Time + Bond + Rage")):
            saved = self.save(name, combo)
            self.assertEqual((saved["name"], saved["recipe_id"]), (name, expected[name]))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recipes WHERE name IN (?,?)", names).fetchone()[0], 2)

    def test_legacy_bad_origin_migration_keeps_undo_and_retry_receipt_consistent(self):
        """Repair proven old origin links in all saved copies so Undo can reverse the corrected approval."""
        names = self.legacy()
        context = logger.scan_context()
        saved = logger.accept_propagation_part(context, runes=["Time"], recipe=names[1], request_id="unicode-retry")
        correct = saved["provenance"][0]["recipe_id"]
        wrong = logger._recipe_identifier(names[0])
        # Recreate pre-fix storage, including its inverse journal and receipt.
        from PoE2_Data_Logger.core import undo_store
        with logger._connect() as db:
            db.execute("DELETE FROM recipe_ids WHERE recipe_name=?", (names[1],))
            db.execute("UPDATE chain_step_provenance SET recipe_id=? WHERE recipe=?", (wrong, names[1]))
            for table, key, column in (("commits", "number", "details_json"),
                                       ("chain_append_receipts", "request_id", "result_json")):
                for row in db.execute(f"SELECT {key},{column} FROM {table}").fetchall():
                    db.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?",
                               (row[column].replace(correct, wrong), row[key]))
            for row in db.execute("SELECT id,changes FROM undo_actions").fetchall():
                entries = undo_store._unpack(row["changes"])
                raw = json.dumps(entries).replace(correct, wrong)
                db.execute("UPDATE undo_actions SET changes=? WHERE id=?",
                           (undo_store._pack(json.loads(raw)), row["id"]))
        logger._READY = False
        logger.initialize()
        retry = logger.accept_propagation_part(context, runes=["Time"], recipe=names[1], request_id="unicode-retry")
        self.assertEqual(retry["provenance"][0]["recipe_id"], correct)
        self.assertEqual(logger.undo_last_action()["action"], "Propagation approval")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 0)

    def test_unidentified_duplicate_spellings_in_old_pack_fail_atomically(self):
        """Reject conflicting unidentifiable legacy entries rather than silently choosing one recipe's combo."""
        raw = reference_pack.export_pack()
        with ZipFile(io.BytesIO(raw)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files["manifest.json"])
        for name, combo in (("Äudit Imported", "Rage + Time + Bond"), ("äudit imported", "Time + Bond + Rage")):
            manifest["data"]["recipes"].append({"name": name, "sockets": 3, "combo": combo})
        files["manifest.json"] = json.dumps(manifest).encode()
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with self.assertRaisesRegex(ValueError, "ambiguous case"):
            reference_pack.import_pack(self.pack(output.getvalue()))
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM recipes WHERE name=?", ("Äudit Imported",)).fetchone())


if __name__ == "__main__":
    unittest.main()
