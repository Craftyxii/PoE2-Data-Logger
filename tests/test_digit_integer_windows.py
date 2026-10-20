"""Keep exact digit IoU arithmetic across integral/correlation window sums."""
import unittest

import numpy as np

from PoE2_Data_Logger.ocr import digit_kernels


def window_oracle(source, glyph, dy_low, dy_high, minimum_ink):
    """Retain the original explicit-window arithmetic as an independent oracle."""
    height, width = source.shape
    glyph_height, glyph_width = glyph.shape
    if glyph_width > width or glyph_height > height:
        return []
    offsets = np.arange(dy_low, dy_high + 1)
    starts = height // 2 + offsets - glyph_height // 2
    valid = (starts >= 0) & (starts + glyph_height <= height)
    offsets, starts = offsets[valid], starts[valid]
    if not len(offsets):
        return []
    windows = np.lib.stride_tricks.sliding_window_view(
        source, (glyph_height, glyph_width))[starts]
    ink = windows.sum(axis=(-2, -1), dtype=np.int64)
    glyph_ink = int(glyph.sum())
    intersection = (windows * glyph).sum(axis=(-2, -1), dtype=np.int64)
    union = ink + glyph_ink - intersection
    scores = np.divide(intersection, union, out=np.zeros_like(intersection, dtype=np.float64),
                       where=union > 0)
    scores[ink < minimum_ink * glyph_ink] = 0
    best = scores.argmax(axis=0)
    return [{"x": x, "dy": int(offsets[best[x]]), "score": float(scores[best[x], x])}
            for x in range(width - glyph_width + 1) if scores[best[x], x] > 0]


class DigitIntegerWindowTests(unittest.TestCase):
    """Use known binary input bounds, exact scores and unchanged first-offset ties."""

    def test_seeded_binary_windows_match_original_at_all_supported_dimensions(self):
        """Exercise odd/even glyphs, clipping, valid/invalid starts and ink fractions."""
        rng = np.random.default_rng(9831)
        for index in range(500):
            height, width = int(rng.integers(1, 65)), int(rng.integers(1, 129))
            glyph_height, glyph_width = int(rng.integers(1, 33)), int(rng.integers(1, 33))
            source = (rng.random((height, width)) < rng.choice([0, .2, .5, 1])).astype(np.uint8)
            glyph = (rng.random((glyph_height, glyph_width)) < rng.choice([0, .3, 1])).astype(np.uint8)
            low, high = int(rng.integers(-10, 1)), int(rng.integers(0, 11))
            fraction = float(rng.choice([0, .45, 1]))
            with self.subTest(index=index, source=source.shape, glyph=glyph.shape):
                self.assertEqual(digit_kernels._slide_arrays(source, glyph, low, high, fraction),
                                 window_oracle(source, glyph, low, high, fraction))

    def test_maximum_ink_windows_do_not_overflow_and_keep_first_offset_ties(self):
        """Intersections above uint8 range retain exact unity and ascending-offset ties."""
        source = np.ones((64, 128), dtype=np.uint8)
        for shape in ((32, 32), (31, 32), (32, 31), (1, 1), (10, 8)):
            glyph = np.ones(shape, dtype=np.uint8)
            with self.subTest(glyph=shape):
                actual = digit_kernels._slide_arrays(source, glyph, -64, -32, 1)
                self.assertEqual(actual, window_oracle(source, glyph, -64, -32, 1))
                actual = digit_kernels._slide_arrays(source, glyph, -16, 16, 1)
                self.assertEqual(actual, window_oracle(source, glyph, -16, 16, 1))
                self.assertTrue(actual)
                self.assertTrue(all(match["score"] == 1 for match in actual))

    def test_minimum_ink_gate_and_zero_union_keep_the_original_boundaries(self):
        """Zero glyphs and exact non-integer ratios retain their float64 IoU scores."""
        source = np.zeros((24, 40), dtype=np.uint8)
        source[8:15, 2:9] = 1
        for glyph in (np.zeros((10, 8), dtype=np.uint8), np.ones((10, 8), dtype=np.uint8)):
            for fraction in (0, .45, 49 / 80, np.nextafter(49 / 80, 1), 1):
                with self.subTest(ink=int(glyph.sum()), fraction=fraction):
                    self.assertEqual(digit_kernels._slide_arrays(source, glyph, -4, 4, fraction),
                                     window_oracle(source, glyph, -4, 4, fraction))


if __name__ == "__main__":
    unittest.main()
