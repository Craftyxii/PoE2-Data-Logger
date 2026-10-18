"""Exercise guarded Family 19 startup migration, default reset and real Undo.

These tests use isolated profiles and saved PNG bytes; no OCR is executed.
"""

import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, reference_updates, store, undo_store
from tests.test_family19_reference_update import NAMES, OLD_NAMES, restore_legacy_family19


class BundledReferenceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-bundled-reference-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, tables):
        with logger._connect() as db:
            result = {}
            for table in tables:
                columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
                keys = [row["name"] for row in sorted(db.execute(f"PRAGMA table_info({table})"),
                                                       key=lambda row: row["pk"]) if row["pk"]]
                order = ",".join(keys or columns)
                result[table] = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            return result

    def references(self):
        return self.rows(("recipes", "families", "seed_states", "aliases", "alias_keys", "alias_conflicts"))

    def legacy(self):
        restore_legacy_family19()
        with logger._connect() as db:
            db.execute("DELETE FROM meta WHERE key=?", (reference_updates.FAMILY19_MARKER,))

    def restart(self):
        logger._READY = False
        logger.initialize()

    def marker(self):
        with logger._connect() as db:
            return logger._meta(db, reference_updates.FAMILY19_MARKER)

    def family(self):
        with logger._connect() as db:
            return json.loads(db.execute("SELECT recipes_json FROM families WHERE id=19").fetchone()[0])

    def test_new_profile_and_shipped_sources_match_packet_without_undo(self):
        self.assertEqual(self.family(), NAMES)
        self.assertEqual(self.marker(), "already_current")
        self.assertEqual(logger.undo_action(), "")
        self.assertNotIn(reference_updates.FAMILY19_MARKER, undo_store.META_KEYS)
        packet = json.loads((logger.HERE.parent / "docs/reference_updates/family19-chaos-x10/manifest.json").read_text())
        with logger._connect() as db:
            actual = dict(db.execute("SELECT * FROM recipes WHERE name=?", (NAMES[-1],)).fetchone())
            expected = dict(packet["data"]["recipes"][0])
            expected.pop("recipe_id")
            self.assertEqual(actual, expected)
        self.assertEqual([row["recipe"] for row in logger.resolve(NAMES[0], family=19)["rows"]], NAMES)

    def test_existing_default_profile_updates_only_three_reference_rows(self):
        self.legacy()
        before = self.references()
        identities = self.rows(("recipe_ids",))["recipe_ids"]
        self.restart()
        after = self.references()
        self.assertEqual([row for row in after["recipes"] if row[0] != NAMES[-1]], before["recipes"])
        self.assertEqual([row for row in after["families"] if row[0] != 19],
                         [row for row in before["families"] if row[0] != 19])
        self.assertEqual([row for row in after["seed_states"] if row[0] != 19],
                         [row for row in before["seed_states"] if row[0] != 19])
        for table in ("aliases", "alias_keys", "alias_conflicts"):
            self.assertEqual(after[table], before[table])
        self.assertEqual(self.family(), NAMES)
        self.assertEqual(self.marker(), "applied")
        self.assertEqual(logger.undo_action(), reference_updates.UNDO_LABEL)
        after_ids = self.rows(("recipe_ids",))["recipe_ids"]
        self.assertEqual([row for row in after_ids if row[0] != NAMES[-1]], identities)
        self.assertIn((NAMES[-1], logger._recipe_identifier(NAMES[-1])), after_ids)

    def test_saved_history_settings_screenshots_and_alloys_remain_exact(self):
        self.legacy()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.commit_remnant("Aldur's Saga", family=19)
        logger.accept_propagation_part(logger.scan_context(), runes=["Wisdom"],
            recipe="Aldur's Saga", family=19, request_id="old-reference-origin")
        png = io.BytesIO()
        Image.new("RGB", (200, 100), (170, 140, 80)).save(png, format="PNG")
        with patch.object(store, "_reviewed_vector", return_value=None):
            store.save_scan(png.getvalue(), "historical.png", 7, "P1", "Wisdom", 19)
        tables = ("source_cells", "legacy_export", "new_export", "maps", "map_unique_kills",
            "kill_count_snapshots", "expeditions", "chain_completions", "chain_step_provenance",
            "chain_append_receipts", "scan_links", "commits", "currency_snapshots", "ritual_pages", "scans")
        before = self.rows(tables)
        exports = (logger.export_csv(), logger.export_record_history_csv())
        images = {path.name: path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}
        with logger._connect() as db:
            settings = logger._meta(db, "settings")
        alloys = [row for row in self.references()["recipes"] if row[0] in
                  ("The Runefather's Alloy", "The Runebinder's Alloy")]
        self.restart()
        self.assertEqual(self.rows(tables), before)
        self.assertEqual((logger.export_csv(), logger.export_record_history_csv()), exports)
        self.assertEqual({path.name: path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}, images)
        self.assertEqual([row for row in self.references()["recipes"] if row[0] in
                         ("The Runefather's Alloy", "The Runebinder's Alloy")], alloys)
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "settings"), settings)

    def test_undo_restores_legacy_rows_once_and_restart_does_not_reapply(self):
        self.legacy()
        before = self.references()
        self.restart()
        self.assertEqual(logger.undo_last_action()["action"], reference_updates.UNDO_LABEL)
        self.assertEqual(self.references(), before)
        self.assertEqual(self.marker(), "applied")
        self.restart()
        self.assertEqual(self.references(), before)
        self.assertEqual(logger.undo_action(), "")
        self.assertIn((NAMES[-1], logger._recipe_identifier(NAMES[-1])), self.rows(("recipe_ids",))["recipe_ids"])

    def test_prior_reference_undo_remains_reachable_after_correction_undo(self):
        self.legacy()
        logger.save_family({"family": 9999, "top_socket": 7, "recipes": ["Aldur's Saga"]})
        self.restart()
        self.assertEqual(logger.undo_last_action()["action"], reference_updates.UNDO_LABEL)
        self.assertEqual(logger.undo_last_action()["action"], "Family reference")
        self.assertFalse(any(row[0] == 9999 for row in self.references()["families"]))

    def test_custom_records_skip_entire_group_without_overwriting(self):
        changes = (
            ("family order", "UPDATE families SET recipes_json='[\"Aldur''s Saga\"]' WHERE id=19"),
            ("family metadata", "UPDATE families SET valid=0 WHERE id=19"),
            ("seed metadata", "UPDATE seed_states SET status='reviewed' WHERE family=19"),
            ("seed slot", "UPDATE seed_states SET seed_slot='P2' WHERE family=19"),
            ("recipe metadata", "UPDATE recipes SET source='local' WHERE name='Aldur''s Saga'"),
            ("extra seed", "INSERT INTO seed_states VALUES(19,6,'P1','Wisdom','[]','local')"),
            ("existing recipe", "INSERT INTO recipes VALUES('Chaos Orb x10',7,'local','local','local','local')"),
            ("case variant", "INSERT INTO recipes VALUES('chaos orb X10',7,'local','local','local','local')"),
            ("alias collision", "INSERT INTO aliases VALUES(99999,'Chaos Orb x10','Aldur''s Saga')"),
            ("direct alias key", "INSERT INTO alias_keys VALUES('chaosorbx10','Aldur''s Saga')"),
            ("registered custom identity", "INSERT INTO recipe_ids VALUES('Chaos Orb x10','local-identity')"),
            ("retired case variant", "INSERT INTO recipe_ids VALUES('chaos orb X10','retired-identity')"),
            ("identity owner conflict", "INSERT INTO recipe_ids VALUES('Retired reward','RCP-3d3b9c16959c508f96961e1e8a0e3d6f')"),
            ("custom dependency", "INSERT INTO families VALUES(9999,7,1,'[\"Chaos Orb x10\"]')"),
        )
        for index, (label, sql) in enumerate(changes):
            with self.subTest(label=label):
                # Recipe ID tombstones intentionally survive reference reset;
                # independent profiles keep the custom-ID cases independent.
                store.DATA_DIR = Path(self.temporary.name) / f"custom-{index}"
                logger._READY = False
                logger.initialize()
                self.legacy()
                with logger._connect() as db:
                    db.execute("DELETE FROM undo_actions")
                    db.execute(sql)
                before = self.references()
                self.restart()
                self.assertEqual(self.references(), before)
                self.assertEqual(self.marker(), "skipped_custom")
                self.assertEqual(logger.undo_action(), "")
                self.restart()
                self.assertEqual(self.references(), before)

    def test_already_imported_packet_is_unchanged_without_new_undo(self):
        self.legacy()
        pack = logger.HERE.parent / "docs/reference_updates/family19-chaos-x10/Family19_ChaosOrb_x10_reference_proposal.zip"
        reference_pack.import_pack(pack, replace_existing=True)
        before = self.references()
        actions = self.rows(("undo_actions",))
        self.restart()
        self.assertEqual(self.references(), before)
        self.assertEqual(self.rows(("undo_actions",)), actions)
        self.assertEqual(self.marker(), "already_current")

    def test_restarting_an_applied_update_adds_no_duplicate_action(self):
        self.legacy()
        self.restart()
        before = self.references()
        actions = self.rows(("undo_actions",))
        self.restart()
        self.assertEqual(self.references(), before)
        self.assertEqual(self.rows(("undo_actions",)), actions)

    def test_failed_write_rolls_back_rows_marker_identity_and_undo(self):
        self.legacy()
        before = self.references()
        identities = self.rows(("recipe_ids",))
        with logger._connect() as db:
            db.execute("CREATE TRIGGER reject_family19_update BEFORE UPDATE ON families WHEN NEW.id=19 "
                       "BEGIN SELECT RAISE(ABORT,'deliberate write failure'); END")
        logger._READY = False
        with self.assertRaisesRegex(sqlite3.IntegrityError, "deliberate write failure"):
            logger.initialize()
        # Read directly: another initialize would deliberately retry the failed transaction.
        with store._connect() as db:
            self.assertIsNone(logger._meta(db, reference_updates.FAMILY19_MARKER))
            self.assertIsNone(db.execute("SELECT 1 FROM recipes WHERE name=?", (NAMES[-1],)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM recipe_ids WHERE recipe_name=?", (NAMES[-1],)).fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM undo_actions").fetchone()[0], 0)
            db.execute("DROP TRIGGER reject_family19_update")
        logger._READY = True
        self.assertEqual(self.references(), before)
        self.assertEqual(self.rows(("recipe_ids",)), identities)

    def test_reset_uses_new_defaults_and_undo_keeps_legacy_choice_after_restart(self):
        self.legacy()
        with logger._connect() as db:
            logger._set_meta(db, reference_updates.FAMILY19_MARKER, "applied")
        before = self.references()
        reference_pack.reset_to_defaults()
        self.assertEqual(self.family(), NAMES)
        self.assertEqual(logger.undo_last_action()["action"], "Reset OCR references")
        self.assertEqual(self.references(), before)
        self.restart()
        self.assertEqual(self.references(), before)

    def test_bundled_historical_snapshot_and_alloy_references_are_not_rewritten(self):
        with gzip.open(logger.HERE / "bootstrap.json.gz", "rt", encoding="utf-8") as file:
            bootstrap = json.load(file)
        # Exact HEAD before-image for all bootstrap content outside these two
        # reference sections, including settings, historical cells and logs.
        untouched = {key: value for key, value in bootstrap.items() if key not in ("families", "recipes")}
        encoded = json.dumps(untouched, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         "efe363c861d02229b5c25eb3b2c526610d2b8f94ffeca790ba5c42539f4b83cd")
        alloys = [row for row in bootstrap["recipes"] if row["name"] in
                  ("The Runefather's Alloy", "The Runebinder's Alloy")]
        self.assertEqual([row["sockets"] for row in alloys], [7, 7])
        self.assertTrue(all(not row["combo"].endswith("Wisdom") for row in alloys))


if __name__ == "__main__":
    unittest.main()
