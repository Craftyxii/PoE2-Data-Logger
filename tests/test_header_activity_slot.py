"""The header's Complete chain slot follows the map's Activity Type.

Expedition, Grand Expedition and no activity keep the Complete chain button and the
60-second same-chain question; Ritual shows its saved pages; Abyss and Breach show nothing.
"""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import native_desktop as ui


class RetainedFuture(Future):
    def cancel(self):
        return False


class HeaderActivitySlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-header-slot-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({"ocr_auto_commit": True})
        self.manager = hotkey.HotkeyManager(supported=False)
        self.hotkey_patch = patch.object(service, "HOTKEY", self.manager)
        self.hotkey_patch.start()
        self.window = ui.LoggerWindow()
        self.window._poll.stop()
        self.window.show()
        self.app.processEvents()
        self.futures = []
        self.submit_patch = patch.object(ui.ocr_workers, "submit", side_effect=self.submit)
        self.submit_patch.start()

    def tearDown(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.submit_patch.stop()
        self.hotkey_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def submit(self, request):
        future = RetainedFuture()
        self.futures.append(future)
        return future

    def choose_activity(self, activity):
        control = self.window.header_activity
        control.setCurrentIndex(control.findData(activity))
        self.app.processEvents()
        self.assertEqual(logger.get_state()["settings"]["activity_type"], activity)

    def slot(self):
        chain, ritual = self.window.header_complete_chain_button, self.window.header_ritual_status
        if chain.isVisibleTo(self.window):
            return "Complete chain"
        return ritual.text() if ritual.isVisibleTo(self.window) else ""

    def capture_ritual(self, index):
        raw = io.BytesIO()
        Image.new("RGB", (600, 250), (index * 40 % 256, 90, 90)).save(raw, format="PNG")
        result = {"captured": True, "map_id": logger.get_state()["current_map_id"], **logger.scan_context(),
                  "_ocr_strictness_values": {"ritual": 50}}
        with self.manager._lock:
            self.manager._publish_event({"mode": "ritual", "result": result, "error": ""}, raw.getvalue())
        self.window.poll()
        self.app.processEvents()

    def finish_ritual(self, index, rolls):
        self.futures[index].set_result({
            "items": [{"category": "Item", "name": "Divine Orb", "quantity": 1, "tribute": 100,
                       "source": "Ritual offer", "deferred": False, "score": .99, "name_match": 1,
                       "needs_review": False}],
            "raw_text": "5000 TRIBUTE", "reward_count": 1, "needs_review": False, "coverage_uncertain": False,
            "tribute_available": 5000, "rerolls_remaining": rolls})
        self.app.processEvents()

    def test_slot_shows_complete_chain_for_expedition_and_no_activity(self):
        self.assertEqual(self.slot(), "Complete chain")
        for activity in ("Expedition", "Grand Expedition", "None"):
            with self.subTest(activity=activity):
                self.choose_activity(activity)
                self.assertEqual(self.slot(), "Complete chain")

    def test_slot_is_empty_for_abyss_and_breach(self):
        for activity in ("Abyss", "Breach"):
            with self.subTest(activity=activity):
                self.choose_activity(activity)
                self.assertEqual(self.slot(), "")

    def test_ritual_slot_counts_saved_pages_and_follows_new_map_and_undo(self):
        self.choose_activity("Ritual")
        self.assertEqual(self.slot(), "M0001 · 0 Ritual pages")
        self.capture_ritual(1)
        self.capture_ritual(2)
        self.assertEqual(self.slot(), "M0001 · 0 Ritual pages\n1 waiting")
        self.finish_ritual(0, 6)
        self.assertEqual(self.slot(), "M0001 · 1 Ritual page\n1 waiting")
        self.window.poll()
        self.app.processEvents()
        self.assertEqual(self.slot(), "M0001 · 1 Ritual page")
        self.finish_ritual(1, 5)
        self.assertEqual(self.slot(), "M0001 · 2 Ritual pages")
        self.window.finish_map()
        self.app.processEvents()
        self.assertEqual(self.slot(), "M0002 · 0 Ritual pages")
        # + New map is two Undo steps (New map, then Finalize map); the third removes the last page.
        for action, expected in (("New map", "M0001 · 2 Ritual pages"), ("Finalize map", "M0001 · 2 Ritual pages"),
                                 ("Ritual approval", "M0001 · 1 Ritual page")):
            self.assertEqual(self.window.undo_map()["action"], action)
            self.app.processEvents()
            self.assertEqual(self.slot(), expected)

    def test_ritual_slot_shows_the_rite_stage(self):
        self.choose_activity("Ritual")
        self.window.state["ritual_rite"] = {**(self.window.state.get("ritual_rite") or {}), "map_ordinal": 2}
        self.window._sync_header_activity_slot()
        self.assertEqual(self.slot(), "M0001 · 0 Ritual pages\nRite stage 2")

    def test_switching_back_to_expedition_restores_the_complete_chain_button(self):
        self.choose_activity("Ritual")
        self.choose_activity("Expedition")
        self.assertEqual(self.slot(), "Complete chain")
        self.assertFalse(self.window.header_ritual_status.isVisibleTo(self.window))

    def test_sixty_second_chain_question_still_fires_on_expedition_maps(self):
        for activity in ("Expedition", "Grand Expedition"):
            with self.subTest(activity=activity):
                self.choose_activity(activity)
                entered = datetime.now(timezone.utc) - timedelta(seconds=120)
                with patch.object(logger, "_now", return_value=entered.isoformat()):
                    logger.commit_chain("Death")
                self.window.refresh()
                self.assertTrue(self.window.header_complete_chain_button.isEnabled())
                with patch.object(ui.QMessageBox, "question",
                                  return_value=ui.QMessageBox.StandardButton.Yes) as question:
                    self.assertTrue(self.window._confirm_chain_continuity(dict(logger.scan_context())))
                question.assert_called_once()
                self.assertEqual(question.call_args.args[1:3], ("Remnant chain", "Is this the same chain?"))
                self.window.header_complete_chain_button.click()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
