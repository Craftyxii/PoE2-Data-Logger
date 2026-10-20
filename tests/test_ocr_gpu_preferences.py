"""Opt-in GPU preferences stay out of map data and freeze with each OCR worker."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers, store


def _gpu_context():
    """Observe the actual spawned process preference without loading an OCR model."""
    return {"pid": os.getpid(), "gpu_labels": ocr_runtime.active_gpu_labels(),
            "cpu_threads": ocr_runtime.active_threads()}


class GPUPreferenceTests(unittest.TestCase):
    """Persist only booleans, keep defaults and active settings distinct, and isolate map data."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="poe2-gpu-preference-"))
        self.stack.enter_context(patch.object(store, "DATA_DIR", Path(temporary)))
        self.stack.enter_context(patch.object(logger, "_READY", False))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", None))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_THREADS", 2))
        logger.initialize()

    def test_default_off_persists_outside_map_settings_and_exports(self):
        self.assertIs(ocr_runtime.saved_gpu_labels(), False)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        self.assertIs(ocr_runtime.save_gpu_labels(True), True)
        self.assertIs(ocr_runtime.saved_gpu_labels(), True)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        self.assertNotIn("ocr_gpu_labels", logger.get_state()["settings"])
        self.assertNotIn(b"ocr_gpu_labels", logger.export_all_csv())
        self.assertEqual(ocr_runtime.active_threads(), 2)

    def test_invalid_user_and_stored_values_do_not_enable_gpu(self):
        for enabled in (0, 1, None, "true", "false", 1.0, [], {}):
            with self.subTest(enabled=enabled):
                with self.assertRaisesRegex(ValueError, "must be on or off"):
                    ocr_runtime.save_gpu_labels(enabled)
                with logger._connect() as db:
                    logger._set_meta(db, "ocr_gpu_labels", enabled)
                self.assertIs(ocr_runtime.saved_gpu_labels(), False)
        with logger._connect() as db:
            db.execute("UPDATE meta SET value=? WHERE key=?", ("invalid JSON", "ocr_gpu_labels"))
        self.assertIs(ocr_runtime.saved_gpu_labels(), False)

    def test_save_before_first_scan_freezes_old_value_until_restart(self):
        ocr_runtime.save_gpu_labels(True)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        with patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", None):
            self.assertIs(ocr_runtime.active_gpu_labels(), True)
            ocr_runtime.save_gpu_labels(False)
            self.assertIs(ocr_runtime.active_gpu_labels(), True)
        self.assertIs(ocr_runtime.saved_gpu_labels(), False)

    def test_failed_write_does_not_change_saved_or_active_preference(self):
        with patch.object(logger, "_set_meta", side_effect=OSError("Preference write failed")):
            with self.assertRaisesRegex(OSError, "Preference write failed"):
                ocr_runtime.save_gpu_labels(True)
        self.assertIs(ocr_runtime.saved_gpu_labels(), False)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)

    def test_concurrent_first_read_freezes_one_boolean(self):
        with patch.object(ocr_runtime, "saved_gpu_labels", return_value=True) as read:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _: ocr_runtime.active_gpu_labels(), range(16))),
                                 [True] * 16)
            read.assert_called_once_with()


class GPUWorkerContextTests(unittest.TestCase):
    """A descriptor and its child keep the captured GPU preference across later saves."""

    def setUp(self):
        ocr_workers.shutdown(wait=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="poe2-gpu-worker-"))
        self.stack.enter_context(patch.object(store, "DATA_DIR", Path(temporary)))
        self.stack.enter_context(patch.object(logger, "_READY", False))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", False))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_THREADS", 2))
        self.stack.enter_context(patch.object(ocr_workers, "MAX_WORKERS", 1))
        self.addCleanup(lambda: ocr_workers.shutdown(wait=True))

    def work(self, enabled):
        return ocr_workers.Request(_gpu_context, (), {}, str(store.DATA_DIR), 2,
                                   time.monotonic() + 10, gpu_labels=enabled)

    def test_old_six_positional_arguments_keep_deadline_and_default_off(self):
        deadline = time.monotonic() + 10
        work = ocr_workers.Request(_gpu_context, (), {}, str(store.DATA_DIR), 2, deadline)
        self.assertEqual(work.deadline, deadline)
        self.assertIs(work.gpu_labels, False)

    def test_factory_freezes_gpu_flag_and_child_receives_it(self):
        with patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", True):
            work = ocr_workers.request(ocr_runtime.active_gpu_labels)
        self.assertIs(work.gpu_labels, True)
        self.assertIs(ocr_workers.submit(work).result(timeout=10), True)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)

    def test_changed_gpu_context_replaces_child_and_same_context_reuses_it(self):
        first = ocr_workers.submit(self.work(False)).result(timeout=10)
        second_work = self.work(True)
        second = ocr_workers.submit(second_work).result(timeout=10)
        third = ocr_workers.submit(replace(second_work, deadline=time.monotonic() + 10)).result(timeout=10)
        self.assertIs(first["gpu_labels"], False)
        self.assertIs(second["gpu_labels"], True)
        self.assertEqual(first["cpu_threads"], second["cpu_threads"])
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertEqual(second, third)


class GPUPreferenceUITests(unittest.TestCase):
    """The checkbox sits beside the existing core selector and rolls back failed saves."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
        cls.application = QApplication.instance() or QApplication([])
        cls.window_type = LoggerWindow

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory(prefix="poe2-gpu-controls-"))
        self.stack.enter_context(patch.object(store, "DATA_DIR", Path(temporary)))
        self.stack.enter_context(patch.object(logger, "_READY", False))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", None))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_THREADS", 2))
        self.window = self.window_type()
        self.window._poll.stop()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.application.processEvents()

    def test_checkbox_is_off_beside_unchanged_core_choices_and_autosaves_for_restart(self):
        box, selector = self.window.ocr_gpu_labels_checkbox, self.window.ocr_threads_select
        self.assertEqual(box.text(), "Use GPU for Currency / Ritual")
        self.assertFalse(box.isChecked())
        self.assertEqual([selector.itemData(i) for i in range(selector.count())], [1, 2, 4, 6])
        row = selector.parentWidget().layout().itemAt(0).layout()
        self.assertIs(row.itemAt(1).widget(), selector)
        self.assertIs(row.itemAt(2).widget(), box)
        box.setChecked(True)
        self.assertIs(ocr_runtime.saved_gpu_labels(), True)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        self.assertIn("Saved GPU: on; restart the app", self.window.ocr_threads_status.text())
        self.assertEqual(ocr_runtime.active_threads(), 2)

    def test_failed_save_restores_checkbox_and_reports_error(self):
        with patch.object(ocr_runtime, "save_gpu_labels", side_effect=OSError("GPU preference write failed")):
            self.window.ocr_gpu_labels_checkbox.setChecked(True)
        self.assertFalse(self.window.ocr_gpu_labels_checkbox.isChecked())
        self.assertIs(ocr_runtime.saved_gpu_labels(), False)
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        self.assertIn("GPU preference write failed", self.window.statusBar().currentMessage())

    def test_reopened_window_shows_saved_choice_without_changing_active_runtime(self):
        self.window.ocr_gpu_labels_checkbox.setChecked(True)
        self.close_window()
        self.window = self.window_type()
        self.window._poll.stop()
        self.assertTrue(self.window.ocr_gpu_labels_checkbox.isChecked())
        self.assertIs(ocr_runtime.active_gpu_labels(), False)
        self.assertIn("Saved GPU: on", self.window.ocr_threads_status.text())


if __name__ == "__main__":
    unittest.main()
