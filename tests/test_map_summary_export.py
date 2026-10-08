"""Map totals use approved inventory gains, never scan-history or offer sums."""
import csv
from contextlib import contextmanager
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PoE2_Data_Logger.core import logger_store as logger, store


class MapSummaryExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-map-summary-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def snapshot(self, phase, **quantities):
        return logger.save_currency_snapshot(phase, [
            {"name": name, "quantity": amount} for name, amount in quantities.items()])

    def summary(self, **kwargs):
        raw = list(csv.reader(io.StringIO(logger.export_map_summary_csv(**kwargs).decode("utf-8-sig"))))
        self.assertEqual(len(raw[0]), len(set(name.casefold() for name in raw[0])))
        self.assertTrue(all(len(row) == len(raw[0]) for row in raw))
        rows = [dict(zip(raw[0], row)) for row in raw[1:]]
        self.assertEqual(len(rows), len({row["Map ID"] for row in rows}))
        return raw[0], {row["Map ID"]: row for row in rows}

    def test_registered_names_have_full_name_columns_before_they_are_seen(self):
        logger.add_currency_item("New Registered Token")
        logger.add_item_name("New Registered Armour")
        logger.add_ritual_name("Omen of Registered Learning")
        logger.start_map()
        headers, rows = self.summary()
        self.assertTrue(all(name in headers for name in (
            "Chaos Orb", "Divine Orb", "Perfect Chaos Orb", "New Registered Token",
            "New Registered Armour", "Omen of Registered Learning")))
        with logger._connect() as db:
            registered = {row[0].casefold() for table in ("currency_items", "item_names", "ritual_names")
                          for row in db.execute(f"SELECT name FROM {table}")}
        item_headers = logger.map_summary_item_headers()
        self.assertEqual(len(item_headers), len(registered))
        self.assertEqual(headers[headers.index("End Recorded UTC") + 1:], item_headers)
        self.assertTrue(all(rows["M0001"][name] == "" for name in item_headers))
        self.assertEqual(rows["M0001"]["Currency Scan Status"], "Not scanned")
        self.snapshot("end")
        _, rows = self.summary()
        self.assertTrue(all(rows["M0001"][name] == "0" for name in item_headers))

    def test_counts_replace_rescans_and_match_session_totals_once_per_map(self):
        logger.save_settings({"tier": 16, "waystone": 91, "ocean": True, "deli": True})
        logger.start_map()
        self.snapshot("end", **{"Divine Orb": 1, "Chaos Orb": 23, "Perfect Chaos Orb": 16})
        self.snapshot("end", **{"Divine Orb": 2, "Chaos Orb": 19})
        logger.save_settings({"expedition": 3})
        logger.finish_map(10, 2, 3, 7, unique=4)
        logger.start_map()
        self.snapshot("start", **{"Chaos Orb": 5, "Perfect Chaos Orb": 3})
        self.snapshot("end", **{"Chaos Orb": 8, "Perfect Chaos Orb": 2, "Divine Orb": 1})
        headers, rows = self.summary()
        self.assertEqual(list(rows), ["M0001", "M0002"])
        self.assertEqual([rows["M0001"][name] for name in ("Divine Orb", "Chaos Orb", "Perfect Chaos Orb")],
                         ["2", "19", "0"])
        self.assertEqual([rows["M0002"][name] for name in ("Divine Orb", "Chaos Orb", "Perfect Chaos Orb")],
                         ["1", "3", "0"])
        totals = {item["name"]: item["quantity"] for item in logger.session_currency_totals()["items"]}
        self.assertEqual({name: sum(int(row[name]) for row in rows.values()) for name in totals}, totals)
        self.assertEqual((rows["M0001"]["Start Baseline"], rows["M0002"]["Start Baseline"]),
                         ("Assumed empty", "Scanned"))
        self.assertEqual([rows["M0001"][name] for name in (
            "Tier", "Waystone %", "Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills",
            "Total Kills", "Expedition 3 Detonated")], ["16", "91", "10", "2", "3", "4", "19", "7"])
        map_rows = csv.DictReader(io.StringIO(logger.export_maps_csv().decode("utf-8-sig")))
        self.assertTrue(all(name in headers for name in map_rows.fieldnames))
        for original in map_rows:
            self.assertTrue(all(rows[original["Map ID"]][name] == value for name, value in original.items()))

    def test_missing_end_and_approved_empty_end_are_distinct(self):
        logger.start_map()
        self.snapshot("start", **{"Chaos Orb": 8})
        _, rows = self.summary()
        pending = rows["M0001"]
        self.assertEqual((pending["Chaos Orb"], pending["Currency Scan Status"], pending["Start Baseline"]),
                         ("", "Awaiting end scan", "Scanned"))
        self.assertTrue(pending["Start Commit #"])
        self.assertTrue(pending["Start Recorded UTC"])
        self.assertEqual((pending["End Commit #"], pending["End Recorded UTC"]), ("", ""))
        self.snapshot("end")
        _, rows = self.summary()
        empty = rows["M0001"]
        self.assertEqual((empty["Chaos Orb"], empty["Currency Scan Status"]), ("0", "End scanned"))
        self.assertTrue(empty["End Commit #"])
        self.assertTrue(empty["End Recorded UTC"])
        logger.clear_export_and_reset_ids()
        self.assertEqual(self.summary()[1], {})

    def test_later_start_recalculates_gains_without_changing_audit_records(self):
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 23})
        _, rows = self.summary()
        self.assertEqual(rows["M0001"]["Chaos Orb"], "23")
        old_end_commit = rows["M0001"]["End Commit #"]
        self.snapshot("start", **{"Chaos Orb": 20})
        _, rows = self.summary()
        self.assertEqual((rows["M0001"]["Chaos Orb"], rows["M0001"]["Start Baseline"]), ("3", "Scanned"))
        self.assertEqual(rows["M0001"]["End Commit #"], old_end_commit)
        history = list(csv.DictReader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
        self.assertEqual([(row["Inventory Phase"], row["Quantity"], row["Start Baseline"]) for row in history],
                         [("end", "23", "Assumed empty"), ("start", "20", "Scanned")])

    def test_ritual_offers_never_become_inventory_gains_and_removed_labels_survive(self):
        logger.start_map()
        logger.add_currency_item("Acquired Token")
        logger.add_item_name("Acquired Armour")
        logger.save_ritual_page([
            {"category": "Omen", "name": "Omen of Offered Reward", "quantity": 7},
            {"category": "Item", "name": "Offered Armour", "quantity": 4}],
            register_names=True, tribute_available=18956, rerolls_remaining=5)
        self.snapshot("end", **{"Acquired Token": 6, "Acquired Armour": 2, "Omen of Offered Reward": 1})
        # The latest approved snapshot removes this obsolete result's count.
        self.snapshot("end", **{"Acquired Token": 3, "Acquired Armour": 2})
        with logger._connect() as db:
            db.execute("DELETE FROM currency_items WHERE name='Acquired Token'")
            db.execute("DELETE FROM item_names WHERE name IN ('Acquired Armour','Offered Armour')")
            db.execute("DELETE FROM ritual_names WHERE name='Omen of Offered Reward'")
            db.execute("DELETE FROM currency_items WHERE name='Omen of Offered Reward'")
        headers, rows = self.summary()
        self.assertTrue(all(name in headers for name in (
            "Acquired Token", "Acquired Armour", "Offered Armour", "Omen of Offered Reward")))
        row = rows["M0001"]
        self.assertEqual([row[name] for name in (
            "Acquired Token", "Acquired Armour", "Offered Armour", "Omen of Offered Reward")], ["3", "2", "0", "0"])
        history = list(csv.DictReader(io.StringIO(logger.export_record_history_csv().decode("utf-8-sig"))))
        offers = [row for row in history if row["Type"] == "Ritual"]
        self.assertEqual([row["Quantity"] for row in offers], ["7", "4"])
        self.assertTrue(all(row["Ritual Tribute Available"] == "18956" for row in offers))
        self.assertTrue(all(row["Ritual Rerolls Remaining"] == "5" for row in offers))

    def test_future_start_scan_is_exported_before_map_exists_and_is_not_duplicated(self):
        logger.save_settings({"tier": 16, "waystone": 83, "biome": "Swamp"})
        self.snapshot("start", **{"Chaos Orb": 8})
        _, rows = self.summary()
        self.assertEqual(list(rows), ["M0001"])
        self.assertEqual((rows["M0001"]["Chaos Orb"], rows["M0001"]["Tier"], rows["M0001"]["Biome"]),
                         ("", "16", "Swamp"))
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 10})
        logger.finish_map(1, 2, 3)
        self.snapshot("start", **{"Chaos Orb": 4})
        _, rows = self.summary()
        self.assertEqual(list(rows), ["M0001", "M0002"])
        self.assertEqual((rows["M0001"]["Chaos Orb"], rows["M0002"]["Chaos Orb"]), ("2", ""))
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 9})
        _, rows = self.summary()
        self.assertEqual(list(rows), ["M0001", "M0002"])
        self.assertEqual(rows["M0002"]["Chaos Orb"], "5")

    def test_name_collisions_keep_recipe_metadata_safe_and_case_duplicates_share_count(self):
        names = ("Map ID", "Matched Recipe", "Type", "Currency Scan Status", "Map Modifiers", "=SUM(1,2)", "'=SUM(1,2)")
        for name in names:
            logger.add_currency_item(name)
        logger.add_currency_item("Reviewed Token")
        with logger._connect() as db:
            db.execute("INSERT INTO currency_items(name) VALUES('REVIEWED TOKEN')")
        logger.start_map()
        logger.save_currency_snapshot("end", [
            *({"name": name, "quantity": count} for count, name in enumerate(names, 1)),
            {"name": "Reviewed Token", "quantity": 2}, {"name": "reviewed token", "quantity": 3}])
        headers, rows = self.summary()
        row = rows["M0001"]
        self.assertEqual(row["Map ID"], "M0001")
        self.assertEqual([row[f"Currency: {name}"] for name in names[:5]], ["1", "2", "3", "4", "5"])
        token_headers = [name for name in headers if name.casefold() == "reviewed token"]
        self.assertEqual(len(token_headers), 1)
        self.assertEqual(row[token_headers[0]], "5")
        formula_headers = [name for name in headers if "SUM(1,2)" in name]
        self.assertEqual(len(formula_headers), 2)
        self.assertEqual(sorted(int(row[name]) for name in formula_headers), [6, 7])
        self.assertTrue(all(not name.lstrip().startswith(("=", "+", "-", "@")) for name in formula_headers))

    def test_concurrent_approval_does_not_mix_old_counts_with_new_commit_number(self):
        logger.start_map()
        self.snapshot("end", **{"Chaos Orb": 5})
        old_commit = self.summary()[1]["M0001"]["End Commit #"]
        original_connect = logger._connect
        changed = False

        class Connection:
            def __init__(proxy, connection):
                proxy.connection = connection

            def __getattr__(proxy, name):
                return getattr(proxy.connection, name)

            def execute(proxy, sql, *arguments):
                nonlocal changed
                if sql.startswith("SELECT number,kind,map_id,reference,recorded_at,details_json,snapshot_json") and not changed:
                    changed = True
                    self.snapshot("end", **{"Chaos Orb": 99})
                return proxy.connection.execute(sql, *arguments)

        @contextmanager
        def interleaved_connect():
            with original_connect() as db:
                yield Connection(db)

        with patch.object(logger, "_connect", interleaved_connect):
            row = self.summary()[1]["M0001"]
        self.assertTrue(changed)
        self.assertEqual((row["Chaos Orb"], row["End Commit #"]), ("5", old_commit))
        self.assertEqual(logger.currency_for_map("M0001")["end"], {"Chaos Orb": 99})


if __name__ == "__main__":
    unittest.main()
