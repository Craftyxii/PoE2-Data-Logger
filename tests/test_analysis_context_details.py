"""Expanded Context describes frozen settings and scoped totals without changing logging."""

from contextlib import closing
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import (analysis_context, analysis_context_data,
                                  logger_store as logger, store, workbook_export)


class AnalysisContextDetailTests(unittest.TestCase):
    """Read the same context through SQLite, CSV and XLSX in disposable profiles."""

    def setUp(self):
        """Initialize isolated logging and context tables without touching the selected profile."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-context-details-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        with logger._connect() as db:
            analysis_context_data.initialize(db, analysis_context.CONTEXT_VERSION)

    def tearDown(self):
        """Restore the selected profile after all SQLite and ZIP handles have closed."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def complete_rows(self, db):
        """Select the ordinary SQL view exactly in worksheet reading order."""
        return [tuple(row) for row in db.execute("SELECT section,topic,guidance,context_version "
                                                "FROM analysis_context_complete ORDER BY position,key")]

    def facts(self, section):
        """Decode structured Guidance objects from one dynamic context section."""
        with logger._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT guidance FROM analysis_context_complete WHERE section=? ORDER BY key", (section,))]

    def metric(self, name):
        """Find one source-scoped session metric by its saved configuration key."""
        return next(row for row in self.facts("Session stat total") if row["metric"] == name)

    def worksheet_rows(self, data, number=4):
        """Decode each ordered Context cell while explicitly closing the workbook archive."""
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        return [tuple("".join(cell.itertext()) for cell in row)
                for row in root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")]

    def catalog(self, *, revision="old", common_value=2, option_value=7, reverse=False):
        """Build a small frozen catalog with an ordinary node, a selector and separate stat identities."""
        options = [{"id": "choice-a", "name": f"{revision} option A", "effects": [f"{option_value}% option A"],
                    "stats": {"context_choice_bonus_+%": option_value}},
                   {"id": "choice-b", "name": f"{revision} option B", "effects": ["99% option B"],
                    "stats": {"context_other_bonus_+%": 99}}]
        if reverse:
            options.reverse()
        return {"schema_version": 1, "version": f"context-fixture-{revision}", "provenance": {"input_hashes": {}},
                "nodes": {"context-common": {"name": f"{revision} common node", "activity": "Main Atlas",
                    "allocatable": True, "effects": [f"{common_value}% common benefit"],
                    "stats": {"context_common_bonus_+%": common_value, "dummy_display_context_marker": 1},
                    "choices": []},
                    "context-choice": {"name": f"{revision} choice node", "activity": "Main Atlas",
                    "allocatable": True, "effects": ["Choose a bonus"],
                    "stats": {"dummy_display_context_selector": 1}, "choices": options}}}

    def setup(self, data=None, *, allocated=("context-common",), choices=None, rarity=11):
        """Persist a content-addressed frozen catalog/setup and select it as the current configuration."""
        data = data or self.catalog()
        encoded = logger._dump(data)
        catalog_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        settings = {"catalog_id": catalog_id, "catalog_version": data["version"],
                    "allocated": sorted(allocated), "choices": dict(sorted((choices or {}).items())),
                    "gear_item_rarity": rarity}
        frozen = logger._atlas_snapshot({"atlas_settings": settings})
        with logger._connect() as db:
            db.execute("INSERT OR IGNORE INTO atlas_catalogs VALUES(?,?,?)",
                       (catalog_id, data["version"], encoded))
            db.execute("INSERT OR IGNORE INTO atlas_setups VALUES(?,?,?)",
                       (frozen["atlas_setup_id"], catalog_id, logger._dump(settings)))
            config = logger._meta(db, "settings")
            config["atlas_settings"] = settings
            logger._set_meta(db, "settings", config)
        return frozen["atlas_setup_id"], catalog_id

    def saved_rows(self):
        """Capture logging, configuration, counters and Undo rows independently of Context metadata."""
        tables = ("meta", "maps", "expeditions", "map_unique_kills", "new_export", "legacy_export",
                  "currency_snapshots", "ritual_pages", "commits", "undo_actions", "atlas_catalogs",
                  "atlas_setups", "chain_step_provenance", "chain_completions")
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in tables}

    def test_standalone_sql_backup_csv_and_xlsx_have_identical_complete_context(self):
        """Deliver static guidance plus dynamic frozen facts identically without app-dependent SQL functions."""
        self.setup(choices={"context-choice": "choice-a"})
        logger.save_settings({"item_rarity": 20, "effectiveness": 35})
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 3}])
        with logger._connect() as db:
            expected = self.complete_rows(db)
        self.assertTrue({"Atlas setup", "Atlas allocation", "Atlas choice", "Map settings", "Session stat total"}
                        <= {row[0] for row in expected})
        path = Path(self.temporary.name) / "complete.sqlite3"
        path.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(path)) as exported:
            self.assertEqual(exported.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(self.complete_rows(exported), expected)
        csv_rows = list(csv.reader(io.StringIO(logger.export_analysis_context_csv().decode("utf-8-sig"))))
        self.assertEqual([tuple(row) for row in csv_rows[1:]], expected)
        worksheet = self.worksheet_rows(workbook_export.export_xlsx())
        self.assertEqual(worksheet[0], analysis_context.HEADERS)
        self.assertEqual(worksheet[1:], expected)

    def test_universally_allocated_nodes_are_explained_when_compact_columns_omit_them(self):
        """Keep omitted allocation-column effects available through their exact setup and node identities."""
        setup_id, catalog_id = self.setup()
        logger.start_map()
        headers = next(csv.reader(io.StringIO(logger.export_atlas_csv().decode("utf-8-sig"))))
        self.assertFalse(any(header.startswith("Atlas Node:") and "[context-common]" in header for header in headers))
        allocation = next(row for row in self.facts("Atlas allocation")
                          if row["setup_id"] == setup_id and row["node_id"] == "context-common")
        self.assertEqual(allocation["catalog_id"], catalog_id)
        self.assertEqual(allocation["name"], "old common node")
        self.assertEqual(allocation["effects"], ["2% common benefit"])
        self.assertEqual(allocation["stats"]["context_common_bonus_+%"], 2)

    def test_remembered_unallocated_choice_is_inactive_and_excluded_from_totals(self):
        """Describe remembered choices without granting them effects or counting selector display markers."""
        setup_id, _ = self.setup(choices={"context-choice": "choice-a"})
        choice = next(row for row in self.facts("Atlas choice") if row["setup_id"] == setup_id)
        self.assertIs(choice["active"], False)
        self.assertEqual((choice["choice_id"], choice["choice_number"]), ("choice-a", 1))
        totals = {row["stat_id"]: row for row in self.facts("Atlas stat total") if row["setup_id"] == setup_id}
        self.assertEqual(totals["context_common_bonus_+%"]["raw_total"], 2)
        self.assertNotIn("context_choice_bonus_+%", totals)
        self.assertFalse(any(key.startswith("dummy_display_") for key in totals))

    def test_library_only_history_does_not_mark_an_old_atlas_setup_current(self):
        """Blank-map tablet edits stay library history rather than saved map/setup references."""
        old_id, _ = self.setup()
        commit = logger.record_commit("Tablet config")
        current_id, _ = self.setup(self.catalog(revision="current"))
        with logger._connect() as db:
            saved = db.execute("SELECT map_id,snapshot_json FROM commits WHERE number=?",
                               (commit,)).fetchone()
            self.assertEqual(saved["map_id"], "")
            self.assertEqual(json.loads(saved["snapshot_json"])["atlas_setup_id"], old_id)
        setups = self.facts("Atlas setup")
        self.assertEqual([row["setup_id"] for row in setups], [current_id])
        self.assertIs(setups[0]["is_current"], True)
        self.assertEqual(setups[0]["referenced_map_count"], 0)
        self.assertEqual(self.facts("Atlas map reference"), [])

    def test_frozen_catalog_names_stats_and_option_order_survive_current_catalog_changes(self):
        """Use each saved catalog's definitions rather than current names, values or choice ordinals."""
        old_id, old_catalog = self.setup(allocated=("context-common", "context-choice"),
                                         choices={"context-choice": "choice-a"})
        logger.start_map()
        new_data = self.catalog(revision="new", common_value=900, option_value=700, reverse=True)
        new_id, new_catalog = self.setup(new_data, allocated=("context-common", "context-choice"),
                                         choices={"context-choice": "choice-a"})
        allocations = {row["setup_id"]: row for row in self.facts("Atlas allocation")
                       if row["node_id"] == "context-common"}
        self.assertEqual(allocations[old_id]["catalog_id"], old_catalog)
        self.assertEqual(allocations[new_id]["catalog_id"], new_catalog)
        self.assertEqual((allocations[old_id]["name"], allocations[old_id]["stats"]["context_common_bonus_+%"]),
                         ("old common node", 2))
        self.assertEqual((allocations[new_id]["name"], allocations[new_id]["stats"]["context_common_bonus_+%"]),
                         ("new common node", 900))
        choices = {row["setup_id"]: row for row in self.facts("Atlas choice")}
        self.assertEqual((choices[old_id]["name"], choices[old_id]["choice_number"]), ("old option A", 1))
        self.assertEqual((choices[new_id]["name"], choices[new_id]["choice_number"]), ("new option A", 2))
        totals = {(row["setup_id"], row["stat_id"]): row["raw_total"] for row in self.facts("Atlas stat total")}
        self.assertEqual(totals[old_id, "context_choice_bonus_+%"], 7)
        self.assertEqual(totals[new_id, "context_choice_bonus_+%"], 700)

    def test_tablet_totals_keep_exact_affix_and_unit_and_exclude_inactive_capacity(self):
        """Sum authoritative active slot values separately for percentage, count, seconds and flags."""
        logger.start_map()
        values = [("%", 10), ("%", 20), ("count", 2), ("count", 3),
                  ("seconds", 4), ("seconds", 6), ("flag", 1), ("flag", 1)]
        pairs = [{"affix": "Shared recorded affix", "value": amount, "unit": unit} for unit, amount in values]
        pairs += [{"affix": "Shared recorded affix for Rares", "value": amount, "unit": "%"}
                  for amount in (100, 200)]
        pairs += [{"affix": "", "value": None, "unit": "%"} for _ in range(2)]
        pairs += [{"affix": "Shared recorded affix", "value": 999, "unit": "%"} for _ in range(4)]
        with logger._connect() as db:
            saved = logger._load(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
            saved.update({"tablet_capacity": 3, "tablets_used": 3, "tablet_affixes": pairs})
            db.execute("UPDATE maps SET snapshot_json=? WHERE map_id='M0001'", (logger._dump(saved),))
        facts = [row for row in self.facts("Tablet stat total") if row["map_id"] == "M0001"]
        totals = {row["unit"]: row for row in facts if row["affix"] == "Shared recorded affix"}
        self.assertEqual({unit: row["total"] for unit, row in totals.items()},
                         {"%": 30, "count": 5, "seconds": 10, "flag": 2})
        self.assertTrue(all(row["affix"] == "Shared recorded affix" and row["contribution_count"] == 2
                            for row in totals.values()))
        self.assertIn("recorded occurrences", totals["flag"]["scope"])
        self.assertIn("not effect multiplication", totals["flag"]["scope"])
        related = next(row for row in facts if row["affix"] == "Shared recorded affix for Rares")
        self.assertEqual((related["unit"], related["total"], related["contribution_count"]), ("%", 300, 2))
        self.assertEqual(len(facts), 5)

    def test_repeated_recipe_and_history_rows_do_not_multiply_map_metric_totals(self):
        """Count saved maps once even when remnants, chain parts and repeated inventory approvals expand rows."""
        logger.save_settings({"item_rarity": 20, "monster_rarity": 30, "pack_size": 10, "effectiveness": 50})
        logger.start_map()
        family = next(item for item in logger.get_state()["families"] if item["valid"] and len(item["recipes"]) > 1)
        logger.commit_remnant(family["recipes"][0], family=family["id"])
        logger.commit_chain("Fire")
        for amount in (2, 4):
            logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": amount},
                                                 {"name": "Divine Orb", "quantity": 1}])
        logger.save_counts(10, 2, 3, 4, unique=1)
        rows = list(csv.DictReader(io.StringIO(logger.export_primary_csv().decode("utf-8-sig"))))
        history = list(csv.DictReader(io.StringIO(logger.export_all_csv().decode("utf-8-sig"))))
        self.assertGreater(len(rows), 1)
        self.assertGreater(len(history), len(rows))
        for name, expected in (("item_rarity", 20), ("monster_rarity", 30), ("pack_size", 10),
                               ("effectiveness", 50), ("known_kills_total", 16), ("remnants_detonated", 4)):
            with self.subTest(metric=name):
                metric = self.metric(name)
                self.assertEqual((metric["created_maps"], metric["known_maps"], metric["total"]), (1, 1, expected))

    def test_missing_zero_and_partial_kill_coverage_remain_distinct(self):
        """Report numeric zero as known, absent stats as missing, and known kill sums as potentially partial."""
        logger.start_map()
        logger.finish_map(None, None, None, None, unique=None)
        logger.start_map()
        logger.save_settings({"effectiveness": 0})
        logger.finish_map(0, 0, 0, 0, unique=0)
        logger.start_map()
        logger.save_settings({"effectiveness": 25})
        logger.save_counts(10, None, 2, 3, unique=None)
        facts = {row["map_id"]: row for row in self.facts("Map settings")}
        self.assertIsNone(facts["M0001"]["known_kills_total"])
        self.assertEqual(facts["M0002"]["known_kills_total"], 0)
        self.assertIs(facts["M0002"]["kill_categories_complete"], True)
        self.assertEqual(facts["M0003"]["known_kills_total"], 12)
        self.assertIs(facts["M0003"]["kill_categories_complete"], False)
        metric = self.metric("effectiveness")
        self.assertEqual((metric["created_maps"], metric["known_maps"], metric["missing_maps"]), (3, 2, 1))
        self.assertEqual((metric["total"], metric["mean"], metric["minimum"], metric["maximum"]), (25, 12.5, 0, 25))
        self.assertEqual((self.metric("known_kills_total")["known_maps"],
                          self.metric("known_kills_total")["total"]), (2, 12))

    def test_context_reads_and_exports_do_not_modify_logs_counters_settings_or_undo(self):
        """Keep live facts and both export formats entirely read-only against operational tables."""
        self.setup(allocated=("context-common", "context-choice"), choices={"context-choice": "choice-a"})
        logger.start_map()
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 5}])
        before = self.saved_rows()
        self.facts("Atlas stat total")
        logger.export_analysis_context_csv()
        workbook_export.export_xlsx()
        logger.backup_bytes()
        self.assertEqual(self.saved_rows(), before)

    def test_dynamic_context_uses_the_same_snapshot_as_workbook_data(self):
        """An intervening writer cannot replace saved metric facts after data-sheet reads begin."""
        logger.save_settings({"effectiveness": 20})
        logger.start_map()
        original = logger.export_primary_csv

        def read_then_change(*, _db):
            """Commit a newer saved value after the workbook acquires its read snapshot."""
            result = original(_db=_db)
            with store._connect() as writer:
                saved = logger._load(writer.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
                saved["effectiveness"] = 999
                writer.execute("UPDATE maps SET snapshot_json=? WHERE map_id='M0001'", (logger._dump(saved),))
            return result

        with patch.object(logger, "export_primary_csv", side_effect=read_then_change):
            worksheet = self.worksheet_rows(workbook_export.export_xlsx())
        facts = [json.loads(row[2]) for row in worksheet[1:] if row[0] == "Map settings"]
        self.assertEqual(facts[0]["effectiveness"], 20)
        self.assertEqual(self.facts("Map settings")[0]["effectiveness"], 999)


if __name__ == "__main__":
    unittest.main()
