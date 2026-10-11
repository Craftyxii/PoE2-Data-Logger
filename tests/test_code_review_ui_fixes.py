"""Regressions for reference Undo, contended user saves and exception-safe window cleanup."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox
from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.ui import native_desktop as ui


class CodeReviewUIFixTests(unittest.TestCase):
    """Exercise the actual window with disposable SQLite state and real writer contention."""
    @classmethod
    def setUpClass(cls):
        """Share Qt's required application across the isolated windows."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Create a fresh profile without changing the user's saved logs."""
        self.temp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temp.name)
        logger._READY = False
        logger.initialize()
        self.window = ui.LoggerWindow()
        self.window._poll.stop()

    def tearDown(self):
        """Retire Qt/worker resources and restore the storage root."""
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temp.cleanup()

    def writer(self):
        """Acquire a genuine competing SQLite writer to exercise the user save path."""
        db = sqlite3.connect(store.DATA_DIR / 'scans.sqlite3')
        db.execute('BEGIN IMMEDIATE')
        self.addCleanup(db.close)
        return db

    def test_reference_add_refreshes_exhausted_undo_and_duplicate_is_noop(self):
        """Adding a name enables the real Undo button; duplicate adds keep its action truthful."""
        while logger.undo_action():
            logger.undo_last_action()
        self.window.refresh()
        self.assertFalse(self.window.undo_button.isEnabled())
        self.window.currency_new_name.setText('Review custom token')
        self.window.add_currency_name()
        action = logger.undo_action()
        self.assertTrue(self.window.undo_button.isEnabled())
        self.assertIn(action, self.window.undo_button.toolTip())
        self.window.currency_new_name.setText('Review custom token')
        self.window.add_currency_name()
        self.assertEqual(logger.undo_action(), action)
        self.window.undo_map()
        self.assertNotIn('Review custom token', logger.currency_names())
        self.assertEqual(self.window.icon_name.findText('Review custom token'), -1)
        self.assertFalse(self.window.undo_button.isEnabled())

    def test_reference_undo_restores_both_lists_and_cached_artwork(self):
        """Undo removal restores persisted icon bytes and every displayed reference cache."""
        name = logger.add_currency_item('Review artwork token')
        logger.save_currency_icon(name, Image.new('RGB', (60, 60), 'blue'))
        self.window.refresh_reference_examples()
        self.window.refresh_currency_references()
        expected = self.window._session_currency_icons[name]
        self.assertTrue(expected)
        self.window.reference_list.setCurrentRow(0)
        self.window.remove_reference_example()
        self.assertEqual(self.window.reference_list.count(), 0)
        self.assertEqual(self.window.icon_list.count(), 0)
        self.assertIsNone(self.window._session_currency_icons[name])
        self.assertIn(logger.undo_action(), self.window.undo_button.toolTip())
        self.window.undo_map()
        self.assertEqual(len(logger.currency_icons()), 1)
        self.assertEqual(self.window.reference_list.count(), 1)
        self.assertEqual(self.window.icon_list.count(), 1)
        self.assertEqual(self.window._session_currency_icons[name], expected)

    def test_locked_counts_return_promptly_preserve_draft_then_retry(self):
        """A held writer cannot freeze count Save, consume its draft or create a commit."""
        before = logger.get_state()
        self.window.normal.setText('123')
        db = self.writer()
        started = time.monotonic()
        self.window.run(self.window.save_counts)
        self.assertLess(time.monotonic() - started, .5)
        self.assertIn('database is busy', self.window.statusBar().currentMessage().lower())
        self.assertEqual(self.window.normal.text(), '123')
        self.assertEqual(logger.get_state()['scan_commit_count'], before['scan_commit_count'])
        db.rollback()
        self.window.run(self.window.save_counts)
        self.assertEqual(logger.get_state()['kills'][0], 123)
        self.assertEqual(logger.get_state()['scan_commit_count'], before['scan_commit_count'] + 1)

    def test_locked_reference_reset_is_atomic_and_retryable(self):
        """Reset contention reports a retry and preserves names, settings and pending drafts."""
        name = logger.add_item_name('Review retained reset token')
        self.window.normal.setText('88')
        before = logger.get_state()
        db = self.writer()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), \
                patch.object(ui.reference_pack, '_default_reference_rows',
                             wraps=ui.reference_pack._default_reference_rows) as rebuild:
            started = time.monotonic()
            self.window.run(self.window.reset_reference_database)
            self.assertLess(time.monotonic() - started, .5)
            rebuild.assert_not_called()
        self.assertIn('database is busy', self.window.statusBar().currentMessage().lower())
        self.assertIn(name, logger.item_names())
        self.assertEqual(logger.get_state()['settings'], before['settings'])
        self.assertEqual(self.window.normal.text(), '88')
        db.rollback()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            self.window.run(self.window.reset_reference_database)
        self.assertNotIn(name, logger.item_names())
        self.assertEqual(self.window.normal.text(), '88')

    def test_listener_failure_does_not_skip_other_shutdown_phases(self):
        """A listener unregister error still retires timers, futures, tasks, OCR and the pool."""
        future = Future()
        timer = QTimer(self.window)
        timer.start(90000)
        self.window._scan_task_timers['test'] = timer
        self.window._scan_task_futures['test'] = future
        self.window._pending_tasks['test'] = (None, logger.session_generation())
        with patch.object(service.HOTKEY, '_unregister', side_effect=RuntimeError('stalled listener')), \
                patch.object(ui.ocr_workers, 'shutdown') as shutdown, \
                patch.object(self.window.pool, 'shutdown') as pool, \
                self.assertLogs(ui.__name__, level='ERROR'):
            self.window.close()
        self.assertTrue(future.cancelled())
        self.assertFalse(timer.isActive())
        self.assertFalse(self.window._pending_tasks)
        shutdown.assert_called_once_with(wait=False)
        pool.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertTrue(self.window._closed)

    def test_busy_between_grouped_saves_reports_partial_completion_and_allows_undo(self):
        """Do not claim no data saved when a later audit transaction encounters a writer."""
        while logger.undo_action():
            logger.undo_last_action()
        self.window.refresh()
        before = logger.get_state()
        self.window.waystone.setText('87')
        save = logger.save_settings
        writers = []

        def save_then_contend(*args, **kwargs):
            """Let the actual settings transaction finish before blocking the audit write."""
            result = save(*args, **kwargs)
            writers.append(self.writer())
            return result

        with patch.object(logger, 'save_settings', side_effect=save_then_contend):
            self.window.run(self.window.save_map_settings)
        self.assertEqual(logger.get_state()['settings']['waystone'], 87)
        self.assertEqual(logger.get_state()['scan_commit_count'], before['scan_commit_count'])
        self.assertIn('could not finish', self.window.statusBar().currentMessage())
        self.assertNotIn('not saved', self.window.statusBar().currentMessage())
        self.assertTrue(self.window.undo_button.isEnabled())
        self.assertIn(logger.undo_action(), self.window.undo_button.toolTip())
        for db in writers:
            db.rollback()
        self.window.undo_map()
        self.assertEqual(logger.get_state()['settings'], before['settings'])
