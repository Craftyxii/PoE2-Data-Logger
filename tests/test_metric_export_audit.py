"""Check user-entered metrics through the real CSV and XLSX exporters."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export


def atlas_fixture():
    """Build three Atlas nodes with ordinary stats, ordered choices, and a Ritual effect."""
    return {"version": "metric-audit-1", "nodes": {
        "0007": {"name": "Map Rarity", "activity": "Main Atlas", "allocatable": True,
                 "stats": {"item_rarity": 5, "zero_stat": 0}, "effects": ["5% map rarity"]},
        "0008": {"name": "Expedition Choice", "activity": "Expedition", "allocatable": True,
                 "stats": {"base_stat": 3}, "effects": ["Choose an effect"], "choices": [
                     {"id": "001", "name": "First effect", "stats": {"choice_stat": 7},
                      "effects": ["7% chosen effect"]},
                     {"id": "002", "name": "Second effect", "stats": {"other_choice": 11},
                      "effects": ["11% other effect"]}]},
        "0009": {"name": "Ritual Tribute", "activity": "Ritual", "allocatable": True,
                 "stats": {"tribute": 2}, "effects": ["2% tribute"]},
    }}


class MetricExportAuditTests(unittest.TestCase):
    """Trace entered map, Atlas, currency, Ritual, and kill metrics through CSV/XLSX cells."""
    def setUp(self):
        """Patch a synthetic Atlas catalog and initialize an isolated first-map database."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-metric-export-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        self.catalog = atlas_fixture()
        self.catalog_patch = patch.object(logger, "_atlas_catalog_data", side_effect=lambda: self.catalog)
        self.identity_patch = patch.object(logger, "_atlas_catalog_identity", side_effect=self.identity)
        self.catalog_patch.start()
        self.identity_patch.start()
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Stop catalog patches, restore logger storage, and remove the temporary database."""
        self.identity_patch.stop()
        self.catalog_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def identity(self):
        """Serialize the current fixture catalog and return its SHA-256 identity."""
        encoded = json.dumps(self.catalog, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest(), encoded

    def csv_rows(self, data):
        """Validate unique CSV headers and row widths, then return named data rows."""
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])), "Column names must be unique")
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows), "Every cell must keep its column")
        return [dict(zip(rows[0], row)) for row in rows[1:]]

    def workbook_rows(self, number=3):
        """Read XLSX sheet values and cell types while checking unique headers and no formulas."""
        with ZipFile(io.BytesIO(workbook_export.export_xlsx())) as archive:
            self.assertIsNone(archive.testzip())
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        ns = {"s": workbook_export.NS}
        rows = root.findall("s:sheetData/s:row", ns)
        headers = {cell.get("r").rstrip("0123456789"): "".join(cell.itertext()) for cell in rows[0]}
        self.assertEqual(len(headers), len(set(headers.values())))
        result = []
        types = []
        for row in rows[1:]:
            values = {name: "" for name in headers.values()}
            row_types = {}
            for cell in row:
                name = headers[cell.get("r").rstrip("0123456789")]
                values[name] = "".join(cell.itertext())
                row_types[name] = cell.get("t")
                self.assertIsNone(cell.find("s:f", ns), "User data must not become a formula")
            result.append(values)
            types.append(row_types)
        return result, types

    def save_atlas(self, *, allocated=("0007", "0008"), rarity=0):
        """Save fixture Atlas allocations, its first choice, and the supplied gear rarity."""
        logger.save_atlas_settings({"catalog_version": self.catalog["version"],
                                   "allocated": list(allocated), "choices": {"0008": "001"},
                                   "gear_item_rarity": rarity})

    def configure_map(self):
        """Save distinct map/tablet/master metrics and return their config and commit number."""
        pairs = []
        for tablet in range(1, 5):
            for slot, unit in enumerate(("%", "count", "seconds", "flag"), 1):
                name = ("Map has additional random Modifiers" if slot == 2 else
                        f"Audit tablet {tablet} {('rarity', 'count', 'duration', 'enabled')[slot - 1]}")
                logger.add_affix(name)
                amount = tablet + 1 if slot == 2 else 1 if slot == 4 else tablet * 10 + slot
                pairs.append({"affix": name, "value": amount, "unit": unit})
        state = logger.get_state()
        selections = {}
        for master, perks in state["masters"].items():
            names = [perk["name"] for perk in perks]
            if master == "Jado":
                names = ["Unexpected Missions", *(name for name in names if name != "Unexpected Missions")]
            selections[master] = names[:4]
        config = {"tier": 16, "waystone": 85.5, "map_mods": 7,
                  "item_rarity": 101.5, "monster_rarity": 202.5,
                  "pack_size": 303.5, "effectiveness": 404.5,
                  "waystone_name": "Audit Waystone", "biome": "Swamp", "city_type": "Vaal",
                  "ocean": True, "irradiated": True, "deli": True, "wisp": True,
                  "aldur": "All +7", "atlas_master": "Jado", "master_selections": selections,
                  "tablets_used": 4, "tablet_affixes": pairs,
                  "tablet_raw_mods": [[f"Tablet {number} original modifier"] for number in range(1, 5)],
                  "waystone_mods": [f"Map modifier {number}" for number in range(1, 11)]}
        logger.save_settings(config)
        return config, logger.record_commit("Map settings")

    def test_all_map_tablet_and_master_fields_reach_their_own_cells(self):
        """Verify all map tablet and master fields reach their own cells."""
        self.save_atlas()
        config, number = self.configure_map()
        expected = {"Map ID": "M0001", "Expedition ID": "M0001-E01", "Expedition #": "1",
                    "Tier": "16", "Area Level": "82", "Waystone %": "85.5",
                    "Base Map Mods": "7", "Map Mods": "8", "Tablet Mods": "14",
                    "Total Mods": "22", "# +2 Mod Tablets": "1", "Master +Mods": "1",
                    "Tablets Used": "4", "Tablet Slot Capacity": "4", "Item Rarity %": "101.5",
                    "Monster Rarity %": "202.5", "Pack Size %": "303.5", "Effectiveness %": "404.5",
                    "Waystone Name": "Audit Waystone", "Biome": "Swamp", "City Type": "Vaal",
                    "Ocean Map": "Yes", "Irradiated": "Yes", "Deli": "Yes", "Wisp": "Yes",
                    "Atlas Master": "Jado", "Aldur's Saga Affix": "All +7", "Gear Item Rarity %": "0"}
        for index, text in enumerate(config["waystone_mods"], 1):
            expected[f"Map Mod {index}"] = text
        for master, perks in config["master_selections"].items():
            for index, name in enumerate(perks, 1):
                expected[f"{master} Configured Perk {index}"] = name
                if master == "Jado":
                    expected[f"Perk {index}"] = name
        for index, pair in enumerate(config["tablet_affixes"]):
            tablet, slot = index // 4 + 1, index % 4 + 1
            prefix = f"Tablet {tablet} Mod {slot}"
            expected.update({prefix + " Affix": pair["affix"], prefix + " Value": str(pair["value"]),
                             prefix + " Unit": pair["unit"],
                             prefix + " %": str(pair["value"]) if pair["unit"] == "%" else ""})
        for tablet in range(1, 5):
            expected[f"Tablet {tablet} Random Modifiers"] = str(tablet + 1)
            expected[f"Tablet {tablet} Modifiers"] = f"Tablet {tablet} original modifier"
        rows = self.csv_rows(logger.export_all_csv())
        workbook, types = self.workbook_rows()
        csv_row = next(row for row in rows if row["Scan Commit #"] == str(number))
        excel_index = next(index for index, row in enumerate(workbook) if row["Scan Commit #"] == str(number))
        for header, value in expected.items():
            with self.subTest(column=header):
                self.assertEqual(csv_row[header], value)
                self.assertEqual(workbook[excel_index][header], value)
        for header in ("Tier", "Tablet 1 Mod 2 Value", "Gear Item Rarity %", "Tablet Slot Capacity"):
            self.assertIsNone(types[excel_index][header])

    def test_currency_removed_stacks_and_ritual_metrics_preserve_zero_and_blank(self):
        """Verify currency removed stacks and Ritual metrics preserve zero and blank."""
        self.save_atlas()
        logger.add_item_name("Audit Equipment")
        logger.save_currency_snapshot("start", [{"name": "Chaos Orb", "quantity": 3},
            {"name": "Chaos Orb", "quantity": 2}, {"name": "Audit Equipment", "quantity": 7}])
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 0},
                                             {"name": "Exalted Orb", "quantity": 9}])
        ritual = logger.save_ritual_page([
            {"category": "Item", "name": "Audit Equipment", "quantity": 3, "tribute": 0,
             "source": "First reward", "deferred": False},
            {"category": "Item", "name": "Audit Equipment", "quantity": 2, "tribute": 999,
             "source": "Deferred reward", "deferred": True},
            {"category": "Omen", "name": "Audit Omen", "quantity": 1, "tribute": None,
             "source": "Unclear tribute", "deferred": False}], tribute_available=1200, rerolls_remaining=0)
        main = self.csv_rows(logger.export_all_csv())
        currency = self.csv_rows(logger.export_currency_csv())
        for name, start, end, net in (("Chaos Orb", "5", "0", "-5"),
                                      ("Audit Equipment", "7", "0", "-7"),
                                      ("Exalted Orb", "0", "9", "9")):
            row = next(row for row in currency if row["Currency"] == name)
            self.assertEqual([row[key] for key in ("Start Count", "End Count", "Net Change")], [start, end, net])
            found = next(row for row in main if row["Inventory Phase"] == "end" and
                         (row["Currency"] == name or row["Item Name"] == name))
            self.assertEqual([found[key] for key in ("Start Count", "End Count", "Net Change")], [start, end, net])
        ritual_rows = [row for row in main if row["Scan Commit #"] == str(ritual["scan_commit_number"])]
        self.assertEqual(len(ritual_rows), 3)
        expected = [("False", "3", "0", "3"), ("True", "2", "999", "0"), ("False", "1", "", "1")]
        for row, values in zip(ritual_rows, expected):
            self.assertEqual(tuple(row[key] for key in ("Deferred", "Quantity", "Tribute", "New Find Quantity")), values)
            self.assertEqual(row["Ritual Tribute Available"], "1200")
            self.assertEqual(row["Ritual Rerolls Remaining"], "0")
        self.assertEqual(ritual_rows[0]["Item: Audit Equipment"], "3")
        self.assertEqual(ritual_rows[1]["Item: Audit Equipment"], "0")
        separate = self.csv_rows(logger.export_ritual_csv())
        self.assertEqual([row["Tribute"] for row in separate], ["0", "999", ""])
        self.assertEqual([row["New Find Quantity"] for row in separate], ["3", "0", "1"])
        workbook, _ = self.workbook_rows()
        for row in main:
            identity = (row["Scan Commit #"], row["Currency"], row["Item Name"], row["OCR Source"])
            found = next(item for item in workbook if
                         (item["Scan Commit #"], item["Currency"], item["Item Name"], item["OCR Source"]) == identity)
            for header in ("Quantity", "Start Count", "End Count", "Net Change", "Tribute",
                           "Ritual Tribute Available", "Ritual Rerolls Remaining", "New Find Quantity"):
                self.assertEqual(found[header], row[header])

    def test_compact_atlas_setups_keep_allocations_choices_and_frozen_catalog_stats(self):
        """Verify compact Atlas setups keep allocations choices and frozen catalog stats."""
        self.save_atlas(allocated=("0007",))
        logger.commit_chain("Rage")
        original = logger._atlas_snapshot(logger.get_state()["settings"])["atlas_setup_id"]
        self.catalog = copy.deepcopy(self.catalog)
        self.catalog["version"] = "metric-audit-2"
        self.catalog["nodes"]["0007"]["stats"] = {"item_rarity": 15, "future_stat": 17}
        self.save_atlas(rarity=None)
        rows = self.csv_rows(logger.export_atlas_csv())
        self.assertEqual(len(rows), 2, "Each referenced setup gets one row")
        self.assertEqual(len({row["Atlas Setup ID"] for row in rows}), 2)
        choice_node = "Atlas Node: Expedition | Expedition Choice [0008]"
        choice = "Atlas Choice: Expedition | Expedition Choice [0008]"
        ritual_node = "Atlas Node: Ritual | Ritual Tribute [0009]"
        old = next(row for row in rows if row["Atlas Setup ID"] == original)
        self.assertEqual(old["Gear Item Rarity %"], "0")
        self.assertEqual((old[choice_node], old[choice], old[ritual_node]), ("No", "1", "No"))
        revised = next(row for row in rows if row["Atlas Setup ID"] != original)
        self.assertEqual((revised[choice_node], revised[choice], revised[ritual_node]), ("Yes", "1", "No"))
        self.assertEqual(revised["Gear Item Rarity %"], "")
        self.assertNotEqual(old["Atlas Catalog ID"], revised["Atlas Catalog ID"])
        self.assertNotIn("Atlas Node: Main Atlas | Map Rarity [0007]", old,
                         "Ordinary nodes checked in every setup need no column")
        self.assertEqual(len(old), 8, "Compact export uses five metadata and three dynamic columns")
        # Stats and their option IDs remain in each immutable dataset. The
        # compact sheet stores allocation plus option number instead of
        # repeating hundreds of stat columns for every node.
        with logger._connect() as db:
            saved = {row["setup_id"]: (json.loads(row["settings_json"]), json.loads(row["catalog_json"]))
                     for row in db.execute("SELECT s.setup_id,s.settings_json,c.catalog_json "
                         "FROM atlas_setups s JOIN atlas_catalogs c ON c.catalog_id=s.catalog_id")}
        old_settings, old_catalog = saved[original]
        new_settings, new_catalog = saved[revised["Atlas Setup ID"]]
        self.assertEqual(old_catalog["nodes"]["0007"]["stats"], {"item_rarity": 5, "zero_stat": 0})
        self.assertEqual(old_catalog["nodes"]["0007"]["effects"], ["5% map rarity"])
        self.assertNotIn("future_stat", old_catalog["nodes"]["0007"]["stats"])
        self.assertEqual(new_catalog["nodes"]["0007"]["stats"], {"item_rarity": 15, "future_stat": 17})
        self.assertIn("0007", old_settings["allocated"])
        self.assertNotIn("0008", old_settings["allocated"])
        self.assertIn("0008", new_settings["allocated"])
        for settings, catalog in ((old_settings, old_catalog), (new_settings, new_catalog)):
            self.assertEqual(settings["choices"]["0008"], "001")
            node = catalog["nodes"]["0008"]
            self.assertEqual(node["stats"], {"base_stat": 3})
            self.assertEqual([option["id"] for option in node["choices"]], ["001", "002"])
            self.assertEqual(node["choices"][0]["stats"], {"choice_stat": 7})
            self.assertEqual(node["choices"][0]["effects"], ["7% chosen effect"])
            self.assertEqual(node["choices"][1]["stats"], {"other_choice": 11})
        workbook, types = self.workbook_rows(2)
        for row in rows:
            index = next(index for index, item in enumerate(workbook) if item["Atlas Setup ID"] == row["Atlas Setup ID"])
            self.assertEqual(workbook[index], row)
            self.assertEqual(types[index]["Atlas Catalog ID"], "inlineStr")
            self.assertEqual(types[index][choice_node], "inlineStr")
            self.assertIsNone(types[index][choice])

    def test_primary_map_fields_and_item_totals_are_logged_once_with_recipe_rows(self):
        """Verify primary map fields and item totals are logged once with recipe rows."""
        self.save_atlas()
        config, _ = self.configure_map()
        logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)
        logger.save_currency_snapshot("end", [{"name": "Chaos Orb", "quantity": 9},
                                              {"name": "Exalted Orb", "quantity": 4}])
        primary = self.csv_rows(logger.export_primary_csv())
        self.assertGreater(len(primary), 1)
        self.assertTrue(all(row["Map ID"] == "M0001" for row in primary))
        first = primary[0]
        self.assertEqual((first["Chaos Orb"], first["Exalted Orb"]), ("9", "4"))
        self.assertEqual(first["Map Modifiers"], "\n".join(config["waystone_mods"]))
        for header in ("Chaos Orb", "Exalted Orb", "Map Modifiers", "Tablet 1 Mod 1 Value"):
            self.assertTrue(first[header], header)
            self.assertTrue(all(row[header] == "" for row in primary[1:]), header)
        self.assertNotIn("Map Mods", first)
        self.assertNotIn("Waystone %", first)
        workbook, types = self.workbook_rows(1)
        self.assertEqual(workbook, primary)
        for header in ("Chaos Orb", "Exalted Orb", "Tablet 1 Mod 1 Value"):
            self.assertIsNone(types[0][header])

    def test_propagation_two_runes_count_one_scan_and_chain_keeps_order(self):
        """Verify propagation two runes count one scan and chain keeps order."""
        self.save_atlas()
        captured = []
        for runes, recipe in ((["Death", "Time"], "Audit Recipe A"),
                              (["Power"], "Audit Recipe B"), (["Opulent"], "Audit Recipe C")):
            captured.append(logger.increment_propagation_detonated(logger.scan_context(),
                                                                   runes=runes, recipe=recipe))
        self.assertEqual([result["detonated"] for result in captured], [1, 2, 3])
        chain = logger.commit_chain_draft([{"rune1": "Death", "rune2": "Time"},
                                          {"rune1": "Power", "rune2": ""},
                                          {"rune1": "Opulent", "rune2": ""}],
                                         expected_context=logger.scan_context())
        self.assertEqual(chain["expedition_id"], "M0001-E01")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        complete = logger.complete_chain(logger.scan_context())
        self.assertEqual(complete["next_expedition_id"], "M0001-E02")
        main = self.csv_rows(logger.export_all_csv())
        propagation = [row for row in main if row["Type"] == "Propagation"]
        self.assertEqual([row["Matched Recipe"] for row in propagation],
                         ["Audit Recipe A", "Audit Recipe B", "Audit Recipe C"])
        self.assertEqual([(row["Propagation Rune 1"], row["Propagation Rune 2"]) for row in propagation],
                         [("Death", "Time"), ("Power", ""), ("Opulent", "")])
        self.assertEqual([row["Remnants Detonated (Expedition)"] for row in propagation], ["1", "2", "3"])
        self.assertEqual([row["Remnants Detonated (Scan)"] for row in propagation], ["1", "1", "1"])
        chain_rows = [row for row in main if row["Type"] == "Chain"]
        self.assertEqual([row["Chain Step #"] for row in chain_rows], ["1", "2", "3"])
        self.assertEqual([row["Propagation Rune 1"] for row in chain_rows], ["Death", "Power", "Opulent"])
        self.assertEqual([row["Remnants Detonated (Expedition)"] for row in chain_rows], ["3"] * 3)
        self.assertTrue(all(row["Remnants Detonated (Scan)"] == "" for row in chain_rows))
        self.assertTrue(all((row["Map ID"], row["Expedition ID"], row["Expedition #"]) ==
                            ("M0001", "M0001-E01", "1") for row in propagation + chain_rows))
        fourth = logger.increment_propagation_detonated(logger.scan_context(), runes=["Rage"], recipe="Next recipe")
        self.assertEqual((fourth["expedition_id"], fourth["detonated"]), ("M0001-E02", 1))
        map_row = self.csv_rows(logger.export_maps_csv())[0]
        self.assertEqual((map_row["Expedition 1 Detonated"], map_row["Expedition 2 Detonated"]), ("3", "1"))
        workbook, types = self.workbook_rows()
        for index, row in enumerate(workbook):
            if row["Type"] == "Propagation":
                self.assertIsNone(types[index]["Remnants Detonated (Scan)"])
                self.assertIsNone(types[index]["Remnants Detonated (Expedition)"])

    def test_four_kill_counts_keep_map_identity_and_unknown_is_not_zero(self):
        """Verify four kill counts keep map identity and unknown is not zero."""
        self.save_atlas()
        first = logger.save_counts(10, 2, 3, unique=4)
        zero = logger.save_counts(0, 0, 0, unique=0)
        unknown = logger.save_counts("", "", "", unique="")
        logger.save_counts(10, 2, 3, unique=4)
        logger.finish_map(10, 2, 3, unique=4)
        logger.start_map()
        second = logger.save_counts("", "", "", unique=7)
        main = self.csv_rows(logger.export_all_csv())
        for commit, expected in ((first, ("10", "2", "3", "4", "19")),
                                 (zero, ("0", "0", "0", "0", "0")),
                                 (unknown, ("", "", "", "", "")),
                                 (second, ("", "", "", "7", "7"))):
            row = next(row for row in main if row["Scan Commit #"] == str(commit["scan_commit_number"]))
            self.assertEqual(tuple(row[key] for key in ("Normal Kills (Map)", "Magic Kills (Map)",
                "Rare Kills (Map)", "Unique Kills (Map)", "Total Kills")), expected)
            self.assertEqual(row["Map ID"], commit["map_id"])
        maps = {row["Map ID"]: row for row in self.csv_rows(logger.export_maps_csv())}
        self.assertEqual(tuple(maps["M0001"][key] for key in
                               ("Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills")),
                         ("10", "2", "3", "4", "19"))
        self.assertEqual(maps["M0002"]["Unique Kills"], "7")
        self.assertEqual(maps["M0002"]["Normal Kills"], "")
        workbook, types = self.workbook_rows()
        for index, row in enumerate(workbook):
            if row["Total Kills"]:
                self.assertIsNone(types[index]["Total Kills"])
            if row["Unique Kills (Map)"]:
                self.assertIsNone(types[index]["Unique Kills (Map)"])

    def test_remnant_family_and_seed_metrics_have_distinct_columns(self):
        """Verify remnant family and seed metrics have distinct columns."""
        self.save_atlas()
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 ORDER BY sockets DESC LIMIT 1").fetchone())
        saved = logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3,
            visible_seed={"sockets": stage["sockets"], "slot": stage["seed_slot"],
                          "rune": stage["seed_rune"], "mode": "seed"})
        main = self.csv_rows(logger.export_all_csv())
        rows = [row for row in main if row["Scan Commit #"] == str(saved["scan_commit_number"])]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["Family ID"], "3")
            self.assertEqual(row["Visible Seed Sockets"], str(stage["sockets"]))
            self.assertEqual(row["Visible Seed Slot"], stage["seed_slot"])
            self.assertEqual(row["Visible Seed Rune"], stage["seed_rune"])
            self.assertEqual(row["Remnant Scan Mode"], "seed")
            self.assertEqual((row["Map ID"], row["Expedition ID"], row["Remnant ID"]),
                             (saved["map_id"], saved["expedition_id"], saved["remnant_id"]))
            self.assertEqual(int(row["Socket Count"]), len(row["Exact Rune Combo"].split(" + ")))
        self.assertEqual([row["Matched Recipe"] for row in rows],
                         [entry["recipe"] for entry in logger.resolve(
                             "Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)["rows"]])


if __name__ == "__main__":
    unittest.main()
