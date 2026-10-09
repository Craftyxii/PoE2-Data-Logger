"""Recipe-row family evidence survives approval, SQLite retry and receipt replay."""

from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PropagationFamilyUITests(unittest.TestCase):
    """Exercise real row approval buttons with conflicting sibling context and a queued SQLite save."""

    @classmethod
    def setUpClass(cls):
        """Reuse the Qt application for isolated logger windows."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create shared-family recipes so incorrect sibling attribution would also pass backend validation."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-family-ui-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        with logger._connect() as db:
            db.executemany("INSERT INTO recipes(name,sockets,combo) VALUES(?,?,?)", [
                ("UI Origin Selected", 2, "Death+Power"), ("UI Origin Sibling", 1, "Time")])
            db.executemany("INSERT INTO families VALUES(?,?,?,?)", [
                (8001, 3, 1, json.dumps(["UI Origin Selected", "UI Origin Sibling"])),
                (8002, 3, 1, json.dumps(["UI Origin Selected"]))])
        self.context = logger.scan_context()
        self.window = LoggerWindow()
        self.window._poll.stop()
        image = io.BytesIO()
        Image.new("RGB", (575, 720), "tan").save(image, format="PNG")
        self.raw = image.getvalue()

    def tearDown(self):
        """Retire queued callbacks and restore the incoming profile after closing SQLite handles."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        QTest.qWait(60)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def assert_selected_origin_survives_retry(self, *, confident, selected_family):
        """Approve the second recipe, mutate old capture evidence during contention and verify its frozen origin."""
        selected = {"selected_recipe": "UI Origin Selected", "can_use": confident,
                    "runes": ["Death", "Power"] if confident else [],
                    "positions": [1, 2] if confident else [],
                    "family": selected_family, "candidates": [8002]}
        sibling = {"selected_recipe": "UI Origin Sibling", "can_use": True,
                   "runes": ["Time"], "positions": [1], "family": "Family 8001", "candidates": [8001]}
        capture = {"mode": "propagation", **sibling, "choices": [sibling, selected], **self.context}
        self.window._propagation_read(capture, self.raw)
        self.window.propagation_recipe_table.setCurrentCell(1, 0)
        fields = self.window._propagation_row_inputs[1]
        if not confident:
            self.assertTrue(all(field.currentData() is None for field in fields))
            for field, slot in zip(fields, (0, 1)):
                field.setCurrentIndex(field.findData(slot))
        self.assertEqual([field.currentText() for field in fields], ["Death", "Power"])
        approve = self.window.propagation_recipe_table.cellWidget(1, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        with closing(sqlite3.connect(store.DATA_DIR / "scans.sqlite3", timeout=2)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            approve.click()
            pending = self.window._chain_save_pending
            self.assertIsNotNone(pending)
            frozen = pending["result"]
            self.assertEqual((frozen["family"], frozen["candidates"]), (selected_family, [8002]))
            self.assertEqual(logger.get_state()["chain"], [])
            # A caller may retain and update the OCR objects; queued approval owns a snapshot.
            self.window._propagation_choices[1]["family"] = "Family 8001"
            self.window._propagation_choices[1]["candidates"][:] = [8001]
            selected["family"] = "Family 8001"
            selected["candidates"][:] = [8001]
            self.window._manual_propagation_context["family"] = "Family 8001"
            self.assertEqual((frozen["family"], frozen["candidates"]), (selected_family, [8002]))
            approve.click()
            writer.rollback()
        deadline = time.monotonic() + 2
        while self.window._chain_save_pending is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window._chain_save_pending)
        state = logger.get_state()
        self.assertEqual(state["chain"], [{"step": 1, "rune1": "Death", "rune2": "Power"}])
        self.assertEqual((state["detonated"], state["scan_commit_count"]), (1, 2))
        origin = state["chain_provenance"][0]
        self.assertEqual((origin["recipe"], origin["family"], origin["family_candidates"], origin["family_source"]),
                         ("UI Origin Selected", 8002, [8002], "supplied" if selected_family else "candidates"))
        with logger._connect() as db:
            receipt = db.execute("SELECT payload_json,result_json FROM chain_append_receipts WHERE request_id=?",
                                 (frozen["_chain_accept_request"],)).fetchone()
            payload, saved = map(json.loads, receipt)
            self.assertEqual(payload["family_candidates"], [8002])
            self.assertEqual(payload.get("family"), 8002 if selected_family else None)
            self.assertEqual(saved["provenance"], [origin])
            db.execute("UPDATE families SET valid=0,recipes_json='[]' WHERE id=8002")
        replay = logger.accept_propagation_part(self.context, runes=frozen["runes"],
            recipe=frozen["selected_recipe"], request_id=frozen["_chain_accept_request"],
            family=frozen["family"], family_candidates=frozen["candidates"])
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["provenance"], [origin])
        self.window._retry_approved_chain_save(pending)
        self.assertEqual((logger.get_state()["detonated"], logger.get_state()["scan_commit_count"]), (1, 2))

    def test_manual_recipe_dropdowns_keep_selected_family_over_sibling_context(self):
        """Manual first/second slot choices save their row's explicit family, even after a busy retry."""
        self.assert_selected_origin_survives_retry(confident=False, selected_family="Family 8002")

    def test_confident_prefills_keep_row_candidates_and_explicit_unknown_family(self):
        """A confident row's explicit None and candidates override the sibling family's panel values."""
        self.assert_selected_origin_survives_retry(confident=True, selected_family=None)


if __name__ == "__main__":
    unittest.main()
