"""Independent catalog queries share immutable art while all scan decisions stay serial."""
from collections import OrderedDict
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.ocr import currency_ocr, item_ocr


class CurrencyArtworkParallelTests(unittest.TestCase):
    """Verify complete rank/scan equality, reader boundaries, pool limits and fallback."""

    def setUp(self):
        """Use the bundled catalogs without any user's learned references or database."""
        self.reader = currency_ocr.CurrencyReader()
        self.requests = [(Image.fromarray(np.asarray(
            self.reader.calibrated_candidates[index][1][4:44, 4:44], dtype=np.uint8), "RGB"), digits)
            for index, digits in zip((0, 50, 100, 250, 400, 538), (1, 2, 3, None, 2, 1))]

    def ranks(self):
        """Read complete serial scores and stable family ordering for this request list."""
        return [self.reader.inventory_ranked(image, calibrated=True, count_digits=digits)
                for image, digits in self.requests]

    def test_parallel_ranks_exactly_equal_serial_and_leave_no_javascript_in_workers(self):
        """The worker owns numerical state only; cached serial reads retain every score."""
        expected = self.ranks()
        self.reader._rank_cache.clear()
        main = threading.get_ident()
        original = currency_ocr._rank_inventory
        worker_ids = set()

        def checked(state, rgb, weights, calibrated):
            """Verify thread ownership alongside real OpenCV arithmetic."""
            self.assertNotEqual(threading.get_ident(), main)
            self.assertFalse(hasattr(state, "context") or hasattr(state, "_rank_cache"))
            self.assertFalse(rgb.flags.writeable or weights.flags.writeable)
            worker_ids.add(threading.get_ident())
            return original(state, rgb, weights, calibrated)

        with patch.object(currency_ocr, "_rank_inventory", side_effect=checked), \
                patch.object(currency_ocr.quickjs, "Context", side_effect=AssertionError("Worker built JS")):
            prepared = currency_ocr.prefetch_catalog_ranks(self.reader, self.requests, workers=2)
        self.assertEqual(prepared, len(self.requests))
        self.assertTrue(worker_ids)
        self.assertLessEqual(len(worker_ids), 2)
        with patch.object(currency_ocr, "_rank_inventory", side_effect=AssertionError("Ranks not cached")):
            self.assertEqual(self.ranks(), expected)
        self.assertFalse(any(thread.name.startswith("CurrencyArt") for thread in threading.enumerate()))
        changed = self.reader.inventory_ranked(self.requests[0][0], calibrated=True, count_digits=1)
        changed[0]["score"] = 123
        self.assertEqual(self.ranks(), expected)

    def test_one_core_keeps_the_original_serial_path_without_a_pool(self):
        """The existing one-core selection performs no speculative artwork work."""
        with patch.object(currency_ocr, "ThreadPoolExecutor", side_effect=AssertionError("Unexpected pool")), \
                patch.object(ocr_runtime, "active_threads", return_value=1):
            self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, self.requests), 0)
        self.assertFalse(self.reader._rank_cache)
        self.assertTrue(all(self.ranks()))

    def test_a_full_old_lru_does_not_evict_requested_hits_when_new_ranks_are_published(self):
        """A mix of old hits and new misses all remain available for the serial icon loop."""
        expected = self.ranks()
        self.reader._rank_cache.clear()
        for (image, digits), ranked in zip(self.requests[:3], expected[:3]):
            key = currency_ocr._inventory_query(image, True, digits)[2]
            self.reader._rank_cache[key] = tuple(dict(row) for row in ranked)
        for index in range(125):
            self.reader._rank_cache[(False, str(index).encode(), b"old query")] = ()
        self.assertEqual(len(self.reader._rank_cache), 128)
        self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, self.requests, workers=2), 3)
        self.assertLessEqual(len(self.reader._rank_cache), 128)
        with patch.object(currency_ocr, "_rank_inventory", side_effect=AssertionError("Evicted needed rank")):
            self.assertEqual(self.ranks(), expected)

    def test_selector_limits_pool_to_two_four_or_six_workers(self):
        """One inventory cannot grow a pool beyond its selected CPU count or task count."""
        from concurrent.futures import ThreadPoolExecutor
        for count in (2, 4, 6):
            self.reader._rank_cache.clear()
            seen = []

            def pool(**kwargs):
                """Record the actual bounded executor, not a simulated map."""
                seen.append(kwargs["max_workers"])
                return ThreadPoolExecutor(**kwargs)

            with patch.object(currency_ocr, "ThreadPoolExecutor", side_effect=pool), \
                    patch.object(ocr_runtime, "active_threads", return_value=count):
                self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, self.requests), 6)
            self.assertEqual(seen, [count])
            self.assertFalse(any(thread.name.startswith("CurrencyArt") for thread in threading.enumerate()))

    def test_empty_duplicate_and_already_cached_queries_do_not_add_comparisons(self):
        """Independent counts keep their exact masks while identical effective art is reused."""
        first, second = self.requests[:2]
        empty = Image.new("RGB", (40, 40), (0, 0, 0))
        requests = [first, (first[0].copy(), first[1]), second, (empty, None)]
        with patch.object(currency_ocr, "_rank_inventory", wraps=currency_ocr._rank_inventory) as ranked:
            self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, requests, workers=2), 2)
            self.assertEqual(ranked.call_count, 2)
            self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, requests, workers=2), 0)
            self.assertEqual(ranked.call_count, 2)
        self.assertEqual(len(self.reader._rank_cache), 2)

    def test_custom_or_overridden_readers_are_never_replaced_by_prefetch(self):
        """Existing mocks, extensions and custom icon/ranking behavior stay authoritative."""
        def untouched():
            """A guarded-out implementation must not even consume speculative queries."""
            raise AssertionError("Consumed custom-reader requests")
            yield

        self.assertEqual(currency_ocr.prefetch_catalog_ranks(SimpleNamespace(), untouched(), workers=2), 0)
        for method in ("icon", "inventory_ranked"):
            with patch.object(self.reader, method, return_value=[]):
                self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, untouched(), workers=2), 0)

    def test_failed_workers_publish_nothing_and_preserve_ordinary_serial_reading(self):
        """Expected preparation failures retain all pixels and existing cache entries for retry."""
        expected = self.ranks()
        original = currency_ocr._rank_inventory
        for error in (MemoryError(), RuntimeError("worker unavailable"), cv2.error("unavailable kernel")):
            self.reader._rank_cache.clear()
            untouched = (False, b"old weights", b"old pixels")
            self.reader._rank_cache[untouched] = ({"name": "old", "score": -1},)
            before = OrderedDict(self.reader._rank_cache)

            def failed(state, *args):
                """Only the optional worker fails; the original reader still has its result path."""
                if isinstance(state, currency_ocr._CatalogRankState):
                    raise error
                return original(state, *args)

            with patch.object(currency_ocr, "_rank_inventory", side_effect=failed):
                self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, self.requests, workers=2), 0)
                self.assertEqual(self.reader._rank_cache, before)
                self.assertEqual(self.ranks(), expected)

    def test_single_query_and_oversized_request_lists_keep_serial_dispatch(self):
        """Only one captured inventory of independent work can create an executor."""
        for requests in (self.requests[:1], self.requests * 11):
            with patch.object(currency_ocr, "ThreadPoolExecutor", side_effect=AssertionError("Unexpected pool")):
                self.assertEqual(currency_ocr.prefetch_catalog_ranks(self.reader, requests, workers=6), 0)
        self.assertFalse(self.reader._rank_cache)

    def test_full_inventory_result_and_count_thread_are_identical_at_one_and_two_cores(self):
        """Precomputed ranks feed the original icon/count/reference/review logic in slot order."""
        grid = Image.new("RGB", (480, 200), (26, 26, 40))
        grid.info["poe2_inventory_aligned"] = True
        for index, (image, _) in enumerate(self.requests):
            grid.paste(image, (index * 40, 0))
        main = threading.get_ident()

        def count(_image):
            """Counts remain entirely outside the artwork workers."""
            self.assertEqual(threading.get_ident(), main)
            return 2

        outputs = []
        for cores in (1, 2):
            self.reader._rank_cache.clear()
            with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                    patch.object(ocr_runtime, "active_threads", return_value=cores), \
                    patch.object(item_ocr, "_inventory_equipment_slots", return_value=set()), \
                    patch.object(self.reader, "count", side_effect=count):
                outputs.append(item_ocr.scan_inventory_grid(grid, read=lambda _: []))
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(outputs[0]["items"] or outputs[0]["unknown"])

    def test_prefetch_excludes_uniform_empty_and_equipment_cells(self):
        """The ordinary scan still handles equipment; optional parallel work does not duplicate it."""
        grid = Image.new("RGB", (480, 200), (26, 26, 40))
        grid.info["poe2_inventory_aligned"] = True
        for index, (image, _) in enumerate(self.requests[:3]):
            grid.paste(image, (index * 40, 0))
        requests = []

        def capture(_reader, values):
            """Snapshot the actual requested cells without doing speculative recognition."""
            requests.extend(values)
            return 0

        with patch.object(currency_ocr, "get_reader", return_value=self.reader), \
                patch.object(currency_ocr, "prefetch_catalog_ranks", side_effect=capture), \
                patch.object(item_ocr, "_inventory_equipment_slots", return_value={2}), \
                patch.object(self.reader, "count", return_value=2):
            item_ocr.scan_inventory_grid(grid, read=lambda _: [])
        self.assertEqual(len(requests), 2)
        self.assertEqual([image.tobytes() for image, _ in requests],
                         [item_ocr.inventory_cell(grid, slot).tobytes() for slot in (1, 3)])


if __name__ == "__main__":
    unittest.main()
