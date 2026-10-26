"""Analysis Context explains every affix in the Affix DB and every bundled Rite affix."""

from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PoE2_Data_Logger.core import analysis_context, logger_store as logger, ritual_rites_catalog, store
from PoE2_Data_Logger.ocr.affix_capture import affix_catalog


class AnalysisContextAffixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-context-affixes-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def rows(self, section):
        context = csv.DictReader(io.StringIO(logger.export_analysis_context_csv().decode("utf-8-sig")))
        return {row["Topic"]: json.loads(row["Guidance"]) for row in context if row["Section"] == section}

    def test_every_affix_db_name_has_exactly_one_definition(self):
        names = logger.get_state()["affixes"]
        definitions = self.rows("Affix definition")
        self.assertEqual(sorted(definitions), sorted(names))
        self.assertGreaterEqual(len(definitions), len(affix_catalog()))
        for name, row in definitions.items():
            with self.subTest(affix=name):
                self.assertEqual(row["affix"], name)
                self.assertIn(row["unit"], ("%", "count", "seconds", "flag"))
                self.assertTrue(row["unit_meaning"] and row["source"] and row["recorded_in"])

    def test_units_follow_the_catalog_the_scan_and_the_wording(self):
        logger.add_affixes(["Map has a Learned Test Shrine", "Learned additional Test Chests"],
                           units={"Map has a Learned Test Shrine": "flag"})
        definitions = self.rows("Affix definition")
        catalog = next(item for item in affix_catalog() if item["unit"] == "count")
        self.assertEqual((definitions[catalog["name"]]["unit"], definitions[catalog["name"]]["unit_basis"],
                          definitions[catalog["name"]]["appears_on"]),
                         ("count", "bundled modifier catalog", sorted(catalog["kinds"])))
        self.assertIn("PoE2DB", definitions[catalog["name"]]["source"])
        learned = definitions["Map has a Learned Test Shrine"]
        self.assertEqual((learned["unit"], learned["unit_basis"], learned["appears_on"]),
                         ("flag", "scanned unit kept when the name was learned", []))
        self.assertIn("has no size", learned["unit_meaning"])
        self.assertEqual((definitions["Learned additional Test Chests"]["unit"],
                          definitions["Learned additional Test Chests"]["unit_basis"]),
                         ("count", "inferred from the wording"))
        # An older local spelling of a catalog modifier is matched to its catalog entry.
        variant = definitions["Increased Gold Found In Map"]
        self.assertEqual((variant["catalog_name"], variant["appears_on"]), ("increased Gold found in Map", ["tablet"]))
        self.assertEqual(definitions["Rare Monster%"]["unit_basis"], "percent by default")

    def test_a_name_learned_or_removed_later_follows_the_affix_db(self):
        logger.add_affix("Typed Test Affix")
        self.assertIn("Typed Test Affix", self.rows("Affix definition"))
        logger.add_affixes(["increased Typed Test Rarity"])
        self.assertEqual(self.rows("Affix definition")["increased Typed Test Rarity"]["unit"], "%")
        logger.undo_last_action()
        self.assertNotIn("increased Typed Test Rarity", self.rows("Affix definition"))
        self.assertEqual(sorted(self.rows("Affix definition")), sorted(logger.get_state()["affixes"]))

    def test_tablet_use_counts_active_slots_by_exact_name(self):
        name = next(item["name"] for item in affix_catalog() if item["unit"] == "%" and "tablet" in item["kinds"])
        pairs = [{"affix": name, "value": 10, "unit": "%"}, {"affix": name, "value": 5, "unit": "%"}]
        pairs += [{"affix": "", "value": None, "unit": "%"}] * 2 + [{"affix": name, "value": 99, "unit": "%"}] * 4
        logger.finish_map(None, None, None)
        logger.start_map()
        for map_id in ("M0001", "M0002"):
            with logger._connect() as db:
                saved = logger._load(db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()[0])
                saved.update({"tablet_capacity": 1, "tablets_used": 1, "tablet_affixes": pairs})
                db.execute("UPDATE maps SET snapshot_json=? WHERE map_id=?", (logger._dump(saved), map_id))
        row = self.rows("Affix definition")[name]
        self.assertEqual((row["tablet_maps"], row["tablet_slots"]), (2, 4))
        self.assertIn("describe use, not effect strength", row["scope"])

    def test_every_bundled_rite_affix_has_a_definition(self):
        definitions = {row["id"]: row for row in self.rows("Rite affix definition").values()}
        bundled = ritual_rites_catalog.affixes()
        self.assertEqual(sorted(definitions), sorted(affix["id"] for affix in bundled))
        for affix in bundled:
            with self.subTest(affix=affix["id"]):
                row = definitions[affix["id"]]
                self.assertEqual((row["text"], row["kind"], row["stat_id"], row["value"], row["unit"]),
                                 (affix["text"], affix["kind"], affix["stat_id"], affix["value"], affix["unit"]))
                self.assertEqual(row["proliferates"], affix["kind"] == "proliferation")
                self.assertTrue(row["source_urls"])
        proliferation = next(row for row in definitions.values() if row["kind"] == "proliferation")
        self.assertIn("other remaining maps", proliferation["effect_scope"])
        self.assertTrue(proliferation["inherited_text"])
        raw = [row for row in definitions.values() if row["translation_status"] == "raw_untranslated"]
        self.assertEqual(len(raw), 3)
        self.assertTrue(all("without a translated effect" in row["translation_note"] for row in raw))

    def test_a_standalone_sqlite_export_carries_the_same_definitions(self):
        logger.add_affixes(["Map has a Learned Test Shrine"], units={"Map has a Learned Test Shrine": "flag"})
        expected = self.rows("Affix definition")
        path = Path(self.temporary.name) / "export.sqlite3"
        path.write_bytes(logger.backup_bytes())
        with closing(sqlite3.connect(path)) as exported:
            exported_rows = {topic: json.loads(guidance) for topic, guidance in exported.execute(
                "SELECT topic,guidance FROM analysis_context_complete WHERE section='Affix definition'")}
        self.assertEqual(exported_rows, expected)
        self.assertEqual(analysis_context.CONTEXT_VERSION, "10")


if __name__ == "__main__":
    unittest.main()
