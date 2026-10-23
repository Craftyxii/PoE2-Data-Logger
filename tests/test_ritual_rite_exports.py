"""Transport optional Rite sources and the final selected tablet set without rewriting history."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export
from PoE2_Data_Logger.core.ritual_rites_catalog import affixes


class RitualRiteExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-rite-exports-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        # Rites apply only while Ritual is the selected Activity Type.
        logger.save_settings({"activity_type": "Ritual"})

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, producer):
        rows = list(csv.reader(io.StringIO(producer().decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def test_completed_map_and_history_export_exact_separate_proliferation_sources(self):
        proliferation = next(row for row in affixes() if row["kind"] == "proliferation")
        rite = logger.new_ritual_rite("Rite export audit")["selected_rite_id"]
        original_set = logger.get_state()["selected_tablet_set_id"]
        logger.save_ritual_rite_affixes([{"id": proliferation["id"], "count": 2}])
        logger.start_map()
        first_event = logger.record_commit("Map settings")
        logger.finish_map(10, 20, 30, unique=1)
        logger.start_map()
        logger.save_ritual_rite_affixes([{"text": "Custom local wording", "kind": "local"}])
        second_set = logger.create_tablet_set("Ritual set")["selected_tablet_set_id"]
        logger.save_settings({"tablets_used": 1})
        library_event = logger.record_commit("Tablet config")
        second_event = logger.record_commit("Map settings")
        logger.finish_map(11, 21, 31, unique=2)
        logger.select_tablet_set(original_set)
        logger.new_ritual_rite("Unrelated later Rite")

        expected = {}
        with logger._connect() as db:
            for row in db.execute("SELECT map_id,snapshot_json FROM maps ORDER BY map_id"):
                expected[row["map_id"]] = json.loads(row["snapshot_json"])
        self.assertEqual(expected["M0002"]["tablet_set_id"], second_set)
        self.assertEqual(expected["M0001"]["ritual_rite_inherited_sources"], [])
        origins = expected["M0002"]["ritual_rite_inherited_sources"]
        self.assertEqual(len(origins), 1)
        self.assertEqual((origins[0]["origin_map_id"], origins[0]["origin_rite_id"], origins[0]["count"]),
                         ("M0001", rite, 2))

        for producer in (logger.export_maps_csv, logger.export_map_summary_csv, logger.export_primary_csv):
            rows = self.rows(producer)
            for mid, snapshot in expected.items():
                row = next(row for row in rows if row["Map ID"] == mid)
                self.assertEqual(row["Ritual Rite ID"], rite)
                self.assertEqual(row["Ritual Rite Map Ordinal"], mid[-1])
                self.assertEqual(row["Tablet Set ID"], snapshot["tablet_set_id"])
                self.assertEqual(json.loads(row["Ritual Rite Affixes JSON"]), snapshot["ritual_rite_affixes"])
                self.assertEqual(json.loads(row["Ritual Rite Inherited Sources JSON"]),
                                 snapshot["ritual_rite_inherited_sources"])

        for producer in (logger.export_record_history_csv, logger.export_all_csv):
            history = {int(row["Scan Commit #"]): row for row in self.rows(producer)
                       if row["Scan Commit #"]}
            self.assertEqual(json.loads(history[first_event]["Ritual Rite Inherited Sources JSON"]), [])
            self.assertEqual(json.loads(history[second_event]["Ritual Rite Inherited Sources JSON"]), origins)
            self.assertEqual(history[library_event]["Map ID"], "")
            self.assertEqual(history[library_event]["Ritual Rite ID"], "")

        backup = Path(self.temporary.name) / "audit.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            saved = {mid: json.loads(raw) for mid, raw in db.execute("SELECT map_id,snapshot_json FROM maps")}
            self.assertEqual(saved, expected)
            context = json.loads(db.execute("SELECT guidance FROM analysis_context_map_values "
                                           "WHERE map_id='M0002'").fetchone()[0])
            self.assertEqual(context["ritual_rite_inherited_sources"], origins)
        # The same exports feed the workbook; its real writer must accept nested source JSON.
        self.assertTrue(workbook_export.export_xlsx().startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
