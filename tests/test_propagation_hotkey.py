"""Hotkey routing checks keeping propagation capture separate from remnant detection and preserving captured context."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import region_select


class PropagationHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-propagation-hotkey-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.temporary.cleanup()

    def manager(self, reader=None, supported=False, **kwargs):
        readers = {"opened": Mock(return_value={"mode": "opened"}),
                   "propagation": reader or Mock(return_value={"selected_recipe": "Medved's Saga",
                       "runes": ["Rage", "Time"], "positions": [1, 5], "can_use": True})}
        return hotkey.HotkeyManager(readers=readers, supported=supported,
            grabber=kwargs.pop("grabber", Mock(return_value=Image.new("RGB", (580, 730), (20, 40, 60)))),
            **kwargs)

    def test_dedicated_capture_reads_only_frozen_screenshot_without_remnant_writes(self):
        logger.start_map()
        logger.save_settings({"expedition": 2})
        region = {"x": 0, "y": 110, "w": 580, "h": 730}
        with logger._connect() as db:
            logger._set_meta(db, "propagation_region", region)
            commit_count = logger._meta(db, "scan_commit_count", 0)
            next_remnant = logger._meta(db, "next_remnant_number")
        copied = Mock(side_effect=AssertionError("Propagation copied hovered text"))
        tooltip = Mock(side_effect=AssertionError("Propagation captured a hovered tooltip"))
        manager = self.manager(supported=True, focused=lambda: True,
                               hover_reader=copied, tooltip_grabber=tooltip)
        with patch.object(hotkey.sys, "platform", "linux"), patch.object(
                logger, "assign_ocr_id", side_effect=AssertionError("Propagation allocated a remnant")):
            manager.capture("propagation")
        event = manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertEqual(event["mode"], "propagation")
        result = event["result"]
        self.assertEqual(result["runes"], ["Rage", "Time"])
        self.assertEqual(result["positions"], [1, 5])
        self.assertEqual(result["_capture_map_id"], "M0001")
        self.assertEqual(result["_capture_expedition"], 2)
        self.assertFalse(result["_capture_map_pending"])
        self.assertEqual(result["_scan_generation"], logger.session_generation())
        self.assertNotIn("_target_map_id", result)
        logger.validate_scan_context(result)
        manager.grabber.assert_called_once_with(bbox=(0, 110, 580, 840), all_screens=True)
        captured = manager.readers["propagation"].call_args.args[0]
        self.assertIsInstance(captured, Image.Image)
        saved_image = Image.open(io.BytesIO(manager.image(event["id"])))
        self.assertEqual(saved_image.format, "PNG")
        self.assertEqual(saved_image.getpixel((0, 0)), (20, 40, 60))
        copied.assert_not_called()
        tooltip.assert_not_called()
        manager.readers["opened"].assert_not_called()
        with logger._connect() as db:
            self.assertIsNone(logger._meta(db, "ocr_pending"))
            self.assertEqual(logger._meta(db, "scan_commit_count", 0), commit_count)
            self.assertEqual(logger._meta(db, "next_remnant_number"), next_remnant)
            self.assertEqual(db.execute("SELECT count(*) FROM new_export").fetchone()[0], 0)

    def test_image_remains_frozen_if_reader_changes_input(self):
        def read(image):
            image.putpixel((0, 0), (90, 80, 70))
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        event = manager.status()["latest"]
        saved_image = Image.open(io.BytesIO(manager.image(event["id"])))
        self.assertEqual(saved_image.getpixel((0, 0)), (20, 40, 60))

    def test_general_and_remnant_capture_cannot_select_propagation_mode(self):
        for kind in ("default", "remnant"):
            with self.subTest(kind=kind):
                manager = self.manager()
                manager.mode = "propagation"
                manager.capture(kind)
                event = manager.status()["latest"]
                self.assertEqual(event["error"], "")
                self.assertEqual(event["mode"], "opened")
                manager.readers["opened"].assert_called_once()
                manager.readers["propagation"].assert_not_called()

    def test_propagation_is_not_a_general_mode_option(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, "remnant mode"):
            manager.set_mode("propagation")
        self.assertEqual(manager.mode, "opened")

    def test_combined_remnant_reader_cannot_deliver_propagation(self):
        manager = self.manager()
        manager.readers["both"] = Mock(return_value={"mode": "propagation", "runes": ["Rage"]})
        manager.set_mode("both")
        manager.capture()
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "both")
        self.assertIn("remnant mode", event["error"])
        self.assertIsNone(event["result"])
        manager.readers["propagation"].assert_not_called()

    def test_legacy_invalid_general_mode_does_not_activate_propagation(self):
        with logger._connect() as db:
            logger._set_meta(db, "ocr_shortcut", {"mode": "propagation", "combo": "F1"})
        manager = self.manager()
        manager.start()
        self.assertEqual(manager.mode, "opened")
        self.assertEqual(manager.combos["propagation"], "")
        manager.capture()
        manager.readers["propagation"].assert_not_called()

    def test_configured_propagation_shortcut_clears_and_stays_disabled_after_restart(self):
        manager = self.manager(supported=True, focused=lambda: True)
        with patch.object(manager, "_register") as register, patch.object(manager, "_unregister"):
            manager.configure_for("propagation", "ctrl+f8")
            self.assertEqual(manager.status()["combos"]["propagation"], "Ctrl+F8")
            self.assertEqual(set(register.call_args.args[0]), {"propagation"})
            manager.configure_for("propagation", "")
            self.assertEqual(register.call_count, 1)
        restarted = self.manager()
        restarted.start()
        self.assertEqual(restarted.status()["combos"]["propagation"], "")
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "ocr_shortcut")["combos"]["propagation"], "")

    def test_focus_guard_prevents_propagation_capture(self):
        manager = self.manager(supported=True, focused=lambda: False)
        manager.capture("propagation")
        manager.grabber.assert_not_called()
        manager.readers["propagation"].assert_not_called()
        self.assertIsNone(manager.status()["latest"])

    def test_windows_default_propagation_region_includes_left_cursor_margin(self):
        self.assertEqual(region_select.REGIONS["propagation_region"][2][0], 0)
        manager = self.manager(supported=True, focused=lambda: True)
        with patch.object(hotkey.sys, "platform", "win32"), patch(
                "PoE2_Data_Logger.platform.hover_copy._tooltip_bounds",
                return_value=(100, 150, 2020, 1230)):
            manager.capture("propagation")
        self.assertEqual(manager.status()["latest"]["error"], "")
        box = manager.grabber.call_args.kwargs["bbox"]
        self.assertEqual(box[0], 100)
        self.assertGreater(box[2] - box[0], 500)
        manager.readers["propagation"].assert_called_once()

    def test_windows_custom_propagation_region_retains_context_before_left_panel_frame(self):
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_boxes", {"propagation_region": [.04, .1, .3, .7]})
        manager = self.manager(supported=True, focused=lambda: True)
        with patch.object(hotkey.sys, "platform", "win32"), patch(
                "PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(100, 150, 2020, 1230)):
            manager.capture("propagation")
        self.assertEqual(manager.status()["latest"]["error"], "")
        box = manager.grabber.call_args.kwargs["bbox"]
        self.assertEqual(box, (100, 258, 753, 1014))
        with logger._connect() as db:
            self.assertEqual(logger._meta(db, "scan_region_boxes")["propagation_region"], [.04, .1, .3, .7])

    def test_cancelled_propagation_capture_cannot_reappear(self):
        manager = None
        def read(image):
            manager.cancel_capture()
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        self.assertIsNone(manager.status()["latest"])
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_remnant_rejection_does_not_cancel_inflight_propagation(self):
        manager = None
        def read(image):
            self.assertEqual(manager._active_capture_mode, "propagation")
            revision = manager._capture_revision
            manager.cancel_capture(modes=("seed", "opened", "both"))
            self.assertEqual(manager._capture_revision, revision)
            return {"runes": ["Death", "Rebirth"], "positions": [1, 2], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "propagation")
        self.assertEqual(event["result"]["runes"], ["Death", "Rebirth"])
        self.assertIsNotNone(manager.image(event["id"]))
        self.assertIsNone(manager._active_capture_mode)

    def test_propagation_cancellation_does_not_cancel_inflight_opened_remnant(self):
        manager = self.manager()
        def read(image):
            self.assertEqual(manager._active_capture_mode, "opened")
            revision = manager._capture_revision
            manager.cancel_capture(modes=("propagation",))
            self.assertEqual(manager._capture_revision, revision)
            return {"mode": "opened", "status": "Ready for review"}
        manager.readers["opened"] = read
        manager.capture("remnant")
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "opened")
        self.assertEqual(event["result"]["status"], "Ready for review")
        self.assertIsNotNone(manager.image(event["id"]))
        self.assertIsNone(manager._active_capture_mode)

    def test_selective_cancellation_discards_matching_worker_and_releases_capture(self):
        manager = None
        def read(image):
            manager.cancel_capture(modes=("propagation",))
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        self.assertIsNone(manager.status()["latest"])
        self.assertIsNone(manager._image)
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_selective_cancellation_clears_matching_latest_without_affecting_other_worker(self):
        manager = self.manager()
        manager.capture("propagation")
        previous = manager.status()["latest"]
        self.assertIsNotNone(manager.image(previous["id"]))
        def read(image):
            revision = manager._capture_revision
            manager.cancel_capture(modes=("propagation",))
            self.assertEqual(manager._capture_revision, revision)
            self.assertIsNone(manager.status()["latest"])
            self.assertIsNone(manager.image(previous["id"]))
            return {"mode": "opened", "status": "New remnant"}
        manager.readers["opened"] = read
        manager.capture("remnant")
        self.assertEqual(manager.status()["latest"]["mode"], "opened")

    def test_selective_cancellation_preserves_unrelated_finished_event(self):
        manager = self.manager()
        manager.capture("propagation")
        event = manager.status()["latest"]
        image = manager.image(event["id"])
        revision = manager._capture_revision
        manager.cancel_capture(modes=("seed", "opened", "both"))
        self.assertIs(manager.status()["latest"], event)
        self.assertEqual(manager.image(event["id"]), image)
        self.assertEqual(manager._capture_revision, revision)
        manager.cancel_capture()
        self.assertIsNone(manager.status()["latest"])
        self.assertIsNone(manager._image)
        self.assertEqual(manager._capture_revision, revision + 1)

    def test_lost_focus_after_capture_preparation_clears_active_mode_and_lock(self):
        manager = self.manager(focused=Mock(side_effect=[True, False]))
        manager.capture("propagation")
        manager.readers["propagation"].assert_not_called()
        self.assertIsNone(manager._active_capture_mode)
        self.assertTrue(manager._capture_lock.acquire(False))
        manager._capture_lock.release()

    def test_map_finished_during_propagation_scan_keeps_original_context(self):
        logger.start_map()
        def read(image):
            logger.finish_map(0, 0, 0, 0)
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        result = manager.status()["latest"]["result"]
        self.assertEqual(result["_capture_map_id"], "M0001")
        self.assertFalse(result["_capture_map_pending"])
        with self.assertRaisesRegex(ValueError, "map ended"):
            logger.validate_scan_context(result)

    def test_expedition_changed_during_propagation_scan_keeps_original_context(self):
        logger.start_map()
        def read(image):
            logger.save_settings({"expedition": 2})
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        result = manager.status()["latest"]["result"]
        self.assertEqual(result["_capture_expedition"], 1)
        with self.assertRaisesRegex(ValueError, "expedition changed"):
            logger.validate_scan_context(result)

    def test_new_map_during_propagation_scan_keeps_original_map(self):
        logger.start_map()
        def read(image):
            logger.finish_map(0, 0, 0, 0)
            logger.start_map()
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        result = manager.status()["latest"]["result"]
        self.assertEqual(result["_capture_map_id"], "M0001")
        with self.assertRaisesRegex(ValueError, "map changed"):
            logger.validate_scan_context(result)

    def test_session_changed_during_propagation_scan_keeps_original_generation(self):
        generation = logger.session_generation()
        def read(image):
            with logger._connect() as db:
                logger._set_meta(db, "session_generation", generation + 1)
            return {"runes": ["Rage"], "positions": [1], "can_use": True}
        manager = self.manager(reader=read)
        manager.capture("propagation")
        result = manager.status()["latest"]["result"]
        self.assertEqual(result["_scan_generation"], generation)
        with self.assertRaisesRegex(ValueError, "previous session"):
            logger.validate_scan_context(result)

    def test_reader_failure_releases_capture_lock(self):
        reader = Mock(side_effect=[RuntimeError("Unreadable cursor"),
            {"runes": ["Rage"], "positions": [1], "can_use": True}])
        manager = self.manager(reader=reader)
        manager.capture("propagation")
        self.assertIn("Unreadable cursor", manager.status()["latest"]["error"])
        manager.capture("propagation")
        self.assertEqual(manager.status()["latest"]["error"], "")
        self.assertEqual(reader.call_count, 2)


if __name__ == "__main__":
    unittest.main()
