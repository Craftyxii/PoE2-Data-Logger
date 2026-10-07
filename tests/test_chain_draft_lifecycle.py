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

    def test_completed_chains_do_not_accumulate_empty_drafts(self):
        for _ in range(10):
            self.window.rune_inputs[0].setText("Death")
            self.window.commit_chain()
            self.assertEqual(self.window._chain_drafts, {})
        self.assertEqual(self.window.state["current_expedition_id"], "M0001-E11")

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
