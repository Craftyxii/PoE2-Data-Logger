"""Recognition contract checks for model/recipe resolution and review holds on incomplete or conflicting evidence."""

import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import auto_commit, logger_store as logger, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, opened_scan
from PoE2_Data_Logger.ui.native_desktop import clear_ritual_read


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = store.DATA_DIR
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()

    def tearDown(self):
        store.DATA_DIR = self.previous
        logger._READY = False
        self.tmp.cleanup()

    def row(self, text, x=30, y=40, score=.99, right=None):
        return {"text": text, "score": score, "x": x, "y": y,
                "right": right if right is not None else x + 220, "bottom": y + 20}

    def test_clear_ritual_prices_still_allow_automatic_approval(self):
        name = "Omen of Whittling"
        result = item_ocr.parse_ritual([self.row(name), self.row("4,000", y=70, right=110)], [name])
        self.assertEqual(result["items"][0]["tribute"], 4000)
        self.assertTrue(clear_ritual_read(result, [name]))
        inline = item_ocr.parse_ritual([self.row(name + " 4,000")], [name])
        self.assertTrue(clear_ritual_read(inline, [name]))

    def test_uncertain_price_is_reviewed_without_discarding_reading(self):
        name = "Omen of Whittling"
        result = item_ocr.parse_ritual([self.row(name), self.row("4,000", y=70, score=.1, right=110)], [name])
        self.assertEqual(result["items"][0]["tribute"], 4000)
        self.assertFalse(clear_ritual_read(result, [name]))

    def test_ritual_available_tribute_header_does_not_block_clear_rewards(self):
        name = "Omen of Whittling"
        for header in ("5,430 Tribute", "2 5,430 Tribute"):
            with self.subTest(header=header):
                result = item_ocr.parse_ritual([
                    self.row("Favours", y=10), self.row("2", y=35),
                    self.row(header, y=40), self.row(name + " 4,000", y=160)], [name])
                self.assertEqual(result["unmatched"], [])
                self.assertEqual(result["items"][0]["tribute"], 4000)
                self.assertTrue(clear_ritual_read(result, [name]))

    def test_ritual_reward_tribute_price_remains_attached_to_its_reward(self):
        name = "Omen of Whittling"
        result = item_ocr.parse_ritual([
            self.row("Favours", y=10), self.row(name, y=80),
            self.row("4,000 Tribute", y=110, right=110)], [name])
        self.assertEqual(result["items"][0]["tribute"], 4000)
        self.assertTrue(clear_ritual_read(result, [name]))

    def test_ritual_unknown_reward_still_blocks_automatic_approval(self):
        name = "Omen of Whittling"
        result = item_ocr.parse_ritual([
            self.row("Favours", y=10), self.row("5,430 Tribute", y=40),
            self.row(name + " 4,000", y=160), self.row("Unreadable ??? reward", y=210)], [name])
        self.assertEqual(result["unmatched"], ["Unreadable ??? reward"])
        self.assertFalse(clear_ritual_read(result, [name]))

    def test_ritual_ambiguous_or_weak_header_is_not_silently_discarded(self):
        name = "Omen of Whittling"
        for rows in ([self.row("Favours", y=10), self.row("5,430 Tribute", y=40, score=.6)],
                     [self.row("Favours", y=10), self.row("5,430 Tribute", y=40, score=.85)],
                     [self.row("Favours", y=10), self.row("Unreadable ??? reward", y=30),
                      self.row("5,430 Tribute", y=40)],
                     [self.row("5,430 Tribute", y=40)]):
            with self.subTest(rows=rows):
                result = item_ocr.parse_ritual([*rows, self.row(name + " 4,000", y=160)], [name])
                self.assertIn("5,430 Tribute", result["unmatched"])
                self.assertFalse(clear_ritual_read(result, [name]))

    def test_prices_stay_in_their_own_column(self):
        names = ["Omen of Whittling", "Omen of Sinistral Erasure"]
        result = item_ocr.parse_ritual([self.row(names[0]), self.row(names[1], x=430, y=42),
                                      self.row("4,000", y=70, right=110)], names)
        self.assertEqual([(row["name"], row["tribute"]) for row in result["items"]],
                         [(names[0], 4000), (names[1], None)])

    def test_deferred_marker_attaches_to_recognized_named_omen(self):
        name = "Omen of Whittling"
        rng = np.random.default_rng(7)
        icon = Image.fromarray(rng.integers(40, 230, (40, 40, 3), dtype=np.uint8))
        raw = io.BytesIO()
        icon.save(raw, format="PNG")
        page = Image.new("RGB", (300, 200), (26, 26, 40))
        page.paste(icon, (100, 80))
        with patch.object(item_ocr, "ocr_lines", return_value=[self.row(name + " 4,000")]), patch.object(
                item_ocr, "ritual_totals", return_value={}), patch.object(
                item_ocr, "deferred_markers", return_value=[{"x": 110, "y": 90, "score": .99}]), patch.object(
                currency_ocr, "omen_references", return_value=()):
            result = item_ocr.scan_ritual_page(page, [name], [{"name": name, "image": raw.getvalue()}])
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["deferred"])
        self.assertTrue(clear_ritual_read(result, [name]))
        logger.start_map()
        logger.save_ritual_page(result["items"])
        self.assertIn(b",0,", logger.export_all_csv())

    def partial(self):
        first, second = "Perfect Chaos Orb x3", "Perfect Exalted Orb x3"
        with logger._connect() as db:
            sockets = db.execute("SELECT sockets FROM recipes WHERE name=?", (first,)).fetchone()[0]
        return {"status": "Review the opened rewards before logging.", "can_use": True,
                "family": "Family 3", "candidates": [3], "sockets": sockets,
                "recipe_sockets": sockets, "socket_source": "opened icons", "first_line_gap": 60,
                "list_complete": False, "first_recipe": first, "next_recipe": second,
                "opened_recipes": [{"recipe": name, "ocr_score": .99, "match_score": 1}
                                   for name in (first, second)]}

    def test_partial_family_list_can_auto_commit_all_inferred_rewards(self):
        opened = self.partial()
        with logger._connect() as db:
            family, candidates, complete = opened_scan._families(db, opened["opened_recipes"], False)
            expected = json.loads(db.execute("SELECT recipes_json FROM families WHERE id=3").fetchone()[0])
        self.assertEqual((family, candidates, complete), (3, [3], True))
        self.assertTrue(auto_commit.candidate(opened)["ready"])
        logger.save_settings({"auto_commit": True})
        opened.update(logger.assign_ocr_id("opened"))
        saved = auto_commit.commit(opened)
        self.assertTrue(saved["committed"])
        self.assertEqual(saved["recipes"], len(expected))
        with logger._connect() as db:
            logged = json.loads(db.execute("SELECT details_json FROM commits WHERE kind='Remnant'").fetchone()[0])
        self.assertEqual([row["recipe"] for row in logged["recipes"]], expected)

    def fresh_auto_capture(self):
        logger.clear_export_and_reset_ids()
        logger.start_map()
        logger.save_settings({"auto_commit": True})
        return {**self.partial(), **logger.scan_context(), **logger.assign_ocr_id("opened")}

    def auto_commit_records(self):
        with logger._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in ("meta", "maps", "expeditions", "new_export", "commits")}

    def test_old_auto_commit_cannot_consume_reused_ids_after_session_reset(self):
        old = self.fresh_auto_capture()
        current = self.fresh_auto_capture()
        self.assertTrue(all(old[key] == current[key] for key in
                            ("remnant_id", "map_id", "expedition_id")))
        self.assertNotEqual(old["_scan_generation"], current["_scan_generation"])
        before = self.auto_commit_records()
        saved = auto_commit.commit(old)
        self.assertFalse(saved["committed"])
        self.assertIn("previous session", saved["reason"])
        self.assertEqual(self.auto_commit_records(), before)
        self.assertEqual(logger.get_state()["ocr_pending"]["remnant_id"], current["remnant_id"])

    def test_reset_during_auto_commit_resolution_is_rechecked_inside_commit_transaction(self):
        old = self.fresh_auto_capture()
        original_candidate = auto_commit.candidate
        after_reset = {}
        def reset_after_resolution(opened, seed=None):
            result = original_candidate(opened, seed)
            self.assertTrue(result["ready"])
            current = self.fresh_auto_capture()
            self.assertTrue(all(old[key] == current[key] for key in
                                ("remnant_id", "map_id", "expedition_id")))
            after_reset["records"] = self.auto_commit_records()
            return result
        with patch.object(auto_commit, "candidate", side_effect=reset_after_resolution):
            saved = auto_commit.commit(old)
        self.assertFalse(saved["committed"])
        self.assertIn("previous session", saved["reason"])
        self.assertEqual(self.auto_commit_records(), after_reset["records"])

    def test_old_seed_context_cannot_confirm_fresh_opened_capture_with_reused_ids(self):
        old_seed = self.fresh_auto_capture()
        current = self.fresh_auto_capture()
        before = self.auto_commit_records()
        saved = auto_commit.commit(current, old_seed)
        self.assertFalse(saved["committed"])
        self.assertIn("previous session", saved["reason"])
        self.assertEqual(self.auto_commit_records(), before)

    def test_current_session_auto_commit_preserves_original_chain_after_propagation_advance(self):
        opened = self.fresh_auto_capture()
        logger.increment_propagation_detonated(logger.scan_context(), runes=["Death", "Rebirth"])
        logger.commit_chain_draft([{"rune1": "Death", "rune2": "Rebirth"}], logger.scan_context())
        logger.complete_chain(logger.scan_context())
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        saved = auto_commit.commit(opened)
        self.assertTrue(saved["committed"])
        self.assertEqual((saved["remnant_id"], saved["expedition_id"]), ("R0001", "M0001-E01"))
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertIsNone(logger.get_state()["ocr_pending"])

    def test_partial_family_ambiguity_and_socket_conflict_require_review(self):
        opened = self.partial()
        opened["candidates"] = [3, 5]
        self.assertFalse(auto_commit.candidate(opened)["ready"])
        opened = self.partial()
        opened["sockets"] -= 1
        self.assertFalse(auto_commit.candidate(opened)["ready"])

    def test_fallback_header_confidence_matches_native_header_policy(self):
        rows = [self.row("Runeshape Combinations", x=0, y=95, right=500),
                self.row("3x Perfect Chaos Orb", x=20, y=175, right=350),
                self.row("3x Perfect Exalted Orb", x=20, y=220, right=350)]
        image = Image.new("RGB", (600, 500), (190, 190, 190))
        with patch.object(opened_scan, "_icon_count", return_value=self.partial()["sockets"]), patch.object(
                opened_scan, "_list_complete", return_value=False):
            good = opened_scan.scan_opened(image, ocr_rows=rows)
            rows[0]["score"] = .1
            bad = opened_scan.scan_opened(image, ocr_rows=rows)
        self.assertTrue(auto_commit.candidate(good)["ready"], good)
        self.assertFalse(auto_commit.candidate(bad)["ready"])
        self.assertFalse(bad["can_use"] if "can_use" in bad else False)

    def test_opened_explicit_gem_level_cannot_be_fuzzily_changed_or_inferred(self):
        with logger._connect() as db:
            names = [row[0] for row in db.execute("SELECT name FROM recipes")]
            for kind in ("Spirit", "Skill"):
                with self.subTest(kind=kind):
                    matched, _ = opened_scan._match(db, f"Uncut {kind} Gem (Level 18)", 1, names)
                    self.assertIsNone(matched)
                expected = f"Uncut {kind} Gem (Level 19)"
                self.assertEqual(opened_scan._match(db, expected, 1, names), (expected, 1.0))
                self.assertIsNone(opened_scan._match(db, f"Uncut {kind} Gern", 1, [expected])[0])
                self.assertEqual(opened_scan._match(db, f"Uncut {kind} Gem", 1, names)[0], f"Uncut {kind} Gem")
                # Typo correction may preserve a clearly read level, but must not change it.
                self.assertEqual(opened_scan._match(db, f"Uncut {kind} Gern (Level 19)", 1, names)[0], expected)

    def test_opened_wrong_gem_level_cannot_automatically_commit_level_19_family(self):
        image = Image.new("RGB", (600, 500), (190, 190, 190))
        rows = [self.row("Runeshape Combinations", x=0, y=80, right=500),
                self.row("1x Uncut Spirit Gem (Level 18)", x=160, y=160, right=490)]
        with patch.object(opened_scan, "_icon_count", return_value=5), patch.object(
                opened_scan, "_list_complete", return_value=True):
            result = opened_scan.scan_opened(image, ocr_rows=rows)
        self.assertEqual(result["opened_recipes"][0]["raw"], "1x Uncut Spirit Gem (Level 18)")
        self.assertIsNone(result["first_recipe"])
        self.assertIsNone(result["family"])
        self.assertFalse(result["can_use"])
        self.assertFalse(auto_commit.candidate(result)["ready"])

    def native_opened_rows(self):
        return [{"text": "Unrelated caption", "score": .99,
                 "x1": 20, "y1": 115, "x2": 200, "y2": 133},
                {"text": "3x Perfect Chaos Orb", "score": .99,
                 "x1": 200, "y1": 175, "x2": 400, "y2": 195},
                {"text": "3x Perfect Exalted Orb", "score": .99,
                 "x1": 200, "y1": 245, "x2": 400, "y2": 265}]

    def native_opened(self, header, verify_header=True):
        image = Image.new("RGB", (600, 500), (190, 190, 190))
        with patch.object(opened_scan.runehelper_ocr, "recognize",
                          return_value=self.native_opened_rows()), patch.object(
                opened_scan, "_engine", return_value=lambda unused: header), patch.object(
                opened_scan, "_icon_count", return_value=self.partial()["sockets"]), patch.object(
                opened_scan, "_list_complete", return_value=False):
            return opened_scan.scan_opened(image, allow_fallback=False,
                                            verify_header=verify_header)

    def test_native_missing_heading_preserves_recipes_but_cannot_auto_commit(self):
        result = self.native_opened(SimpleNamespace(boxes=None))
        self.assertEqual(result["first_recipe"], "Perfect Chaos Orb x3")
        self.assertEqual(result["next_recipe"], "Perfect Exalted Orb x3")
        self.assertEqual(result["family"], "Family 3")
        self.assertFalse(result["header_verified"])
        self.assertIsNone(result["first_line_gap"])
        self.assertFalse(result["can_use"])
        self.assertFalse(auto_commit.candidate(result)["ready"])
        self.assertIn("heading not found", result["status"])

    def test_native_low_confidence_or_wrong_heading_requires_review(self):
        box = [[170, 90], [450, 90], [450, 105], [170, 105]]
        for text, confidence in (("Runeshape Combinations", .1), ("Other heading", .99)):
            with self.subTest(text=text, confidence=confidence):
                result = self.native_opened(SimpleNamespace(boxes=[box], txts=[text],
                                                            scores=[confidence]))
                self.assertFalse(result["header_verified"])
                self.assertFalse(result["can_use"])
                self.assertFalse(auto_commit.candidate(result)["ready"])

    def test_native_verified_heading_still_allows_automatic_approval(self):
        result = self.native_opened(SimpleNamespace(
            boxes=[[[170, 90], [450, 90], [450, 105], [170, 105]]],
            txts=["Runeshape Combinations"], scores=[.99]))
        self.assertTrue(result["header_verified"])
        self.assertEqual(result["first_line_gap"], 70)
        self.assertTrue(result["can_use"])
        self.assertTrue(auto_commit.candidate(result)["ready"])

    def test_explicit_heading_bypass_preserves_propagation_recipe_context(self):
        result = self.native_opened(SimpleNamespace(boxes=None), verify_header=False)
        self.assertFalse(result["header_verified"])
        self.assertEqual(result["first_recipe"], "Perfect Chaos Orb x3")
        self.assertEqual(result["family"], "Family 3")
        self.assertTrue(result["can_use"])

    def test_rank_cache_preserves_results_and_count_mask(self):
        reader = currency_ocr.CurrencyReader()
        entry = next(row for row in json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
                     if "Chaos Orb" in row["members"])
        art = Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4), "RGBA")
        cell = Image.new("RGBA", art.size, (26, 26, 40, 255))
        cell.alpha_composite(art)
        first = reader.inventory_ranked(cell, calibrated=True, count_digits=1)
        with patch.object(currency_ocr.cv2, "matchTemplate", side_effect=AssertionError("matching was repeated")):
            self.assertEqual(reader.inventory_ranked(cell, calibrated=True, count_digits=1), first)
        second = reader.inventory_ranked(cell, calibrated=True, count_digits=2)
        reader._rank_cache.clear()
        self.assertEqual(reader.inventory_ranked(cell, calibrated=True, count_digits=2), second)
        self.assertEqual(reader.inventory_ranked(cell, calibrated=True, count_digits=1), first)

    def test_reader_reuse_is_confined_to_each_worker_thread(self):
        first = currency_ocr.get_reader()
        self.assertIs(currency_ocr.get_reader(), first)
        others = []
        worker = threading.Thread(target=lambda: others.append((currency_ocr.get_reader(), currency_ocr.get_reader())))
        worker.start()
        worker.join()
        self.assertIs(others[0][0], others[0][1])
        self.assertIsNot(others[0][0], first)


if __name__ == "__main__":
    unittest.main()
