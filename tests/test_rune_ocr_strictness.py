"""Focused checks for frozen rune strictness, tentative matches and unchanged safety guards."""

import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, ocr_sensitivity, service, store
from PoE2_Data_Logger.ocr import opened_scan, propagation_scan, runehelper_ocr, scan
from PoE2_Data_Logger.platform import hotkey


class RuneStrictnessTests(unittest.TestCase):
    """Exercise confidence boundaries with controlled OCR and isolated capture preferences."""

    def setUp(self):
        """Create a temporary logger store so preference changes cannot affect real user data."""
        self.temporary = tempfile.TemporaryDirectory(prefix="poe2-rune-strictness-")
        self.previous_data = store.DATA_DIR
        store.DATA_DIR = Path(self.temporary.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        """Restore the original store and delete the test's capture preferences."""
        store.DATA_DIR = self.previous_data
        logger._READY = False
        self.temporary.cleanup()

    def seed_reading(self, strictness=None, socket=.7, glyph=.62, book=.62, states=None):
        """Decode one known seed with controlled confidence while retaining real family guards."""
        states = states or [{"sockets": 3, "seed_slot": "P1", "seed_rune": "Arcane",
                             "family": 1, "status": "calculator", "rewards": ["Known reward"]}]
        image = Image.new("RGB", (500, 100))
        model = Mock()
        with patch.object(scan, "features", return_value=np.ones(1)), patch.object(
                scan, "decode", return_value=(np.log(socket) * 10, 2, 3)), patch.object(
                scan, "crop_at", return_value=image), patch.object(
                scan, "vector", return_value=np.ones(1)):
            options = {} if strictness is None else {"strictness": strictness}
            return scan._scan_bar(image, (100, 0, book), model, np.array(["Arcane"]),
                                  np.array([[glyph]]), states, **options)

    def opened_reading(self, strictness=50, ocr=.7, matched=.85):
        """Build one structurally valid opened row for confidence and resolver checks."""
        return {"status": "Review the opened rewards before logging.", "can_use": True,
                "family": "Family 1", "candidates": [1], "sockets": 3,
                "recipe_sockets": 3, "socket_source": "opened icons", "first_line_gap": 60,
                "first_recipe": "Known reward", "next_recipe": None,
                "opened_recipes": [{"recipe": "Known reward", "ocr_score": ocr,
                                     "match_score": matched}], "_ocr_strictness": strictness}

    @staticmethod
    def resolve(first, second, family):
        """Provide an exact known sequence without replacing any candidate safety checks."""
        return {"status": "ready", "family": 1,
                "rows": [{"recipe": "Known reward", "sockets": 3}]}

    def test_default_seed_and_native_rows_preserve_existing_behavior(self):
        """Verify omitted/default 50 is identical and lower strictness retains weaker OCR rows."""
        self.assertEqual(self.seed_reading(), self.seed_reading(strictness=50))
        image = Image.new("RGB", (200, 100))
        with patch.object(runehelper_ocr, "_gray_panel", return_value=(np.zeros((20, 200)), 1)), patch.object(
                runehelper_ocr, "_rows", return_value=[(0, 20)]), patch.object(
                runehelper_ocr, "_text_start", return_value=0), patch.object(
                runehelper_ocr, "_read_row", return_value=("1x Known reward", 75)):
            baseline = runehelper_ocr._recognize_rows(image)
            self.assertEqual(baseline, runehelper_ocr._recognize_rows(image, strictness=50))
            self.assertEqual(baseline, [])
            self.assertEqual(len(runehelper_ocr._recognize_rows(image, strictness=0)), 1)
            self.assertEqual(runehelper_ocr._recognize_rows(image, strictness=100), [])

    def test_tentative_seed_acceptance_keeps_family_and_maximum_strictness_guards(self):
        """Allow a weaker known seed at zero while holding ambiguous/inferred stages and maximum."""
        self.assertFalse(self.seed_reading()["can_commit"])
        self.assertTrue(self.seed_reading(strictness=0)["can_commit"])
        self.assertFalse(self.seed_reading(strictness=100, socket=1, glyph=1, book=1)["can_commit"])
        base = {"sockets": 3, "seed_slot": "P1", "seed_rune": "Arcane",
                "family": 1, "status": "calculator", "rewards": []}
        for states in ([base, {**base, "family": 2}], [{**base, "status": "inferred"}],
                       [base, {**base, "seed_slot": "P2", "family": 2}]):
            with self.subTest(states=states):
                self.assertFalse(self.seed_reading(strictness=0, states=states)["can_commit"])

    def test_low_strictness_opened_acceptance_keeps_structural_guards(self):
        """Accept tentative opened confidence only with unique families, counts and matching order."""
        low = self.opened_reading(strictness=0)
        self.assertTrue(auto_commit.candidate(low, resolver=self.resolve)["ready"])
        self.assertFalse(auto_commit.candidate(self.opened_reading(), resolver=self.resolve)["ready"])
        moderate = self.opened_reading(ocr=.85, matched=.97)
        self.assertTrue(auto_commit.candidate(moderate, resolver=self.resolve)["ready"])
        self.assertFalse(auto_commit.candidate({**moderate, "_ocr_strictness": 75}, resolver=self.resolve)["ready"])
        maximum = self.opened_reading(strictness=100, ocr=1, matched=1)
        self.assertFalse(auto_commit.candidate(maximum, resolver=self.resolve)["ready"])
        for edit in ({"candidates": [1, 2]}, {"sockets": 4}, {"can_use": False},
                     {"first_recipe": "Wrong reward"}, {"first_line_gap": 5}):
            with self.subTest(edit=edit):
                self.assertFalse(auto_commit.candidate({**low, **edit}, resolver=self.resolve)["ready"])
        seed = {"family": "Family 2", "candidates": [2], "sockets": 3}
        self.assertFalse(auto_commit.candidate(low, seed, resolver=self.resolve)["ready"])

    def test_opened_matching_still_preserves_quantity_level_and_ambiguity(self):
        """Broaden fuzzy spelling at zero without changing quantity, explicit level or tie handling."""
        with patch.object(logger, "_canonical", side_effect=ValueError):
            self.assertIsNone(opened_scan._match(None, "P Chaos Orb", 1, ["Perfect Chaos Orb"])[0])
            self.assertEqual(opened_scan._match(None, "P Chaos Orb", 1, ["Perfect Chaos Orb"],
                                                strictness=0)[0], "Perfect Chaos Orb")
            self.assertIsNone(opened_scan._match(None, "P Chaos Orb", 2, ["Perfect Chaos Orb"], strictness=0)[0])
            self.assertIsNone(opened_scan._match(None, "Uncut Skill Gem (Level 18)", 1,
                                                  ["Uncut Skill Gem (Level 19)"], strictness=0)[0])
            with patch.object(opened_scan, "SequenceMatcher") as matcher:
                matcher.return_value.ratio.return_value = .75
                self.assertIsNone(opened_scan._match(None, "P Chaos Orb", 1,
                                                      ["Perfect Chaos Orb", "Perfect Exalted Orb"], strictness=0)[0])

    def test_propagation_tentative_text_requires_complete_marks_and_positions(self):
        """Allow weaker text at zero only when crown count and the verified tile lattice agree."""
        db = Mock()
        db.execute.return_value.fetchone.return_value = {"sockets": 3, "combo": "Arcane + Rage + Time"}
        row = {"recipe": "Known reward", "text": "1x Known reward", "score": .75,
               "match_score": .85, "y1": 141}
        panel = Image.new("RGB", (575, 300))
        marked = [(71., 100, 38, 38)]
        args = (db, panel, marked, [], row, [row], [1], None, 15)
        with patch.object(propagation_scan, "_tile_layout", return_value=(71., 41.)):
            self.assertFalse(propagation_scan._row_reading(*args)["can_use"])
            low = propagation_scan._row_reading(*args, strictness=0)
            self.assertTrue(low["can_use"])
            self.assertEqual(low["runes"], ["Arcane"])
            partial_args = (db, panel, marked, marked, row, [row], [1], None, 15)
            self.assertFalse(propagation_scan._row_reading(*partial_args, strictness=0)["can_use"])
        with patch.object(propagation_scan, "_tile_layout", return_value=None):
            self.assertFalse(propagation_scan._row_reading(*args, strictness=0)["can_use"])

    def test_service_and_both_keep_seed_and_remnant_settings_separate(self):
        """Route independently frozen settings through uploaded and both-mode readers."""
        values = ocr_sensitivity.saved_values()
        values.update(seed=0, remnant=80)
        memory = io.BytesIO()
        Image.new("RGB", (200, 100)).save(memory, format="PNG")
        encoded = base64.b64encode(memory.getvalue()).decode("ascii")
        with patch.object(service, "scan_both", return_value={"mode": "seed", "remnants": []}) as reader:
            result = service.dispatch("/api/scan?mode=both", {"image": encoded, "ocr_strictness": values})
        self.assertEqual(reader.call_args.kwargs, {"strictness": 80, "seed_strictness": 0})
        self.assertEqual(result["_ocr_strictness"], 0)
        self.assertEqual(set(result["_ocr_strictness_values"]), {key for key, _ in ocr_sensitivity.SCAN_TYPES})
        with patch.object(opened_scan, "scan_opened", return_value={}), patch.object(
                scan, "scan", return_value={"remnants": [{"sockets": 3}]}) as seed_reader:
            opened_scan.scan_both(Path("unused"), strictness=80, seed_strictness=0)
        seed_reader.assert_called_once_with(Path("unused"), strictness=0)

    def test_queued_hotkey_freezes_preferences_and_keeps_injected_reader_contract(self):
        """Preserve request-time values across a delayed worker and one-argument custom reader."""
        values = ocr_sensitivity.saved_values()
        values["propagation"] = 0
        ocr_sensitivity.save_values(values)
        seen = []

        def read(image):
            """Accept exactly one image to detect accidental kwargs on injected readers."""
            seen.append(image)
            return {"runes": ["Arcane"], "positions": [1], "can_use": True}

        manager = hotkey.HotkeyManager(supported=False, readers={"propagation": read},
                                      grabber=Mock(return_value=Image.new("RGB", (575, 300))))
        with patch.object(hotkey.threading, "Thread") as worker:
            manager.capture("propagation", background=True)
            values["propagation"] = 100
            ocr_sensitivity.save_values(values)
            worker.call_args.kwargs["target"](*worker.call_args.kwargs["args"])
        self.assertEqual(len(seen), 1)
        result = manager.status()["latest"]["result"]
        self.assertEqual(result["_ocr_strictness_values"]["propagation"], 0)
        self.assertEqual(result["_ocr_strictness"], 0)

    def test_builtin_hotkey_reader_receives_frozen_strictness(self):
        """Forward nondefault strictness to the built-in propagation reader only."""
        values = ocr_sensitivity.saved_values()
        values["propagation"] = 0
        ocr_sensitivity.save_values(values)
        manager = hotkey.HotkeyManager(supported=False,
                                      grabber=Mock(return_value=Image.new("RGB", (575, 300))))
        with patch.object(propagation_scan, "scan_propagation", return_value={"runes": []}) as reader:
            manager.capture("propagation")
        self.assertEqual(reader.call_args.kwargs, {"strictness": 0})
        self.assertEqual(manager.status()["latest"]["error"], "")


if __name__ == "__main__":
    unittest.main()
