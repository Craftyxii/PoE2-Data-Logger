"""Compare Fast recognition against the original readers and ordered grid search."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.ocr import currency_ocr, digit_reader, icon_reader, item_ocr, ritual_grid

FIXTURES = Path(__file__).parent / 'fixtures'


class FastRecognitionParityTests(unittest.TestCase):
    def setUp(self):
        self.preference = patch.object(ocr_runtime, '_ACTIVE_THREADS', 2)
        self.preference.start()
        self.addCleanup(self.preference.stop)
        self.reader = currency_ocr.CurrencyReader()

    def cells(self):
        for name in ('inventory_120330.png', 'inventory_121810.png'):
            with Image.open(FIXTURES / 'currency_training' / name) as source:
                inventory = item_ocr.inventory_grid(source.convert('RGB'))
            for slot in (1, 6, 15, 26, 44, 60):
                yield name, slot, item_ocr.inventory_cell(inventory, slot)

    def test_python_digits_match_javascript_complete_results(self):
        for name, slot, cell in self.cells():
            for size in (40, 56, 80):
                with self.subTest(image=name, slot=slot, size=size):
                    shown = cell.resize((size, size), Image.Resampling.LANCZOS)
                    pixels = self.reader._pixels(shown)
                    cx, cy, scale = int(size * .27), int(size * .25), max(.5, size / 40)
                    expected = json.loads(self.reader.read_count(json.dumps(
                        {**pixels, 'cx': cx, 'cy': cy, 'scale': scale})))
                    actual = digit_reader.read_count(np.asarray(shown.convert('RGBA')),
                        cx, cy, self.reader._digit_templates, scale)
                    self.assertEqual(actual, expected)

    def test_numpy_icon_reader_matches_javascript_ranks_and_scores(self):
        for name, slot, cell in self.cells():
            with self.subTest(image=name, slot=slot):
                pixels = self.reader._pixels(cell)
                expected = json.loads(self.reader.identify(json.dumps(pixels)))
                actual = icon_reader.identify(pixels['data'], pixels['w'], pixels['h'],
                                              self.reader._identify_bank)
                self.assertEqual(actual, expected)

    def test_shift_rankings_match_original_with_no_result_cache_reuse(self):
        for name, slot, cell in self.cells():
            for digits in (None, 1, 2):
                with self.subTest(image=name, slot=slot, digits=digits):
                    self.reader.clear_results()
                    with patch.object(ocr_runtime, '_FAST', False):
                        expected = self.reader.inventory_ranked(cell, calibrated=True, count_digits=digits)
                    self.reader.clear_results()
                    with patch.object(ocr_runtime, '_FAST', True):
                        actual = self.reader.inventory_ranked(cell, calibrated=True, count_digits=digits)
                    self.assertEqual(actual, expected)

    def test_speculative_grid_matches_ordered_search_when_heading_allows_it(self):
        with Image.open(FIXTURES / 'ritual_grayscale' / '264_tribute.png') as source:
            original = source.convert('RGB')
        accepted = rejected = 0
        for scale in (1, 4 / 3, 2):
            image = original.resize((round(original.width * scale), round(original.height * scale)),
                                    Image.Resampling.LANCZOS)
            grid, allows = ritual_grid.speculative_grid(image)
            self.assertIsNotNone(grid)
            headers = (None, (0, 0, image.width, 10),
                       (0, image.height - 20, image.width, image.height))
            for header in headers:
                with self.subTest(scale=scale, header=header):
                    if allows(header):
                        accepted += 1
                        self.assertEqual(grid, ritual_grid.detect_reward_grid(image, header_box=header))
                    else:
                        rejected += 1
        self.assertGreater(accepted, 0)
        self.assertGreater(rejected, 0)


if __name__ == '__main__':
    unittest.main()
