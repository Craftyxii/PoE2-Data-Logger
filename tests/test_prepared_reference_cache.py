"""Local learned-reference preparation stays immutable, current and worker bounded."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import io
import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, item_ocr


class PreparedReferenceCacheTests(unittest.TestCase):
    """Exercise real catalog pixels without using any user's reference database."""
    @classmethod
    def setUpClass(cls):
        """Encode independent bundled artwork as realistic learned PNG references."""
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
        cls.references = []
        for entry in entries:
            if entry.get("ignored"):
                continue
            image = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
            image.alpha_composite(Image.fromarray(
                np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4)))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
            cls.references.append({"id": len(cls.references), "name": entry["members"][0],
                                   "kind": "currency", "columns": 1, "rows": 1,
                                   "reviewed": True, "image": output.getvalue()})
            if len(cls.references) == 200:
                break

    def setUp(self):
        """Start each check with an independent worker reader and empty learned banks."""
        self.reader = currency_ocr.CurrencyReader()

    def test_real_png_warm_bank_skips_decode_resize_and_preparation(self):
        """Repeated 90-reference scans reuse preparation and preserve fresh rankings."""
        references = self.references[:90]
        with patch.object(item_ocr, "_reference_image", wraps=item_ocr._reference_image) as decode, \
                patch.object(item_ocr, "_reviewed_pixels", wraps=item_ocr._reviewed_pixels) as resize, \
                patch.object(self.reader, "prepare_examples", wraps=self.reader.prepare_examples) as prepare:
            cold = item_ocr._reviewed_bank(self.reader, references)
            warm = item_ocr._reviewed_bank(self.reader, [dict(row) for row in references])
        self.assertIs(cold, warm)
        self.assertEqual((decode.call_count, resize.call_count, prepare.call_count), (90, 90, 1))
        with Image.open(io.BytesIO(references[13]["image"])) as source:
            cell = source.convert("RGB")
        fresh_reader = currency_ocr.CurrencyReader()
        fresh = item_ocr._reviewed_bank(fresh_reader, references)
        self.assertEqual(self.reader.examples(item_ocr._reviewed_pixels(cell), warm[0]),
                         fresh_reader.examples(item_ocr._reviewed_pixels(cell), fresh[0]))
        self.assertEqual(item_ocr._reviewed_match(self.reader, cell, warm),
                         item_ocr._reviewed_match(fresh_reader, cell, fresh))

    def test_every_reference_identity_field_and_delete_rebuilds(self):
        """Rename, relabel, image replacement, dimensions, IDs and review state invalidate."""
        original = dict(self.references[0])
        before = item_ocr._reviewed_bank(self.reader, [original])
        for field, value in (("id", 9001), ("name", "Renamed reference"), ("kind", "omen"),
                             ("columns", 2), ("rows", 2), ("reviewed", False),
                             ("image", self.references[1]["image"])):
            with self.subTest(field=field):
                changed = dict(original, **{field: value})
                self.assertIsNot(item_ocr._reviewed_bank(self.reader, [changed]), before)
        deleted = item_ocr._reviewed_bank(self.reader, [])
        self.assertFalse(deleted[0])
        # An Undo restoring exact content can safely reuse that exact bank.
        self.assertIs(item_ocr._reviewed_bank(self.reader, [original]), before)

    def test_inventory_and_ritual_results_match_with_fresh_and_warm_banks(self):
        """Preparation reuse preserves complete suggestions, counts and review flags."""
        references = self.references[:12]
        with Image.open(io.BytesIO(references[4]["image"])) as source:
            cell = source.convert("RGB")
        inventory = Image.new("RGB", (480, 200), (26, 26, 40))
        inventory.paste(cell, (0, 0))
        inventory.info["poe2_inventory_aligned"] = True
        ritual = Image.new("RGB", (41, 41), (26, 26, 40))
        ritual.paste(cell, (1, 1))
        grid = {"pitch": 40, "rewards": [{"slots": [1], "box": (0, 0, 41, 41)}]}

        def scan(reader):
            """Keep non-reference evidence fixed while real prepared artwork is matched."""
            unknown = {"family": None, "members": [], "score": 0, "all": []}
            with patch.object(currency_ocr, "get_reader", return_value=reader), \
                    patch.object(reader, "icon", return_value=unknown), \
                    patch.object(reader, "count", return_value=1), \
                    patch.object(item_ocr, "_ritual_gray_omen_icon", return_value=None), \
                    patch.object(item_ocr, "_ritual_cell_labels", return_value={
                        0: {"count": 1, "count_verified": True}}):
                return (item_ocr.scan_inventory_grid(inventory, references, read=lambda _: []),
                        item_ocr._ritual_grid_items(ritual, grid, [], [], (), references))

        cold = scan(self.reader)
        warm = scan(self.reader)
        fresh = scan(currency_ocr.CurrencyReader())
        self.assertEqual(cold, warm)
        self.assertEqual(cold, fresh)
        self.assertEqual(cold[0]["items"][0]["name"], references[4]["name"])
        self.assertEqual(cold[1][0]["name"], references[4]["name"])

    def test_capture_snapshot_and_cached_banks_resist_caller_mutation(self):
        """Mutable dictionaries/PIL inputs cannot rewrite an already captured bank."""
        with Image.open(io.BytesIO(self.references[0]["image"])) as source:
            image = source.convert("RGB")
        reference = dict(self.references[0], image=image)
        snapshot = item_ocr._reference_snapshot([reference])
        before = item_ocr._reviewed_bank(self.reader, snapshot)
        original_pixels = before[0].references
        reference.update(name="Later edit", kind="omen")
        image.paste((90, 100, 110), (0, 0, 40, 40))
        self.assertIs(item_ocr._reviewed_bank(self.reader, snapshot), before)
        changed = item_ocr._reviewed_bank(self.reader, [reference])
        self.assertNotEqual(changed[0].signature, before[0].signature)
        self.assertEqual(before[0].references, original_pixels)
        with self.assertRaises(TypeError):
            before[1]["Injected"] = "omen"
        with self.assertRaises(TypeError):
            before[0].order["Injected"] = 1
        with self.assertRaises(FrozenInstanceError):
            before[0].references = ()

    def test_reader_restart_and_threads_have_independent_preparations(self):
        """A worker starts fresh and never receives another thread's prepared pixels."""
        reference = self.references[:1]
        original = item_ocr._reviewed_bank(self.reader, reference)
        restarted = currency_ocr.CurrencyReader()
        self.assertIsNot(item_ocr._reviewed_bank(restarted, reference), original)

        def prepare_in_worker():
            """Prepare and repeat locally while rejecting the caller's reader."""
            worker = currency_ocr.get_reader()
            first = item_ocr._reviewed_bank(worker, reference)
            self.assertIs(first, item_ocr._reviewed_bank(worker, reference))
            with self.assertRaisesRegex(ValueError, "worker thread"):
                item_ocr._reviewed_bank(self.reader, reference)
            return first

        with ThreadPoolExecutor(max_workers=1) as pool:
            other = pool.submit(prepare_in_worker).result()
        self.assertIsNot(original, other)
        self.assertNotEqual(original[0].owner, other[0].owner)

    def test_prepared_bank_count_and_retained_payload_stay_bounded(self):
        """Many revisions evict old banks by both count and retained prepared bytes."""
        for revision in range(30):
            references = [dict(self.references[0], id=revision)]
            item_ocr._reviewed_bank(self.reader, references)
        self.assertEqual(len(self.reader._learned_reference_banks), 24)
        for revision in range(24):
            references = [dict(row, id=revision * 1000 + index)
                          for index, row in enumerate(self.references)]
            item_ocr._reviewed_bank(self.reader, references)
        self.assertLess(len(self.reader._learned_reference_banks), 24)
        self.assertLessEqual(self.reader._learned_reference_bytes, 16 * 1024 * 1024)
        self.assertEqual(self.reader._learned_reference_bytes,
                         sum(size for _, size in self.reader._learned_reference_banks.values()))

    def test_unreviewed_and_footprint_banks_keep_separate_normalization(self):
        """Inventory fallback and reviewed multi-cell artwork retain their own pixels."""
        references = [dict(self.references[0], reviewed=False),
                      dict(self.references[1], columns=2, rows=3)]
        ordinary = item_ocr._prepared_reference_bank(self.reader, references, (1, 1), False)
        equipment = item_ocr._reviewed_bank(self.reader, references, (2, 3))
        self.assertEqual(set(ordinary[0].order), {references[0]["name"]})
        self.assertEqual(set(equipment[0].order), {references[1]["name"]})
        self.assertIs(ordinary, item_ocr._prepared_reference_bank(
            self.reader, references, (1, 1), False))
        self.assertIs(equipment, item_ocr._reviewed_bank(self.reader, references, (2, 3)))


if __name__ == "__main__":
    unittest.main()
