"""Keep concurrent catalog imports and failed screenshot saves consistent and reversible."""

import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store, undo_store


class CatalogScanWriteIntegrityTests(unittest.TestCase):
    """Exercise public writes against real SQLite failures in an isolated profile."""

    RECIPE = "Concurrent Write Review Recipe"

    def setUp(self):
        """Start a separate profile without changing the selected user's saved data."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-catalog-scan-write-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()

    def tearDown(self):
        """Restore the selected profile after all synchronized writer threads have exited."""
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def recipe(self, sockets):
        """Build a distinct valid recipe whose socket count can change during an import."""
        return {"name": self.RECIPE, "sockets": sockets,
                "combo": " + ".join(("Power", "Death", "Sky", "Ward", "Bond", "Cold")[:sockets])}

    def pack(self, *, recipes=(), families=()):
        """Write a real supported reference ZIP containing only the competing catalog edits."""
        keys = ("families", "recipes", "aliases", "seed_states", "affixes", "master_perks",
                "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans",
                "review_icon_examples")
        data = {key: [] for key in keys}
        data.update(recipes=list(recipes), families=list(families))
        path = store.DATA_DIR / "competing-reference.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": reference_pack.FORMAT,
                                                        "version": 3, "data": data}))
        return path

    def interleave_import(self, save, insertion, path):
        """Attempt an actual import after Save validates but before it executes its row write."""
        original = store._connect
        validated, resume = threading.Event(), threading.Event()
        saved, errors = [], []

        @contextmanager
        def traced_connection():
            """Pause only the editor writer at its first final catalog insertion."""
            with original() as db:
                if threading.current_thread().name == "catalog-editor-writer":
                    def trace(sql):
                        """Signal the precise validation/write boundary without altering its SQL."""
                        if sql.startswith(insertion) and not validated.is_set():
                            validated.set()
                            resume.wait(10)
                    db.set_trace_callback(trace)
                yield db

        def editor_save():
            """Retain the public editor call's result or failure for the asserting thread."""
            try:
                saved.append(save())
            except BaseException as error:
                errors.append(error)

        with patch.object(store, "_connect", traced_connection):
            writer = threading.Thread(target=editor_save, name="catalog-editor-writer")
            writer.start()
            try:
                self.assertTrue(validated.wait(5), "Editor did not reach the validation/write boundary")
                with store.database_timeout(0):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        reference_pack.import_pack(path, replace_existing=True)
            finally:
                resume.set()
                writer.join(10)
            self.assertFalse(writer.is_alive(), "Catalog writer did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(len(saved), 1)
        # Once the editor's transaction commits, the import must validate its
        # now-conflicting edit against the complete saved catalog and roll back.
        with self.assertRaises(ValueError):
            reference_pack.import_pack(path, replace_existing=True)
        return saved[0]

    def test_family_save_serializes_recipe_validation_with_background_import(self):
        """Prevent a six-socket imported recipe entering a newly saved three-socket family."""
        logger.save_recipe(self.recipe(3))
        path = self.pack(recipes=[self.recipe(6)])
        self.interleave_import(
            lambda: logger.save_family({"family": 9991, "top_socket": 3, "recipes": [self.RECIPE]}),
            "INSERT INTO families VALUES", path)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?", (self.RECIPE,)).fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT top_socket FROM families WHERE id=9991").fetchone()[0], 3)
        self.assertEqual(logger.undo_last_action()["action"], "Family reference")
        self.assertFalse(any(row["id"] == 9991 for row in logger.get_state()["families"]))

    def test_recipe_save_serializes_family_validation_with_background_import(self):
        """Prevent an imported lowered family limit racing a checked larger recipe."""
        logger.save_recipe(self.recipe(3))
        logger.save_family({"family": 9991, "top_socket": 6, "recipes": [self.RECIPE]})
        path = self.pack(families=[{"id": 9991, "top_socket": 3, "valid": True,
                                   "recipes_json": json.dumps([self.RECIPE])}])
        self.interleave_import(lambda: logger.save_recipe(self.recipe(6)), "INSERT INTO recipes VALUES", path)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?", (self.RECIPE,)).fetchone()[0], 6)
            self.assertEqual(db.execute("SELECT top_socket FROM families WHERE id=9991").fetchone()[0], 6)
        self.assertEqual(logger.undo_last_action()["action"], "Recipe reference")
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?", (self.RECIPE,)).fetchone()[0], 3)

    def test_seed_save_serializes_reward_validation_with_background_import(self):
        """Keep a saved three-socket seed reward from becoming a raced four-socket recipe."""
        logger.save_recipe(self.recipe(3))
        logger.save_family({"family": 9991, "top_socket": 6, "recipes": [self.RECIPE]})
        path = self.pack(recipes=[self.recipe(4)])
        self.interleave_import(lambda: logger.save_seed_state({"family": 9991, "sockets": 3,
            "seed_slot": "P1", "seed_rune": "Power", "rewards": [self.RECIPE]}),
            "INSERT INTO seed_states VALUES", path)
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT sockets FROM recipes WHERE name=?", (self.RECIPE,)).fetchone()[0], 3)
            self.assertEqual(json.loads(db.execute("SELECT rewards_json FROM seed_states WHERE family=9991").fetchone()[0]),
                             [self.RECIPE])
        self.assertEqual(logger.undo_last_action()["action"], "Seed reference")
        with logger._connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM seed_states WHERE family=9991").fetchone())

    def screenshot(self):
        """Create a valid capture whose bytes have an unambiguous hash-owned file."""
        output = io.BytesIO()
        Image.new("RGB", (200, 100), (170, 140, 80)).save(output, format="PNG")
        return output.getvalue()

    def save_screenshot(self):
        """Use the public screenshot endpoint, including real upload and metadata validation."""
        from PoE2_Data_Logger.core import service
        return service.dispatch("/api/save-scan", {"image": base64.b64encode(self.screenshot()).decode("ascii"),
            "filename": "reviewed-reference.png", "sockets": 3, "slot": "P1", "rune": "Review Unknown Rune"})

    def reject_screenshot_writes(self):
        """Make real SQLite reject scan metadata after the filesystem creation has completed."""
        with logger._connect() as db:
            db.execute("CREATE TRIGGER reject_test_scan BEFORE INSERT ON scans "
                       "BEGIN SELECT RAISE(ABORT,'review test rejected scan'); END")

    def image_files(self):
        """List final and temporary captures without depending on their private digest calculation."""
        directory = store.DATA_DIR / "images"
        return sorted(directory.iterdir()) if directory.exists() else []

    def test_locked_screenshot_save_creates_no_untracked_capture(self):
        """Fail before image creation when another writer owns the database."""
        before = logger.undo_action()
        with sqlite3.connect(store.DATA_DIR / "scans.sqlite3") as writer:
            writer.execute("BEGIN IMMEDIATE")
            with patch.object(store, "_reviewed_vector", return_value=None), store.database_timeout(0):
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(self.image_files(), [])
        self.assertEqual(logger.undo_action(), before)

    def test_rejected_scan_metadata_removes_only_its_new_capture(self):
        """Rollback a newly created image and leave the preceding Undo action untouched."""
        before = logger.undo_action()
        self.reject_screenshot_writes()
        with patch.object(store, "_reviewed_vector", return_value=None):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "rejected scan"):
                self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(self.image_files(), [])
        self.assertEqual(logger.undo_action(), before)

    def test_failed_undo_journal_rolls_back_scan_glyph_and_new_capture(self):
        """Remove the image when a failure occurs after both scan and glyph SQL succeeded."""
        before = logger.undo_action()
        with patch.object(store, "_reviewed_vector", return_value=b"review test glyph"), \
                patch.object(undo_store, "_pack", side_effect=RuntimeError("review journal failure")):
            with self.assertRaisesRegex(RuntimeError, "journal failure"):
                self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(self.image_files(), [])
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reviewed_glyphs").fetchone()[0], 0)
        self.assertEqual(logger.undo_action(), before)

    def test_commit_failure_removes_new_capture_after_actual_sqlite_rollback(self):
        """Clean up even when SQLite rejects the commit after the journal has been generated."""
        original = store._connect
        with original() as db:
            db.execute("CREATE TABLE deferred_test_reference(scan_id INTEGER REFERENCES scans(id) "
                       "DEFERRABLE INITIALLY DEFERRED)")
            db.execute("CREATE TRIGGER fail_test_scan_commit AFTER INSERT ON scans "
                       "BEGIN INSERT INTO deferred_test_reference VALUES(-1); END")

        @contextmanager
        def enforce_foreign_keys():
            """Enable SQLite's genuine deferred constraint checks on save and cleanup connections."""
            with original() as db:
                db.execute("PRAGMA foreign_keys=ON")
                yield db

        before = logger.undo_action()
        with patch.object(store, "_connect", enforce_foreign_keys), \
                patch.object(store, "_reviewed_vector", return_value=None):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
                self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(self.image_files(), [])
        self.assertEqual(logger.undo_action(), before)

    def test_failed_resave_preserves_existing_scan_file_and_undo(self):
        """Never delete a previously saved shared capture when changing its metadata fails."""
        with patch.object(store, "_reviewed_vector", return_value=None):
            saved = self.save_screenshot()
            before = store.list_scans()
            image = store.image_for(saved["id"])
            self.reject_screenshot_writes()
            with self.assertRaises(sqlite3.IntegrityError):
                self.save_screenshot()
        self.assertEqual(image.read_bytes(), self.screenshot())
        self.assertEqual(store.list_scans(), before)
        self.assertEqual(logger.undo_last_action()["action"], "Seed screenshot review")
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(image.read_bytes(), self.screenshot())

    def test_failed_resave_preserves_capture_retained_by_successful_undo(self):
        """Keep raw bytes retained by a prior Undo even though no active scan row references them."""
        with patch.object(store, "_reviewed_vector", return_value=None):
            saved = self.save_screenshot()
            image = store.image_for(saved["id"])
            logger.undo_last_action()
            self.reject_screenshot_writes()
            with self.assertRaises(sqlite3.IntegrityError):
                self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(image.read_bytes(), self.screenshot())

    def test_failed_save_keeps_recreated_capture_needed_only_by_pending_undo(self):
        """Protect an inverse scan row's image even when its current metadata and old file are absent."""
        with patch.object(store, "_reviewed_vector", return_value=None):
            saved = self.save_screenshot()
            image = store.image_for(saved["id"])
            with logger._connect(undo_label="Review scan removal") as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM scans WHERE id=?", (saved["id"],))
            image.unlink()
            self.reject_screenshot_writes()
            with self.assertRaises(sqlite3.IntegrityError):
                self.save_screenshot()
        self.assertEqual(store.list_scans(), [])
        self.assertEqual(image.read_bytes(), self.screenshot())
        with logger._connect() as db:
            db.execute("DROP TRIGGER reject_test_scan")
        self.assertEqual(logger.undo_last_action()["action"], "Review scan removal")
        self.assertEqual(store.image_for(saved["id"]).read_bytes(), self.screenshot())

    def test_failure_cleanup_preserves_capture_reused_by_another_successful_writer(self):
        """Recheck ownership after rollback so a concurrently reused hash cannot be deleted."""
        cleanup = store._remove_failed_scan_image
        saved = []
        self.reject_screenshot_writes()

        def share_before_cleanup(destination):
            """Commit another public save in the rollback/cleanup window, then perform real cleanup."""
            with logger._connect() as db:
                db.execute("DROP TRIGGER reject_test_scan")
            writer = threading.Thread(target=lambda: saved.append(self.save_screenshot()))
            writer.start()
            writer.join(10)
            self.assertFalse(writer.is_alive())
            cleanup(destination)

        with patch.object(store, "_reviewed_vector", return_value=None), \
                patch.object(store, "_remove_failed_scan_image", side_effect=share_before_cleanup):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "rejected scan"):
                self.save_screenshot()
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(store.list_scans()), 1)
        self.assertEqual(store.image_for(saved[0]["id"]).read_bytes(), self.screenshot())
        self.assertEqual(logger.undo_last_action()["action"], "Seed screenshot review")

    def connection_timeout(self):
        """Read SQLite's actual connection timeout rather than its thread-local implementation state."""
        with store._connect() as db:
            return db.execute("PRAGMA busy_timeout").fetchone()[0]

    def test_database_timeout_nested_scopes_restore_after_exception(self):
        """Retain ten-second defaults while nested fail-fast overrides unwind safely."""
        self.assertEqual(self.connection_timeout(), 10000)
        with store.database_timeout(0):
            self.assertEqual(self.connection_timeout(), 0)
            with self.assertRaisesRegex(RuntimeError, "nested timeout"):
                with store.database_timeout(.025):
                    self.assertEqual(self.connection_timeout(), 25)
                    raise RuntimeError("nested timeout")
            self.assertEqual(self.connection_timeout(), 0)
        self.assertEqual(self.connection_timeout(), 10000)

    def test_database_timeout_does_not_change_other_worker_threads(self):
        """Keep worker lock waits independent of a simultaneous GUI fail-fast scope."""
        observed = []

        def worker_timeout():
            """Observe worker defaults before and after its own nested timeout override."""
            observed.append(self.connection_timeout())
            with store.database_timeout(.015):
                observed.append(self.connection_timeout())
            observed.append(self.connection_timeout())

        with store.database_timeout(0):
            worker = threading.Thread(target=worker_timeout)
            worker.start()
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.connection_timeout(), 0)
        self.assertEqual(observed, [10000, 15, 10000])

    def test_database_timeout_zero_rejects_real_writer_without_waiting_ten_seconds(self):
        """Fail quickly against genuine SQLite contention and restore the normal timeout afterward."""
        with sqlite3.connect(store.DATA_DIR / "scans.sqlite3") as writer:
            writer.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with store.database_timeout(0):
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    logger.save_recipe(self.recipe(3))
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.connection_timeout(), 10000)

    def test_database_timeout_rejects_invalid_waits_without_changing_scope(self):
        """Reject negative, boolean, infinite and malformed timeouts without poisoning later connections."""
        for invalid in (-1, True, None, "not a timeout", float("nan"), float("inf")):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    with store.database_timeout(invalid):
                        self.fail("Invalid timeout entered its scope")
        self.assertEqual(self.connection_timeout(), 10000)


if __name__ == "__main__":
    unittest.main()
