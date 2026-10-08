"""Native/Python icon scoring comparisons with QuickJS and tests for ranking, alignment and score-margin boundaries."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import logger_store as logger, reference_pack, store
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr


class CurrencyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.reader = currency_ocr.CurrencyReader()

    def oracle(self, cell, references):
        def pixels(image):
            return np.asarray(image.convert("RGB").resize(
                (40, 40), Image.Resampling.LANCZOS), dtype=np.uint8).reshape(-1).tolist()
        payload = {"cell": pixels(cell), "refs": [
            {"name": entry["name"], "rgb": pixels(entry["image"])} for entry in references]}
        return json.loads(self.reader.match_examples(json.dumps(payload, separators=(",", ":"))))

    def test_native_custom_scores_match_real_quickjs_with_shifts_and_foreground_weights(self):
        rng = np.random.default_rng(17)
        for background in (False, True):
            with self.subTest(background=background):
                pixels = rng.integers(45, 210, (40, 40, 3), dtype=np.uint8)
                if background:
                    pixels[:15] = [32, 34, 38]
                    pixels[25:] = [26, 26, 40]
                cell = Image.fromarray(pixels)
                references = [{"name": "Winner", "image": cell},
                              {"name": "Shifted", "image": Image.fromarray(np.roll(pixels, 2, axis=1))},
                              {"name": "Other", "image": Image.fromarray(rng.integers(
                                  45, 210, (60, 48, 3), dtype=np.uint8))}]
                native, oracle = self.reader.examples(cell, references), self.oracle(cell, references)
                self.assertEqual([entry["name"] for entry in native], [entry["name"] for entry in oracle])
                for actual, expected in zip(native, oracle):
                    self.assertAlmostEqual(actual["score"], expected["score"], places=6)

    def test_same_name_variants_keep_a_close_competing_name_visible(self):
        cell = Image.fromarray(np.random.default_rng(51).integers(0, 256, (40, 40, 3), dtype=np.uint8))
        references = [{"name": "Chaos Orb", "image": cell} for _ in range(3)]
        references.append({"name": "Exalted Orb", "image": cell})
        for ranked in (self.reader.examples(cell, references), self.oracle(cell, references)):
            self.assertEqual([entry["name"] for entry in ranked], ["Chaos Orb", "Exalted Orb"])
            self.assertEqual([entry["score"] for entry in ranked], [0, 0])
            self.assertFalse(ranked[0]["score"] - ranked[1]["score"] > 150)

    def test_global_ranking_preserves_references_across_atlas_batches(self):
        rng = np.random.default_rng(22)
        pixels = rng.integers(50, 180, (40, 40, 3), dtype=np.uint8)
        cell = Image.fromarray(pixels)
        references = [{"name": f"Other {index}", "image": Image.fromarray(rng.integers(
            0, 256, (40, 40, 3), dtype=np.uint8))} for index in range(130)]
        references.extend([{"name": "Last winner", "image": cell},
                           {"name": "Close competitor", "image": Image.fromarray(pixels + 1)},
                           {"name": "Other 0", "image": Image.fromarray(pixels + 2)}])
        ranked = self.reader.examples(cell, references)
        self.assertEqual([entry["name"] for entry in ranked], ["Last winner", "Close competitor", "Other 0"])
        self.assertEqual([entry["score"] for entry in ranked], [0, -3, -12])
        self.assertGreaterEqual(len(self.reader._example_atlases), 2)

    def test_exact_refinement_preserves_150_margin_and_1200_score_boundaries(self):
        pixels = np.random.default_rng(10).integers(50, 180, (40, 40, 3), dtype=np.uint8)
        y, x = np.indices((40, 40))
        positions = np.flatnonzero(np.repeat(~((y < 13) & (x < 13)), 3).reshape(-1))
        runner = pixels.astype(np.int16) + 7
        runner.reshape(-1)[positions[:286]] += 1
        cell = Image.fromarray(pixels)
        references = [{"name": "Winner", "image": cell},
                      {"name": "Runner", "image": Image.fromarray(runner.astype(np.uint8))}]
        native, oracle = self.reader.examples(cell, references), self.oracle(cell, references)
        self.assertEqual(native, oracle)
        self.assertFalse(native[0]["score"] - native[1]["score"] > 150)
        for adjustment, accepted in ((-1, True), (1, False)):
            candidate = pixels.astype(np.int16) + 20
            candidate.reshape(-1)[positions[0]] += adjustment
            references = [{"name": "Boundary", "image": Image.fromarray(candidate.astype(np.uint8))}]
            native, oracle = self.reader.examples(cell, references), self.oracle(cell, references)
            self.assertEqual(native, oracle)
            self.assertEqual(native[0]["score"] > -1200, accepted)

    def test_prepared_pixels_and_names_are_snapshots_and_invalidate_rank_cache(self):
        source = Image.fromarray(np.random.default_rng(33).integers(0, 256, (40, 40, 3), dtype=np.uint8))
        cell = source.copy()
        bank = self.reader.prepare_examples([{"name": "Original", "image": source}])
        initial = self.reader.examples(cell, bank)
        source.paste((90, 100, 110), (0, 0, 40, 40))
        self.assertEqual(self.reader.examples(cell, bank), initial)
        renamed = self.reader.prepare_examples([{"name": "Renamed", "image": cell}])
        changed = self.reader.prepare_examples([{"name": "Original", "image": source}])
        self.assertNotEqual(renamed.signature, bank.signature)
        self.assertNotEqual(changed.signature, bank.signature)
        self.assertEqual(self.reader.examples(cell, renamed)[0]["name"], "Renamed")
        self.assertLess(self.reader.examples(cell, changed)[0]["score"], -1200)
        initial[0]["name"] = "Mutated caller result"
        self.assertEqual(self.reader.examples(cell, bank)[0]["name"], "Original")

    def test_prepared_bank_and_reader_stay_with_their_worker(self):
        image = Image.new("RGB", (40, 40), (100, 120, 140))
        bank = self.reader.prepare_examples([{"name": "Example", "image": image}])
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(ValueError, "different reader"):
                pool.submit(lambda: currency_ocr.get_reader().examples(image, bank)).result()
            with self.assertRaisesRegex(ValueError, "worker thread"):
                pool.submit(lambda: self.reader.examples(image, bank)).result()

    def test_prepared_atlas_and_rank_caches_remain_bounded(self):
        pixels = np.random.default_rng(55).integers(0, 256, (40, 40, 3), dtype=np.uint8)
        for index in range(160):
            candidate = pixels.copy()
            candidate[30, 30] = [index, 230 - index, 90]
            image = Image.fromarray(candidate)
            self.reader.examples(image, [{"name": "Example", "image": image}])
        self.assertEqual(len(self.reader._example_atlases), 8)
        self.assertEqual(len(self.reader._example_rank_cache), 128)
        self.assertTrue(all(atlas.nbytes <= 128 * 48 * 48 * 3 * 4
                            for atlas in self.reader._example_atlases.values()))
        self.assertTrue(all(len(key[0]) == 32 and len(values) <= 3
                            for key, values in self.reader._example_rank_cache.items()))

    def test_1999_imported_canonical_icons_scan_without_large_quickjs_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = store.DATA_DIR
            store.DATA_DIR = Path(temporary)
            logger._READY = False
            try:
                logger.initialize()
                data = {key: [] for key in (
                    "families", "recipes", "aliases", "seed_states", "affixes", "master_perks",
                    "currency_names", "omen_names", "item_names", "glyphs", "icons", "scans")}
                data["item_names"] = [{"name": "Last custom item"}]
                rng = np.random.default_rng(79)
                path = Path(temporary) / "many-references.zip"
                with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
                    for index in range(1999):
                        pixels = rng.integers(45, 210, (96, 96, 3), dtype=np.uint8)
                        image = Image.fromarray(pixels)
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                        raw = output.getvalue()
                        file = f"icons/{index:04d}.png"
                        last = index == 1998
                        data["icons"].append({"name": "Last custom item" if last else "Chaos Orb",
                                              "kind": "item" if last else "currency", "file": file,
                                              "sha256": hashlib.sha256(raw).hexdigest()})
                        archive.writestr(file, raw)
                        if last:
                            cell = image.resize((40, 40), Image.Resampling.LANCZOS)
                    archive.writestr("manifest.json", json.dumps({
                        "format": reference_pack.FORMAT, "version": 2, "data": data}))
                counts = reference_pack.import_pack(path)
                self.assertEqual((counts["currency_icons"], counts["item_icons"]), (1998, 1))
                grid = Image.new("RGB", (480, 200), (26, 26, 40))
                grid.paste(cell, (0, 0))
                with patch.object(currency_ocr, "get_reader", return_value=self.reader), patch.object(
                        self.reader, "icon", return_value={"family": None, "score": 0, "all": []}), patch.object(
                        item_ocr, "inventory_grid", side_effect=lambda image: image), patch.object(
                        self.reader, "match_examples", side_effect=AssertionError("Large QuickJS reference payload")):
                    result = item_ocr.scan_inventory_grid(grid, logger.inventory_icons(), read=lambda image: [])
                self.assertEqual([entry["name"] for entry in result["items"]], ["Last custom item"])
                self.assertLessEqual(len(self.reader._example_atlases), 8)
                self.assertLessEqual(sum(atlas.nbytes for atlas in self.reader._example_atlases.values()),
                                     8 * 128 * 48 * 48 * 3 * 4)
                self.assertTrue(self.reader.context.eval("1 + 1") == 2)
            finally:
                store.DATA_DIR = previous
                logger._READY = False


if __name__ == "__main__":
    unittest.main()
