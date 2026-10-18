"""Protect dedicated screenshot kill-count capture and independent scan-key routing."""

import base64
from concurrent.futures import Future
import io
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers, store
from PoE2_Data_Logger.platform import hotkey
from PoE2_Data_Logger.ui import region_select


class KillCountHotkeyTests(unittest.TestCase):
    """Keep primary readers, saved evidence, cancellation and chain-free scans independent."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-hotkey-")
        self.previous_data_dir = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        self.region = {"x": 1600, "y": 250, "w": 260, "h": 130}
        self.main_region = {"x": 100, "y": 150, "w": 600, "h": 700}
        self.calls = []
        self.context = {"_scan_generation": logger.session_generation(),
                        "_capture_map_id": "M0001", "_capture_map_pending": False,
                        "_capture_expedition": 1, "_capture_chain_revision": 0,
                        "_capture_chain_associated": False,
                        "captured_at": "2026-10-16T22:00:00+00:00"}
        self.context_reader = Mock(side_effect=self.capture_context)
        # This file exercises the capture boundary independently of OCR and
        # persistence, both of which have their own real-data tests.
        module = types.ModuleType("PoE2_Data_Logger.core.kill_counts")
        module.capture_context = self.context_reader
        self.modules = patch.dict(sys.modules, {module.__name__: module})
        self.modules.start()
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.cancel_capture()
        self.modules.stop()
        store.DATA_DIR = self.previous_data_dir
        logger._READY = False
        self.temporary.cleanup()

    def capture_context(self, source_mode):
        self.calls.append("context")
        return {**self.context, "source_mode": source_mode}

    def configure(self, enabled=True):
        with logger._connect() as db:
            logger._set_meta(db, "kill_count_scan_enabled", enabled)
            logger._set_meta(db, "kill_count_region", self.region)
            for name in ("live_region", "propagation_region", "inventory_region", "ritual_region"):
                logger._set_meta(db, name, self.main_region)

    def grab(self, **kwargs):
        box = kwargs.get("bbox")
        is_kill = box == (1600, 250, 1860, 380)
        self.calls.append("grab-kills" if is_kill else "grab-main")
        return Image.new("RGB", (260, 130) if is_kill else (600, 700),
                         (10, 20, 30) if is_kill else (70, 80, 90))

    def manager(self, kill_reader=None, **kwargs):
        def primary(_picture):
            self.calls.append("read-primary")
            return {"mode": "opened", "runes": ["Ward"], "positions": [2], "can_use": True}
        def kills(picture, strictness):
            self.calls.append("read-kills")
            self.assertEqual(picture.getpixel((0, 0)), (10, 20, 30))
            return {"normal": 984, "magic": 719, "rare": 656, "unique": 4,
                    "complete": True, "status": "ready", "readings": []}
        readers = {name: Mock(side_effect=primary) for name in ("seed", "opened", "both", "propagation")}
        readers["kills"] = kill_reader or Mock(side_effect=kills)
        manager = hotkey.HotkeyManager(grabber=Mock(side_effect=self.grab), readers=readers,
                                      supported=kwargs.pop("supported", False), **kwargs)
        self.managers.append(manager)
        return manager

    def test_missing_enable_preference_defaults_to_dedicated_kill_capture(self):
        with logger._connect() as db:
            logger._set_meta(db, "kill_count_region", self.region)
        manager = self.manager()
        manager.capture("kills")
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "kills")
        self.assertTrue(event["kill_counts"]["result"]["complete"])
        manager.grabber.assert_called_once_with(bbox=(1600, 250, 1860, 380), all_screens=True)

    def test_focus_guard_prevents_dedicated_kill_capture(self):
        self.configure()
        manager = self.manager(focused=lambda: False)
        manager.capture("kills")
        manager.grabber.assert_not_called()
        manager.readers["kills"].assert_not_called()
        self.assertIsNone(manager.status()["latest"])

    def test_disabled_setting_keeps_existing_primary_capture_and_no_extra_work(self):
        self.configure(enabled=False)
        manager = self.manager()
        manager.capture("propagation")
        event = manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertNotIn("kill_counts", event)
        self.assertEqual(self.calls, ["grab-main", "read-primary"])
        self.context_reader.assert_not_called()
        manager.readers["kills"].assert_not_called()
        self.assertEqual(manager.grabber.call_count, 1)

    def test_corrupt_truthy_opt_in_does_not_enable_new_capture(self):
        self.configure(enabled="true")
        manager = self.manager()
        manager.capture("kills")
        event = manager.status()["latest"]
        self.assertNotIn("kill_counts", event)
        self.assertIn("Kill counts region", event["error"])
        manager.grabber.assert_not_called()
        manager.readers["kills"].assert_not_called()
        self.context_reader.assert_not_called()

    def test_dedicated_roi_and_context_are_frozen_before_recognition(self):
        self.configure()
        manager = self.manager()
        def read(picture, strictness):
            self.context["_capture_map_id"] = "M0999"
            picture.putpixel((0, 0), (255, 0, 0))
            return {"normal": 984, "magic": 719, "rare": 656, "unique": 4,
                    "complete": True, "status": "ready", "readings": []}
        manager.readers["kills"].side_effect = read
        manager.capture("kills")
        event = manager.status()["latest"]
        reading = event["kill_counts"]
        self.assertEqual(self.calls, ["context", "grab-kills"])
        self.assertEqual(reading["context"]["_capture_map_id"], "M0001")
        self.assertEqual(reading["source_mode"], "kills")
        self.assertEqual(reading["scope"], "map_final")
        self.assertEqual(str(uuid.UUID(reading["request_id"])), reading["request_id"])
        evidence = Image.open(io.BytesIO(base64.b64decode(reading["image_base64"], validate=True)))
        self.assertEqual(evidence.format, "PNG")
        self.assertEqual(evidence.size, (260, 130))
        self.assertEqual(evidence.getpixel((0, 0)), (10, 20, 30))
        self.assertIsNone(event["result"])
        self.assertIsNone(manager.image(event["id"]))

    def test_dedicated_reader_mutation_cannot_change_frozen_counter_evidence(self):
        self.configure()
        def read(image, strictness):
            image.putpixel((0, 0), (255, 0, 0))
            return {"normal": 984, "magic": 719, "rare": 656, "unique": 4,
                    "complete": True, "status": "ready", "readings": []}
        manager = self.manager(kill_reader=Mock(side_effect=read))
        manager.capture("kills")
        aux = manager.status()["latest"]["kill_counts"]
        evidence = Image.open(io.BytesIO(base64.b64decode(aux["image_base64"])))
        self.assertEqual(evidence.getpixel((0, 0)), (10, 20, 30))
        self.assertTrue(aux["result"]["complete"])

    def test_native_opened_shortcut_early_return_never_reads_kills(self):
        self.configure()
        manager = self.manager()
        manager.readers["opened"] = hotkey.scan_opened
        native = {"mode": "opened", "family": 57, "source": "native"}
        def recognition(revision, reader, *args, **kwargs):
            self.assertIs(reader, hotkey.scan_opened)
            self.assertEqual(kwargs, {"allow_fallback": False, "verify_header": True})
            return dict(native)
        with patch.object(manager, "_ocr_capture_call", side_effect=recognition), \
                patch("PoE2_Data_Logger.core.auto_commit.candidate", return_value={"ready": True}):
            manager.capture("default")
        event = manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertEqual(event["result"]["family"], 57)
        self.assertNotIn("kill_counts", event)
        manager.readers["kills"].assert_not_called()
        self.context_reader.assert_not_called()

    def test_chain_association_does_not_enable_sidecars_and_final_scope_stays_map(self):
        self.configure()
        self.context["_capture_chain_associated"] = True
        manager = self.manager()
        manager.capture("propagation")
        self.assertNotIn("kill_counts", manager.status()["latest"])
        manager.capture("kills")
        reading = manager.status()["latest"]["kill_counts"]
        self.assertEqual(reading["scope"], "map_final")
        self.assertTrue(reading["context"]["_capture_chain_associated"])

    def test_dedicated_kills_need_no_chain_or_other_activity_and_do_not_copy(self):
        self.configure()
        copied, tooltip = Mock(), Mock()
        manager = self.manager(supported=True, hover_reader=copied, tooltip_grabber=tooltip,
                               focused=lambda: True)
        with patch.object(hotkey.sys, "platform", "linux"):
            manager.capture("kills")
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "kills")
        self.assertIsNone(event["result"])
        self.assertEqual(event["error"], "")
        self.assertEqual(event["kill_counts"]["scope"], "map_final")
        self.assertEqual(event["kill_counts"]["context"]["_capture_map_id"], "M0001")
        self.assertEqual(manager.grabber.call_count, 1)
        self.assertIsNone(manager.image(event["id"]))
        copied.assert_not_called()
        tooltip.assert_not_called()
        for name in ("seed", "opened", "both", "propagation"):
            manager.readers[name].assert_not_called()
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM new_export").fetchone()[0], 0)

    def test_dedicated_kills_explain_missing_opt_in_without_a_capture(self):
        self.configure(enabled=False)
        manager = self.manager()
        manager.capture("kills")
        self.assertIn("Kill counts region", manager.status()["latest"]["error"])
        manager.grabber.assert_not_called()
        manager.readers["kills"].assert_not_called()

    def test_overlay_never_captures_counters_even_when_enabled(self):
        self.configure()
        with logger._connect() as db:
            logger._set_meta(db, "hud_overlay", True)
        manager = self.manager()
        manager.capture("overlay")
        self.assertEqual(manager.status()["overlay_sequence"], 1)
        manager.grabber.assert_not_called()
        self.context_reader.assert_not_called()

    def test_all_other_image_scan_keys_never_capture_or_read_kills(self):
        self.configure()
        for kind, mode in (("remnant", "opened"), ("remnant", "seed"),
                           ("remnant", "both"), ("default", "opened"),
                           ("currency", None), ("ritual", None), ("propagation", None)):
            with self.subTest(kind=kind, mode=mode):
                manager = self.manager()
                if mode:
                    manager.mode = mode
                manager.capture(kind)
                event = manager.status()["latest"]
                self.assertEqual(event["error"], "")
                self.assertNotIn("kill_counts", event)
                self.assertEqual(manager.grabber.call_count, 1)
                manager.readers["kills"].assert_not_called()
        self.context_reader.assert_not_called()

    def test_copied_item_early_return_keeps_tooltip_without_kill_sidecar(self):
        self.configure()
        for kind in ("default", "waystone", "tablet"):
            with self.subTest(kind=kind):
                tooltip = Image.new("RGB", (600, 700), (40, 50, 60))
                copied = Mock(return_value="Item Class: Waystones\nA copied item")
                manager = self.manager(supported=True, hover_reader=copied,
                                       tooltip_grabber=Mock(return_value=tooltip), focused=lambda: True)
                item_kind = "tablet" if kind == "tablet" else "waystone"
                item = {"kind": item_kind, "matches": ["an affix"]}
                with patch.object(hotkey.sys, "platform", "linux"), \
                        patch.object(hotkey, "parse_item_text", return_value=item):
                    manager.capture(kind)
                event = manager.status()["latest"]
                self.assertEqual(event["mode"], "item")
                self.assertEqual(event["result"]["kind"], item_kind)
                self.assertEqual(event["error"], "")
                self.assertNotIn("kill_counts", event)
                copied.assert_called_once_with()
                self.assertIsNotNone(manager.image(event["id"]))
                manager.readers["propagation"].assert_not_called()
                manager.readers["kills"].assert_not_called()

    def test_uncertain_dedicated_kill_reading_preserves_unknown_values(self):
        self.configure()
        uncertain = {"normal": None, "magic": 719, "rare": 656, "unique": 4,
                     "complete": False, "status": "review", "readings": []}
        manager = self.manager(kill_reader=Mock(return_value=uncertain))
        manager.capture("kills")
        event = manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertIsNone(event["result"])
        self.assertEqual(event["kill_counts"]["result"], uncertain)
        self.assertEqual(event["mode"], "kills")

    def test_dedicated_kill_failures_preserve_mode_and_do_not_run_other_readers(self):
        self.configure()
        for failure in ("recognition", "capture"):
            with self.subTest(failure=failure):
                manager = self.manager(kill_reader=Mock(side_effect=RuntimeError("counter unreadable")))
                if failure == "capture":
                    manager.grabber.side_effect = RuntimeError("counter crop unavailable")
                manager.capture("kills")
                event = manager.status()["latest"]
                self.assertEqual(event["mode"], "kills")
                self.assertEqual(event["error"], "")
                self.assertIsNone(event["result"])
                self.assertIn("failed", event["kill_counts"]["error"])
                self.assertEqual(manager.grabber.call_count, 1)
                for name in ("seed", "opened", "both", "propagation"):
                    manager.readers[name].assert_not_called()

    def test_missing_kill_region_is_reported_only_for_dedicated_key(self):
        self.configure()
        with logger._connect() as db:
            logger._set_meta(db, "kill_count_region", None)
        manager = self.manager()
        manager.capture("propagation")
        self.assertNotIn("kill_counts", manager.status()["latest"])
        self.assertEqual(manager.grabber.call_count, 1)
        manager.capture("kills")
        event = manager.status()["latest"]
        self.assertEqual(event["mode"], "kills")
        self.assertIn("Select the Kill counts", event["kill_counts"]["error"])
        manager.readers["kills"].assert_not_called()
        self.assertEqual(manager.grabber.call_count, 1)

    def test_primary_capture_failure_never_attempts_an_unrequested_kill_scan(self):
        self.configure()
        manager = self.manager()
        manager.grabber.side_effect = RuntimeError("main panel unavailable")
        manager.capture("propagation")
        event = manager.status()["latest"]
        self.assertIn("main panel unavailable", event["error"])
        self.assertNotIn("kill_counts", event)
        self.context_reader.assert_not_called()
        manager.readers["kills"].assert_not_called()
        manager.readers["propagation"].assert_not_called()

    def test_propagation_cancellation_never_reads_kills(self):
        self.configure()
        manager = self.manager()
        manager.readers["propagation"].side_effect = lambda _image: manager.cancel_capture()
        manager.capture("propagation")
        self.assertIsNone(manager.status()["latest"])
        manager.readers["kills"].assert_not_called()
        self.assertFalse(manager._capture_lock.locked())

    def test_dedicated_kill_cancellation_cannot_publish_late_reading(self):
        self.configure()
        manager = self.manager()
        manager.readers["kills"].side_effect = lambda *_args, **_kwargs: manager.cancel_capture()
        manager.capture("kills")
        self.assertIsNone(manager.status()["latest"])
        self.assertFalse(manager._capture_lock.locked())

    def test_windows_dedicated_kill_key_uses_only_its_native_region(self):
        self.configure()
        manager = self.manager(supported=True, focused=lambda: True)
        selected = {"x": 2150, "y": 320, "w": 450, "h": 200}
        def region_for(key, bounds):
            self.assertEqual(key, "kill_count_region")
            self.assertEqual(bounds, (100, 50, 2660, 1490))
            return selected
        with patch.object(hotkey.sys, "platform", "win32"), \
                patch("PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(100, 50, 2660, 1490)), \
                patch("PoE2_Data_Logger.ui.region_select.region_for", side_effect=region_for) as convert:
            manager.capture("kills")
        self.assertEqual(manager.status()["latest"]["error"], "")
        manager.grabber.assert_called_once_with(bbox=(2150, 320, 2600, 520), all_screens=True)
        self.assertEqual([call.args[0] for call in convert.call_args_list], ["kill_count_region"])

    def test_native_counter_capture_preserves_pixels_at_720p_1080p_1440p_and_4k(self):
        self.configure()
        fractions = [.85, .24, .15, .12]
        with logger._connect() as db:
            logger._set_meta(db, "scan_region_boxes", {"kill_count_region": fractions})
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                bounds = (100, 50, 100 + width, 50 + height)
                expected_box = (100 + round(.85 * width), 50 + round(.24 * height),
                                100 + width, 50 + round(.36 * height))
                size = (expected_box[2] - expected_box[0], expected_box[3] - expected_box[1])
                source = Image.new("RGB", size, (37, 59, 81))
                source.putpixel((size[0] - 1, size[1] - 1), (92, 111, 137))
                def read(image, strictness):
                    self.assertEqual(image.size, source.size)
                    self.assertEqual(image.tobytes(), source.tobytes())
                    return {"normal": 984, "magic": 719, "rare": 656, "unique": 4,
                            "complete": True, "status": "ready", "readings": []}
                manager = self.manager(supported=True, focused=lambda: True,
                                       kill_reader=Mock(side_effect=read))
                manager.grabber.side_effect = None
                manager.grabber.return_value = source
                with patch.object(hotkey.sys, "platform", "win32"), \
                        patch("PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=bounds):
                    manager.capture("kills")
                event = manager.status()["latest"]
                self.assertEqual(event["error"], "")
                self.assertNotIn("error", event["kill_counts"])
                self.assertTrue(event["kill_counts"]["result"]["complete"])
                manager.grabber.assert_called_once_with(bbox=expected_box, all_screens=True)
                saved = Image.open(io.BytesIO(base64.b64decode(event["kill_counts"]["image_base64"])))
                self.assertEqual(saved.size, size)
                self.assertEqual(saved.tobytes(), source.tobytes())
                self.assertEqual(event["kill_counts"]["scope"], "map_final")

    def test_invalid_counter_regions_never_capture_or_invent_kill_values(self):
        self.configure()
        invalid = [
            {**self.region, "w": 0}, {**self.region, "h": -1},
            {**self.region, "w": 19}, {**self.region, "h": 19},
            {**self.region, "w": 8193}, {**self.region, "h": 8193},
            {**self.region, "w": 4000, "h": 4000},
            {**self.region, "x": 32769}, {**self.region, "x": -32769},
            {**self.region, "x": 32700},
            {**self.region, "w": float("inf")},
            {**self.region, "x": float("nan")},
            {**self.region, "h": 130.5}, {**self.region, "w": True},
            {"x": 1, "y": 2, "w": 200},
        ]
        for region in invalid:
            with self.subTest(region=region):
                with logger._connect() as db:
                    logger._set_meta(db, "kill_count_region", region)
                manager = self.manager()
                manager.capture("kills")
                event = manager.status()["latest"]
                self.assertEqual(event["error"], "")
                self.assertIsNone(event["result"])
                self.assertIn("Kill count capture failed", event["kill_counts"]["error"])
                self.assertEqual(manager.grabber.call_count, 0)
                manager.readers["kills"].assert_not_called()

    def test_known_game_bounds_reject_counter_crops_outside_the_window(self):
        self.configure()
        manager = self.manager(supported=True, focused=lambda: True)
        outside = {"x": 2450, "y": 320, "w": 450, "h": 200}
        def region_for(key, bounds):
            return outside if key == "kill_count_region" else self.main_region
        with patch.object(hotkey.sys, "platform", "win32"), \
                patch("PoE2_Data_Logger.platform.hover_copy._tooltip_bounds", return_value=(100, 50, 2660, 1490)), \
                patch("PoE2_Data_Logger.ui.region_select.region_for", side_effect=region_for):
            manager.capture("kills")
        event = manager.status()["latest"]
        self.assertEqual(event["error"], "")
        self.assertIsNone(event["result"])
        self.assertIn("inside the game", event["kill_counts"]["error"])
        self.assertEqual(manager.grabber.call_count, 0)
        manager.readers["kills"].assert_not_called()

    def test_small_counter_roi_does_not_relax_existing_primary_region_validation(self):
        self.configure()
        manager = self.manager()
        small = {"x": 0, "y": 0, "w": 192, "h": 86}
        manager._grab_kill_region(small, (0, 0, 1280, 720))
        self.assertEqual(manager.grabber.call_count, 1)
        with self.assertRaisesRegex(ValueError, "at least 200"):
            manager._grab(small)
        self.assertEqual(manager.grabber.call_count, 1)

    def test_dedicated_kill_worker_uses_short_deadline_and_frozen_strictness(self):
        manager = self.manager()
        manager._capture_lock.acquire()
        manager._capture_revision = manager._capture_owner = 1
        manager._capture_deadline = time.monotonic() + 40
        descriptor = ocr_workers.Request(lambda: None, (), {}, str(store.DATA_DIR),
                                         ocr_runtime.active_threads(), time.monotonic() + 90)
        completed = Future()
        completed.set_result({"complete": True})
        start = time.monotonic()
        with patch.object(ocr_workers, "request", return_value=descriptor) as request, \
                patch.object(ocr_workers, "submit", return_value=completed) as submit:
            manager._ocr_capture_call(1, manager.readers["kills"], Image.new("RGB", (260, 130)),
                                      strictness=73, _timeout=hotkey.KILL_COUNT_TIMEOUT_SECONDS)
        self.assertEqual(request.call_args.kwargs, {"strictness": 73})
        self.assertLessEqual(submit.call_args.args[0].deadline, time.monotonic() + 5)
        self.assertGreaterEqual(submit.call_args.args[0].deadline, start + 5)
        self.assertIsNone(manager._ocr_capture_future)
        manager._finish_capture(None, None, 1)

    def test_dedicated_kill_deadline_preserves_publication_margin(self):
        manager = self.manager()
        manager._capture_lock.acquire()
        manager._capture_revision = manager._capture_owner = 1
        manager._capture_deadline = time.monotonic() + .1
        descriptor = ocr_workers.Request(lambda: None)
        with patch.object(ocr_workers, "request", return_value=descriptor), \
                patch.object(ocr_workers, "submit") as submit:
            with self.assertRaisesRegex(TimeoutError, "primary scan"):
                manager._ocr_capture_call(1, manager.readers["kills"], _timeout=5)
        submit.assert_not_called()
        manager._finish_capture(None, None, 1)


if __name__ == "__main__":
    unittest.main()
