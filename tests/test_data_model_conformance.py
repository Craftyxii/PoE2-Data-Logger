"""The logger records what the data model promises, read back from the real export sheets.

One scripted session (the same one tools/compare_logging_versions.py runs) covers an
Expedition map with remnants and propagation, a Ritual map with a Rite and a deferred
reward, Abyss and Breach maps, currency, a kill reading and Undo after + New map.
"""

import csv
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compare_logging_versions", ROOT / "tools" / "compare_logging_versions.py")
compare_logging_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_logging_versions)

SETUP = {"Tier": "16", "Waystone %": "87", "Map Mods": "5", "Item Rarity %": "40", "Monster Rarity %": "25",
         "Pack Size %": "15", "Effectiveness %": "30", "City Type": "None", "Ocean Map": "Yes",
         "Irradiated": "Yes", "Deli": "No", "Wisp": "Yes", "Atlas Master": "Jado"}


class DataModelConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="poe2-conformance-") as folder:
            cls.session = compare_logging_versions.run(ROOT, folder)

    def sheet(self, name):
        return list(csv.DictReader(io.StringIO(self.session["exports"][name])))

    def test_every_step_of_the_session_was_accepted(self):
        self.assertEqual([result for _, result in self.session["steps"]], ["ok"] * len(self.session["steps"]))

    def test_each_map_id_bundles_its_setup_activity_and_biome(self):
        maps = {row["Map ID"]: row for row in self.sheet("export_maps_csv")}
        # Undo after + New map leaves no gap in the IDs.
        self.assertEqual(list(maps), ["M0001", "M0002", "M0003", "M0004"])
        for map_id, (activity, biome) in {"M0001": ("Expedition", "Forest"), "M0002": ("Ritual", "Grass"),
                                         "M0003": ("Abyss", "Desert"), "M0004": ("Breach", "Mountain")}.items():
            with self.subTest(map_id=map_id):
                row = maps[map_id]
                self.assertEqual({key: row[key] for key in SETUP}, SETUP)
                self.assertEqual((row["Activity Type"], row["Biome"]), (activity, biome))
                self.assertTrue(row["Atlas Setup ID"])

    def test_ritual_logs_every_page_item_roll_count_and_tribute_except_deferred(self):
        rewards = self.sheet("export_ritual_csv")
        self.assertEqual([(row["Map ID"], row["Ritual Page"], row["Name"], row["Quantity"], row["Tribute"],
                           row["Ritual Tribute Available"], row["Ritual Rerolls Remaining"]) for row in rewards],
                         [("M0002", "1", "Omen of Whittling", "1", "900", "5000", "6"),
                          ("M0002", "2", "Divine Orb", "1", "2500", "4100", "5")])
        self.assertNotIn("Chaos Orb", [row["Name"] for row in rewards])
        ritual_map = next(row for row in self.sheet("export_maps_csv") if row["Map ID"] == "M0002")
        self.assertEqual((ritual_map["Ritual Reading Count"], ritual_map["Ritual First Tribute Available"],
                          ritual_map["Ritual Final Tribute Available"], ritual_map["Ritual First Rerolls Remaining"],
                          ritual_map["Ritual Final Rerolls Remaining"]), ("2", "5000", "4100", "6", "5"))

    def test_rite_fields_belong_to_the_ritual_map_only(self):
        maps = {row["Map ID"]: row for row in self.sheet("export_maps_csv")}
        self.assertEqual((maps["M0002"]["Ritual Rite Name"], maps["M0002"]["Ritual Rite Map Ordinal"]),
                         ("Comparison Rite", "1"))
        for map_id in ("M0001", "M0003", "M0004"):
            self.assertEqual(maps[map_id]["Ritual Rite Name"], "")

    def test_expedition_logs_each_remnant_and_its_propagated_runes_under_one_expedition(self):
        rows = self.sheet("export_primary_csv")
        remnants = [row for row in rows if row["Type"] == "Remnant"]
        chain = [row for row in rows if row["Type"] == "Chain"]
        self.assertEqual(sorted({row["Remnant ID"] for row in remnants}), ["R0001", "R0002"])
        self.assertTrue(all(row["Exact Rune Combo"] for row in remnants))
        self.assertEqual([(row["Chain Step #"], row["Propagation Rune 1"], row["Propagation Rune 2"]) for row in chain],
                         [("1", "Rage", "Time"), ("2", "Rage", "")])
        self.assertEqual({row["Expedition ID"] for row in remnants + chain}, {"M0001-E01"})

    def test_currency_counts_start_and_end_and_excludes_what_was_brought_in(self):
        rows = {(row["Map ID"], row["Currency"]): row for row in self.sheet("export_currency_csv")}
        chaos = rows[("M0001", "Chaos Orb")]
        self.assertEqual((chaos["Start Count"], chaos["End Count"], chaos["Net Change"]), ("5", "17", "12"))
        self.assertEqual(rows[("M0001", "Divine Orb")]["Net Change"], "2")
        # Abyss and Breach maps log currency only, from an assumed-empty start.
        self.assertEqual(rows[("M0003", "Exalted Orb")]["Net Change"], "4")
        self.assertEqual(rows[("M0004", "Regal Orb")]["Net Change"], "6")
        types = {row["Map ID"]: set() for row in self.sheet("export_record_history_csv")}
        for row in self.sheet("export_record_history_csv"):
            types[row["Map ID"]].add(row["Type"])
        for map_id in ("M0003", "M0004"):
            self.assertFalse(types[map_id] & {"Remnant", "Chain", "Ritual"}, map_id)

    def test_kill_reading_is_kept_and_sets_the_map_kill_totals(self):
        reading = self.sheet("kill_counts.export_csv")
        self.assertEqual([(row["Map ID"], row["Normal Kills (Observed)"], row["Magic Kills (Observed)"],
                           row["Rare Kills (Observed)"], row["Unique Kills (Observed)"]) for row in reading],
                         [("M0001", "300", "40", "9", "1")])
        totals = next(row for row in self.sheet("export_record_history_csv")
                      if row["Map ID"] == "M0001" and row["Type"] == "Map totals")
        self.assertEqual((totals["Normal Kills (Map)"], totals["Magic Kills (Map)"], totals["Rare Kills (Map)"]),
                         ("300", "40", "9"))


if __name__ == "__main__":
    unittest.main()
