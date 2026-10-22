"""Actual gray Ritual art suggests complete source-backed names without learning identity."""

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from PoE2_Data_Logger.core.ritual_catalog import OMEN_NAMES
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr
from PoE2_Data_Logger.ocr.ritual_grid import detect_reward_grid


SHARED_NAMES = {
    "VoodooOmens1": ["Omen of Refreshment", "Omen of Sinistral Alchemy", "Omen of Whittling",
                     "Omen of Greater Annulment", "Omen of Sinistral Coronation", "Omen of Greater Exaltation"],
    "VoodooOmens2": ["Omen of Resurgence", "Omen of Dextral Alchemy", "Omen of Sinistral Erasure",
                     "Omen of Sinistral Annulment", "Omen of Dextral Coronation", "Omen of Sinistral Exaltation"],
    "VoodooOmens3": ["Omen of Amelioration", "Omen of Dextral Erasure", "Omen of Dextral Annulment",
                     "Omen of Corruption", "Omen of Dextral Exaltation"],
    "VoodooOmens4": ["Omen of Answered Prayers", "Omen of the Hunt", "Omen of Secret Compartments",
                     "Omen of Reinforcements"],
}
GRAY_SLOTS = {1: "VoodooOmens2", 2: "VoodooOmens2", 13: "VoodooOmens1", 25: "VoodooOmens2",
              37: "VoodooOmens2", 49: "VoodooOmens3", 61: "VoodooOmens2", 109: None}
EXPECTED_FOOTPRINTS = [[1], [2], [13], [14], [25], [37], [49], [61],
                       [73, 74, 85, 86], [97], [109]]
FIXTURES = Path(__file__).parent / "fixtures"


def reward_cells(image, grid):
    """Crop the actual occupied cells with the scanner's existing frame inset."""
    inset = max(1, round(grid["pitch"] * .02))
    return {reward["slots"][0]: image.crop((reward["box"][0] + inset, reward["box"][1] + inset,
                                          reward["box"][2], reward["box"][3]))
            for reward in grid["rewards"] if len(reward["slots"]) == 1}


def armor_fragment_cells(image, grid):
    """Retain real armor subcell pixels as Omen negatives, not separate reward truth."""
    left, top, _, _ = grid["bounds"]
    pitch = grid["pitch"]
    inset = max(1, round(pitch * .02))
    cells = {}
    for slot in (73, 74, 85, 86):
        row, column = divmod(slot - 1, 12)
        cells[slot] = image.crop((round(left + column * pitch) + inset,
                                 round(top + row * pitch) + inset,
                                 round(left + (column + 1) * pitch),
                                 round(top + (row + 1) * pitch)))
    return cells


