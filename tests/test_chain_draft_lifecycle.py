import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class ChainDraftLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-chain-drafts-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_appending_and_completing_chains_do_not_accumulate_empty_drafts(self):
        for number in range(1, 11):
            self.window.rune_inputs[0].setText("Death")
            self.window.commit_chain()
            self.assertEqual(self.window.state["current_expedition_id"], f"M0001-E{number:02}")
            self.assertEqual([(part["rune1"], part["rune2"]) for part in self.window.state["chain"]],
                             [("Death", "")])
            self.assertTrue(all(not field.text() for field in self.window.rune_inputs))
            self.assertEqual(self.window._chain_drafts, {})
            self.window.complete_chain()
            self.assertEqual(self.window._chain_drafts, {})
        self.assertEqual(self.window.state["current_expedition_id"], "M0001-E11")

    def test_committed_open_chain_survives_restart_while_unsaved_fields_do_not(self):
        self.window.rune_inputs[0].setText("Death")
        self.window.expedition_commit_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.window.rune_inputs[0].setText("Time")
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        logger._READY = False
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E01")
        self.assertFalse(logger.get_state()["chain_completed"])
        self.assertEqual([(part["rune1"], part["rune2"]) for part in self.window.state["chain"]],
                         [("Death", "")])
        self.assertEqual([self.window.chain_list.item(i).text()
                          for i in range(self.window.chain_list.count())], ["#1  Death"])
        self.assertTrue(all(not field.text() for field in self.window.rune_inputs))
        self.assertEqual(self.window._chain_drafts, {})
        self.assertFalse(self.window.expedition_commit_chain_button.isEnabled())
        self.assertTrue(self.window.expedition_complete_chain_button.isEnabled())
        self.window.expedition_complete_chain_button.click()
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")

    def test_clearing_one_draft_preserves_other_expedition_unsaved_runes(self):
        self.window.rune_inputs[0].setText("Rage")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.window.rune_inputs[0].setText("Time")
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(1))
        self.assertEqual(self.window.rune_inputs[0].text(), "Rage")
        self.window.rune_inputs[0].clear()
        self.assertEqual(len(self.window._chain_drafts), 1)
        self.window.header_expedition.setCurrentIndex(self.window.header_expedition.findData(2))
        self.assertEqual(self.window.rune_inputs[0].text(), "Time")


if __name__ == "__main__":
    unittest.main()
