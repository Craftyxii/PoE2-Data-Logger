"""Image checks preserving deferred markers across scales without attaching them to adjacent or empty slots."""

import json
from pathlib import Path
import unittest

from PIL import Image

from PoE2_Data_Logger.ocr.item_ocr import deferred_markers
from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid
from tests.test_ritual_grid_geometry import synthetic_grid


class RitualDeferredScaleTests(unittest.TestCase):
    """Check deferred-marker attachment across scaled real captures and empty synthetic grids."""
    fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"

    def assert_anchors(self, image, expected):
        """Detect reward-grid markers and assert their scores, bounds, and owning reward anchors."""
        grid = detect_reward_grid(image)
        self.assertIsNotNone(grid)
        markers = deferred_markers(image, grid=grid)
        anchors = set()
        for marker in markers:
            self.assertGreaterEqual(marker["score"], .86)
            self.assertGreaterEqual(marker["x"], 0)
            self.assertGreaterEqual(marker["y"], 0)
            self.assertLess(marker["x"], image.width)
            self.assertLess(marker["y"], image.height)
            for reward in grid["rewards"]:
                left, top, right, bottom = reward["box"]
                if left <= marker["x"] <= right and top <= marker["y"] <= bottom:
                    anchors.add(min(reward["slots"]))
        self.assertEqual(sorted(anchors), expected)

    def test_supplied_reward_markers_survive_resizing_without_false_positives(self):
        """Verify supplied reward markers survive resizing without false positives."""
        cases = json.loads((self.fixtures / "expected.json").read_text(encoding="utf-8"))
        for expected in cases:
            with Image.open(self.fixtures / expected["file"]) as source:
                original = source.convert("RGB")
            for scale in (.75, 1, 1.25):
                with self.subTest(capture=expected["source"], scale=scale):
                    image = original.resize((round(original.width * scale),
                                             round(original.height * scale)),
                                            Image.Resampling.LANCZOS)
                    self.assert_anchors(image, expected["deferred_anchors"])

    def test_scaled_full_captures_keep_marker_attachment_to_the_reward(self):
        """Verify scaled full captures keep marker attachment to the reward."""
        for name, anchors in (("03_context.webp", []), ("11_context.webp", [1, 13])):
            with Image.open(self.fixtures / name) as source:
                original = source.convert("RGB")
            for scale in (.75, 1, 1.25):
                with self.subTest(capture=name, scale=scale):
                    image = original.resize((round(original.width * scale),
                                             round(original.height * scale)),
                                            Image.Resampling.LANCZOS)
                    self.assert_anchors(image, anchors)

    def test_occupied_slots_and_empty_cursor_do_not_invent_deferred_markers(self):
        """Verify occupied slots and empty cursor do not invent deferred markers."""
        for occupied, cursor in (([], True), (range(1, 121), False)):
            with self.subTest(occupied=bool(occupied), cursor=cursor):
                image = synthetic_grid(occupied, cursor=cursor)
                grid = detect_reward_grid(image)
                self.assertIsNotNone(grid)
                self.assertEqual(deferred_markers(image, grid=grid), [])

    def test_plain_images_do_not_invent_markers(self):
        """Verify plain images do not invent markers."""
        for color in ("black", "white", (185, 143, 58)):
            with self.subTest(color=color):
                self.assertEqual(deferred_markers(Image.new("RGB", (640, 535), color)), [])


if __name__ == "__main__":
    unittest.main()
