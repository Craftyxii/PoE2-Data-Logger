import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

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
