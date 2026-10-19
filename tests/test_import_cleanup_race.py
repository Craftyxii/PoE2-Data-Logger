"""Keep newly shared screenshot evidence when a reference import rolls back."""

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store


class ImportCleanupRaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-import-cleanup-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.raw = self.png("red")
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.target = store.DATA_DIR / "images" / (self.digest + ".png")
        self.pack = self.invalid_pack()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    @staticmethod
    def png(color):
        data = io.BytesIO()
        Image.new("RGB", (240, 140), color).save(data, format="PNG")
        return data.getvalue()

    def invalid_pack(self):
        """Fail metadata validation after a first valid screenshot has been staged."""
        other = self.png("blue")
        other_digest = hashlib.sha256(other).hexdigest()
        data = {key: [] for key in ("families", "recipes", "aliases", "seed_states",
            "affixes", "master_perks", "currency_names", "omen_names", "item_names",
            "glyphs", "icons", "scans", "review_icon_examples")}
        for digest, sockets in ((self.digest, 3), (other_digest, 11)):
            data["scans"].append({"file": "screens/" + digest + ".png", "sha256": digest,
                "file_name": "review.png", "sockets": sockets, "slot": "P1",
                "rune": "Unknown Audit Rune", "family": "", "rewards_json": "[]",
                "status": "unresolved"})
        path = store.DATA_DIR / "references.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": reference_pack.FORMAT,
                "version": 3, "data": data}))
            archive.writestr("screens/" + self.digest + ".png", self.raw)
            archive.writestr("screens/" + other_digest + ".png", other)
        return path

    def assert_shared_image_survives(self, *, undo_only=False):
        """Choose a real two-writer interleaving after rollback, before cleanup."""
        rolled_back, completed = threading.Event(), threading.Event()
        saved, errors = [], []
        connect = logger._connect

        @contextmanager
        def pause_after_rollback(*args, **kwargs):
            try:
                with connect(*args, **kwargs) as db:
                    yield db
            except Exception:
                if kwargs.get("undo_label") == "Import OCR references":
                    rolled_back.set()
                    if not completed.wait(10):
                        raise AssertionError("The competing writer did not finish.")
                raise

        def accept_same_capture():
            try:
                if not rolled_back.wait(10):
                    raise AssertionError("Import did not roll back.")
                record = store.save_scan(self.raw, "accepted.png", 3, "P1", "Unknown Audit Rune")
                saved.append(record)
                if undo_only:
                    with connect(undo_label="Remove saved screenshot") as db:
                        db.execute("DELETE FROM scans WHERE id=?", (record["id"],))
            except Exception as error:
                errors.append(error)
            finally:
                completed.set()

        worker = threading.Thread(target=accept_same_capture)
        with patch.object(store, "_reviewed_vector", return_value=None), \
                patch.object(logger, "_connect", pause_after_rollback):
            worker.start()
            try:
                with self.assertRaisesRegex(ValueError, "Screenshot sockets"):
                    reference_pack.import_pack(self.pack)
            finally:
                rolled_back.set()
                worker.join(15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(saved), 1)
        self.assertTrue(self.target.is_file(), "Cleanup deleted accepted screenshot evidence.")
        self.assertEqual(self.target.read_bytes(), self.raw)
        if undo_only:
            self.assertEqual(logger.undo_last_action()["action"], "Remove saved screenshot")
        self.assertEqual(store.image_for(saved[0]["id"]), self.target)

    def test_preserves_capture_accepted_after_import_rollback(self):
        self.assert_shared_image_survives()

    def test_preserves_undo_only_capture_after_import_rollback(self):
        self.assert_shared_image_survives(undo_only=True)

    def test_failed_import_removes_its_unreferenced_new_capture(self):
        with self.assertRaisesRegex(ValueError, "Screenshot sockets"):
            reference_pack.import_pack(self.pack)
        self.assertFalse(self.target.exists())
        self.assertEqual(store.list_scans(), [])

    def test_cleanup_failure_retains_original_import_error(self):
        unlink = Path.unlink

        def locked_image(path, *args, **kwargs):
            if path == self.target:
                raise PermissionError("Capture is temporarily locked.")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", locked_image):
            with self.assertRaisesRegex(ValueError, "Screenshot sockets"):
                reference_pack.import_pack(self.pack)
        self.assertTrue(self.target.is_file())
        self.assertEqual(store.list_scans(), [])
