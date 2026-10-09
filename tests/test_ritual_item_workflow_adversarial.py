"""Native review races retain independent propagation corrections during remnant OCR."""

import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class RitualItemWorkflowAdversarialTests(unittest.TestCase):
    """Inject only worker completions; operate the normal independent review controls."""

    @classmethod
    def setUpClass(cls):
        """Create the shared offscreen application required by native review widgets."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open isolated storage, stop polling, and hold submitted OCR work."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ritual-item-adversarial-")
        self.previous_data = store.DATA_DIR
        self.previous_mode = service.HOTKEY.status()["mode"]
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        logger.save_settings({"auto_commit": False, "ocr_auto_commit": False})
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.resize(1366, 1000)
        self.window.show()
        self.app.processEvents()
        self.jobs = []
        self.window._submit = lambda label, work, done=None: self.jobs.append((work, done))
        output = io.BytesIO()
        Image.new("RGB", (600, 400), (100, 120, 140)).save(output, format="PNG")
        self.raw = output.getvalue()
        self.path = Path(self.tmp.name) / "remnant.png"
        self.path.write_bytes(self.raw)

    def tearDown(self):
        """Close workers and restore the caller's data and scan mode."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        service.HOTKEY.set_mode(self.previous_mode)
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def pending_remnant(self, mode):
        """Queue a real file scan while leaving its recognition completion pending."""
        self.window.mode_select.setCurrentIndex(self.window.mode_select.findData(mode))
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            self.window.scan_file()
        context = logger.scan_context()
        self.assertIsNotNone(self.window._remnant_reading)
        return self.jobs[-1][1], context

    def uncertain_propagation_with_selection(self, context, confident=False):
        """Keep both uncertain corrections and confident prefilled runes pending for approval."""
        self.window._propagation_read({"mode": "propagation", "can_use": confident,
            "runes": ["Death"] if confident else [], "positions": [1] if confident else [],
            "selected_recipe": "Divine Orb x2", "_ocr_strictness": 0,
            "status": "Choose the marked runes.",
            "choices": [{"selected_recipe": "Divine Orb x2",
                         "runes": ["Death"] if confident else [],
                         "positions": [1] if confident else [],
                         "can_use": confident}], **context}, self.raw)
        table = self.window.propagation_recipe_table
        table.setCurrentCell(0, 0)
        first, _second = self.window._propagation_row_inputs[0]
        self.window.tabs.widget(0).ensureWidgetVisible(table)
        self.app.processEvents()
        self.assertTrue(first.isVisible())
        QTest.mouseClick(first, Qt.MouseButton.LeftButton)
        QTest.keyClick(first, Qt.Key.Key_Escape)
        QTest.keyClick(first, Qt.Key.Key_Home)
        QTest.keyClick(first, Qt.Key.Key_Down)
        self.app.processEvents()
        self.assertEqual(first.currentData(), 0)
        self.assertEqual(first.currentText(), "Death")
        approve = table.cellWidget(0, 2).findChild(QPushButton, "approvePropagationRecipe")
        self.assertTrue(approve.isEnabled())
        self.assertEqual(logger.get_state()["scan_commit_count"], 0)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertIsNone(logger.get_state()["detonated"])
        return copy.deepcopy(self.window._manual_propagation_context)

    def opened_result(self, context):
        """Build a real family-26 reward sequence at the scanner boundary."""
        with logger._connect() as db:
            rewards = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=26").fetchone()[0])[1:]
        return {"mode": "opened", "can_use": True, "family": "Family 26",
                "candidates": [26], "sockets": 3, "status": "Review opened rewards.",
                "first_recipe": rewards[0], "next_recipe": rewards[1],
                "opened_recipes": [{"recipe": reward} for reward in rewards], **context}

    def seed_result(self, context):
        """Build a valid family-3 seed from the genuine catalog stage."""
        with logger._connect() as db:
            stage = dict(db.execute("SELECT * FROM seed_states WHERE family=3 ORDER BY sockets DESC LIMIT 1").fetchone())
        reading = {"sockets": stage["sockets"], "seed_slot": stage["seed_slot"],
                   "seed_rune": stage["seed_rune"], "family": "Family 3",
                   "candidates": [3], "can_commit": True,
                   "rewards": json.loads(stage["rewards_json"])}
        return {"mode": "seed", "status": "Review visible seed.",
                "remnants": [reading], **context}

    def finish_independent_reviews(self, kind, expected_propagation):
        """Finish both activities through their buttons without rescanning lost corrections."""
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertIsNone(logger.get_state()["detonated"])
        self.assertEqual(self.window.review_kind.property("scanKind"), "propagation")
        self.assertEqual(self.window.pending_review_kind, kind)
        self.assertEqual(self.window._manual_propagation_context, expected_propagation)
        table = self.window.propagation_recipe_table
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(self.window._propagation_row_inputs[0][0].currentText(), "Death")
        table.cellWidget(0, 2).findChild(QPushButton, "approvePropagationRecipe").click()
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual([(part["rune1"], part["rune2"]) for part in logger.get_state()["chain"]],
                         [("Death", "")])
        self.window.manual_remnant_button.click()
        self.assertEqual(self.window.review_kind.property("scanKind"), kind)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.approve_scan_button.click()
        self.window.approve_scan_button.click()
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertEqual(logger.get_state()["detonated"], 1)
        with logger._connect() as db:
            remnants = db.execute("SELECT reference,map_id,expedition_id FROM commits WHERE kind='Remnant'").fetchall()
        self.assertEqual([tuple(row) for row in remnants], [("R0001", "M0001", "M0001-E01")])

    def test_opened_worker_completion_preserves_independent_recipe_correction(self):
        """An earlier opened OCR completion must not erase a newer propagation selection."""
        callback, context = self.pending_remnant("opened")
        expected = self.uncertain_propagation_with_selection(context)
        callback(self.opened_result(context))
        self.finish_independent_reviews("remnant", expected)

    def test_seed_worker_completion_preserves_independent_recipe_correction(self):
        """An earlier seed OCR completion must not erase a newer propagation selection."""
        callback, context = self.pending_remnant("seed")
        expected = self.uncertain_propagation_with_selection(context)
        callback(self.seed_result(context))
        self.finish_independent_reviews("seed", expected)

    def test_confident_propagation_waits_for_approval_during_opened_completion(self):
        """Auto settings cannot save a confident propagation part behind an opened result."""
        self.window.set_auto_all(True)
        callback, context = self.pending_remnant("opened")
        expected = self.uncertain_propagation_with_selection(context, confident=True)
        callback(self.opened_result(context))
        self.finish_independent_reviews("remnant", expected)

    def test_confident_propagation_waits_for_approval_during_seed_completion(self):
        """Auto settings cannot save a confident propagation part behind a seed result."""
        self.window.set_auto_all(True)
        callback, context = self.pending_remnant("seed")
        expected = self.uncertain_propagation_with_selection(context, confident=True)
        callback(self.seed_result(context))
        self.finish_independent_reviews("seed", expected)


if __name__ == "__main__":
    unittest.main()