class RitualGrayscaleOmenTests(unittest.TestCase):
    """Verify native captured pixels, independent text, learned references and worker caches."""

    @classmethod
    def setUpClass(cls):
        """Read the unaltered supplied capture and its real grid, marker and count evidence."""
        with Image.open(FIXTURES / "ritual_grayscale/264_tribute.png") as source:
            cls.image = source.convert("RGB")
        cls.grid = detect_reward_grid(cls.image)
        cls.cells = reward_cells(cls.image, cls.grid)
        cls.armor_fragments = armor_fragment_cells(cls.image, cls.grid)
        cls.markers = item_ocr.deferred_markers(cls.image, grid=cls.grid)
        by_index = {index: cls.cells[reward["slots"][0]] for index, reward in enumerate(cls.grid["rewards"])
                    if len(reward["slots"]) == 1}
        cls.labels = item_ocr._ritual_cell_labels(by_index)

    def grid_items(self, references=(), parsed=(), strictness=50):
        """Retain real cell/marker evidence while reusing already read count labels."""
        with patch.object(item_ocr, "_ritual_cell_labels", return_value=self.labels):
            items = item_ocr._ritual_grid_items(self.image, self.grid, parsed, self.markers,
                                               OMEN_NAMES, references, strictness=strictness)
        return {item["grid_slots"][0]: item for item in items}

    def assert_gray_candidates(self, items):
        """Check complete candidates, blank identities and unchanged deferred ownership."""
        for slot, shape in GRAY_SLOTS.items():
            with self.subTest(slot=slot):
                item = items[slot]
                self.assertEqual(item["candidate_names"], SHARED_NAMES[shape] if shape else ["Omen of the Ancients"])
                self.assertEqual((item["name"], item["category"]), ("", "Omen"))
                self.assertTrue(item["unresolved"] and item["name_needs_review"] and item["needs_review"])
                self.assertTrue(item["category_verified"])
                self.assertEqual(item["deferred"], slot in (1, 13, 25))
                self.assertIsNone(item["tribute"])

    def test_actual_page_at_native_and_double_scale_keeps_gray_names_manual(self):
        """Actual gray badge-free and badged shapes retain their supported siblings at both scales."""
        for scale in (1, 2):
            with self.subTest(scale=scale):
                image = self.image if scale == 1 else self.image.resize(
                    (self.image.width * scale, self.image.height * scale), Image.Resampling.LANCZOS)
                result = item_ocr.scan_ritual_page(image, OMEN_NAMES)
                self.assertEqual([item["grid_slots"] for item in result["items"]],
                                 EXPECTED_FOOTPRINTS)
                self.assertEqual((result["tribute_available"], result["rerolls_remaining"],
                                  result["deferred_count"]), (264, 13, 3))
                items = {item["grid_slots"][0]: item for item in result["items"]}
                self.assert_gray_candidates(items)
                self.assertEqual(items[14]["name"], "Omen of Greater Exaltation")
                self.assertFalse(items[14]["name_needs_review"])
                armor = items[73]
                self.assertEqual((armor["grid_slots"], armor["category"], armor["quantity"]),
                                 ([73, 74, 85, 86], "Item", 1))
                self.assertTrue(armor["category_verified"])
                self.assertFalse(armor["count_needs_review"])
                self.assertTrue({74, 85, 86}.isdisjoint(items))
                for slot in (73, 97):
                    self.assertEqual(items[slot]["category"], "Item")
                    self.assertFalse(any(name.startswith("Omen of ") for name in items[slot].get("candidate_names", [])))

    def test_bundled_source_art_defines_all_and_only_present_siblings(self):
        """Shared groups follow source-art filenames, including the shorter five/four-member sets."""
        path = currency_ocr.ROOT / "inventory-icons.json"
        entries = json.loads(path.read_text(encoding="utf-8"))["icons"]
        for stem, expected in SHARED_NAMES.items():
            source_names = [name for entry in entries
                            if entry.get("url", "").rsplit("/", 1)[-1].startswith(stem)
                            for name in entry["members"]]
            self.assertEqual(source_names, expected)
        stat = path.stat()
        members, owners, atlas = item_ocr._ritual_omen_shapes(str(path), stat.st_mtime_ns, stat.st_size)
        for expected in SHARED_NAMES.values():
            self.assertIn(tuple(expected), members)
        self.assertFalse(owners.flags.writeable or atlas.flags.writeable)

    def test_shape_cache_ignores_only_pixels_excluded_from_correlation(self):
        """Frame-only changes reuse exact masked math; artwork changes rerun matching."""
        cell = self.cells[1].convert("RGB").resize((40, 40), Image.Resampling.LANCZOS)
        cache = getattr(item_ocr._RITUAL_OMEN_CACHE, "ranks", None)
        if cache is not None:
            cache.clear()
        # Count fresh query comparisons even when reference FFTs are already
        # prepared; warming reference arithmetic must not bypass new artwork.
        with patch.object(currency_ocr.artwork_cache, "match",
                          wraps=currency_ocr.artwork_cache.match) as matching:
            original = item_ocr._ritual_gray_omen_icon(cell)
            self.assertIsNotNone(original)
            self.assertEqual(matching.call_count, 1)
            frame_changed = cell.copy()
            frame_changed.putpixel((0, 0), (220, 220, 220))
            self.assertEqual(item_ocr._ritual_gray_omen_icon(frame_changed), original)
            self.assertEqual(matching.call_count, 1)
            artwork_changed = cell.copy()
            pixel = cell.getpixel((18, 18))
            artwork_changed.putpixel((18, 18), tuple(255 - value for value in pixel))
            item_ocr._ritual_gray_omen_icon(artwork_changed)
            self.assertEqual(matching.call_count, 2)

    def test_reviewed_and_local_single_siblings_cannot_resolve_gray_art_at_any_strictness(self):
        """Learning one identical captured sibling preserves the complete ambiguity on repeat scans."""
        for reviewed in (False, True):
            references = []
            for slot, name in ((2, "Omen of Sinistral Erasure"), (13, "Omen of Whittling"),
                               (49, "Omen of Corruption"), (109, "Omen of the Ancients")):
                output = io.BytesIO()
                self.cells[slot].save(output, format="PNG")
                references.append({"name": name, "kind": "omen", "reviewed": reviewed,
                                   "image": output.getvalue(), "columns": 1, "rows": 1})
            for strictness in (0, 50, 100):
                with self.subTest(reviewed=reviewed, strictness=strictness):
                    items = self.grid_items(references=references, strictness=strictness)
                    self.assert_gray_candidates(items)

    def test_independent_anchored_name_text_still_confirms_a_supported_sibling(self):
        """Exact anchored OCR may confirm a candidate, while a different shape's label cannot."""
        proposals = [{"name": "Omen of Whittling", "category": "Omen", "name_match": 1.0,
                      "box": self.grid["rewards"][index]["box"], "tribute": 1200}
                     for index in (1, 2)]
        items = self.grid_items(parsed=proposals)
        self.assertEqual(items[13]["name"], "Omen of Whittling")
        self.assertEqual(items[13]["tribute"], 1200)
        self.assertFalse(items[13]["unresolved"] or items[13]["name_needs_review"])
        # Independent anchored text resolves this sibling's identity; gray art
        # alone still cannot confirm the different shape below.
        self.assertTrue(items[13]["identity_verified"])
        self.assertFalse(items[13]["needs_review"])
        self.assertEqual(items[2]["name"], "")

    def test_actual_colored_and_non_omen_crops_keep_existing_paths(self):
        """Color and rival shapes keep both bundled recognition and unrelated review learning."""
        negative_cells = {**self.cells, **self.armor_fragments}
        for slot in (14, 73, 74, 85, 86, 97):
            with self.subTest(slot=slot):
                self.assertIsNone(item_ocr._ritual_gray_omen_icon(negative_cells[slot]))
        with Image.open(FIXTURES / "ritual_rewards/08.png") as source:
            image = source.convert("RGB")
        old_cells = reward_cells(image, detect_reward_grid(image))
        reader = currency_ocr.get_reader()
        for slot in (87, 111):
            self.assertIsNone(item_ocr._ritual_gray_omen_icon(old_cells[slot]))
            self.assertEqual(item_ocr._ritual_reward_icon(reader, old_cells[slot], {}, ())[0], "Omen of Resurgence")
        output = io.BytesIO()
        self.cells[97].save(output, format="PNG")
        items = self.grid_items(references=[{"name": "Reviewed necklace reward", "kind": "item",
                                            "reviewed": True, "image": output.getvalue()}])
        self.assertEqual((items[97]["name"], items[97]["category"]), ("Reviewed necklace reward", "Item"))
        self.assert_gray_candidates(items)
        for color in ("black", "white", "gray"):
            self.assertIsNone(item_ocr._ritual_gray_omen_icon(Image.new("RGB", (40, 40), color)))

    def test_candidate_copies_and_worker_caches_do_not_share_mutable_reader_state(self):
        """Workers share only immutable assets, and callers cannot mutate cached suggestions."""
        first = item_ocr._ritual_gray_omen_icon(self.cells[2])
        first["members"].clear()
        self.assertEqual(item_ocr._ritual_gray_omen_icon(self.cells[2])["members"], SHARED_NAMES["VoodooOmens2"])
        barrier = threading.Barrier(2)

        def worker():
            result = item_ocr._ritual_gray_omen_icon(self.cells[2])
            cache = item_ocr._RITUAL_OMEN_CACHE.ranks
            barrier.wait(timeout=10)
            return result["members"], id(cache), len(cache)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda unused: worker(), range(2)))
        self.assertNotEqual(results[0][1], results[1][1])
        self.assertTrue(all(names == SHARED_NAMES["VoodooOmens2"] and size <= 128
                            for names, unused, size in results))

    def test_catalog_changes_invalidate_bounded_shape_and_result_caches(self):
        """New catalog metadata replaces stale identities, and varied captures cannot grow caches forever."""
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text(encoding="utf-8"))["icons"]
        selected = [entry for entry in entries if entry["family"] in ("omen-of-the-ancients", "artificer-s-orb")]
        self.assertEqual(len(selected), 2)
        with tempfile.TemporaryDirectory(prefix="poe2-ritual-shape-cache-") as directory:
            root = Path(directory)
            path = root / "inventory-icons.json"
            path.write_text(json.dumps({"icons": selected}), encoding="utf-8")
            with patch.object(currency_ocr, "ROOT", root):
                self.assertEqual(item_ocr._ritual_gray_omen_icon(self.cells[109])["members"], ["Omen of the Ancients"])
                # Only the excluded count corner changes; this exercises cache
                # cardinality, not synthetic brightness or recognition accuracy.
                for value in range(129):
                    cell = self.cells[109].resize((40, 40), Image.Resampling.LANCZOS)
                    cell.putpixel((1, 1), (50 + value,) * 3)
                    item_ocr._ritual_gray_omen_icon(cell)
                self.assertLessEqual(len(item_ocr._RITUAL_OMEN_CACHE.ranks), 128)
                path.write_text(json.dumps({"icons": [entry for entry in selected
                                                     if entry["family"] != "omen-of-the-ancients"]}), encoding="utf-8")
                self.assertIsNone(item_ocr._ritual_gray_omen_icon(self.cells[109]))
            self.assertLessEqual(item_ocr._ritual_omen_shapes.cache_info().currsize, 2)


if __name__ == "__main__":
    unittest.main()
