"""Exercise selected tablet sets, Rite origins and resource boundaries together across maps."""

from collections import defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, store, workbook_export
from PoE2_Data_Logger.core.ritual_rites_catalog import affixes


class TabletRiteMapProjectionTests(unittest.TestCase):
    """Keep an independent twenty-map ledger while using the real persistence/export APIs."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-tablet-rite-projection-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name) / "profile"
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def csv_rows(self, data):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows[0]), len(set(rows[0])))
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        return rows[0], [dict(zip(rows[0], row)) for row in rows[1:]]

    def saved_rows(self, map_id):
        """Fingerprint complete historical rows instead of reconstructing them from current settings."""
        with logger._connect() as db:
            maps = [tuple(row) for row in db.execute("SELECT * FROM maps WHERE map_id=?", (map_id,))]
            commits = [tuple(row) for row in db.execute(
                "SELECT * FROM commits WHERE map_id=? ORDER BY number", (map_id,))]
        return hashlib.sha256(json.dumps([maps, commits], ensure_ascii=False).encode()).hexdigest()

    def workbook_rows(self, data, number):
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
        raw_rows = []
        for row in root.findall(f"{{{workbook_export.NS}}}sheetData/{{{workbook_export.NS}}}row"):
            cells = {}
            for cell in row:
                cells[workbook_export._col_number(cell.get("r")) - 1] = "".join(cell.itertext())
                if cell.get("t") == "inlineStr":
                    self.assertLessEqual(len("".join(cell.itertext())), 32767)
            raw_rows.append(cells)
        headers = [raw_rows[0].get(index, "") for index in range(max(raw_rows[0]) + 1)]
        self.assertEqual(len(headers), len(set(headers)))
        return headers, [{header: row.get(index, "") for index, header in enumerate(headers)}
                         for row in raw_rows[1:]]

    @staticmethod
    def joined_json(row, field, additional_headers):
        pattern = re.compile(re.escape(field) + r" continuation (\d+)$")
        pieces = [(int(match.group(1)), name) for name in additional_headers
                  if (match := pattern.search(name))]
        return row[field] + "".join(row[name] for _, name in sorted(pieces))

    def test_twenty_maps_keep_selected_sets_same_rite_sources_and_latest_resource_readings(self):
        catalog = list(affixes())
        local = next(row for row in catalog if row["kind"] == "local")
        bounty = next(row for row in catalog if row["kind"] == "bounty")
        proliferations = [row for row in catalog if row["kind"] == "proliferation"]
        percent = next(name for name in logger.get_state()["affixes"] if "Pack Size" in name)
        sets, library_commits = [], []
        for index, amount in enumerate((11, 37)):
            if index:
                logger.create_tablet_set("Second activity set")
            selected = logger.get_state()["selected_tablet_set_id"]
            logger.save_settings({"tablets_used": 1,
                                  "tablet_affixes": [{"affix": percent, "value": amount}] +
                                  [{"affix": "", "value": None} for _ in range(15)],
                                  "tablet_raw_mods": [[f"{amount}% increased Pack Size"], [], [], []]})
            library_commits.append(logger.record_commit("Tablet config"))
            sets.append((selected, amount))
        rites = [logger.new_ritual_rite(name)["selected_rite_id"] for name in ("Rite A", "Rite B")]
        expected, frozen, completed = {}, {}, defaultdict(list)
        currencies = ("Divine Orb", "Chaos Orb", "Exalted Orb")

        for number in range(1, 21):
            mid = f"M{number:04d}"
            rite = (rites[0], rites[0], rites[1], None)[(number - 1) % 4]
            logger.select_ritual_rite(rite)
            chosen = catalog if number == 1 else [local, bounty, *(proliferations if rite else [])]
            local_rows = [{**row, "count": number % 3 + 1} for row in chosen]
            logger.save_ritual_rite_affixes([{"id": row["id"], "count": row["count"]} for row in local_rows])
            inherited = [source for prior in completed[rite] for source in prior["own_sources"]] if rite else []
            ordinal = len(completed[rite]) + 1 if rite else None
            logger.start_map()
            logger.record_commit("Map settings")
            logger.commit_remnant("Perfect Chaos Orb x3", "Perfect Exalted Orb x3", 3)

            first_tribute = 0 if number % 2 else None
            final_tribute = 0 if number % 4 == 0 else number * 7
            final_rerolls = None if number % 3 == 0 else 0
            reward = [{"name": "Projection audit reward", "quantity": 1}]
            first = logger.save_ritual_page(reward, scan_hash="a" * 64, expected_map_id=mid,
                                            tribute_available=first_tribute, rerolls_remaining=5)
            logger.save_ritual_page(reward, scan_hash="b" * 64, expected_map_id=mid,
                                   tribute_available=1000 + number, rerolls_remaining=3)
            last = logger.save_ritual_page(reward, scan_hash="a" * 64, expected_map_id=mid,
                                           tribute_available=final_tribute, rerolls_remaining=final_rerolls)
            self.assertTrue(last["updated"])
            self.assertEqual(last["page_number"], 1)
            for phase, quantity in (("start", 1), ("end", 2), ("end", 3)):
                logger.save_currency_snapshot(phase, [{"name": name, "quantity": quantity} for name in currencies], mid)

            set_id, amount = sets[number % 2]
            logger.select_tablet_set(set_id)
            logger.finish_map(number, number + 1, number + 2, unique=number + 3)
            if number % 5 == 0:
                logger.select_tablet_set(sets[(number + 1) % 2][0])
                logger.finish_map(number, number + 1, number + 2, unique=number + 3)
            counters = [3, first_tribute, 5, final_tribute, final_rerolls,
                        first["scan_commit_number"], last["scan_commit_number"]]
            expected[mid] = {"set_id": set_id, "amount": amount, "rite": rite, "ordinal": ordinal,
                             "affixes": local_rows, "origins": inherited, "counters": counters}
            own = [(mid, rite, ordinal, position + 1, row["id"], row["count"], row)
                   for position, row in enumerate(local_rows) if row["kind"] == "proliferation"]
            if rite:
                completed[rite].append({"own_sources": own})
            for prior, fingerprint in frozen.items():
                self.assertEqual(self.saved_rows(prior), fingerprint, prior)
            frozen[mid] = self.saved_rows(mid)

        with logger._connect() as db:
            before_maps = [tuple(row) for row in db.execute("SELECT * FROM maps ORDER BY map_id")]
            before_commits = [tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")]
            self.assertEqual(len(before_commits), 20 * 9 + 4 + len(library_commits))
            for number in range(1, 21):
                counts = dict(db.execute("SELECT kind,COUNT(*) FROM commits WHERE map_id=? GROUP BY kind",
                                         (f"M{number:04d}",)))
                self.assertEqual(counts, {"Map settings": 1, "Remnant": 1, "Ritual": 3,
                                          "Currency": 3, "Map totals": 1 + (number % 5 == 0)})
        sources = {"maps": logger.export_maps_csv(), "primary": logger.export_primary_csv(),
                   "history": logger.export_all_csv()}
        decoded = {key: self.csv_rows(data) for key, data in sources.items()}
        for kind in ("maps", "primary"):
            by_map = defaultdict(list)
            for row in decoded[kind][1]:
                by_map[row["Map ID"]].append(row)
            self.assertEqual(set(by_map), set(expected))
            for mid, wanted in expected.items():
                first, *remaining = by_map[mid]
                self.assertEqual(first["Tablet Set ID"], wanted["set_id"])
                self.assertEqual(first["Tablet 1 Mod 1 Value"], str(wanted["amount"]))
                self.assertEqual(first["Ritual Rite ID"], wanted["rite"] or "")
                self.assertEqual(first["Ritual Rite Map Ordinal"], str(wanted["ordinal"] or ""))
                self.assertEqual(json.loads(first["Ritual Rite Affixes JSON"]), wanted["affixes"])
                inherited = json.loads(first["Ritual Rite Inherited Sources JSON"])
                actual = [(row["origin_map_id"], row["origin_rite_id"], row["origin_map_ordinal"],
                           row["origin_affix_position"], row["affix_id"], row["count"], row["affix"])
                          for row in inherited]
                self.assertEqual(actual, wanted["origins"])
                self.assertEqual(len({row[:4] for row in actual}), len(actual))
                self.assertEqual([first[key] for key in logger.RITUAL_COUNTER_HEADERS],
                                 ["" if value is None else str(value) for value in wanted["counters"]])
                if kind == "primary":
                    for name in currencies:
                        self.assertEqual(first[name], "2")
                        self.assertTrue(all(row[name] == "" for row in remaining))
                    self.assertTrue(all(row[key] == "" for row in remaining
                                        for key in (*logger.RITUAL_COUNTER_HEADERS, *logger.MAP_CONTEXT_HEADERS)))

        history = decoded["history"][1]
        for number in library_commits:
            row = next(row for row in history if row["Scan Commit #"] == str(number))
            self.assertEqual((row["Map ID"], row["Expedition ID"], row["Ritual Rite ID"]), ("", "", ""))
        self.assertEqual({int(row["Scan Commit #"]) for row in history}, {row[0] for row in before_commits})

        backup = Path(self.temporary.name) / "combined.sqlite3"
        backup.write_bytes(logger.backup_bytes())
        with sqlite3.connect(backup) as db:
            self.assertEqual(list(db.execute("SELECT * FROM maps ORDER BY map_id")), before_maps)
            self.assertEqual(list(db.execute("SELECT * FROM commits ORDER BY number")), before_commits)
        package = workbook_export.export_xlsx()
        for sheet, kind in ((1, "primary"), (3, "history")):
            headers, rows = self.workbook_rows(package, sheet)
            old_headers, old_rows = decoded[kind]
            self.assertEqual(headers[:len(old_headers)], old_headers)
            self.assertEqual(len(rows), len(old_rows))
            extras = headers[len(old_headers):]
            self.assertTrue(extras)
            for old, current in zip(old_rows, rows):
                for field in ("Ritual Rite Affixes JSON", "Ritual Rite Inherited Sources JSON"):
                    self.assertEqual(self.joined_json(current, field, extras), old[field])
                self.assertEqual(current["Map ID"], old["Map ID"])
        with logger._connect() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM maps ORDER BY map_id")], before_maps)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM commits ORDER BY number")], before_commits)


if __name__ == "__main__":
    unittest.main()
