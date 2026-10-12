"""Serialize inventory-name registration with competing catalog writes and preserve Undo."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store, undo_store


class CatalogNameConcurrencyTests(unittest.TestCase):
    """Exercise supported reference imports and editor registrations against a real WAL database."""

    def setUp(self):
        """Create an isolated profile while preserving the caller's selected profile and readiness."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-name-concurrency-")
        self.previous_data, self.previous_ready = store.DATA_DIR, logger._READY
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()

    def tearDown(self):
        """Remove the disposable database only after all worker connections have been closed."""
        store.DATA_DIR, logger._READY = self.previous_data, self.previous_ready
        self.temporary.cleanup()

    def pack(self, kind, name):
        """Write a supported minimal reference pack containing a competing name registration."""
        keys = ("families", "recipes", "aliases", "seed_states", "affixes", "master_perks",
                "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans",
                "review_icon_examples")
        data = {key: [] for key in keys}
        data[kind] = [{"name": name}]
        path = store.DATA_DIR / "competing-reference.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": reference_pack.FORMAT,
                                                        "version": 3, "data": data}))
        return path

    def matching_names(self, name):
        """Read all category rows for one Unicode-casefold identity without ASCII-only SQL matching."""
        with logger._connect() as db:
            return {table: [row[0] for row in db.execute(f"SELECT name FROM {table}")
                            if row[0].casefold() == name.casefold()]
                    for table in ("currency_items", "item_names", "ritual_names")}

    def actions(self):
        """Read chronological Undo labels to distinguish successful writes from failed/no-op calls."""
        with logger._connect() as db:
            return [row[0] for row in db.execute("SELECT label FROM undo_actions ORDER BY id")]

    def interleave(self, winner, insertion, competitor):
        """Pause a real writer after validation; a competing command must fail promptly and atomically.

        A zero-timeout contender avoids synchronously waiting for the paused writer.
        After the winner commits, the caller retries and checks current category rules.
        """
        original = store._connect
        validated, resume = threading.Event(), threading.Event()
        results, errors = [], []

        @contextmanager
        def traced_connection():
            """Pause only the winning connection at its final INSERT while retaining its transaction."""
            with original() as db:
                if threading.current_thread().name == "name-registration-winner":
                    def trace(sql):
                        """Expose the original validation/write race boundary without replacing SQLite."""
                        if sql.startswith(insertion) and not validated.is_set():
                            validated.set()
                            resume.wait(10)
                    db.set_trace_callback(trace)
                yield db

        def write():
            """Retain the public writer result or failure for assertions after joining its thread."""
            try:
                results.append(winner())
            except BaseException as error:
                errors.append(error)

        with patch.object(store, "_connect", traced_connection):
            thread = threading.Thread(target=write, name="name-registration-winner")
            thread.start()
            try:
                self.assertTrue(validated.wait(5), "Winner never reached its catalog INSERT")
                started = time.monotonic()
                with store.database_timeout(0):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        competitor()
                self.assertLess(time.monotonic() - started, 1, "Contender waited on the GUI timeout path")
            finally:
                resume.set()
                thread.join(10)
            self.assertFalse(thread.is_alive(), "Winning writer did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        return results[0]

    def test_currency_registration_serializes_with_item_reference_import(self):
        """Prevent a checked Currency insertion from following a successfully imported Item identity."""
        name = "Élan Concurrent Currency"
        path = self.pack("item_names", name.upper())
        self.assertEqual(self.interleave(lambda: logger.add_currency_item(name),
            "INSERT OR IGNORE INTO currency_items", lambda: reference_pack.import_pack(path)), name)
        with self.assertRaisesRegex(ValueError, "both Currency and Item"):
            reference_pack.import_pack(path, replace_existing=True)
        self.assertEqual(self.matching_names(name),
                         {"currency_items": [name], "item_names": [], "ritual_names": []})
        self.assertEqual(self.actions(), ["Currency reference name"])
        logger.save_currency_snapshot("start", [{"name": name.upper(), "quantity": 2}], register_names=True)
        with logger._connect() as db:
            items = db.execute("SELECT items_json FROM currency_snapshots WHERE map_id='M0001' AND phase='start'")
            self.assertEqual(json.loads(items.fetchone()[0]), {name: 2})
        self.assertEqual(logger.undo_last_action()["action"], "Currency approval")
        self.assertEqual(logger.undo_last_action()["action"], "Currency reference name")
        self.assertEqual(self.matching_names(name)["currency_items"], [])

    def test_item_registration_serializes_with_currency_reference_import(self):
        """Prevent reciprocal Item registration/import races from leaving a dual-category name."""
        name = "Straße Concurrent Item"
        path = self.pack("currency_names", "STRASSE Concurrent Item")
        self.interleave(lambda: logger.add_item_name(name), "INSERT INTO item_names",
                        lambda: reference_pack.import_pack(path, replace_existing=True))
        with self.assertRaisesRegex(ValueError, "both Currency and Item"):
            reference_pack.import_pack(path)
        self.assertEqual(self.matching_names(name),
                         {"currency_items": [], "item_names": [name], "ritual_names": []})
        self.assertEqual(self.actions(), ["Item reference name"])

    def test_omen_registration_serializes_with_item_reference_import(self):
        """Protect both Omen category writes because the resolver requires exclusion from Item."""
        name = "Omen of Élan Concurrency"
        path = self.pack("item_names", name.upper())
        self.interleave(lambda: logger.add_ritual_name(name), "INSERT INTO ritual_names",
                        lambda: reference_pack.import_pack(path))
        with self.assertRaisesRegex(ValueError, "both Currency and Item"):
            reference_pack.import_pack(path)
        self.assertEqual(self.matching_names(name),
                         {"currency_items": [name], "item_names": [], "ritual_names": [name]})
        self.assertEqual(self.actions(), ["Omen reference name"])
        self.assertEqual(logger.undo_last_action()["action"], "Omen reference name")
        self.assertEqual(self.matching_names(name),
                         {"currency_items": [], "item_names": [], "ritual_names": []})

    def test_reference_import_winning_first_is_seen_by_registration_retry(self):
        """A competing add checks the imported category after the import transaction finishes."""
        name = "Élan Imported Item"
        path = self.pack("item_names", name)
        self.interleave(lambda: reference_pack.import_pack(path), "INSERT OR IGNORE INTO item_names",
                        lambda: logger.add_currency_item(name.upper()))
        with self.assertRaisesRegex(ValueError, "Item database"):
            logger.add_currency_item(name.upper())
        self.assertEqual(self.matching_names(name),
                         {"currency_items": [], "item_names": [name], "ritual_names": []})
        self.assertEqual(self.actions(), ["Import OCR references"])

    def test_reciprocal_currency_item_registrations_obey_the_first_saved_category(self):
        """Concurrent public add calls cannot each validate against the other's unsaved absence."""
        cases = ((logger.add_currency_item, "INSERT OR IGNORE INTO currency_items", logger.add_item_name,
                  "Currency", "currency_items"),
                 (logger.add_item_name, "INSERT INTO item_names", logger.add_currency_item,
                  "Item", "item_names"))
        for index, (winner, insertion, competitor, category, table) in enumerate(cases):
            with self.subTest(winner=category):
                name = f"Élan Reciprocal {index}"
                before = self.actions()
                self.interleave(lambda: winner(name), insertion, lambda: competitor(name.upper()))
                with self.assertRaisesRegex(ValueError, f"{category} database"):
                    competitor(name.upper())
                expected = {"currency_items": [], "item_names": [], "ritual_names": []}
                expected[table] = [name]
                self.assertEqual(self.matching_names(name), expected)
                self.assertEqual(self.actions(), [*before, f"{category} reference name"])

    def test_reciprocal_omen_item_registrations_obey_category_exclusion(self):
        """Omen creation remains dual-catalog while Item exclusion works in either writer order."""
        cases = ((logger.add_ritual_name, "INSERT INTO ritual_names", logger.add_item_name,
                  "Currency", "Omen"),
                 (logger.add_item_name, "INSERT INTO item_names", logger.add_ritual_name,
                  "Item", "Item"))
        for index, (winner, insertion, competitor, conflict, category) in enumerate(cases):
            with self.subTest(winner=category):
                name = f"Omen of Élan Reciprocal {index}"
                before = self.actions()
                self.interleave(lambda: winner(name), insertion, lambda: competitor(name.upper()))
                with self.assertRaisesRegex(ValueError, f"{conflict} database"):
                    competitor(name.upper())
                expected = ({"currency_items": [name], "item_names": [], "ritual_names": [name]}
                            if category == "Omen" else
                            {"currency_items": [], "item_names": [name], "ritual_names": []})
                self.assertEqual(self.matching_names(name), expected)
                self.assertEqual(self.actions(), [*before, f"{category} reference name"])

    def test_concurrent_same_category_returns_one_unicode_canonical_name_and_one_undo(self):
        """A retried Unicode-equivalent registration is a canonical no-op rather than a second record."""
        cases = ((logger.add_currency_item, "INSERT OR IGNORE INTO currency_items", "Élan Currency", "Currency"),
                 (logger.add_item_name, "INSERT INTO item_names", "Straße Item", "Item"),
                 (logger.add_ritual_name, "INSERT INTO ritual_names", "Omen of Élan", "Omen"))
        for add, insertion, name, category in cases:
            with self.subTest(category=category):
                before = self.actions()
                self.interleave(lambda: add(name), insertion, lambda: add(name.upper()))
                self.assertEqual(add(name.upper()), name)
                self.assertEqual(self.actions(), [*before, f"{category} reference name"])
                rows = self.matching_names(name)
                self.assertEqual(rows["item_names" if category == "Item" else "currency_items"], [name])
                self.assertEqual(rows["ritual_names"], [name] if category == "Omen" else [])

    def test_existing_names_and_retired_revivals_preserve_noop_and_undo_semantics(self):
        """No-op adds create no Undo, but intentionally reviving a hidden retired name remains reversible."""
        with logger._connect() as db:
            db.execute("INSERT OR IGNORE INTO currency_items(name) VALUES('Sun Artifact')")
            db.execute("INSERT OR IGNORE INTO ritual_names(name) VALUES('Omen of Corruption')")
        self.assertNotIn("Sun Artifact", logger.currency_names())
        self.assertEqual(logger.add_currency_item("SUN ARTIFACT"), "Sun Artifact")
        self.assertEqual(logger.add_currency_item("sun artifact"), "Sun Artifact")
        self.assertEqual(self.actions(), ["Currency reference name"])
        self.assertIn("Sun Artifact", logger.currency_names())
        self.assertEqual(logger.undo_last_action()["action"], "Currency reference name")
        self.assertNotIn("Sun Artifact", logger.currency_names())
        self.assertEqual(logger.add_ritual_name("OMEN OF CORRUPTION"), "Omen of Corruption")
        self.assertEqual(logger.add_ritual_name("omen of corruption"), "Omen of Corruption")
        self.assertEqual(self.actions(), ["Omen reference name"])
        self.assertIn("Omen of Corruption", logger.currency_names())
        self.assertIn("Omen of Corruption", logger.ritual_names())
        self.assertEqual(logger.undo_last_action()["action"], "Omen reference name")
        self.assertNotIn("Omen of Corruption", logger.currency_names())
        self.assertNotIn("Omen of Corruption", logger.ritual_names())
        self.assertEqual(self.actions(), [])

    def test_busy_registration_retains_catalog_metadata_and_undo_until_retry(self):
        """UI zero-timeout registration fails without partial rows and succeeds once the writer exits."""
        before = self.actions()
        cases = ((logger.add_currency_item, "Sun Artifact"), (logger.add_item_name, "Élan Busy Item"),
                 (logger.add_ritual_name, "Omen of Corruption"))
        with closing(sqlite3.connect(store.DATA_DIR / "scans.sqlite3")) as writer, writer:
            writer.execute("BEGIN IMMEDIATE")
            for add, name in cases:
                with self.subTest(name=name), store.database_timeout(0):
                    started = time.monotonic()
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        add(name)
                    self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.actions(), before)
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "active_retired_currency_names", []), [])
        for add, name in cases:
            self.assertEqual(add(name), name)
        self.assertEqual(self.actions(), ["Currency reference name", "Item reference name", "Omen reference name"])

    def test_failed_journal_rolls_back_registration_and_retirement_override(self):
        """A later Undo-journal failure cannot leave either the name or its revival metadata saved."""
        with patch.object(undo_store, "_pack", side_effect=RuntimeError("name journal failure")):
            with self.assertRaisesRegex(RuntimeError, "name journal failure"):
                logger.add_currency_item("Sun Artifact")
        self.assertEqual(self.matching_names("Sun Artifact")["currency_items"], [])
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "active_retired_currency_names", []), [])
        self.assertEqual(self.actions(), [])


if __name__ == "__main__":
    unittest.main()
