"""Actual-model kill-counter checks and explicitly controlled negative derivatives."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps

from PoE2_Data_Logger.core import logger_store as logger, store
from PoE2_Data_Logger.ocr import kill_counts


class KillCountOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="poe2-kill-ocr-")
        cls.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(cls.temporary.name)
        logger._READY = False
        with Image.open(kill_counts.EXAMPLE) as source:
            cls.image = source.convert("RGB")

    @classmethod
    def tearDownClass(cls):
        store.DATA_DIR = cls.previous_data
        logger._READY = False
        cls.temporary.cleanup()

    def assert_counts(self, image, **options):
        """Positive image checks always use the real bundled recognition model."""
        result = kill_counts.scan(image, **options)
        self.assertTrue(result["complete"], result)
        self.assertEqual([result[key] for key in kill_counts.CATEGORIES], [984, 719, 656, 4])
        self.assertEqual(len(result["readings"]), 4)
        for row in result["readings"]:
            self.assertGreaterEqual(row["confidence"], .90)
            self.assertEqual(row["raw"], [str(row["value"])] * 2)
            self.assertEqual(len(row["bounds"]), 4)
        json.dumps(result)
        return result

    def test_reference_is_unchanged_original_user_capture(self):
        self.assertEqual(hashlib.sha256(kill_counts.EXAMPLE.read_bytes()).hexdigest(),
                         "09ccea376c93c950ea03cad500d988e420673c17020f844dad16d72e5cb8cdc5")
        self.assertEqual(self.image.size, (1919, 1079))

    def test_original_full_screenshot_reads_all_four_without_ocr_injection(self):
        self.assert_counts(self.image)

    def test_controlled_full_screenshot_resolution_derivatives(self):
        # These are resized versions of one image, not independent game captures.
        for size in ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(size=size):
                result = self.assert_counts(self.image.resize(size, Image.Resampling.LANCZOS))
                for row in result["readings"]:
                    left, top, right, bottom = row["bounds"]
                    self.assertTrue(0 <= left < right <= size[0])
                    self.assertTrue(0 <= top < bottom <= size[1])

    def test_selected_region_and_name_distractor(self):
        self.assert_counts(self.image.crop((1631, 259, 1919, 389)))
        self.assert_counts(self.image.crop((1494, 290, 1905, 334)))

    def test_controlled_selected_region_scale_derivatives(self):
        # Every UI scale between the fixed reference sizes, including dividers
        # and border lines that resampling thins to one pixel.
        region = self.image.crop((1631, 259, 1919, 389))
        for factor in (.5, .6, 2 / 3, .75, .8, .9, 1.1, 4 / 3, 1.5, 1.55, 1.75, 2, 2.5):
            with self.subTest(factor=factor):
                self.assert_counts(region.resize((round(region.width * factor), round(region.height * factor)),
                                                 Image.Resampling.LANCZOS))

    def test_125_percent_region_keeps_actual_low_confidence_reading_under_review(self):
        region = self.image.crop((1631, 259, 1919, 389))
        result = kill_counts.scan(region.resize((360, 162), Image.Resampling.LANCZOS))
        self.assertFalse(result["complete"])
        self.assertIsNone(result["normal"])
        self.assertEqual(result["readings"][0]["raw"], ["984", "984"])
        self.assertLess(result["readings"][0]["confidence"], .90)

    def test_highest_strictness_keeps_original_actual_readings(self):
        self.assert_counts(self.image, strictness=100)

    def test_each_erased_skull_prevents_completeness(self):
        for box in kill_counts.SKULL_BOUNDS:
            with self.subTest(box=box):
                derivative = self.image.copy()
                derivative.paste((15, 15, 15), box)
                with patch.object(kill_counts, "_read_numbers") as reader:
                    result = kill_counts.scan(derivative)
                self.assertFalse(result["complete"])
                reader.assert_not_called()

    def test_reordered_skull_colours_are_not_relabelled_by_position(self):
        derivative = self.image.copy()
        first, second = kill_counts.SKULL_BOUNDS[1:3]
        derivative.paste(self.image.crop(second), first)
        derivative.paste(self.image.crop(first), second)
        self.assertFalse(kill_counts.scan(derivative)["complete"])

    def test_grayscale_skulls_are_insufficient_for_colour_identity(self):
        self.assertFalse(kill_counts.scan(ImageOps.grayscale(self.image).convert("RGB"))["complete"])

    def test_each_erased_number_is_unknown_and_never_default_zero(self):
        for box in ((1668, 300, 1705, 325), (1723, 300, 1759, 325),
                    (1777, 300, 1815, 325), (1833, 300, 1869, 325)):
            with self.subTest(box=box):
                derivative = self.image.copy()
                derivative.paste((15, 15, 15), box)
                result = kill_counts.scan(derivative)
                self.assertFalse(result["complete"])
                self.assertNotIn(0, [result[key] for key in kill_counts.CATEGORIES])

    def test_clipped_first_count_is_not_read_as_its_visible_suffix(self):
        self.assertFalse(kill_counts.scan(self.image.crop((1682, 290, 1905, 334)))["complete"])

    def test_widened_first_number_is_not_read_as_its_cropped_suffix(self):
        # Controlled derivatives only: prepend the actual captured Magic 7 to
        # the unchanged Normal 984. This does not assert a real four-digit layout.
        for left in (1654, 1662):
            with self.subTest(left=left):
                derivative = self.image.copy()
                derivative.paste(self.image.crop((1725, 303, 1734, 323)), (left, 303))
                with patch.object(kill_counts, "_read_numbers") as reader:
                    result = kill_counts.scan(derivative)
                self.assertFalse(result["complete"])
                self.assertIsNone(result["normal"])
                reader.assert_not_called()

    def test_region_without_first_numeric_gutter_cannot_certify_a_complete_number(self):
        # All visible digits remain, but there is no captured left context to
        # exclude additional leading digits outside the selected region.
        self.assertFalse(kill_counts.scan(self.image.crop((1668, 290, 1905, 334)))["complete"])

    def test_leading_digit_cut_by_selected_region_still_requires_review(self):
        derivative = self.image.copy()
        derivative.paste(self.image.crop((1725, 303, 1734, 323)), (1654, 303))
        self.assertFalse(kill_counts.scan(derivative.crop((1659, 290, 1905, 334)))["complete"])

    def test_duplicate_actual_rows_remain_ambiguous(self):
        row = self.image.crop((1631, 290, 1919, 334))
        derivative = Image.new("RGB", (row.width, row.height * 2 + 20), (15, 15, 15))
        derivative.paste(row, (0, 0))
        derivative.paste(row, (0, row.height + 20))
        self.assertFalse(kill_counts.scan(derivative)["complete"])

    def test_map_content_icons_and_other_numbers_are_not_counter_row(self):
        self.assertFalse(kill_counts.scan(self.image.crop((1510, 430, 1919, 660)))["complete"])
        self.assertFalse(kill_counts.scan(Image.new("RGB", (288, 130), "black"))["complete"])

    def test_separate_additional_number_within_one_cell_is_not_concatenated(self):
        derivative = self.image.copy()
        # Place a second captured digit before Unique's actual 4, with a gap.
        derivative.paste(self.image.crop((1856, 303, 1867, 324)), (1839, 303))
        self.assertFalse(kill_counts.scan(derivative)["complete"])

    def test_independent_numeric_disagreement_requires_review(self):
        # Unit-only contradictory model outputs; positive image tests above never inject OCR.
        trials = [(str(value), .999) for value in (984, 984, 719, 719, 656, 656, 4, 9)]
        with patch.object(kill_counts, "_read_numbers", return_value=trials):
            result = kill_counts.scan(self.image)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["unique"])
        self.assertEqual(result["readings"][3]["raw"], ["4", "9"])

    def test_explicit_zero_numeric_contract_and_weak_readings(self):
        # No original image containing a zero counter has been supplied. This
        # checks the parser contract, not an asserted real-image zero result.
        trials = [("0", .999)] * 8
        with patch.object(kill_counts, "_read_numbers", return_value=trials):
            result = kill_counts.scan(self.image)
        self.assertTrue(result["complete"])
        self.assertEqual([result[key] for key in kill_counts.CATEGORIES], [0, 0, 0, 0])
        for raw, confidence in (("0", .4), ("1,234", .999), ("1k", .999), ("-1", .999), ("4 9", .999)):
            with self.subTest(raw=raw, confidence=confidence):
                with patch.object(kill_counts, "_read_numbers", return_value=[(raw, confidence)] * 8):
                    self.assertFalse(kill_counts.scan(self.image)["complete"])

    def test_invalid_preferences_and_input_are_rejected(self):
        for level in (-1, 101, True, 50.5):
            with self.subTest(level=level), self.assertRaises(ValueError):
                kill_counts.scan(self.image, strictness=level)
        for image in (None, Image.new("RGB", (19, 30))):
            with self.subTest(image=image), self.assertRaises(ValueError):
                kill_counts.scan(image)


if __name__ == "__main__":
    unittest.main()
