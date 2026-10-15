"""Transport frozen map context consistently without adding Atlas bonuses to observations."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import analysis_context, atlas_catalog, logger_store as logger, store, workbook_export


class MapContextExportTests(unittest.TestCase):
    """Exercise map, event, workbook and standalone-SQL projections of saved context."""

    def setUp(self):
        """Create an isolated profile without replacing existing session or reference data."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-context-exports-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Restore the selected storage root after all per-operation handles close."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, data):
        """Reject duplicate headers and ragged records before decoding their named fields."""
        raw = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        return raw[0], [dict(zip(raw[0], row)) for row in raw[1:]]

    def context(self, activity="Ritual", saved=True):
        """Construct a transport fixture with real saved Atlas identity and no simulated bonuses."""
        with logger._connect() as db:
            original = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
        snapshot = {"schema_version": 1, "atlas_setup_id": original["atlas_setup_id"],
                    "catalog_id": original["atlas_catalog_id"],
                    "catalog_version": original["atlas_settings"]["catalog_version"],
                    "activity_type": activity, "biome": "Desert", "city_type": "Faridun",
                    "effective_biomes": ["Desert"], "totals": [], "unresolved": []}
        original.update({"activity_type": activity, "waystone_setup_saved": saved,
                         "atlas_effect_snapshot": snapshot, "biome": "Desert", "city_type": "Faridun"})
        return original

    def replace_snapshot(self, context, *, commit=None):
        """Install recorded transport evidence without depending on the context-calculation helper."""
        with logger._connect() as db:
            encoded = json.dumps(context)
            if commit is None:
                db.execute("UPDATE maps SET snapshot_json=? WHERE map_id='M0001'", (encoded,))
            else:
                db.execute("UPDATE commits SET snapshot_json=? WHERE number=?", (encoded, commit))

    def workbook_rows(self, data, number):
        """Decode inline-string and scalar cells by header, preserving absent cells as blanks."""
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        rows = root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
        result = []
        for row in rows[1:]:
            values = dict.fromkeys(headers.values(), "")
            for cell in row:
                self.assertIsNone(cell.find(f"{{{workbook_export.NS}}}f"))
                values[headers[cell.get("r").rstrip("0123456789")]] = "".join(cell.itertext())
            result.append(values)
        return result

    def test_map_context_and_aggregate_export_once_without_changing_saved_metrics(self):
        """Project one map context even when its recognized family exports multiple recipe rows."""
        logger.save_settings({"item_rarity": 12, "monster_rarity": 34, "pack_size": 56, "effectiveness": 78})
        logger.start_map()
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        context = self.context()
        self.replace_snapshot(context)
        with logger._connect() as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM maps")]
        headers, maps = self.rows(logger.export_maps_csv())
        self.assertEqual(headers[-3:], list(logger.MAP_CONTEXT_HEADERS))
        self.assertEqual(headers[13:15], ["Expedition 1 Detonated", "Expedition 2 Detonated"])
        self.assertEqual(headers[26:28], ["Biome", "City Type"])
        self.assertEqual([maps[0][name] for name in logger.MAP_CONTEXT_HEADERS[:2]], ["Ritual", "Yes"])
        self.assertEqual(json.loads(maps[0]["Atlas Effects JSON"]), context["atlas_effect_snapshot"])
        _, primary = self.rows(logger.export_primary_csv())
        self.assertGreater(len(primary), 1)
        self.assertEqual([primary[0][name] for name in logger.MAP_CONTEXT_HEADERS[:2]], ["Ritual", "Yes"])
        self.assertEqual(json.loads(primary[0]["Atlas Effects JSON"]), context["atlas_effect_snapshot"])
        self.assertTrue(all(all(row[name] == "" for name in logger.MAP_CONTEXT_HEADERS) for row in primary[1:]))
        for name, value in (("Item Rarity %", "12"), ("Monster Rarity %", "34"),
                            ("Pack Size %", "56"), ("Effectiveness %", "78")):
            self.assertEqual(primary[0][name], value)
        self.assertEqual(self.workbook_rows(workbook_export.export_xlsx(), 1), primary)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM maps")], before)

    def test_history_keeps_each_event_snapshot_and_all_csv_old_type_position(self):
        """Read each saved event's context instead of replacing it with the current map category."""
        logger.start_map()
        first = logger.record_commit("Map settings")
        second = logger.record_commit("Tablet config")
        contexts = [self.context("Breach", False), self.context("Grand Expedition", True)]
        for number, context in zip((first, second), contexts):
            self.replace_snapshot(context, commit=number)
        for producer in (logger.export_record_history_csv, logger.export_all_csv):
            headers, rows = self.rows(producer())
            self.assertEqual(headers[-3:], list(logger.MAP_CONTEXT_HEADERS))
            if producer == logger.export_all_csv:
                self.assertEqual(headers.index("Type"), 127)
                self.assertEqual(headers.index("Scan Commit #"), 90)
            by_commit = {int(row["Scan Commit #"]): row for row in rows}
            for number, context in zip((first, second), contexts):
                row = by_commit[number]
                self.assertEqual(row["Activity Type"], context["activity_type"])
                self.assertEqual(row["Map Scanned"], "Yes" if context["waystone_setup_saved"] else "No")
                self.assertEqual(json.loads(row["Atlas Effects JSON"]), context["atlas_effect_snapshot"])
        _, history = self.rows(logger.export_all_csv())
        self.assertEqual(self.workbook_rows(workbook_export.export_xlsx(), 3), history)

    def test_missing_legacy_metadata_stays_unknown_and_explicit_none_is_retained(self):
        """Do not manufacture availability from a default tier, today's Atlas or unrelated scans."""
        logger.start_map()
        number = logger.record_commit("Map settings")
        context = self.context()
        for key in ("activity_type", "waystone_setup_saved", "atlas_effect_snapshot"):
            context.pop(key)
        self.replace_snapshot(context)
        self.replace_snapshot(context, commit=number)
        for producer in (logger.export_maps_csv, logger.export_map_summary_csv,
                         logger.export_primary_csv, logger.export_record_history_csv, logger.export_all_csv):
            _, rows = self.rows(producer())
            self.assertTrue(rows)
            self.assertTrue(all(all(row[name] == "" for name in logger.MAP_CONTEXT_HEADERS) for row in rows))
        context.update({"activity_type": "None", "waystone_setup_saved": False})
        self.replace_snapshot(context)
        _, rows = self.rows(logger.export_maps_csv())
        self.assertEqual((rows[0]["Activity Type"], rows[0]["Map Scanned"]), ("None", "No"))

    def test_queued_inventory_map_context_does_not_require_a_created_map_row(self):
        """Retain saved pre-map inventory context without adding a synthetic created map."""
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3}])
        metadata = {"activity_type": "Abyss", "waystone_setup_saved": False,
                    "atlas_effect_snapshot": {"schema_version": 1, "activity_type": "Abyss", "totals": []}}
        with logger._connect() as db:
            context = json.loads(db.execute("SELECT snapshot_json FROM currency_snapshots").fetchone()[0])
            context.update(metadata)
            encoded = json.dumps(context)
            db.execute("UPDATE currency_snapshots SET snapshot_json=?", (encoded,))
            db.execute("UPDATE commits SET snapshot_json=?", (encoded,))
        _, rows = self.rows(logger.export_map_summary_csv())
        self.assertEqual((rows[0]["Activity Type"], rows[0]["Map Scanned"]), ("Abyss", "No"))
        self.assertEqual(json.loads(rows[0]["Atlas Effects JSON"]), metadata["atlas_effect_snapshot"])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM maps").fetchone()[0], 0)

    def test_sql_backup_and_workbook_context_keep_nested_json_and_boolean_missingness(self):
        """A standalone SQL reader gets the same map context and explanatory field dictionary."""
        logger.start_map()
        context = self.context("Expedition", False)
        self.replace_snapshot(context)
        backup = Path(self.temporary.name) / "map-context.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            fact = json.loads(db.execute("SELECT guidance FROM analysis_context_complete "
                                         "WHERE section='Map settings' AND topic='M0001'").fetchone()[0])
            self.assertEqual(fact["activity_type"], "Expedition")
            self.assertIs(fact["waystone_setup_saved"], False)
            self.assertEqual(fact["atlas_effect_snapshot"], context["atlas_effect_snapshot"])
            raw = json.loads(db.execute("SELECT snapshot_json FROM maps").fetchone()[0])
            self.assertEqual(raw["atlas_effect_snapshot"], context["atlas_effect_snapshot"])
            expected = [tuple(row) for row in db.execute("SELECT section,topic,guidance,context_version "
                                                       "FROM analysis_context_complete ORDER BY position,key")]
        workbook = self.workbook_rows(workbook_export.export_xlsx(), 4)
        self.assertEqual([tuple(row[name] for name in analysis_context.HEADERS) for row in workbook], expected)
        guides = {entry[0]: entry[3] for entry in analysis_context.COLUMN_GUIDANCE}
        self.assertIn("applied waystone scan", guides["field_map_scanned"])
        self.assertIn("Conditional coefficients", guides["field_atlas_effect_snapshot"])
        for key in ("activity_type", "waystone_setup_saved", "atlas_effect_snapshot"):
            context.pop(key)
        self.replace_snapshot(context)
        with logger._connect() as db:
            missing = json.loads(db.execute("SELECT guidance FROM analysis_context_map_values").fetchone()[0])
        for key in ("activity_type", "waystone_setup_saved", "atlas_effect_snapshot"):
            self.assertIsNone(missing[key])


    def test_all_allocated_atlas_snapshot_and_context_round_trip_without_excel_truncation(self):
        """Use the real catalog's largest tested context to catch XLSX's 32,767-character limit."""
        catalog = atlas_catalog.catalog()
        allocated = [key for key, node in catalog["nodes"].items() if node.get("allocatable")]
        choices = {key: catalog["nodes"][key]["choices"][0]["id"]
                   for key in allocated if catalog["nodes"][key].get("choices")}
        logger.save_atlas_settings({"catalog_version": catalog["version"], "allocated": allocated,
                                    "choices": choices, "gear_item_rarity": None})
        logger.save_settings({"activity_type": "Abyss", "city_type": "Vaal", "ocean": True, "deli": True})
        logger.start_map()
        # Keep the deliberate manual unknown after city classification on map creation.
        logger.save_settings({"biome": "None"})
        with logger._connect() as db:
            original = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
            guidance = db.execute("SELECT guidance FROM analysis_context_complete "
                                  "WHERE section='Map settings' AND topic='M0001'").fetchone()[0]
        self.assertEqual((original["activity_type"], original["biome"], original["city_type"], original["ocean"], original["deli"]),
                         ("Abyss", "None", "Vaal", "Yes", "Yes"))
        _, primary = self.rows(logger.export_primary_csv())
        snapshot_text = primary[0]["Atlas Effects JSON"]
        self.assertGreater(len(snapshot_text), 25000, "Stress the actual large compact aggregate")
        self.assertLessEqual(len(snapshot_text), 32767)
        self.assertLessEqual(len(guidance), 32767)
        self.assertEqual(json.loads(snapshot_text), original["atlas_effect_snapshot"])
        data = workbook_export.export_xlsx()
        excel = self.workbook_rows(data, 1)
        self.assertEqual(excel[0]["Atlas Effects JSON"], snapshot_text)
        self.assertEqual(json.loads(excel[0]["Atlas Effects JSON"]), original["atlas_effect_snapshot"])
        context_rows = self.workbook_rows(data, 4)
        map_guidance = next(row["Guidance"] for row in context_rows
                            if row["Section"] == "Map settings" and row["Topic"] == "M0001")
        self.assertEqual(map_guidance, guidance)
        self.assertEqual(json.loads(map_guidance)["atlas_effect_snapshot"], original["atlas_effect_snapshot"])

    def test_real_delirium_allocation_survives_sql_csv_and_excel_with_independent_activity(self):
        """The source coefficient remains conditional in all exports and is frozen with its Deli map."""
        node_id = "AtlasDeliriumSmall1"
        stat_id = "map_delirium_giga_mirror_chance_+%_at_max_depth"
        logger.save_atlas_settings({"allocated": [node_id]})
        logger.save_settings({"activity_type": "Expedition", "biome": "Desert", "deli": True})
        logger.start_map()
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 1}])
        # Future settings may differ; exports must describe the already-recorded map.
        logger.save_settings({"deli": False})
        with logger._connect() as db:
            original = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
        snapshot = original["atlas_effect_snapshot"]
        self.assertEqual((original["activity_type"], original["deli"], snapshot["deli"]),
                         ("Expedition", "Yes", "Yes"))
        self.assertEqual(original["atlas_settings"]["allocated"], [node_id])
        self.assertEqual(len(snapshot["totals"]), 1)
        coefficient = snapshot["totals"][0]
        self.assertEqual((coefficient["stat_id"], coefficient["raw_total"]), (stat_id, 5))
        self.assertIn("catalog_trigger", coefficient["conditions"])
        _, csv_maps = self.rows(logger.export_maps_csv())
        self.assertEqual(json.loads(csv_maps[0]["Atlas Effects JSON"]), snapshot)
        workbook = workbook_export.export_xlsx()
        self.assertEqual(json.loads(self.workbook_rows(workbook, 1)[0]["Atlas Effects JSON"]), snapshot)
        backup = Path(self.temporary.name) / "deli-context.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(backup)) as db:
            raw = json.loads(db.execute("SELECT snapshot_json FROM maps WHERE map_id='M0001'").fetchone()[0])
        self.assertEqual(raw["atlas_effect_snapshot"], snapshot)
        self.assertEqual(raw["atlas_settings"]["allocated"], [node_id])

    def test_large_delirium_context_guidance_round_trips_through_continuation_columns(self):
        """The previously truncated all-node Deli context remains exact JSON with ordinary map stats/perks."""
        catalog = atlas_catalog.catalog()
        allocated = [key for key, node in catalog["nodes"].items() if node.get("allocatable")]
        choices = {key: max(catalog["nodes"][key]["choices"],
                           key=lambda choice: len(json.dumps(choice.get("stats", {}))))["id"]
                   for key in allocated if catalog["nodes"][key].get("choices")}
        logger.save_atlas_settings({"catalog_version": catalog["version"], "allocated": allocated, "choices": choices})
        logger.save_settings({"activity_type": "Abyss", "city_type": "Vaal", "ocean": True, "deli": True,
                              "tier": 16, "waystone": 150, "map_mods": 8, "waystone_name": "Corrupted Waystone of Abundance",
                              "item_rarity": 123.5, "monster_rarity": 234.5, "pack_size": 45.5, "effectiveness": 178.5,
                              "irradiated": True, "wisp": True, "atlas_master": "Doryani",
                              "master_selections": {"Doryani": ["Evolutionary Pressure", "Remnants of Greatness",
                                                                  "Improved Calibration", "Mechanical Guardians"]}})
        logger.start_map()
        logger.save_settings({"biome": "None"})
        logger.finish_map(1250, 137, 23, 2)
        with logger._connect() as db:
            expected = [tuple(row) for row in db.execute("SELECT section,topic,guidance,context_version "
                                                        "FROM analysis_context_complete ORDER BY position,key")]
        map_guidance = next(row[2] for row in expected if row[:2] == ("Map settings", "M0001"))
        self.assertGreater(len(map_guidance), 32767, "Exercise the actual Deli-induced overflow")
        data = workbook_export.export_xlsx()
        rows = self.workbook_rows(data, 4)
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("xl/worksheets/sheet4.xml"))
        header_row = root.find(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row")
        headers = ["".join(cell.itertext()) for cell in header_row]
        self.assertEqual(headers[:4], list(analysis_context.HEADERS))
        self.assertEqual(headers[4:], ["Guidance continuation 1"])
        reconstructed = []
        for row in rows:
            self.assertTrue(all(len(value) <= 32767 for value in row.values()))
            guidance = row["Guidance"] + row["Guidance continuation 1"]
            reconstructed.append((row["Section"], row["Topic"], guidance, row["Context Version"]))
        self.assertEqual(reconstructed, expected)
        exported_map = next(row for row in rows if row["Section"] == "Map settings" and row["Topic"] == "M0001")
        self.assertEqual(exported_map["Guidance"], map_guidance[:32767])
        self.assertEqual(exported_map["Guidance continuation 1"], map_guidance[32767:])
        self.assertEqual(json.loads(exported_map["Guidance"] + exported_map["Guidance continuation 1"]),
                         json.loads(map_guidance))
        _, csv_rows = self.rows(logger.export_analysis_context_csv())
        self.assertEqual([tuple(row[name] for name in analysis_context.HEADERS) for row in csv_rows], expected)
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT section,topic,guidance,context_version "
                                                             "FROM analysis_context_complete ORDER BY position,key")], expected)


if __name__ == "__main__":
    unittest.main()
