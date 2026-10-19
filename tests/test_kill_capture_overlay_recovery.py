"""Dedicated kill-key failure handoffs through real Qt and production capture routing.

Native focus/listener/bounds adapters and OCR output are injected. These checks
exercise visibility, capture ownership and saved/manual accounting, not Windows
painting or recognition accuracy.
"""

import unittest
from unittest.mock import Mock

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger
from tests import test_overlay_recovery as overlay_fixture


class KillCaptureOverlayRecoveryTests(unittest.TestCase):
    """Restore explicit kill-key errors only for the HUD hidden by that capture."""

    @classmethod
    def setUpClass(cls):
        """Reuse the existing real-Qt overlay fixture's controlled native adapters."""
        overlay_fixture.OverlayRecoveryTests.setUpClass()
        cls.app = overlay_fixture.OverlayRecoveryTests.app

    def setUp(self):
        """Open an isolated active map with a visible HUD and unsaved manual counts."""
        self.fixture = overlay_fixture.OverlayRecoveryTests()
        self.fixture.setUp()
        self.window = self.fixture.window
        self.manager = self.fixture.manager
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_kills(31, 4, 2, unique=1)
        with logger._connect() as db:
            logger._set_meta(db, "kill_count_scan_enabled", True)
            logger._set_meta(db, "kill_count_region",
                             {"x": 1000, "y": 400, "w": 260, "h": 130})
            logger._set_meta(db, "scan_region_boxes",
                             {"kill_count_region": [.6, .2, .2, .2]})
        self.window.refresh()
        self.window.show_overlay()
        self.app.processEvents()
        self.fields = (self.window.normal, self.window.magic,
                       self.window.rare, self.window.unique)
        self.drafts = ("901", "902", "903", "904")
        for field, text in zip(self.fields, self.drafts):
            field.setText(text)
        self.assertTrue(self.window.isVisible())

    def tearDown(self):
        """Retire workers/listeners and remove only this fixture's temporary profile."""
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def capture(self, outcome="capture_error"):
        """Use the real kill-key capture and retain its published nested outcome."""
        if outcome == "capture_error":
            self.manager.grabber = Mock(side_effect=RuntimeError("Kill crop unavailable"))
        else:
            self.manager.grabber = Mock(return_value=Image.new("RGB", (384, 216), "tan"))
            if outcome == "reader_error":
                self.manager.readers["kills"] = Mock(side_effect=RuntimeError("Kill reader unavailable"))
            elif outcome == "incomplete":
                self.manager.readers["kills"] = Mock(return_value={
                    "complete": False, "status": "The four counters were not verified.", "readings": []})
            else:
                self.manager.readers["kills"] = Mock(return_value={
                    "complete": True, "normal": 10, "magic": 2, "rare": 3, "unique": 1})
        self.manager.capture("kills")
        event = self.manager.status()["latest"]
        self.assertEqual(event["mode"], "kills")
        self.assertEqual(event["error"], "")
        self.assertFalse(self.manager._capture_lock.locked())
        self.manager.grabber.assert_called_once()
        return event

    def deliver(self):
        """Deliver the retained event and Qt notifications as the desktop poll does."""
        self.window.poll()
        self.app.processEvents()

    def assert_manual_counts(self):
        """Keep saved totals and all four pending count edits independent of pixel readings."""
        state = logger.get_state()
        self.assertEqual((state["kills"], state["unique_kills"]), ([31, 4, 2], 1))
        self.assertEqual(tuple(field.text() for field in self.fields), self.drafts)

    def assert_no_observation(self):
        """Failed or incomplete readings must not create a snapshot or overwrite totals."""
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kill_count_snapshots").fetchone()[0], 0)

    def test_nested_capture_error_restores_previously_visible_hud(self):
        """An explicit nested crop failure restores its hidden HUD and visible error."""
        event = self.capture()
        self.assertIn("Kill crop unavailable", event["kill_counts"]["error"])
        self.assertFalse(self.window.isVisible())
        self.assertIsNotNone(self.window._overlay_capture_restore)
        self.deliver()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertIn("Kill crop unavailable", self.window.statusBar().currentMessage())
        self.assertIsNone(self.window._overlay_capture_restore)
        self.assertIsNone(self.window.pending_review_kind)
        self.assert_no_observation()
        self.assert_manual_counts()
        self.deliver()
        self.assertTrue(self.window.isVisible())

    def test_nested_reader_error_restores_previously_visible_hud(self):
        """An explicit OCR exception has the same owned restoration as a crop failure."""
        event = self.capture("reader_error")
        self.assertIn("Kill reader unavailable", event["kill_counts"]["error"])
        self.deliver()
        self.assertTrue(self.window.isVisible())
        self.assertIn("Kill reader unavailable", self.window.statusBar().currentMessage())
        self.assert_no_observation()
        self.assert_manual_counts()

    def test_nested_error_preserves_previously_hidden_hud(self):
        """A failed key pressed while already hidden must not reveal the HUD."""
        self.window.hide_overlay()
        self.capture()
        self.assertIsNone(self.window._overlay_capture_restore)
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assert_no_observation()
        self.assert_manual_counts()

    def test_nested_error_does_not_override_later_escape(self):
        """A later user hide retires the earlier capture's review token."""
        self.capture()
        captured_token = self.window._overlay_capture_restore[0]
        self.window.hide_overlay()
        self.assertNotEqual(self.window._overlay_review_token, captured_token)
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._overlay_capture_restore)
        self.assert_no_observation()
        self.assert_manual_counts()

    def test_nested_error_does_not_restore_after_generation_changes(self):
        """Undo invalidates the hidden capture even when the Map ID is unchanged."""
        logger.save_settings({"wisp": True})
        self.capture()
        old_context = self.window._overlay_capture_restore[1]
        logger.undo_last_action()
        self.assertEqual(logger.scan_context()["_capture_map_id"], old_context["_capture_map_id"])
        self.assertNotEqual(logger.session_generation(), old_context["_scan_generation"])
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._overlay_capture_restore)
        self.assert_no_observation()
        self.assert_manual_counts()

    def test_nested_error_does_not_restore_after_map_changes(self):
        """A failure captured on the previous map cannot reopen the HUD on the next map."""
        self.capture()
        logger.finish_map(31, 4, 2, unique=1)
        logger.start_map()
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._overlay_capture_restore)
        self.assertEqual(tuple(field.text() for field in self.fields), self.drafts)
        with logger._connect() as db:
            self.assertEqual(logger._load(db.execute(
                "SELECT kills_json FROM maps WHERE map_id='M0001'").fetchone()[0]), [31, 4, 2])
        self.assert_no_observation()

    def test_successful_snapshot_keeps_hud_hidden_and_manual_drafts_untouched(self):
        """Successful observations remain unobtrusive and separate from manual map totals."""
        event = self.capture("complete")
        self.assertTrue(event["kill_counts"]["result"]["complete"])
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._overlay_capture_restore)
        with logger._connect() as db:
            rows = list(db.execute("SELECT normal,magic,rare,unique_kills FROM kill_count_snapshots"))
        self.assertEqual([tuple(row) for row in rows], [(10, 2, 3, 1)])
        self.assert_manual_counts()

    def test_incomplete_snapshot_keeps_current_unobtrusive_hidden_behavior(self):
        """A normal incomplete OCR result remains a note without revealing or saving."""
        event = self.capture("incomplete")
        self.assertNotIn("error", event["kill_counts"])
        self.assertFalse(event["kill_counts"]["result"]["complete"])
        self.deliver()
        self.assertFalse(self.window.isVisible())
        self.assertIn("not verified", self.window.statusBar().currentMessage())
        self.assertIsNone(self.window.pending_review_kind)
        self.assertIsNone(self.window._overlay_capture_restore)
        self.assert_no_observation()
        self.assert_manual_counts()


if __name__ == "__main__":
    unittest.main()
