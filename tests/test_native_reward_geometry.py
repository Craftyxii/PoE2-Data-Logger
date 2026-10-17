"""Native reward OCR retries use image evidence and preserve quantities and levels."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import runehelper_ocr as native
from PoE2_Data_Logger.ocr import opened_scan


def reward_row(*, rail=True):
    """Make a padded text band with short glyph stems and an optional panel rail."""
    gray = np.full((24, 160), 225, dtype=np.uint8)
    # Text occupies the middle of a row; the surrounding padding distinguishes
    # its character stems from the near-full-height parchment frame.
    for x in range(8, 140, 8):
        gray[6:18, x:x + 3] = 35
    if rail:
        gray[:, 150:152] = 25
    return gray


class NativeRewardFrameGeometryTests(unittest.TestCase):
    """The trailing frame is removed from model input, never from decoded strings."""

    def test_crop_stops_before_actual_right_edge_rail_without_mutating_pixels(self):
        gray = reward_row()
        previous = gray.copy()
        cropped = native._reward_frame_crop(gray)
        self.assertIsNotNone(cropped)
        self.assertEqual(cropped.shape, (24, 149))
        np.testing.assert_array_equal(cropped, gray[:, :149])
        np.testing.assert_array_equal(gray, previous)

    def test_padded_character_stems_at_the_right_edge_are_not_a_frame(self):
        gray = reward_row(rail=False)
        gray[5:19, 150:153] = 25
        self.assertIsNone(native._reward_frame_crop(gray))

    def test_full_height_column_outside_bounded_right_margin_is_not_a_frame(self):
        gray = reward_row(rail=False)
        gray[:, 120:122] = 25
        self.assertIsNone(native._reward_frame_crop(gray))

    def test_reward_signature_retains_actual_quantity_and_all_explicit_levels(self):
        examples = [
            ("3x Greater Orb of Augmentation", ("quantity", "3", ())),
            ("1x Uncut Skill Gem (Level 20)", ("quantity", "1", ("20",))),
            ("2× Reward (Level 19) (Level 20)", ("quantity", "2", ("19", "20"))),
            ("Skill Level 20: Grim Pillars", ("skill", "20")),
        ]
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(native._reward_signature(text), expected)
        for text in ("Runeshape Combinations", "Greater Orb of Augmentation", "1x", "Level 20"):
            with self.subTest(non_reward=text):
                self.assertIsNone(native._reward_signature(text))

    def test_reward_is_decoded_again_from_bounded_pixels_instead_of_stripping_text(self):
        gray = reward_row()
        initial = ("1x Orb of Augmentationl", 98.0)
        actual = ("1x Orb of Augmentation", 97.0)
        with patch.object(native, "_decode_row", side_effect=[initial, actual]) as decode:
            result = native._read_row(gray)
        self.assertEqual(result, actual)
        self.assertEqual(decode.call_count, 2)
        np.testing.assert_array_equal(decode.call_args_list[0].args[0], gray)
        np.testing.assert_array_equal(decode.call_args_list[1].args[0], gray[:, :149])

    def test_legitimate_final_letters_and_digits_remain_model_output(self):
        # These names need not exist in a catalog. The image retry must return
        # the model's raw characters, including a legitimate trailing l or 9.
        for text in ("1x Test Scroll", "1x Unknown Reward 9", "Skill Level 20: Test Scroll"):
            with self.subTest(text=text):
                actual = (text, 96.0)
                with patch.object(native, "_decode_row",
                                  side_effect=[(text + "l", 98.0), actual]) as decode:
                    result = native._read_row(reward_row())
                self.assertEqual(result, actual)
                self.assertEqual(decode.call_count, 2)

    def test_quantity_or_explicit_level_change_cannot_replace_original_read(self):
        readings = [
            ("3x Chaos Orbl", "1x Chaos Orb"),
            ("1x Uncut Skill Gem (Level 20)l", "1x Uncut Skill Gem (Level 19)"),
            ("1x Uncut Skill Gem (Level 20)l", "1x Uncut Skill Gem"),
            ("1x Unknown Rewardl", "1x Unknown Reward (Level 20)"),
            ("Skill Level 20: Grim Pillarsl", "Skill Level 19: Grim Pillars"),
            ("Skill Level 20: Grim Pillarsl", "1x Grim Pillars (Level 20)"),
            ("1x Chaos Orbl", "Chaos Orb"),
        ]
        for original_text, retry_text in readings:
            with self.subTest(original=original_text, retry=retry_text):
                initial = (original_text, 98.0)
                with patch.object(native, "_decode_row",
                                  side_effect=[initial, (retry_text, 99.0)]) as decode:
                    result = native._read_row(reward_row())
                self.assertEqual(result, initial)
                self.assertEqual(decode.call_count, 2)

    def test_uncertain_retry_retains_original_confidence_and_text(self):
        initial = ("1x Chaos Orbl", 98.0)
        with patch.object(native, "_decode_row",
                          side_effect=[initial, ("1x Chaos Orb", 79.99)]) as decode:
            result = native._read_row(reward_row())
        self.assertEqual(result, initial)
        self.assertEqual(decode.call_count, 2)

    def test_stricter_capture_keeps_original_when_retry_would_fail_its_threshold(self):
        initial = ("1x Chaos Orbl", 99.0)
        with patch.object(native, "_decode_row",
                          side_effect=[initial, ("1x Chaos Orb", 85.0)]) as decode:
            result = native._read_row(reward_row(), minimum_confidence=90)
        self.assertEqual(result, initial)
        self.assertEqual(decode.call_count, 2)

    def test_caller_cannot_lower_retry_below_native_confidence_floor(self):
        initial = ("1x Chaos Orbl", 98.0)
        with patch.object(native, "_decode_row",
                          side_effect=[initial, ("1x Chaos Orb", 79.99)]):
            result = native._read_row(reward_row(), minimum_confidence=20)
        self.assertEqual(result, initial)

    def test_identical_retry_text_preserves_original_confidence(self):
        initial = ("1x Test Scroll", 98.0)
        with patch.object(native, "_decode_row",
                          side_effect=[initial, (initial[0], 82.0)]) as decode:
            result = native._read_row(reward_row())
        self.assertEqual(result, initial)
        self.assertEqual(decode.call_count, 2)

    def test_heading_is_never_retried_even_when_it_contains_a_frame(self):
        initial = ("Runeshape Combinations", 98.0)
        with patch.object(native, "_decode_row", return_value=initial) as decode:
            result = native._read_row(reward_row())
        self.assertEqual(result, initial)
        self.assertEqual(decode.call_count, 1)

    def test_reward_without_pixel_frame_does_not_spend_another_model_call(self):
        initial = ("1x Test Scroll", 98.0)
        with patch.object(native, "_decode_row", return_value=initial) as decode:
            result = native._read_row(reward_row(rail=False))
        self.assertEqual(result, initial)
        self.assertEqual(decode.call_count, 1)


class NativeMissingRowBoundaryTests(unittest.TestCase):
    """Missing-row rescue cannot replace existing readings or fabricate evidence."""

    def test_no_captured_reward_prevents_geometry_rescue(self):
        heading = {"text": "Runeshape Combinations", "y1": 5, "y2": 15}
        with patch.object(native, "_row_bands") as bands:
            self.assertEqual(native._refined_rows(reward_row(), 1, [heading]), [])
            self.assertEqual(native._refined_rows(reward_row(), 1, []), [])
        bands.assert_not_called()

    def test_no_legacy_rejection_does_not_search_for_extra_rows(self):
        original = [{"text": "1x Existing Reward", "y1": 10, "y2": 20}]
        with patch.object(native, "_row_bands", return_value=([], [], {})) as bands:
            self.assertEqual(native._refined_rows(reward_row(), 1, original), [])
        self.assertEqual(bands.call_count, 1)

    def test_alignment_rejection_requires_an_actual_frame_rail(self):
        original = [{"text": "1x Existing Reward", "y1": 10, "y2": 20}]
        rejected = [{"start": 40, "end": 49, "reason": "alignment"}]
        with patch.object(native, "_row_bands", side_effect=[
                ([], rejected, {}), ([(35, 55)], [], {"rails": [], "separators": []})]):
            self.assertEqual(native._refined_rows(reward_row(), 1, original), [])

    def test_density_rejection_requires_separator_at_the_rejected_ink(self):
        original = [{"text": "1x Existing Reward", "y1": 10, "y2": 20}]
        rejected = [{"start": 40, "end": 49, "reason": "density"}]
        with patch.object(native, "_row_bands", side_effect=[
                ([], rejected, {}),
                ([(35, 55)], [], {"rails": [190], "separators": [(70, 72)]})]):
            self.assertEqual(native._refined_rows(reward_row(), 1, original), [])

    def test_proposals_cannot_overlap_old_rows_or_each_other_or_precede_first_reward(self):
        original = [{"text": "1x Existing Reward", "y1": 10, "y2": 20},
                    {"text": "Already read non-reward", "y1": 50, "y2": 60}]
        proposals = [(5, 15), (18, 25), (35, 45), (50, 60), (40, 48)]
        rejected = [{"start": start, "end": end - 1, "reason": "alignment"}
                    for start, end in proposals]
        with patch.object(native, "_row_bands", side_effect=[
                ([], rejected, {}),
                (proposals, [], {"rails": [190], "separators": []})]):
            self.assertEqual(native._refined_rows(reward_row(), 1, original), [(35, 45)])

    def test_nonoverlap_uses_original_capture_coordinates_at_reduced_scale(self):
        original = [{"text": "1x Existing Reward", "y1": 20, "y2": 40},
                    {"text": "Already read non-reward", "y1": 100, "y2": 120}]
        proposals = [(18, 25), (35, 45), (50, 60)]
        rejected = [{"start": start, "end": end - 1, "reason": "alignment"}
                    for start, end in proposals]
        with patch.object(native, "_row_bands", side_effect=[
                ([], rejected, {}),
                (proposals, [], {"rails": [190], "separators": []})]):
            self.assertEqual(native._refined_rows(reward_row(), .5, original), [(35, 45)])

    def test_rescue_preserves_every_original_field_and_discards_unprefixed_additions(self):
        original = [{"text": "Runeshape Combinations", "score": .99,
                     "x1": 30, "x2": 160, "y1": 1, "y2": 8},
                    {"text": "1x Existing Reward", "score": .98,
                     "x1": 50, "x2": 160, "y1": 10, "y2": 20}]
        recovered = {"text": "1x Actual New Reward", "score": .95,
                     "x1": 60, "x2": 160, "y1": 40, "y2": 50}
        decoration = {"text": "More frame artwork", "score": .99,
                      "x1": 60, "x2": 160, "y1": 60, "y2": 70}
        with patch.object(native, "_gray_panel", return_value=(reward_row(), 1)), \
                patch.object(native, "_rows", return_value=[(1, 8), (10, 20)]), \
                patch.object(native, "_refined_rows", return_value=[(40, 50), (60, 70)]), \
                patch.object(native, "_read_rows", side_effect=[original, [recovered, decoration]]):
            result = native._recognize_rows(Image.new("RGB", (160, 80)))
        self.assertEqual(result, original + [recovered])
        self.assertIs(result[0], original[0])
        self.assertIs(result[1], original[1])


class RealNativeMissingRewardTests(unittest.TestCase):
    """Read source captures through the real bundled model without catalog cleanup."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="poe2-native-reward-geometry-")
        cls.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(cls.temporary.name)
        logger._READY = False
        logger.initialize()
        fixtures = Path(__file__).resolve().parent / "fixtures"
        cls.captures = fixtures / "opened_rewards"
        cls.labels = json.loads((fixtures / "propagation_crowns/manifest.json").read_text())["rows"]

    @classmethod
    def tearDownClass(cls):
        store.DATA_DIR = cls.previous_data
        logger._READY = False
        cls.temporary.cleanup()

    @staticmethod
    def normalized(text):
        """Normalize typography only; never repair names, quantities or levels."""
        return " ".join(text.replace("’", "'").split())

    def expected(self, filename):
        names = [row["recipe"] for row in self.labels
                 if row["source_filename"] == filename and not row["recipe"].startswith("[")]
        return [self.normalized(name if name.startswith("1x ") else "1x " + name)
                for name in names]

    def assert_readable_rewards(self, rows, expected):
        raw = [self.normalized(row["text"]) for row in rows]
        # Only independently readable annotations form the expected set.
        # A newly legible clipped line is not a missing readable annotation.
        self.assertEqual([text for text in raw if text in expected], expected, raw)
        for text in expected:
            self.assertEqual(raw.count(text), 1, raw)

    def test_original_and_direct_panel_inputs_recover_all_ten_previously_missing_rows(self):
        # Source-coordinate crops used by the native panel-preparation pass.
        # Each preserves every independently readable reward in that source.
        panel_boxes = {"Pair12": (43, 131, 573, 601),
                       "Pair40": (0, 0, 575, 842),
                       "Pair87": (49, 0, 575, 842),
                       "Pair92": (48, 207, 575, 842)}
        for pair in ("Pair12", "Pair40", "Pair87", "Pair92"):
            capture = next(self.captures.glob(pair + "*_opened.*"))
            expected = self.expected(capture.name)
            with Image.open(capture) as source:
                source = source.convert("RGB")
                with self.subTest(pair=pair, entry="original capture"):
                    self.assert_readable_rewards(native.recognize(source), expected)
                panel = source.crop(panel_boxes[pair])
                with self.subTest(pair=pair, entry="direct native panel"):
                    self.assert_readable_rewards(native._recognize_rows(panel), expected)

    def test_recovered_order_resolves_opened_family_without_writing_logs(self):
        capture = next(self.captures.glob("Pair92*_opened.*"))
        result = opened_scan.scan_opened(capture)
        self.assertEqual(result["family"], "Family 70", result)
        self.assertEqual(result["candidates"], [70], result)
        self.assertEqual([self.normalized(row["raw"]) for row in result["opened_recipes"]],
                         self.expected(capture.name), result)
        self.assertTrue(auto_commit.candidate(result)["ready"], result)
        # Recognition and an opened auto-commit candidate cannot write a log.
        # Propagation approval is verified by its separate existing regressions.
        with logger._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commits").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
