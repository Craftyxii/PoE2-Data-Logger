"""Failed preference writes restore saved controls without disturbing activity drafts."""

import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from PoE2_Data_Logger.core import logger_store as logger, service, store
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow


class PreferenceFailureRollbackTests(unittest.TestCase):
    """Exercise real checkbox signals and a competing SQLite writer in disposable profiles."""

    @classmethod
    def setUpClass(cls):
        """Reuse Qt while each test owns its profile and shortcut manager."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Open isolated settings with native shortcut registration disabled."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-preference-rollback-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        self.hotkey_patch = patch.object(service, "HOTKEY", HotkeyManager(supported=False))
        self.hotkey_patch.start()
        self.window = LoggerWindow()
        self.window._poll.stop()
        self.window.tabs.setCurrentIndex(6)
        self.writers = []

    def tearDown(self):
        """Close database handles and UI resources before removing the disposable profile."""
        for writer in self.writers:
            writer.close()
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.app.processEvents()
        self.hotkey_patch.stop()
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def writer(self):
        """Reserve the real profile writer while reads remain available through WAL."""
        writer = sqlite3.connect(store.DATA_DIR / "scans.sqlite3")
        self.writers.append(writer)
        writer.execute("BEGIN IMMEDIATE")
        return writer

    def assert_saved_auto_controls(self):
        """Check all related values and enabled states against persisted preferences."""
        saved = logger.get_state()["settings"]
        all_scans = bool(saved.get("ocr_auto_commit"))
        for field, key in ((self.window.auto_all_checkbox, "ocr_auto_commit"),
                           (self.window.auto_commit_checkbox, "auto_commit"),
                           (self.window.auto_tablet_checkbox, "tablet_auto_commit")):
            self.assertEqual(field.isChecked(), bool(saved.get(key)), key)
        self.assertEqual(self.window.auto_commit_checkbox.isEnabled(), not all_scans)
        self.assertEqual(self.window.auto_tablet_checkbox.isEnabled(), not all_scans)

    def drafts(self):
        """Snapshot unrelated unsaved form and propagation edits."""
        return (self.window.normal.text(), self.window._waystone_form(),
                tuple(field.text() for field in self.window.rune_inputs))

    def enter_drafts(self):
        """Keep distinct manual edits present while a preference save fails or retries."""
        self.window.normal.setText("128")
        self.window.waystone.setText("41")
        self.window.rune_inputs[0].setText("Death")
        return self.drafts()

    def test_busy_auto_preferences_restore_both_directions_and_retry_without_losing_drafts(self):
        """Each failed toggle stays truthful, emits no recursive saves, and can be retried."""
        for target in ("all", "remnant", "tablet"):
            for enabled in (False, True):
                with self.subTest(target=target, saved=enabled):
                    config = {"ocr_auto_commit": enabled if target == "all" else False,
                              "auto_commit": enabled if target in ("all", "remnant") else False,
                              "tablet_auto_commit": enabled if target in ("all", "tablet") else False}
                    logger.save_settings(config)
                    self.window.refresh()
                    draft = self.enter_drafts()
                    field = {"all": self.window.auto_all_checkbox,
                             "remnant": self.window.auto_commit_checkbox,
                             "tablet": self.window.auto_tablet_checkbox}[target]
                    before = logger.get_state()
                    writer = self.writer()
                    with patch.object(logger, "save_settings", wraps=logger.save_settings) as save:
                        started = time.monotonic()
                        field.click()
                        writer.rollback()
                        self.assertLess(time.monotonic() - started, 1)
                        self.assertEqual(save.call_count, 1, "Restoring controls must not emit new saves")
                    self.assertIn("database is busy", self.window.statusBar().currentMessage().lower())
                    self.assertEqual(logger.get_state()["settings"], before["settings"])
                    self.assertEqual(logger.get_state()["scan_commit_count"], before["scan_commit_count"])
                    self.assert_saved_auto_controls()
                    self.assertEqual(self.drafts(), draft)
                    field.click()
                    self.assertEqual(field.isChecked(), not enabled)
                    self.assert_saved_auto_controls()
                    self.assertEqual(self.drafts(), draft)

    def test_failed_tablet_enable_keeps_next_clear_scan_pending_for_approval(self):
        """An unsaved enable cannot become the live Tablet auto-save decision."""
        writer = self.writer()
        self.window.auto_tablet_checkbox.click()
        writer.rollback()
        before = logger.get_state()
        saved = self.window._tablet_read(1, {
            "kind": "tablet", "source": "screen OCR", "mods": ["30% increased Pack Size"],
            "matches": [], "uncertain": [], **logger.scan_context()})
        self.assertIsNone(saved)
        self.assertEqual(self.window.pending_review_kind, "tablet")
        self.assertTrue(self.window.approve_scan_button.isEnabled())
        self.assertEqual(logger.tablet_next_slot(), 1)
        self.assertEqual(logger.get_state()["scan_commit_count"], before["scan_commit_count"])
        self.assertFalse(logger.get_state()["settings"].get("tablet_auto_commit", False))
        self.window.approve_scan_button.click()
        self.assertEqual(logger.tablet_next_slot(), 2)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"], 30)

    def test_busy_overlay_toggle_restores_saved_runtime_and_controls_in_both_directions(self):
        """Failure cannot change HUD behavior, and ordinary refresh keeps the control truthful."""
        for enabled in (False, True):
            with self.subTest(saved=enabled):
                self.window.run(lambda: self.window.set_overlay_enabled(enabled))
                draft = self.enter_drafts()
                runtime = (self.window._overlay_enabled, self.window.overlay_escape.isEnabled(),
                           self.window.overlay_toggle_button.isEnabled())
                writer = self.writer()
                self.window.overlay_checkbox.click()
                writer.rollback()
                self.assertIn("database is busy", self.window.statusBar().currentMessage().lower())
                self.assertEqual(self.window.overlay_checkbox.isChecked(), enabled)
                self.assertEqual((self.window._overlay_enabled, self.window.overlay_escape.isEnabled(),
                                  self.window.overlay_toggle_button.isEnabled()), runtime)
                with logger._connect() as db:
                    self.assertEqual(bool(logger._meta(db, "hud_overlay", False)), enabled)
                self.window.refresh()
                self.assertEqual(self.window.overlay_checkbox.isChecked(), enabled)
                self.assertEqual(self.drafts(), draft)
                self.window.overlay_checkbox.click()
                self.assertEqual(self.window.overlay_checkbox.isChecked(), not enabled)
                self.assertEqual(self.window._overlay_enabled, not enabled)
                self.assertEqual(self.drafts(), draft)

    def test_nonbusy_preference_errors_restore_controls_and_report_original_error(self):
        """Disk and default-HUD-shortcut errors follow the same rollback path as contention."""
        for field in (self.window.auto_all_checkbox, self.window.auto_commit_checkbox,
                      self.window.auto_tablet_checkbox):
            self.window.refresh()
            with self.subTest(field=field.text()), patch.object(
                    logger, "save_settings", side_effect=OSError("preference disk failure")):
                field.click()
                self.assert_saved_auto_controls()
                self.assertIn("preference disk failure", self.window.statusBar().currentMessage())
        with patch.object(logger, "_set_meta", side_effect=OSError("overlay disk failure")):
            self.window.overlay_checkbox.click()
        self.assertFalse(self.window.overlay_checkbox.isChecked())
        self.assertFalse(self.window._overlay_enabled)
        self.assertIn("overlay disk failure", self.window.statusBar().currentMessage())
        status = service.HOTKEY.status()
        with patch.object(service.HOTKEY, "status", return_value={**status, "supported": True}), \
                patch.object(service.HOTKEY, "configure_for", side_effect=OSError("shortcut disk failure")):
            self.window.overlay_checkbox.click()
        self.assertFalse(self.window.overlay_checkbox.isChecked())
        self.assertIn("shortcut disk failure", self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
