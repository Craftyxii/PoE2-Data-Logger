"""Real opened socket geometry reaches desktop review without bypassing approval or conflicts."""

import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


CAPTURE = Path(__file__).resolve().parent / (
    "fixtures/opened_rewards/Pair67_7socket_Family_28_TranscendentAlloy_opened.jpg")
GEMS_CAPTURE = CAPTURE.parent / "Pair32_6socket_Family73_Thaumaturgic_Gems_opened.jpg"
VERISIUM_CAPTURE = CAPTURE.parent / "Pair61_5socket_Family_57_Thaumaturgic_Verisuim_chain_opened.jpg"


class OpenedSocketReviewUITests(unittest.TestCase):
    """Exercise the actual single-reward capture through existing review and approval controls."""

    @classmethod
    def setUpClass(cls):
        """Reuse the QApplication for isolated desktop profiles."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Start a fresh logger map and stop polling before delivering the real capture."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-opened-socket-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        """Close workers and callbacks before restoring the incoming logger profile."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def logged_records(self):
        """Snapshot committed records and exported rows without including the review reservation."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(
                f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("commits", "new_export")}

    def show_capture(self):
        """Run production OCR and deliver its result with fresh capture ownership and original pixels."""
        reading = {**opened_scan.scan_opened(CAPTURE), **logger.scan_context()}
        self.assertEqual(reading["first_recipe"], "Transcendent Alloy", reading)
        self.assertIsNone(reading["next_recipe"], reading)
        self.assertEqual([row["recipe"] for row in reading["opened_recipes"]],
                         ["Transcendent Alloy"], reading)
        self.assertEqual((reading["family"], reading["candidates"]), ("Family 28", [28]), reading)
        self.assertEqual((reading["sockets"], reading["socket_source"]), (7, "opened icons"), reading)
        self.assertTrue(reading["header_verified"], reading)
        self.assertTrue(reading["list_complete"], reading)
        self.window._scan_done("opened", reading, CAPTURE.read_bytes())
        self.app.processEvents()
        self.assertTrue(self.window.preview.isVisible())
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertEqual(self.window.pending_review_kind, "remnant")
        pending = logger.get_state()["ocr_pending"]
        self.assertIsNotNone(pending)
        self.assertEqual(tuple(pending[key] for key in ("remnant_id", "map_id", "expedition_id")),
                         tuple(reading[key] for key in ("remnant_id", "map_id", "expedition_id")))
        return reading, pending

    def test_real_single_reward_seven_sockets_waits_for_manual_approval(self):
        """The original Family 28 screenshot previews seven sockets and saves one remnant only on Approve."""
        self.window.set_auto_commit(False)
        before = logger.get_state()
        records = self.logged_records()
        with logger._connect() as db:
            combo = db.execute("SELECT combo FROM recipes WHERE name=?",
                               ("Transcendent Alloy",)).fetchone()[0]
        reading, pending = self.show_capture()
        self.assertTrue(reading["can_use"], reading)
        self.assertEqual(reading["recipe_sockets"], 7, reading)
        self.assertEqual(logger.get_state()["scan_commit_count"], before["scan_commit_count"])
        self.assertEqual(self.logged_records(), records)
        self.assertEqual(self.window.recipe_table.rowCount(), 1)
        self.assertEqual([self.window.recipe_table.item(0, column).text() for column in range(3)],
                         ["Transcendent Alloy", "7", combo])
        self.assertTrue(self.window.approve_scan_button.isEnabled())

        self.window.approve_scan_button.click()
        self.app.processEvents()
        saved = logger.get_state()
        self.assertEqual(saved["scan_commit_count"], before["scan_commit_count"] + 1)
        self.assertIsNone(saved["ocr_pending"])
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window.results["opened"])
        self.assertIsNone(self.window.images["opened"])
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.assertEqual(self.window.recipe_table.property("savedRemnant"), pending["remnant_id"])
        self.assertEqual(self.window.recipe_table.item(0, 1).text(), "7")
        with logger._connect() as db:
            commits = db.execute("SELECT reference,details_json FROM commits WHERE kind='Remnant'").fetchall()
            exports = db.execute("SELECT row_json FROM new_export WHERE remnant_id=?",
                                 (pending["remnant_id"],)).fetchall()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["reference"], pending["remnant_id"])
        self.assertEqual(json.loads(commits[0]["details_json"]),
                         {"family": 28, "recipes": [{"recipe": "Transcendent Alloy",
                                                      "sockets": 7, "combo": combo}]})
        self.assertEqual([json.loads(row["row_json"])[1:3] for row in exports],
                         [["Transcendent Alloy", 7]])

    def test_real_seven_sockets_with_database_conflict_stays_pending_with_auto_enabled(self):
        """Automatic saving keeps the screenshot pending when its seven visible sockets contradict the catalog."""
        with logger._connect() as db:
            db.execute("UPDATE recipes SET sockets=4 WHERE name=?", ("Transcendent Alloy",))
        self.window.set_auto_commit(True)
        before = logger.get_state()
        records = self.logged_records()
        reading, pending = self.show_capture()
        self.assertFalse(reading["can_use"], reading)
        self.assertEqual(reading["recipe_sockets"], 4, reading)
        self.assertIn("Opened icons show 7 sockets, but Recipe DB says 4", reading["status"])
        self.assertIn(reading["status"], self.window.review_summary.text())
        self.assertEqual(self.window.results["opened"], reading)
        self.assertEqual(logger.get_state()["ocr_pending"], pending)
        self.assertEqual(logger.get_state()["scan_commit_count"], before["scan_commit_count"])
        self.assertEqual(self.logged_records(), records)

    def approve_recovered_top(self, capture, first, family, sockets):
        """Review original recovered pixels and save the correct family once through existing Approve."""
        self.window.set_auto_commit(False)
        before = logger.get_state()["scan_commit_count"]
        records = self.logged_records()
        reading = {**opened_scan.scan_opened(capture), **logger.scan_context()}
        self.assertEqual(reading["first_recipe"], first, reading)
        self.assertEqual((reading["family"], reading["sockets"]), (f"Family {family}", sockets), reading)
        self.window._scan_done("opened", reading, capture.read_bytes())
        self.app.processEvents()
        self.assertTrue(self.window.preview.isVisible())
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertEqual(self.window.pending_review_kind, "remnant")
        pending = logger.get_state()["ocr_pending"]
        self.assertIsNotNone(pending)
        self.assertEqual(self.window.recipe_table.item(0, 0).text(), first)
        self.assertEqual(self.window.recipe_table.item(0, 1).text(), str(sockets))
        self.assertEqual(self.logged_records(), records)
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.window.approve_scan_button.click()
        self.app.processEvents()
        self.assertEqual(logger.get_state()["scan_commit_count"], before + 1)
        self.assertIsNone(logger.get_state()["ocr_pending"])
        self.assertIsNone(self.window.results["opened"])
        with logger._connect() as db:
            commits = db.execute("SELECT reference,details_json FROM commits WHERE kind='Remnant'").fetchall()
            exports = db.execute("SELECT row_json FROM new_export WHERE remnant_id=? ORDER BY rowid",
                                 (pending["remnant_id"],)).fetchall()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["reference"], pending["remnant_id"])
        details = json.loads(commits[0]["details_json"])
        self.assertEqual(details["family"], family)
        self.assertEqual((details["recipes"][0]["recipe"], details["recipes"][0]["sockets"]),
                         (first, sockets))
        self.assertEqual(json.loads(exports[0]["row_json"])[1:3], [first, sockets])

    def test_original_gem_top_reward_still_requires_manual_review_and_saves_level_20(self):
        """Correcting Pair32 leaves screenshot review and the existing manual approval intact."""
        self.approve_recovered_top(GEMS_CAPTURE, "Thaumaturgic Flux (Level 20)", 73, 6)

    def test_original_verisium_top_reward_still_requires_manual_review_and_saves_level_19(self):
        """Correcting Pair61 saves its Level 19 first reward ahead of Level 20 skills."""
        self.approve_recovered_top(VERISIUM_CAPTURE, "Thaumaturgic Flux (Level 19)", 57, 5)


if __name__ == "__main__":
    unittest.main()
