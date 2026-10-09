"""Ritual header image checks for tribute/reroll totals and blank fields when numerals are absent or unreadable."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.ocr import item_ocr


class RitualTotalsTests(unittest.TestCase):
    """Check tribute and reroll readings use visible header evidence and retain blank or zero values accurately."""
    def test_visible_grid_header_populates_both_fields_when_title_ocr_is_missing(self):
        """Verify visible grid header populates both fields when title OCR is missing."""
        folder = Path(__file__).parent / "fixtures/ritual_headers"
        for case in json.loads((folder / "expected.json").read_text()):
            with self.subTest(rerolls=case["rerolls"]), Image.open(folder / case["image"]) as image:
                result = item_ocr.ritual_totals([], image, grid={"bounds": case["bounds"]})
                self.assertEqual(result, {"tribute_available": case["tribute"],
                                          "rerolls_remaining": case["rerolls"]})

    def test_rerolls_can_populate_when_available_tribute_is_unreadable(self):
        """Verify rerolls can populate when available tribute is unreadable."""
        folder = Path(__file__).parent / "fixtures/ritual_headers"
        case = next(case for case in json.loads((folder / "expected.json").read_text()) if case["rerolls"] == 0)
        with Image.open(folder / case["image"]) as image, \
                patch.object(item_ocr, "ocr_lines", return_value=[]):
            result = item_ocr.ritual_totals([], image, grid={"bounds": case["bounds"]})
        self.assertEqual(result, {"tribute_available": None, "rerolls_remaining": 0})

    def test_header_tribute_uses_grid_position_without_favours_title(self):
        """Verify header tribute uses grid position without favours title."""
        rows = [{"text": "20,237 Tribute", "score": .999, "x": 900, "y": 10, "right": 1100, "bottom": 30},
                {"text": "18,956 Tribute", "score": .999, "x": 382, "y": 185, "right": 555, "bottom": 212}]
        with patch.object(item_ocr, "_ritual_grid_rerolls", return_value=4), \
                patch.object(item_ocr, "ocr_lines") as read:
            result = item_ocr.ritual_totals(rows, Image.new("RGB", (1200, 900)),
                                            grid={"bounds": (121, 237, 753, 763)})
            read.assert_not_called()
        self.assertEqual(result, {"tribute_available": 18956, "rerolls_remaining": 4})

    def test_reduced_header_reads_complete_two_digit_counter(self):
        """Verify reduced header reads complete two digit counter."""
        folder = Path(__file__).parent / "fixtures/ritual_headers"
        for value, bounds in ((36, (14, 65, 330, 328)), (35, (9, 67, 325, 330))):
            with self.subTest(rerolls=value), Image.open(folder / f"header_{value}_half.png") as image:
                self.assertEqual(item_ocr._ritual_grid_rerolls(image, {"bounds": bounds}), value)

    def test_grid_only_capture_leaves_header_fields_blank(self):
        """Verify grid only capture leaves header fields blank."""
        with patch.object(item_ocr, "_engine") as engine:
            result = item_ocr.ritual_totals([], Image.new("RGB", (640, 530)),
                                           grid={"bounds": (2, 2, 634, 529)})
            self.assertEqual(result, {"tribute_available": None, "rerolls_remaining": None})
            engine.assert_not_called()

    def test_zero_available_tribute_is_preserved_without_title(self):
        """Verify zero available tribute is preserved without title."""
        rows = [{"text": "0 Tribute", "score": .999, "x": 382, "y": 185, "right": 555, "bottom": 212}]
        with patch.object(item_ocr, "_ritual_grid_rerolls", return_value=0):
            result = item_ocr.ritual_totals(rows, Image.new("RGB", (1200, 900)),
                                           grid={"bounds": (121, 237, 753, 763)})
        self.assertEqual(result, {"tribute_available": 0, "rerolls_remaining": 0})

    def test_tribute_number_and_word_detected_as_separate_parts_use_merged_row(self):
        """Verify tribute number and word detected as separate parts use merged row."""
        rows = [{"text": "18,956 Tribute", "score": .999, "x": 382, "y": 185, "right": 555, "bottom": 212,
                 "parts": [{"text": "18,956", "score": .999, "x": 382, "y": 185, "right": 455, "bottom": 212},
                           {"text": "Tribute", "score": .999, "x": 460, "y": 185, "right": 555, "bottom": 212}]}]
        with patch.object(item_ocr, "_ritual_grid_rerolls", return_value=4), \
                patch.object(item_ocr, "ocr_lines") as read:
            result = item_ocr.ritual_totals(rows, Image.new("RGB", (1200, 900)),
                                           grid={"bounds": (121, 237, 753, 763)})
            read.assert_not_called()
        self.assertEqual(result, {"tribute_available": 18956, "rerolls_remaining": 4})

    def test_actual_counter_pixels_preserve_zero_and_two_digit_values(self):
        """Verify actual counter pixels preserve zero and two digit values."""
        folder = Path(__file__).parent / "fixtures/ritual_counters"
        cases = json.loads((folder / "expected.json").read_text())
        for case in cases:
            with self.subTest(rerolls=case["rerolls"]), Image.open(folder / case["image"]) as image:
                result = item_ocr._ritual_grid_rerolls(image, {"bounds": case["bounds"]})
                self.assertEqual(result, case["rerolls"])

    def test_inconsistent_counter_reads_are_held_instead_of_clipping_second_digit(self):
        """Verify inconsistent counter reads are held instead of clipping second digit."""
        folder = Path(__file__).parent / "fixtures/ritual_counters"
        case = next(case for case in json.loads((folder / "expected.json").read_text()) if case["rerolls"] == 35)
        with Image.open(folder / case["image"]) as source:
            image = source.convert("RGB")
        grid = {"bounds": case["bounds"]}
        for texts, scores in ((["3", "35"], [.999, .999]), (["35", "35"], [.99, .8]),
                              (["", "35"], [0, .999])):
            with self.subTest(texts=texts, scores=scores), patch.object(item_ocr, "_engine", return_value=SimpleNamespace(
                    text_rec=lambda value: SimpleNamespace(txts=texts, scores=scores))):
                self.assertIsNone(item_ocr._ritual_grid_rerolls(image, grid))

    def test_grid_without_visible_header_cannot_read_numbers_outside_capture(self):
        """Verify grid without visible header cannot read numbers outside capture."""
        with patch.object(item_ocr, "_engine") as engine:
            self.assertIsNone(item_ocr._ritual_grid_rerolls(
                Image.new("RGB", (640, 530)), {"bounds": (2, 2, 634, 529)}))
            engine.assert_not_called()

    def test_available_tribute_is_separate_from_hovered_reward_price(self):
        """Verify available tribute is separate from hovered reward price."""
        rows = [{"text": "20,237 Tribute", "score": .999, "x": 900, "y": 10, "right": 1100, "bottom": 30},
                {"text": "Favours", "score": .999, "x": 380, "y": 110, "right": 490, "bottom": 140},
                {"text": "25,205 Tribute", "score": .999, "x": 382, "y": 185, "right": 555, "bottom": 212}]
        image = Image.new("RGB", (1200, 900))
        with patch.object(item_ocr, "_ritual_grid_rerolls", return_value=5):
            result = item_ocr.ritual_totals(rows, image, grid={"bounds": (121, 237, 753, 763)})
        self.assertEqual(result, {"tribute_available": 25205, "rerolls_remaining": 5})

    def test_tooltip_cost_without_favours_header_is_not_available_tribute(self):
        """Verify tooltip cost without favours header is not available tribute."""
        rows = [{"text": "20,237 Tribute", "score": .999, "x": 900, "y": 400, "right": 1100, "bottom": 430}]
        with patch.object(item_ocr, "_engine") as engine:
            result = item_ocr.ritual_totals(rows, Image.new("RGB", (1200, 900)))
            self.assertEqual(result, {"tribute_available": None, "rerolls_remaining": None})
            engine.assert_not_called()

    def test_merged_ocr_row_uses_individual_header_and_counter_parts(self):
        """Verify merged OCR row uses individual header and counter parts."""
        rows = [{"text": "Favours", "score": .999, "x": 100, "y": 10, "right": 200, "bottom": 30},
                {"text": "36 46,797 Tribute", "score": .999, "x": 20, "y": 60, "right": 260, "bottom": 85,
                 "parts": [{"text": "36", "score": .999, "x": 20, "y": 60, "right": 45, "bottom": 85},
                           {"text": "46,797 Tribute", "score": .999, "x": 100, "y": 60, "right": 260, "bottom": 85}]}]
        self.assertEqual(item_ocr.ritual_totals(rows), {"tribute_available": 46797, "rerolls_remaining": 36})


if __name__ == "__main__":
    unittest.main()
