"""Ritual panel/lattice checks retaining multi-cell equipment footprints and holding clipped or unsupported grids."""

from pathlib import Path
import json
import unittest

from PIL import Image, ImageDraw

from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid, has_grid_structure


EXPECTED_FOOTPRINTS = [[1], [2], [13], [14], [25, 26], [37, 38, 49, 50],
                       [61, 73, 85], [97], [109, 110]]


def synthetic_grid(occupied=(), cursor=False):
    image = Image.new("RGB", (540, 500), (9, 7, 7))
    draw = ImageDraw.Draw(image)
    for row in range(10):
        for column in range(12):
            left, top = 30 + column * 40, 50 + row * 40
            draw.rectangle((left, top, left + 40, top + 40), fill=(4, 4, 4))
            draw.ellipse((left + 12, top + 12, left + 28, top + 28), outline=(15, 15, 15), width=2)
    for column in range(13):
        draw.line((30 + column * 40, 50, 30 + column * 40, 450), fill=(70, 56, 30))
    for row in range(11):
        draw.line((30, 50 + row * 40, 510, 50 + row * 40), fill=(70, 56, 30))
    for slot in occupied:
        row, column = divmod(slot - 1, 12)
        left, top = 30 + column * 40, 50 + row * 40
        draw.rectangle((left + 2, top + 2, left + 38, top + 38), fill=(8, 4, 28))
        draw.rectangle((left + 8, top + 8, left + 32, top + 32), fill=(115, 124, 142))
    if cursor:
        left, top = 470, 330
        draw.rectangle((left + 1, top + 1, left + 39, top + 39), outline=(215, 185, 115), width=3)
        draw.line((left + 12, top + 39, left + 20, top + 34, left + 28, top + 39), fill=(215, 185, 115), width=2)
    return image


class RitualGridGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).resolve().parent.parent / "PoE2_Data_Logger/region_examples/ritual.jpg"
        with Image.open(fixture) as source:
            cls.full = source.convert("RGB")
        width, height = cls.full.size
        cls.crop = cls.full.crop((round(width * .098), round(height * .106),
                                 round(width * (.098 + .379)), round(height * (.106 + .78))))

    def assert_rewards(self, image, **kwargs):
        grid = detect_reward_grid(image, **kwargs)
        self.assertIsNotNone(grid, "The complete visible Ritual grid should be detected")
        self.assertEqual([reward["slots"] for reward in grid["rewards"]], EXPECTED_FOOTPRINTS)
        self.assertEqual((grid["columns"], grid["rows"]), (12, 10))
        self.assertTrue(grid["evidence"]["complete"])
        self.assertGreaterEqual(grid["bounds"][0], 0)
        self.assertGreaterEqual(grid["bounds"][1], 0)
        self.assertLessEqual(grid["bounds"][2], image.width)
        self.assertLessEqual(grid["bounds"][3], image.height)
        self.assertNotIn(96, [slot for reward in grid["rewards"] for slot in reward["slots"]],
                         "A highlighted empty cell is not an occupied reward")
        return grid

    def test_actual_page_groups_equipment_and_ignores_empty_cursor_cell(self):
        self.assert_rewards(self.crop)

    def test_full_capture_uses_ritual_grid_and_excludes_player_inventory(self):
        grid = self.assert_rewards(self.full, header_box=(410, 178, 625, 218))
        self.assertLess(grid["bounds"][2], 1000)
        self.assertGreater(grid["bounds"][1], 250)

    def test_normal_resolution_changes_preserve_all_nine_rewards(self):
        for scale in (.5, .75, 1.25, 2):
            with self.subTest(scale=scale):
                image = self.crop.resize((round(self.crop.width * scale), round(self.crop.height * scale)),
                                         Image.Resampling.LANCZOS)
                self.assert_rewards(image)

    def test_grid_only_and_border_crops_preserve_all_rewards(self):
        for box in ((30, 190, 670, 725), (0, 170, 726, 760), (35, 193, 666, 720)):
            with self.subTest(box=box):
                self.assert_rewards(self.crop.crop(box))

    def test_clipped_grids_are_not_reported_as_complete_pages(self):
        for box in ((0, 0, 626, 840), (0, 0, 726, 600), (50, 0, 726, 840), (0, 245, 726, 840)):
            with self.subTest(box=box):
                self.assertIsNone(detect_reward_grid(self.crop.crop(box)))
                self.assertTrue(has_grid_structure(self.crop.crop(box)))

    def test_blank_images_do_not_invent_grid_geometry(self):
        self.assertIsNone(detect_reward_grid(Image.new("RGB", (726, 840), "black")))
        self.assertFalse(has_grid_structure(Image.new("RGB", (726, 840), "black")))

    def test_full_120_cell_grid_keeps_each_one_cell_reward_separate(self):
        grid = detect_reward_grid(synthetic_grid(range(1, 121)))
        self.assertIsNotNone(grid)
        self.assertEqual([reward["slots"] for reward in grid["rewards"]], [[slot] for slot in range(1, 121)])

    def test_empty_grid_and_cursor_only_grid_have_no_rewards(self):
        for cursor in (False, True):
            with self.subTest(cursor=cursor):
                grid = detect_reward_grid(synthetic_grid(cursor=cursor))
                self.assertIsNotNone(grid)
                self.assertEqual(grid["rewards"], [])

    def test_actual_reward_pages_keep_every_equipment_footprint_separate(self):
        fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"
        for expected in json.loads((fixtures / "expected.json").read_text(encoding="utf-8")):
            with self.subTest(capture=expected["source"]):
                with Image.open(fixtures / expected["file"]) as image:
                    grid = detect_reward_grid(image)
                self.assertIsNotNone(grid, "The complete visible grid must be detected")
                actual = [reward["slots"] for reward in grid["rewards"]]
                self.assertEqual(actual, expected["footprints"],
                                 "Each large item is one reward; adjacent items remain separate")
                if expected["highlighted_empty_slot"] is not None:
                    self.assertNotIn(expected["highlighted_empty_slot"],
                                     [slot for group in actual for slot in group])
                if expected["bottom_at_image_edge"]:
                    self.assertTrue(grid["evidence"]["image_boundary"])

    def test_scaled_supplied_pages_keep_all_reward_footprints(self):
        fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"
        cases = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
        for expected in cases:
            with Image.open(fixtures / expected["file"]) as source:
                original = source.convert("RGB")
            for scale in (.75, 1.25):
                with self.subTest(capture=expected["source"], scale=scale):
                    image = original.resize((round(original.width * scale),
                                             round(original.height * scale)),
                                            Image.Resampling.LANCZOS)
                    grid = detect_reward_grid(image)
                    self.assertIsNotNone(grid)
                    self.assertEqual([reward["slots"] for reward in grid["rewards"]],
                                     expected["footprints"])
                    for reward in grid["rewards"]:
                        self.assertGreaterEqual(min(reward["box"]), 0)
                        self.assertLessEqual(reward["box"][2], image.width)
                        self.assertLessEqual(reward["box"][3], image.height)

    def test_small_full_captures_and_image_edge_keep_complete_grid(self):
        fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"
        cases = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
        for name in ("03.png", "11.png"):
            expected = next(case for case in cases if case["file"] == name)
            with Image.open(fixtures / (Path(name).stem + "_context.webp")) as source:
                original = source.convert("RGB")
            for scale in (.75, 1, 1.25):
                with self.subTest(capture=name, scale=scale):
                    image = original.resize((round(original.width * scale),
                                             round(original.height * scale)),
                                            Image.Resampling.LANCZOS)
                    grid = detect_reward_grid(image)
                    self.assertIsNotNone(grid, "Full-image line detection must retain the visible grid")
                    self.assertEqual([reward["slots"] for reward in grid["rewards"]],
                                     expected["footprints"])
                    if expected["bottom_at_image_edge"]:
                        self.assertTrue(grid["evidence"]["image_boundary"])

    def test_resampling_never_completes_clipped_supplied_pages(self):
        fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"
        cases = json.loads((fixtures / "expected.json").read_text(encoding="utf-8"))
        for expected in cases:
            with Image.open(fixtures / expected["file"]) as source:
                original = source.convert("RGB")
            for box in ((0, 0, original.width, original.height - 54),
                        (54, 0, original.width, original.height)):
                clipped = original.crop(box)
                for scale in (.75, 1.25):
                    with self.subTest(capture=expected["source"], clip=box, scale=scale):
                        image = clipped.resize((round(clipped.width * scale),
                                                round(clipped.height * scale)),
                                               Image.Resampling.LANCZOS)
                        self.assertIsNone(detect_reward_grid(image))

    def test_image_edge_does_not_allow_a_missing_bottom_reward_row(self):
        fixtures = Path(__file__).resolve().parent / "fixtures/ritual_rewards"
        with Image.open(fixtures / "11.png") as image:
            # This capture's complete lower frame coincides with the image
            # edge. Removing an actual row must still reject completeness.
            clipped = image.crop((0, 0, image.width, image.height - 53))
        self.assertIsNone(detect_reward_grid(clipped))
        self.assertTrue(has_grid_structure(clipped))


if __name__ == "__main__":
    unittest.main()
