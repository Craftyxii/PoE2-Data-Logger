"""Frozen propagation origins remain attributable across replay, correction and every export format."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


class PropagationFamilyProvenanceTests(unittest.TestCase):
    """Check originating recipe/family validation, frozen history and paired-rune analysis without new counts."""

    def setUp(self):
        """Create a fresh session with controlled unique and shared recipe memberships."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-family-origin-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        with logger._connect() as db:
            db.executemany("INSERT INTO recipes(name,sockets,combo) VALUES(?,?,?)", [
                ("Origin Unique Test", 2, "Death+Power"),
                ("Origin Shared Test", 2, "Death+Power"),
                ("Origin Other Test", 1, "Time")])
            db.executemany("INSERT INTO families VALUES(?,?,?,?)", [
                (8001, 3, 1, json.dumps(["Origin Unique Test", "Origin Shared Test"])),
                (8002, 3, 1, json.dumps(["Origin Other Test", "Origin Shared Test"])),
                (8003, 3, 0, json.dumps(["Origin Unique Test"]))])
            db.execute("INSERT INTO alias_keys VALUES(?,?)", ("originunique", "Origin Unique Test"))
        self.context = logger.scan_context()

    def tearDown(self):
        """Restore the incoming profile and delete the isolated session after all connections close."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def accept(self, **overrides):
        """Approve one paired part with configurable origin hints and a stable captured context."""
        arguments = {"runes": ["Death", "Power"], "recipe": "Origin Shared Test", "request_id": "approved"}
        arguments.update(overrides)
        return logger.accept_propagation_part(self.context, **arguments)

    def records(self):
        """Snapshot all approval writes to verify atomic failure and receipt replay leave records intact."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "expeditions", "new_export", "commits", "chain_append_receipts",
                                  "chain_step_provenance")}

    def commits(self):
        """Read ordered commit details without rematching current catalogs."""
        with logger._connect() as db:
            return [(row["kind"], json.loads(row["details_json"]))
                    for row in db.execute("SELECT kind,details_json FROM commits ORDER BY number")]

    def csv_rows(self, producer):
        """Validate CSV header uniqueness and row widths before returning named fields."""
        rows = list(csv.reader(io.StringIO(producer().decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return rows[0], [dict(zip(rows[0], row)) for row in rows[1:]]

    def test_explicit_family_freezes_pair_recipe_and_existing_audits_without_extra_counts(self):
        """Persist both rune slots under one selected family with the original two commits and one detonation."""
        pending = logger.assign_ocr_id("opened")
        result = self.accept(family="Family 8002", family_candidates=[8002, 8001])
        state = logger.get_state()
        self.assertEqual(state["chain"], [{"step": 1, "rune1": "Death", "rune2": "Power"}])
        self.assertEqual((state["scan_commit_count"], state["detonated"], len(state["chain"])), (2, 1, 1))
        self.assertEqual(state["ocr_pending"], pending)
        origin = state["chain_provenance"][0]
        self.assertEqual((origin["recipe"], origin["family"], origin["family_candidates"],
                          origin["family_status"], origin["family_source"]),
                         ("Origin Shared Test", 8002, [8001, 8002], "known", "supplied"))
        self.assertEqual((origin["propagation_commit_number"], origin["chain_commit_number"]), (1, 2))
        self.assertEqual(result["provenance"], [origin])
        audits = self.commits()
        self.assertEqual([kind for kind, _ in audits], ["Propagation", "Chain"])
        self.assertEqual(audits[0][1]["origin"]["family"], 8002)
        self.assertEqual(audits[1][1]["origins"][0]["family"], 8002)
        self.assertEqual(audits[1][1]["steps"], state["chain"])
        with logger._connect() as db:
            runes = [dict(row) for row in db.execute("SELECT * FROM chain_rune_provenance ORDER BY rune_slot")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM new_export WHERE remnant_id IS NOT NULL").fetchone()[0], 0)
        self.assertEqual([(row["rune_slot"], row["rune"], row["family_id"]) for row in runes],
                         [(1, "Death", 8002), (2, "Power", 8002)])
        self.assertTrue(all((row["rune1"], row["rune2"]) == ("Death", "Power") for row in runes))
        logger._READY = False
        logger.initialize()
        self.assertEqual(logger.get_state()["chain_provenance"], [origin])

    def test_unique_recipe_candidate_and_ambiguous_unknown_origins_are_distinct(self):
        """Infer a unique canonical family or single captured candidate, keeping ambiguous/manual origins unknown."""
        cases = [
            ({"recipe": "origin unique"}, ("Origin Unique Test", 8001, "known", "recipe", [8001])),
            ({"family_candidates": [8002]}, ("Origin Shared Test", 8002, "known", "candidates", [8002])),
            ({}, ("Origin Shared Test", None, "ambiguous", "recipe", [8001, 8002])),
            ({"family_candidates": [8002, 8001, 8002]},
             ("Origin Shared Test", None, "ambiguous", "candidates", [8001, 8002])),
            ({"recipe": "Manual unknown", "family": None, "family_candidates": []},
             ("Manual unknown", None, "unknown", "unknown", []))]
        for index, (arguments, expected) in enumerate(cases):
            with self.subTest(arguments=arguments):
                origin = self.accept(request_id=f"origin-{index}", **arguments)["provenance"][0]
                self.assertEqual(tuple(origin[key] for key in
                                 ("recipe", "family", "family_status", "family_source", "family_candidates")), expected)
        self.assertEqual((logger.get_state()["detonated"], len(logger.get_state()["chain"])), (5, 5))

    def test_invalid_recipe_family_or_candidate_rejects_without_mutations(self):
        """Reject inactive, nonexistent, malformed and incompatible family hints without counting or appending."""
        before = self.records()
        cases = [
            {"family": 9999}, {"family": 8003}, {"family": True}, {"family": "Family broken"},
            {"recipe": "Origin Unique Test", "family": 8002},
            {"recipe": "Origin Unique Test", "family_candidates": [8002]},
            {"recipe": "Uncatalogued label", "family": 8001},
            {"family": 8001, "family_candidates": [8002]},
            {"family_candidates": "8001"}, {"family_candidates": [None]},
            {"family_candidates": [True]}, {"family_candidates": [9999]}]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.accept(**arguments)
            self.assertEqual(self.records(), before)

    def test_atomic_failure_rolls_back_origin_and_receipt_then_retry_saves_once(self):
        """Roll back every approval write if receipt persistence fails after the origin was inserted."""
        before = self.records()
        with patch.object(logger, "_save_chain_receipt", side_effect=RuntimeError("receipt write failed")):
            with self.assertRaisesRegex(RuntimeError, "receipt write failed"):
                self.accept(family=8001)
        self.assertEqual(self.records(), before)
        result = self.accept(family=8001)
        self.assertEqual((result["detonated"], result["steps"], result["scan_commit_number"]), (1, [1], 2))
        self.assertEqual(len(logger.get_state()["chain_provenance"]), 1)

    def test_concurrent_replay_normalizes_hints_and_freezes_origin_after_catalog_edit(self):
        """Deduplicate repeated origin-bearing approvals and retain saved attribution after the Family DB changes."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.accept(family="Family 8001", family_candidates=[8002, 8001]), range(2)))
        self.assertEqual(sorted(result["reused"] for result in results), [False, True])
        self.assertEqual((logger.get_state()["detonated"], len(logger.get_state()["chain"])), (1, 1))
        with logger._connect() as db:
            db.execute("UPDATE families SET valid=0,recipes_json='[]' WHERE id=8001")
        before = self.records()
        replay = self.accept(family=8001, family_candidates=[8001, 8002, 8001])
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["provenance"][0]["family"], 8001)
        self.assertEqual(self.records(), before)
        with self.assertRaisesRegex(ValueError, "different scan"):
            self.accept(family=8002, family_candidates=[8001, 8002])
        self.assertEqual(self.records(), before)
        with self.assertRaisesRegex(ValueError, "active Family DB"):
            self.accept(family=8001, request_id="later-capture")
        self.assertEqual(self.records(), before)

    def test_correction_and_completion_preserve_origin_while_raw_view_tracks_corrected_pair(self):
        """Correct rune labels without rewriting the approved recipe/family or inventing detonation counts."""
        original = self.accept(family=8002)["provenance"][0]
        logger.update_chain_steps([{"step": 1, "rune1": "Time", "rune2": "Death"}], self.context)
        self.assertEqual(logger.get_state()["chain_provenance"], [original])
        with logger._connect() as db:
            raw = list(db.execute("SELECT rune,rune1,rune2,family_id FROM chain_rune_provenance ORDER BY rune_slot"))
        self.assertEqual([tuple(row) for row in raw], [("Time", "Time", "Death", 8002), ("Death", "Time", "Death", 8002)])
        correction = self.commits()[-1][1]
        self.assertEqual(correction["origins"], [original])
        self.assertEqual(correction["changes"][0]["previous_rune1"], "Death")
        logger.complete_chain(self.context)
        completion = self.commits()[-1][1]
        self.assertEqual(completion["origins"], [original])
        self.assertEqual(completion["steps"], [{"step": 1, "rune1": "Time", "rune2": "Death"}])
        logger.save_settings({"expedition": 1})
        self.assertEqual((logger.get_state()["detonated"], logger.get_state()["chain_provenance"]), (1, [original]))
        for producer in (logger.export_csv, logger.export_record_history_csv, logger.export_all_csv,
                         logger.export_primary_csv):
            _, rows = self.csv_rows(producer)
            chain_rows = [row for row in rows if row.get("Chain Step #") or row.get("Type") == "Propagation"]
            self.assertTrue(chain_rows)
            self.assertTrue(all((row["Propagation Family ID"], row["Propagation Recipe"]) ==
                                ("8002", "Origin Shared Test") for row in chain_rows))

    def test_legacy_receipt_and_manual_rows_remain_unknown_after_upgrade(self):
        """Replay the pre-origin payload without migrating guessed families into older saved parts."""
        payload = {"operation": "propagation", "runes": ["Death", "Power"], "recipe": "Origin Unique Test",
                   "current_value": None, "count_from_saved": True}
        with logger._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            mid, expedition, eid = logger._chain_context(db, self.context)
            accepted = logger._increment_propagation(db, mid, expedition, eid, payload["runes"], payload["recipe"], logger._UNSET)
            result = logger._append_chain_steps(db, mid, expedition, eid, [("Death", "Power")])
            result.update(detonated=accepted["detonated"], propagation_commit_number=accepted["scan_commit_number"])
            logger._save_chain_receipt(db, "pre-upgrade", payload, result)
            # Simulate initialization of a profile saved before the provenance schema existed.
            db.execute("DROP VIEW chain_rune_provenance")
            db.execute("DROP TABLE chain_step_provenance")
        logger._READY = False
        logger.initialize()
        before = self.records()
        replay = self.accept(recipe="Origin Unique Test", request_id="pre-upgrade", family=None, family_candidates=[])
        self.assertEqual(replay, {**result, "reused": True})
        self.assertEqual(self.records(), before)
        self.assertEqual(logger.get_state()["chain_provenance"][0]["family_status"], "unknown")
        logger.commit_chain("Time")
        self.assertTrue(all(origin["family"] is None for origin in logger.get_state()["chain_provenance"]))
        for producer in (logger.export_csv, logger.export_record_history_csv, logger.export_all_csv,
                         logger.export_primary_csv):
            _, rows = self.csv_rows(producer)
            chain_rows = [row for row in rows if row.get("Chain Step #") or row.get("Type") == "Propagation"]
            self.assertTrue(all(row["Propagation Family ID"] == "Unknown" for row in chain_rows))
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_rune_provenance WHERE family='Unknown'").fetchone()[0], 3)

    def test_exports_preserve_known_numeric_and_unknown_text_and_backup_analysis_view(self):
        """Expose explicit origins in CSV/XLSX and raw SQLite while retaining paired steps, unknowns and reset behavior."""
        self.accept(family=8001)
        self.accept(request_id="ambiguous")
        logger.commit_chain("Time")
        for producer in (logger.export_csv, logger.export_record_history_csv, logger.export_all_csv,
                         logger.export_primary_csv):
            headers, rows = self.csv_rows(producer)
            for header in logger.CHAIN_ORIGIN_HEADERS:
                self.assertIn(header, headers)
            chain_rows = [row for row in rows if row.get("Chain Step #")]
            self.assertEqual([row["Propagation Family ID"] for row in chain_rows], ["8001", "Unknown", "Unknown"])
            self.assertEqual([row["Propagation Family Status"] for row in chain_rows], ["known", "ambiguous", "unknown"])
            self.assertEqual(chain_rows[1]["Propagation Family Candidates"], "8001;8002")
        self.assertEqual(self.csv_rows(logger.export_all_csv)[0].index("Type"), 127)
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            namespace = {"s": workbook_export.NS}
            for path in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet3.xml"):
                rows = ET.fromstring(archive.read(path)).findall("s:sheetData/s:row", namespace)
                family_column = next(cell.get("r").rstrip("0123456789") for cell in rows[0]
                                     if "".join(cell.itertext()) == "Propagation Family ID")
                cells = [cell for row in rows[1:] for cell in row
                         if cell.get("r").rstrip("0123456789") == family_column]
                self.assertTrue(any(cell.get("t") is None and cell.findtext("s:v", namespaces=namespace) == "8001"
                                    for cell in cells), path)
                self.assertTrue(any(cell.get("t") == "inlineStr" and "".join(cell.itertext()) == "Unknown"
                                    for cell in cells), path)
        destination = Path(self.temporary.name) / "exports"
        destination.mkdir()
        logger.save_export_folder(str(destination))
        backup = logger.save_export_file("sqlite3")
        with closing(sqlite3.connect(backup["path"])) as db:
            counts = db.execute("SELECT family,COUNT(*) FROM chain_rune_provenance GROUP BY family ORDER BY family").fetchall()
            self.assertEqual(counts, [("8001", 2), ("Unknown", 3)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        logger.clear_export_and_reset_ids()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_step_provenance").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_rune_provenance").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
