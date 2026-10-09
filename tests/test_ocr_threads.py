"""OCR runtime preference checks for bounded CPU thread settings and engine/session configuration."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, store
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class OCRThreadPreferenceTests(unittest.TestCase):
    """Check supported OCR thread preferences persist separately from the process-wide active setting."""
    def setUp(self):
        """Initialize isolated logger data and reset the active OCR thread cache."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ocr-threads-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        self.active_patch = patch.object(ocr_runtime, "_ACTIVE_THREADS", None)
        self.active_patch.start()
        logger.initialize()

    def tearDown(self):
        """Restore the active-thread patch and data directory, then remove the isolated database."""
        self.active_patch.stop()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_default_and_all_supported_preferences_are_saved_outside_map_settings(self):
        """Verify default and all supported preferences are saved outside map settings."""
        self.assertEqual(ocr_runtime.saved_threads(), 2)
        self.assertEqual(ocr_runtime.active_threads(), 2)
        for count in (1, 2, 4, 6):
            with self.subTest(count=count):
                ocr_runtime.save_threads(count)
                self.assertEqual(ocr_runtime.saved_threads(), count)
                self.assertEqual(ocr_runtime.active_threads(), 2)
                self.assertNotIn("ocr_cpu_threads", logger.get_state()["settings"])
        self.assertNotIn(b"ocr_cpu_threads", logger.export_all_csv())

    def test_invalid_values_are_rejected_without_changing_saved_or_active_setting(self):
        """Verify invalid values are rejected without changing saved or active setting."""
        for count in (True, False, None, "4", 4.0, 0, -1, 3, 8, 100):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "must be 1, 2, 4 or 6"):
                    ocr_runtime.save_threads(count)
                self.assertEqual(ocr_runtime.saved_threads(), 2)
                self.assertEqual(ocr_runtime.active_threads(), 2)

    def test_invalid_stored_preference_safely_uses_default(self):
        """Verify invalid stored preference safely uses default."""
        for count in (True, "6", 6.0, 8, None, {"threads": 4}):
            with self.subTest(count=count):
                with logger._connect() as db:
                    logger._set_meta(db, "ocr_cpu_threads", count)
                self.assertEqual(ocr_runtime.saved_threads(), 2)
        with logger._connect() as db:
            db.execute("UPDATE meta SET value=? WHERE key=?", ("invalid JSON", "ocr_cpu_threads"))
        self.assertEqual(ocr_runtime.saved_threads(), 2)

    def test_saved_change_before_first_model_keeps_original_process_setting(self):
        """Verify saved change before first model keeps original process setting."""
        ocr_runtime.save_threads(6)
        self.assertEqual(ocr_runtime.active_threads(), 2)
        self.assertEqual(ocr_runtime.saved_threads(), 6)
        # A fresh process starts with the persisted setting.
        with patch.object(ocr_runtime, "_ACTIVE_THREADS", None):
            self.assertEqual(ocr_runtime.active_threads(), 6)

    def test_concurrent_first_read_initializes_one_shared_value(self):
        """Verify concurrent first read initializes one shared value."""
        with patch.object(ocr_runtime, "saved_threads", return_value=4) as read:
            with ThreadPoolExecutor(max_workers=6) as pool:
                counts = list(pool.map(lambda _: ocr_runtime.active_threads(), range(30)))
            self.assertEqual(counts, [4] * 30)
            read.assert_called_once_with()

    def test_real_sessions_use_startup_count_and_do_not_rebuild_after_save(self):
        """Verify real sessions use startup count and do not rebuild after save."""
        root = Path(__file__).resolve().parents[1]
        program = """
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, store
from PoE2_Data_Logger.ocr import item_ocr, opened_scan, runehelper_ocr

store.DATA_DIR = Path(sys.argv[1])
logger._READY = False
with logger._connect() as db:
    logger._set_meta(db, "ocr_cpu_threads", 4)
assert ocr_runtime.active_threads() == 4
engine = opened_scan._engine()
info, native = runehelper_ocr._model()
sessions = [native] + [part.session.session for part in
    (engine.text_det, engine.text_cls, engine.text_rec)]
for session in sessions:
    options = session.get_session_options()
    assert options.intra_op_num_threads == 4
    assert options.inter_op_num_threads == 1
assert runehelper_ocr._read_row(np.full((32, 160), 180, dtype=np.uint8)) == ("", 0)
image = Image.new("RGB", (600, 150), "white")
font = ImageFont.truetype("PoE2_Data_Logger/fonts/DejaVuSans.ttf", 36)
ImageDraw.Draw(image).text((20, 40), "Chaos Orb", font=font, fill="black")
assert any("Chaos Orb" in row["text"] for row in item_ocr.ocr_lines(image))
ocr_runtime.save_threads(6)
assert ocr_runtime.saved_threads() == 6
assert ocr_runtime.active_threads() == 4
assert opened_scan._engine() is engine
assert runehelper_ocr._model()[1] is native
assert all(session.get_session_options().intra_op_num_threads == 4 for session in sessions)
"""
        result = subprocess.run([sys.executable, "-c", program, self.tmp.name],
                                cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class OCRThreadSettingUITests(unittest.TestCase):
    """Check the thread selector shows saved and active counts, restart requirements and save failures."""
    @classmethod
    def setUpClass(cls):
        """Create or reuse the QApplication required by the Qt test fixtures."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open an isolated logger window with a fresh active OCR thread cache and polling stopped."""
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-ocr-threads-ui-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        self.active_patch = patch.object(ocr_runtime, "_ACTIVE_THREADS", None)
        self.active_patch.start()
        self.window = LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        """Close the window and workers, restore thread/data state and remove temporary logger data."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.active_patch.stop()
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.tmp.cleanup()

    def test_setting_shows_supported_values_and_pending_restart_without_changing_workers(self):
        """Verify setting shows supported values and pending restart without changing workers."""
        selector = self.window.ocr_threads_select
        self.assertEqual([selector.itemData(i) for i in range(selector.count())], [1, 2, 4, 6])
        self.assertEqual(selector.currentData(), 2)
        self.assertEqual(self.window.pool._max_workers, 2)
        selector.setCurrentIndex(selector.findData(4))
        self.assertEqual(ocr_runtime.saved_threads(), 4)
        self.assertEqual(ocr_runtime.active_threads(), 2)
        self.assertIn("Using 2", self.window.ocr_threads_status.text())
        self.assertIn("Saved: 4", self.window.ocr_threads_status.text())
        self.assertIn("restart the app", self.window.ocr_threads_status.text())
        self.assertEqual(self.window.pool._max_workers, 2)
        selector.setCurrentIndex(selector.findData(2))
        self.assertNotIn("Saved:", self.window.ocr_threads_status.text())

    def test_saved_setting_is_displayed_when_window_reopens_without_reloading_runtime(self):
        """Verify saved setting is displayed when window reopens without reloading runtime."""
        ocr_runtime.save_threads(6)
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.assertEqual(self.window.ocr_threads_select.currentData(), 6)
        self.assertEqual(ocr_runtime.active_threads(), 2)
        self.assertIn("Using 2", self.window.ocr_threads_status.text())
        self.assertIn("Saved: 6", self.window.ocr_threads_status.text())

    def test_failed_save_restores_selection_and_reports_error(self):
        """Verify failed save restores selection and reports error."""
        with patch.object(ocr_runtime, "save_threads", side_effect=OSError("Could not save preference")):
            self.window.ocr_threads_select.setCurrentIndex(self.window.ocr_threads_select.findData(4))
        self.assertEqual(self.window.ocr_threads_select.currentData(), 2)
        self.assertEqual(ocr_runtime.saved_threads(), 2)
        self.assertEqual(ocr_runtime.active_threads(), 2)
        self.assertNotIn("Saved:", self.window.ocr_threads_status.text())
        self.assertIn("Could not save preference", self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
