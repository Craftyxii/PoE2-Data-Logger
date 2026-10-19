"""Keep selected kill regions independent of full-screen aspect-ratio normalization."""

import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, ocr_runtime, ocr_workers, store
from PoE2_Data_Logger.ocr import kill_counts
from PoE2_Data_Logger.platform.hotkey import HotkeyManager


class KillCountCropContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-crop-context-")
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()
        logger.start_map()
        with Image.open(kill_counts.EXAMPLE) as source:
            self.image = source.convert("RGB")
        self.crop = self.image.crop((1119, 100, 1919, 550))
        with logger._connect() as db:
            logger._set_meta(db, "kill_count_region", {"x": 1119, "y": 100, "w": 800, "h": 450})
        self.manager = None

    def tearDown(self):
        if self.manager is not None:
            self.manager.cancel_capture()
        ocr_workers.shutdown(wait=True)
        store.DATA_DIR = self.previous
        logger._READY = False
        self.temporary.cleanup()

    def assert_counts(self, result, size):
        self.assertTrue(result["complete"], result)
        self.assertEqual([result[key] for key in kill_counts.CATEGORIES], [984, 719, 656, 4])
        for row in result["readings"]:
            left, top, right, bottom = row["bounds"]
            self.assertTrue(0 <= left < right <= size[0])
            self.assertTrue(0 <= top < bottom <= size[1])

    def test_original_pixels_in_wide_selected_regions_cross_the_450_pixel_threshold(self):
        """The same captured skull row remains readable when one extra crop row is included."""
        for bottom in (549, 550):
            with self.subTest(height=bottom - 100):
                crop = self.image.crop((1119, 100, 1919, bottom))
                self.assert_counts(kill_counts.scan(crop, is_crop=True), crop.size)

    def test_direct_path_reader_preserves_explicit_crop_context(self):
        path = Path(self.temporary.name) / "selected-kill-region.png"
        self.crop.save(path)
        self.assert_counts(kill_counts.scan(path, is_crop=True), self.crop.size)

    def test_dedicated_hotkey_transports_selected_pixels_and_crop_context_to_actual_worker(self):
        """Exercise the canonical recognizer in a real child through the production kill-key path."""
        grabber = Mock(return_value=self.crop)
        self.manager = HotkeyManager(supported=False, grabber=grabber)
        with patch.object(ocr_workers, "submit", wraps=ocr_workers.submit) as submit:
            self.manager.capture("kills")
        event = self.manager.status()["latest"]
        self.assertEqual((event["mode"], event["error"], event["result"]), ("kills", "", None))
        payload = event["kill_counts"]
        self.assertNotIn("error", payload)
        self.assert_counts(payload["result"], self.crop.size)
        self.assertEqual(payload["source_mode"], "kills")
        self.assertEqual(payload["scope"], "map_final")
        self.assertEqual(payload["context"]["_capture_map_id"], "M0001")
        self.assertNotIn("_capture_expedition", payload["context"])
        grabber.assert_called_once_with(bbox=(1119, 100, 1919, 550), all_screens=True)
        submit.assert_called_once()
        request = submit.call_args.args[0]
        self.assertIsInstance(request, ocr_workers.Request)
        self.assertIs(request.function, kill_counts.scan)
        self.assertEqual(request.kwargs, {"strictness": 50, "is_crop": True})
        self.assertEqual(request.data_dir, str(store.DATA_DIR))
        self.assertEqual(request.cpu_threads, ocr_runtime.active_threads())
        self.assertEqual(request.args[0].size, self.crop.size)
        evidence = Image.open(io.BytesIO(base64.b64decode(payload["image_base64"])))
        self.assertEqual(evidence.size, self.crop.size)
        self.assertEqual(evidence.tobytes(), self.crop.tobytes())
        self.assertIsNone(self.manager.image(event["id"]))
        state = logger.get_state()
        self.assertIsNone(state["ocr_pending"])
        self.assertEqual(state["scan_commit_count"], 0)

    def test_injected_reader_retains_its_image_and_strictness_only_api(self):
        calls = []

        def injected(image, strictness):
            calls.append((image.size, strictness))
            return {"complete": False, "status": "Review the four counters.", "readings": []}

        self.manager = HotkeyManager(supported=False, grabber=lambda **_: self.crop,
                                    readers={"kills": injected})
        self.manager.capture("kills")
        self.assertEqual(calls, [(self.crop.size, 50)])
        payload = self.manager.status()["latest"]["kill_counts"]
        self.assertNotIn("error", payload)
        self.assertFalse(payload["result"]["complete"])


if __name__ == "__main__":
    unittest.main()
