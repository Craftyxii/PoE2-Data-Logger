"""Opened top-row recovery preserves observed quantities, levels and review guards."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import opened_scan


class OpenedTopRewardRecoveryTests(unittest.TestCase):
    """Exercise production scanning with controlled native and bounded panel readings."""

    def setUp(self):
        self.data = tempfile.TemporaryDirectory(prefix="poe2-opened-top-guards-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.data.name)
        logger._READY = False
        logger.initialize()
        self.image = Image.new("RGB", (575, 800), (190, 190, 190))
        self.activity_before = self.activity()

    def tearDown(self):
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.data.cleanup()

    def activity(self):
        """Record persisted logging state independently of OCR return values."""
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits",
                                  "chain_completions", "chain_step_provenance", "seed_states")}

    @staticmethod
    def row(text, y1=307, y2=354, x1=49, x2=575, score=.99):
        return {"text": text, "score": score,
                "x1": x1, "x2": x2, "y1": y1, "y2": y2}

    def heading(self, text="Runeshape Combinations", score=.99, y1=227, y2=262):
        return self.row(text, y1=y1, y2=y2, x1=180, x2=395, score=score)

    def native(self, first="1x Taumatrgs tlx deelz2)", score=.86):
        return [self.row(first, score=score),
                self.row("1x Uncut Skill Gem (Level 20)", y1=386, y2=419)]

    def recovered(self, text="1x Thaumaturgic Flux (Level 20)", **changes):
        return {**self.row(text, y1=306, y2=340, x1=216, x2=539, score=.98541), **changes}

    def scan(self, native=None, header=None, retry=None, **options):
        """Keep the matcher, family resolution and final approval checks real."""
        native = self.native() if native is None else native
        header = [self.heading()] if header is None else header
        retry = [self.recovered()] if retry is None else retry
        with patch.object(opened_scan.runehelper_ocr, "recognize", return_value=copy.deepcopy(native)), \
                patch.object(opened_scan, "_panel_text", side_effect=[copy.deepcopy(header),
                                                                      copy.deepcopy(retry)]) as panel, \
                patch.object(opened_scan, "_engine", side_effect=AssertionError("Unexpected full OCR")), \
                patch.object(opened_scan, "_icon_count", return_value=6) as icons, \
                patch.object(opened_scan, "_list_complete", return_value=False):
            result = opened_scan.scan_opened(self.image, **options)
        self.assertEqual(self.activity(), self.activity_before, "Scanning must not log any activity")
        return result, panel, icons

    def assert_held(self, result, raw="1x Taumatrgs tlx deelz2)"):
        self.assertIsNone(result["first_recipe"], result)
        self.assertEqual(result["opened_recipes"][0]["raw"], raw)
        self.assertFalse(auto_commit.candidate(result)["ready"], result)

    def test_unmatched_top_recovers_one_reading_and_keeps_native_geometry(self):
        result, panel, icons = self.scan()
        self.assertEqual(result["first_recipe"], "Thaumaturgic Flux (Level 20)")
        self.assertEqual(result["next_recipe"], "Uncut Skill Gem (Level 20)")
        self.assertEqual(result["first_line_gap"], 45)
        self.assertEqual(result["opened_recipes"][0]["match_score"], 1.0)
        self.assertEqual(panel.call_count, 2)
        self.assertEqual(panel.call_args.args[1:], (278, 575, 370))
        self.assertEqual(icons.call_args.args[1], 307)

    def test_retry_keeps_an_already_explicit_level(self):
        for level, expected in ((20, "Thaumaturgic Flux (Level 20)"), (19, None)):
            raw = f"1x ??? (Level {level})"
            with self.subTest(native_level=level):
                result, panel, _ = self.scan(native=self.native(raw))
                self.assertEqual(result["first_recipe"], expected)
                self.assertEqual(panel.call_count, 2)
                if expected is None:
                    self.assert_held(result, raw)

    def test_retry_does_not_change_an_explicit_native_quantity(self):
        raw = "2x Taumatrgs tlx deelz2)"
        result, panel, _ = self.scan(native=self.native(raw))
        self.assert_held(result, raw)
        self.assertEqual(panel.call_count, 2)

    def test_skill_prefix_recovers_the_same_explicit_level(self):
        result, _, _ = self.scan(native=self.native("Skill Level 20: ???"),
                                 retry=[self.recovered("Skill Level 20: Thaumaturgic Flux")])
        self.assertEqual(result["first_recipe"], "Thaumaturgic Flux (Level 20)")
        self.assertEqual(result["first_line_gap"], 45)

    def test_retry_requires_one_prefixed_overlapping_reading(self):
        cases = {
            "no prefix": [self.recovered("Thaumaturgic Flux (Level 20)")],
            "two readings": [self.recovered(), self.recovered(y1=310, y2=344)],
            "unknown competing reward": [self.recovered(), self.recovered("1x Completely Unreadable")],
            "not overlapping": [self.recovered(y1=360, y2=369)],
        }
        for label, rows in cases.items():
            with self.subTest(case=label):
                result, _, _ = self.scan(retry=rows)
                self.assert_held(result)

    def test_retry_rejects_readings_outside_the_real_crop(self):
        for changes in ({"y1": 277}, {"y2": 371}, {"x2": 576},
                        {"y1": 335, "y2": 334}, {"x1": -1}, {"x1": 540, "x2": 539}):
            with self.subTest(box_changes=changes):
                result, _, _ = self.scan(retry=[self.recovered(**changes)])
                self.assert_held(result)

    def test_weak_retry_uses_the_existing_strictness_confidence_floor(self):
        for strictness, confidence in ((50, .79), (75, .89)):
            with self.subTest(strictness=strictness):
                result, _, _ = self.scan(native=self.native(score=.99),
                                         retry=[self.recovered(score=confidence)], strictness=strictness)
                self.assert_held(result)

    def test_tied_recipe_matches_stay_unresolved(self):
        with logger._connect() as db:
            db.executemany("INSERT INTO recipes(name,sockets,combo) VALUES(?,6,'')",
                           [("Quartz Sentinel A",), ("Quartz Sentinel B",)])
        result, _, _ = self.scan(retry=[self.recovered("1x Quartz Sentinel")])
        self.assert_held(result)

    def test_recovery_requires_a_verified_heading(self):
        for header in ([], [self.heading(score=.79)], [self.heading(text="Other heading")]):
            with self.subTest(header=header):
                result, panel, _ = self.scan(header=header)
                self.assert_held(result)
                self.assertFalse(result["header_verified"])
                self.assertEqual(panel.call_count, 1)

    def test_heading_outside_the_real_strip_cannot_authorize_missing_row_insertion(self):
        native = [self.row("1x Uncut Skill Gem (Level 20)"),
                  self.row("1x Uncut Spirit Gem (Level 20)", y1=386, y2=419)]
        invalid_heading = self.heading(y1=110, y2=126)
        earlier = self.recovered(y1=170, y2=200)
        result, panel, _ = self.scan(native=native, header=[invalid_heading, earlier])
        self.assertEqual(result["first_recipe"], "Uncut Skill Gem (Level 20)")
        self.assertEqual(len(result["opened_recipes"]), 2)
        self.assertEqual(panel.call_count, 1)

    def test_heading_in_padding_cannot_authorize_a_retry_of_a_later_native_row(self):
        later_raw = "1x ??? (Level 20)"
        native = [self.row("1x Thaumaturgic Flux (Level 20)"),
                  self.row(later_raw, y1=386, y2=419)]
        padded_heading = self.heading(y1=300, y2=310)
        retry = self.recovered("1x Uncut Skill Gem (Level 20)", y1=390, y2=419)
        result, panel, _ = self.scan(native=native, header=[padded_heading], retry=[retry])
        self.assert_held(result, later_raw)
        self.assertEqual(panel.call_count, 1)

    def test_heading_outside_panel_width_cannot_authorize_a_retry(self):
        for changes in ({"x1": -1}, {"x2": 576}, {"x1": 396, "x2": 395}):
            with self.subTest(box_changes=changes):
                result, panel, _ = self.scan(header=[{**self.heading(), **changes}])
                self.assert_held(result)
                self.assertEqual(panel.call_count, 1)

    def test_explicit_heading_bypass_does_not_run_recovery(self):
        result, panel, _ = self.scan(verify_header=False)
        self.assert_held(result)
        panel.assert_not_called()

    def test_no_full_fallback_still_recovers_a_bounded_verified_native_top(self):
        result, panel, _ = self.scan(allow_fallback=False)
        self.assertEqual(result["first_recipe"], "Thaumaturgic Flux (Level 20)")
        self.assertEqual(panel.call_count, 2)
        self.assertEqual(panel.call_args.args[1:], (278, 575, 370))

    def test_no_fallback_with_no_native_reward_does_not_run_any_rapid_ocr(self):
        for native in ([], [self.row("Unrelated caption")]):
            with self.subTest(native=native):
                result, panel, _ = self.scan(native=native, allow_fallback=False)
                self.assertIsNone(result["first_recipe"])
                self.assertEqual(result["opened_recipes"], [])
                panel.assert_not_called()

    def test_known_native_top_needs_no_retry_or_duplicate(self):
        native = self.native("1x Thaumaturgic Flux (Level 20)", score=.99)
        result, panel, _ = self.scan(native=native, header=[self.heading(), self.recovered()])
        self.assertEqual(result["first_recipe"], "Thaumaturgic Flux (Level 20)")
        self.assertEqual(len(result["opened_recipes"]), 2)
        self.assertEqual(panel.call_count, 1)

    def missing_top(self):
        native = [self.row("Skill Level 20: Verisium Manifestations", y1=386, y2=419),
                  self.row("Skill Level 20: Powered by Verisium", y1=467, y2=500)]
        heading = self.heading(y1=231, y2=259)
        earlier = self.recovered("1x Thaumaturgic Flux (Level 19)", y1=308, y2=338, score=.9989)
        return native, heading, earlier

    def test_missing_top_reuses_the_heading_pass_without_comparing_next_levels(self):
        native, heading, earlier = self.missing_top()
        result, panel, icons = self.scan(native=native, header=[heading, earlier], allow_fallback=False)
        self.assertEqual(result["first_recipe"], "Thaumaturgic Flux (Level 19)")
        self.assertEqual(result["next_recipe"], "Verisium Manifestations (Level 20)")
        self.assertEqual(result["first_line_gap"], 49)
        self.assertEqual(panel.call_count, 1)
        self.assertEqual(icons.call_args.args[1], 308)
        # A recovered name must still respect a conflicting visible socket count.
        self.assertFalse(result["can_use"])
        self.assertFalse(auto_commit.candidate(result)["ready"])

    def test_missing_top_holds_weak_multiple_and_unmatched_header_readings(self):
        native, heading, earlier = self.missing_top()
        cases = {
            "weak": [{**earlier, "score": .79}],
            "multiple": [earlier, {**earlier, "y1": 342, "y2": 368}],
            "unknown competitor": [earlier, {**earlier, "text": "1x Completely Unreadable", "y1": 342, "y2": 368}],
            "unknown": [{**earlier, "text": "1x Completely Unreadable"}],
        }
        for label, rows in cases.items():
            with self.subTest(case=label):
                result, panel, _ = self.scan(native=native, header=[heading, *rows])
                self.assertEqual(result["first_recipe"], "Verisium Manifestations (Level 20)")
                self.assertEqual(result["first_line_gap"], 127)
                self.assertEqual(panel.call_count, 1)
                self.assertFalse(auto_commit.candidate(result)["ready"])

    def test_missing_top_rejects_header_readings_outside_reward_and_crop_bounds(self):
        native, heading, earlier = self.missing_top()
        for changes in ({"y1": 274}, {"y2": 386}, {"y2": 379}, {"x2": 576},
                        {"x1": -1}, {"x1": 540, "x2": 539}):
            with self.subTest(box_changes=changes):
                result, panel, _ = self.scan(native=native, header=[heading, {**earlier, **changes}])
                self.assertEqual(result["first_recipe"], "Verisium Manifestations (Level 20)")
                self.assertEqual(panel.call_count, 1)
                self.assertFalse(auto_commit.candidate(result)["ready"])

    def test_injected_ocr_rows_do_not_invoke_native_or_recovery_ocr(self):
        source_rows = [self.heading(), *self.native()]
        injected = [{"text": row["text"], "score": row["score"], "x": row["x1"],
                     "right": row["x2"], "y": row["y1"], "bottom": row["y2"]}
                    for row in source_rows]
        original = copy.deepcopy(injected)
        with patch.object(opened_scan.runehelper_ocr, "recognize") as native, \
                patch.object(opened_scan, "_panel_text") as panel, \
                patch.object(opened_scan, "_engine") as engine, \
                patch.object(opened_scan, "_icon_count", return_value=6), \
                patch.object(opened_scan, "_list_complete", return_value=False):
            result = opened_scan.scan_opened(self.image, ocr_rows=injected)
        native.assert_not_called()
        panel.assert_not_called()
        engine.assert_not_called()
        self.assertEqual(injected, original)
        self.assert_held(result)
        self.assertEqual(self.activity(), self.activity_before)


if __name__ == "__main__":
    unittest.main()
