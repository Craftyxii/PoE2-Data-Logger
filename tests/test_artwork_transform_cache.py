"""Compare retained artwork arithmetic with the pinned OpenCV response oracle."""
from concurrent.futures import ThreadPoolExecutor
import gc
import unittest
from unittest.mock import patch
import weakref

import cv2
import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import artwork_cache, currency_ocr
from PoE2_Data_Logger.core import ocr_runtime

_FAST = patch.object(ocr_runtime, "_FAST", False)


def setUpModule():
    """These tests cover the original FFT ranking, so Fast recognition is off here."""
    _FAST.start()


def tearDownModule():
    _FAST.stop()


class ArtworkTransformCacheTests(unittest.TestCase):
    """Preserve whole response maps, final rankings and safe cache ownership."""

    def setUp(self):
        """Keep cache lifetime and sample generation independent between checks."""
        artwork_cache._CACHE.clear()
        self.rng = np.random.default_rng(6721)

    def tearDown(self):
        """Release spectrum banks so other test modules retain their prior memory use."""
        artwork_cache._CACHE.clear()

    def source(self, height=288, gray=False):
        """Create immutable reference pixels spanning full and partial FFT blocks."""
        shape = (height, 48) if gray else (height, 48, 3)
        result = self.rng.uniform(0, 255, shape).astype(np.float32)
        result.flags.writeable = False
        return result

    def mask(self, gray=False):
        """Use the real gray binary mask or the inventory's fractional pixel weights."""
        if not gray:
            return np.sqrt(self.rng.choice([0, .2, 1], size=(40, 40)).astype(np.float32))
        result = np.zeros((40, 40), dtype=np.uint8)
        result[4:-4, 4:-4] = 1
        result[:15, :26] = result[25:, 25:] = 0
        return result

    def assert_response_equal(self, source, query, mask, method):
        """A second call must use the cache, including all baseline nonfinite values."""
        expected = cv2.matchTemplate(source, query, method, mask=mask)
        self.assertTrue(np.array_equal(artwork_cache.match(source, query, method, mask=mask),
                                       expected, equal_nan=True))
        with patch.object(artwork_cache.cv2, "matchTemplate",
                          side_effect=AssertionError("Warm path rebuilt its reference")):
            actual = artwork_cache.match(source, query, method, mask=mask)
        np.testing.assert_array_equal(actual, expected)

    def test_startup_preparation_reuses_the_bank_and_checks_the_first_real_query(self):
        """Reference-only startup work leaves the original real-query proof intact."""
        for gray in (False, True):
            source = self.source(gray=gray)
            query, mask = source[4:44, 4:44].copy(), self.mask(gray=gray)
            method = cv2.TM_CCOEFF_NORMED if gray else cv2.TM_SQDIFF
            builder = artwork_cache._GrayCorrelation if gray else artwork_cache._SquaredDifference
            with patch.object(artwork_cache.cv2, "matchTemplate",
                              side_effect=AssertionError("Startup manufactured a query")), \
                    patch.object(builder, "run",
                                 side_effect=AssertionError("Startup ran query arithmetic")):
                self.assertTrue(artwork_cache.prepare(source, method, mask=mask))
            prepared = next(value[1] for value in artwork_cache._CACHE.values()
                            if value[0]() is source)
            self.assertFalse(prepared.verified)
            original = cv2.matchTemplate
            expected = original(source, query, method, mask=mask)
            with patch.object(builder, "__init__",
                              side_effect=AssertionError("First scan rebuilt startup artwork")), \
                    patch.object(artwork_cache.cv2, "matchTemplate", wraps=original) as proof:
                np.testing.assert_array_equal(artwork_cache.match(source, query, method,
                                                                mask=mask), expected)
                self.assertEqual(proof.call_count, 1)
            self.assertTrue(prepared.verified)
            self.assertIs(next(value[1] for value in artwork_cache._CACHE.values()
                               if value[0]() is source), prepared)
            with patch.object(artwork_cache.cv2, "matchTemplate",
                              side_effect=AssertionError("Warm scan repeated its proof")):
                np.testing.assert_array_equal(artwork_cache.match(source, query, method,
                                                                mask=mask), expected)

    def test_prepared_bank_still_disables_a_backend_that_fails_the_real_query_proof(self):
        """Prebuilding transforms never enables an unproved numerical backend."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        self.assertTrue(artwork_cache.prepare(source, cv2.TM_SQDIFF, mask=mask))
        expected = cv2.matchTemplate(source, query, cv2.TM_SQDIFF, mask=mask)
        with patch.object(artwork_cache._SquaredDifference, "run", return_value=expected + 32):
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)
        self.assertIsNone(next(iter(artwork_cache._CACHE.values()))[1])
        with patch.object(artwork_cache._SquaredDifference, "__init__",
                          side_effect=AssertionError("Retried a rejected prepared backend")):
            self.assertFalse(artwork_cache.prepare(source, cv2.TM_SQDIFF, mask=mask))
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)

    def test_rgb_full_responses_match_for_fractional_masks_and_partial_blocks(self):
        """Every placement and channel agrees, including changing query-dependent masks."""
        for height in (48, 240, 288, 576):
            source = self.source(height)
            for _ in range(3):
                query = self.rng.uniform(0, 255, (40, 40, 3)).astype(np.float32)
                self.assert_response_equal(source, query, self.mask(), cv2.TM_SQDIFF)

    def test_gray_full_responses_preserve_degenerate_and_random_queries(self):
        """Zero-variance queries retain baseline NaNs and infinities as well as real scores."""
        for height in (48, 240, 288, 576):
            source = self.source(height, gray=True)
            for query in (np.zeros((40, 40), np.float32), np.full((40, 40), 77, np.float32),
                          self.rng.uniform(0, 255, (40, 40)).astype(np.float32),
                          source[4:44, 4:44].copy()):
                self.assert_response_equal(source, query, self.mask(gray=True),
                                           cv2.TM_CCOEFF_NORMED)

    def test_mask_replacement_and_source_replacement_do_not_reuse_stale_terms(self):
        """Different gray masks and changed reference arrays each produce fresh evidence."""
        source = self.source(gray=True)
        query = source[4:44, 4:44].copy()
        for mask in (self.mask(gray=True), np.ones((40, 40), np.uint8)):
            self.assert_response_equal(source, query, mask, cv2.TM_CCOEFF_NORMED)
        changed = source.copy()
        changed[::3] = 0
        changed.flags.writeable = False
        self.assert_response_equal(changed, query, self.mask(gray=True), cv2.TM_CCOEFF_NORMED)

    def test_mutable_source_unsupported_version_and_mask_use_original(self):
        """Future kernels and inputs outside the cache contract keep baseline dispatch."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        original = cv2.matchTemplate
        for data, version in ((source.copy(), "4.10.0"), (source, "5.0.0")):
            with patch.object(artwork_cache.cv2, "__version__", version), \
                    patch.object(artwork_cache.cv2, "matchTemplate", wraps=original) as call:
                artwork_cache.match(data, query, cv2.TM_SQDIFF, mask=mask)
                self.assertEqual(call.call_count, 1)
        gray = self.source(gray=True)
        weighted = np.full((40, 40), .5, np.float32)
        with patch.object(artwork_cache.cv2, "matchTemplate", wraps=original) as call:
            artwork_cache.match(gray, gray[4:44, 4:44].copy(), cv2.TM_CCOEFF_NORMED, mask=weighted)
            self.assertEqual(call.call_count, 1)
        self.assertFalse(artwork_cache._CACHE)

    def test_backend_parity_mismatch_disables_reuse_for_that_bank(self):
        """A numerically incompatible backend uses original matching on subsequent calls."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        expected = cv2.matchTemplate(source, query, cv2.TM_SQDIFF, mask=mask)
        with patch.object(artwork_cache._SquaredDifference, "run", return_value=expected + 32):
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)
        with patch.object(artwork_cache._SquaredDifference, "__init__",
                          side_effect=AssertionError("Retried a rejected backend")):
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)

    def test_allocation_failure_keeps_baseline_response(self):
        """An unavailable cache never makes a previously readable icon fail."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        expected = cv2.matchTemplate(source, query, cv2.TM_SQDIFF, mask=mask)
        with patch.object(artwork_cache._Correlation, "__init__", side_effect=MemoryError):
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)

    def test_warm_cache_error_retries_the_reading_with_original_matching(self):
        """Failure after successful preparation still preserves the current scan."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        expected = artwork_cache.match(source, query, cv2.TM_SQDIFF, mask=mask)
        for error in (MemoryError(), cv2.error("simulated warm failure")):
            with patch.object(artwork_cache._SquaredDifference, "run", side_effect=error):
                np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                                mask=mask), expected)
            self.assertIsNone(next(iter(artwork_cache._CACHE.values()))[1])
            artwork_cache._CACHE.clear()
            artwork_cache.match(source, query, cv2.TM_SQDIFF, mask=mask)

    def test_cache_is_bounded_and_does_not_keep_obsolete_source_arrays_alive(self):
        """Evicted/deleted catalogs cannot accumulate retained transforms or source pixels."""
        sources = [self.source() for _ in range(4)]
        for source in sources:
            artwork_cache.match(source, source[4:44, 4:44].copy(), cv2.TM_SQDIFF, mask=self.mask())
            self.assertLessEqual(len(artwork_cache._CACHE), artwork_cache._MAX_BANKS)
            self.assertLessEqual(sum(bank.nbytes for _, bank in artwork_cache._CACHE.values()
                                     if bank), artwork_cache._MAX_BYTES)
        reference = weakref.ref(source)
        del source
        sources.clear()
        gc.collect()
        self.assertIsNone(reference())
        source = self.source()
        with patch.object(artwork_cache, "_MAX_BYTES", 1):
            artwork_cache.match(source, source[4:44, 4:44].copy(), cv2.TM_SQDIFF, mask=self.mask())
            self.assertTrue(all(bank is None for _, bank in artwork_cache._CACHE.values()))

    def test_shared_bank_supports_concurrent_queries_without_retaining_scan_arrays(self):
        """Readers can share immutable spectra without overwriting another scan's work."""
        source = self.source()
        queries = [self.rng.uniform(0, 255, (40, 40, 3)).astype(np.float32) for _ in range(4)]
        mask = self.mask()
        expected = [cv2.matchTemplate(source, query, cv2.TM_SQDIFF, mask=mask) for query in queries]
        with ThreadPoolExecutor(max_workers=3) as pool:
            actual = list(pool.map(lambda query: artwork_cache.match(source, query,
                               cv2.TM_SQDIFF, mask=mask), queries))
        for got, wanted in zip(actual, expected):
            np.testing.assert_array_equal(got, wanted)

    def test_oversized_build_workspace_uses_original_without_allocating_spectra(self):
        """A restricted preparation budget affects speed only, never the recognized pixels."""
        source = self.source()
        query, mask = source[4:44, 4:44].copy(), self.mask()
        expected = cv2.matchTemplate(source, query, cv2.TM_SQDIFF, mask=mask)
        with patch.object(artwork_cache, "_MAX_BUILD_BYTES", 1), \
                patch.object(artwork_cache._Correlation, "__init__",
                             side_effect=AssertionError("Unreserved transform allocation")):
            np.testing.assert_array_equal(artwork_cache.match(source, query, cv2.TM_SQDIFF,
                                                            mask=mask), expected)

    def test_real_inventory_catalog_rankings_are_exact_with_and_without_cache(self):
        """All top scores, ordering and scale refinements retain the original results."""
        reader = currency_ocr.CurrencyReader()
        for index in (0, 50, 100, 250, 400, 538):
            image = Image.fromarray(np.asarray(reader.calibrated_candidates[index][1][4:44, 4:44],
                                               dtype=np.uint8), "RGB")
            with patch.object(artwork_cache, "match", side_effect=cv2.matchTemplate):
                expected = reader.inventory_ranked(image, calibrated=True)
            reader._rank_cache.clear()
            actual = reader.inventory_ranked(image, calibrated=True)
            self.assertEqual(actual, expected)
        self.assertTrue(any(bank for _, bank in artwork_cache._CACHE.values()))


if __name__ == "__main__":
    unittest.main()
