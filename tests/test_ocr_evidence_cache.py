"""Exact-pixel performance caches retain raw evidence and independent review checks."""

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from PoE2_Data_Logger.ocr import currency_ocr, item_ocr, propagation_scan


class _TextReader:
    """Small deterministic recognizer exposing the real batch-padding configuration."""

    def __init__(self):
        self.rec_image_shape = [3, 48, 320]
        self.rec_batch_num = 6
        self.cfg = {"model": "first"}
        self.session = SimpleNamespace(session=object())
        self.postprocess_op = object()
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args.img))
        return SimpleNamespace(txts=tuple(str(int(image[0, 0, 0])) for image in args.img),
                               scores=tuple(.995 for image in args.img))


class InventoryEvidenceCacheTests(unittest.TestCase):
    """Cache raw readings while retaining crop positions, confidence and all votes."""

    def setUp(self):
        self.reader = _TextReader()
        self.engine = SimpleNamespace(text_rec=self.reader)
        self.patch = patch.object(item_ocr, "_engine", return_value=self.engine)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        item_ocr._INVENTORY_TEXT_CACHE.state = None

    @staticmethod
    def pixels(value, shape=(12, 18, 3)):
        return np.full(shape, value, dtype=np.uint8)

    def test_repeated_patches_retain_order_confidence_and_each_evidence_vote(self):
        a, b = self.pixels(14), self.pixels(3)
        expected = (("14", .995), ("3", .995), ("14", .995), ("14", .995))
        self.assertEqual(item_ocr._inventory_patch_readings([a, b, a.copy(), a]), expected)
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(len(self.reader.calls[0]), 2)
        self.assertEqual(item_ocr._inventory_patch_readings([b, a]), (expected[1], expected[0]))
        self.assertEqual(len(self.reader.calls), 1)

    def test_identical_count_crops_still_supply_both_original_votes(self):
        image = Image.new("RGB", (120, 50), (0, 0, 0))
        crop = Image.fromarray(self.pixels(14))
        numbers = [([crop, crop.copy()], True)] + [([], False)] * 59
        with patch("PoE2_Data_Logger.ocr.inventory_labels.count_crops", side_effect=numbers), \
                patch("PoE2_Data_Logger.ocr.inventory_labels.tier_crops", return_value=([], False)):
            labels = item_ocr._inventory_labels(image)
        self.assertEqual(labels[1]["count"], 14)
        self.assertEqual(labels[1]["count_candidate"], 14)
        self.assertEqual(len(self.reader.calls[0]), 1)

    def test_changed_count_tier_crop_dimensions_and_dtype_miss(self):
        a = self.pixels(14)
        variants = [a, self.pixels(3), self.pixels(14, (13, 18, 3)), a.astype(np.int16)]
        for candidate in variants:
            item_ocr._inventory_patch_readings([candidate])
        self.assertEqual(len(self.reader.calls), len(variants))

    def test_reader_model_configuration_and_session_changes_invalidate(self):
        image = self.pixels(14)
        item_ocr._inventory_patch_readings([image])
        self.reader.cfg["model"] = "second"
        item_ocr._inventory_patch_readings([image])
        self.reader.session.session = object()
        item_ocr._inventory_patch_readings([image])
        self.assertEqual(len(self.reader.calls), 3)
        replacement = _TextReader()
        self.engine.text_rec = replacement
        item_ocr._inventory_patch_readings([image])
        self.assertEqual(len(replacement.calls), 1)

    def test_wide_batch_keeps_original_padding_context_and_duplicate_inputs(self):
        wide = self.pixels(14, (10, 100, 3))
        for _ in range(2):
            self.assertEqual(item_ocr._inventory_patch_readings([wide, wide]),
                             (("14", .995), ("14", .995)))
        self.assertEqual([len(call) for call in self.reader.calls], [2, 2])

    def test_cache_is_bounded_and_separate_for_each_worker_thread(self):
        for index in range(520):
            candidate = self.pixels(14)
            candidate[0, 1, :2] = [index % 256, index // 256]
            item_ocr._inventory_patch_readings([candidate])
        self.assertEqual(len(item_ocr._INVENTORY_TEXT_CACHE.state[2]), 512)
        candidate = self.pixels(14)
        item_ocr._inventory_patch_readings([candidate])
        before = len(self.reader.calls)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(item_ocr._inventory_patch_readings, [candidate]).result()
        self.assertEqual(len(self.reader.calls), before + 1)


class CurrencyCountCacheTests(unittest.TestCase):
    """The independent native count recognizer caches both verified counts and None."""

    def reader(self, callback):
        reader = currency_ocr.CurrencyReader.__new__(currency_ocr.CurrencyReader)
        reader._count_caches = threading.local()
        reader.read_count = callback
        return reader

    def test_verified_and_failed_counts_reuse_only_identical_input(self):
        calls = []

        def native(payload):
            value = json.loads(payload)["data"][0]
            calls.append(value)
            return json.dumps({"text": str(value), "conf": .99 if value else .1})

        reader = self.reader(native)
        for value, expected in ((14, 14), (0, None), (3, 3)):
            image = Image.new("RGB", (40, 40), (value, 0, 0))
            self.assertEqual(reader.count(image), expected)
            self.assertEqual(reader.count(image.copy()), expected)
        self.assertEqual(calls, [14, 0, 3])

    def test_replacement_reader_and_threads_have_independent_bounded_caches(self):
        calls = []
        callback = lambda payload: calls.append(payload) or '{"text":"14","conf":0.99}'
        reader = self.reader(callback)
        image = Image.new("RGB", (40, 40), (14, 0, 0))
        reader.count(image)
        self.reader(callback).count(image)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(reader.count, image).result()
        self.assertEqual(len(calls), 3)
        reader.read_count = lambda payload: '{"text":"3","conf":0.99}'
        self.assertEqual(reader.count(image), 3)
        for index in range(140):
            reader.count(Image.new("RGB", (40, 40), (index, 0, 0)))
        cache = reader._count_caches.state[1]
        self.assertEqual(len(cache), 128)
        self.assertTrue(all(len(key[1]) <= 96 * 96 * 4 for key in cache))


class _PanelReader:
    """Deterministic raw OCR with no recipe or manual-approval knowledge."""

    def __init__(self):
        self.cfg = {"model": "first"}
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return SimpleNamespace(boxes=[[[0, 0], [200, 0], [200, 20], [0, 20]],
                                      [[0, 40], [100, 40], [100, 60], [0, 60]]],
                               txts=["Runeshape Combinations", "1x Divine Orb"], scores=[.8, .95])


class PropagationEvidenceCacheTests(unittest.TestCase):
    """Only raw rows are cached; callers can rerun current confidence/family checks."""

    def setUp(self):
        self.reader = _PanelReader()
        self.patch = patch.object(propagation_scan.opened_scan, "_engine", return_value=self.reader)
        self.mock_engine = self.patch.start()
        self.addCleanup(self.patch.stop)
        propagation_scan._RAW_ROW_CACHE.state = None

    def test_exact_pixels_reuse_confidence_and_boxes_without_sharing_mutable_rows(self):
        image = Image.new("RGB", (300, 200))
        first = propagation_scan._rapid_rows(image)
        expected = [dict(row) for row in first]
        first[0]["text"] = "changed by review"
        self.assertEqual(propagation_scan._rapid_rows(image.copy()), expected)
        self.assertEqual(self.reader.calls, 1)
        self.assertEqual(expected[0]["score"], .8)
        self.assertEqual(expected[1]["y1"], 40)

    def test_changed_pixels_size_mode_engine_or_model_invalidate(self):
        image = Image.new("RGB", (300, 200))
        propagation_scan._rapid_rows(image)
        image.putpixel((1, 1), (1, 0, 0))
        propagation_scan._rapid_rows(image)
        propagation_scan._rapid_rows(image.crop((0, 0, 299, 200)))
        propagation_scan._rapid_rows(image.convert("RGBA"))
        self.reader.cfg["model"] = "second"
        propagation_scan._rapid_rows(image)
        self.assertEqual(self.reader.calls, 5)
        replacement = _PanelReader()
        self.mock_engine.return_value = replacement
        propagation_scan._rapid_rows(image)
        self.assertEqual(replacement.calls, 1)

    def test_current_heading_strictness_is_evaluated_after_raw_cache_lookup(self):
        image = Image.new("RGB", (300, 200))
        relaxed = propagation_scan._read_panel_rows(image, strictness=0)
        strict = propagation_scan._read_panel_rows(image, strictness=100)
        self.assertIsNotNone(relaxed[1])
        self.assertIsNone(strict[1])
        self.assertEqual(self.reader.calls, 1)

    def test_effective_preprocessing_and_detection_changes_invalidate(self):
        image = Image.new("RGB", (300, 200))
        self.reader.text_det = SimpleNamespace(limit_side_len=736,
            session=SimpleNamespace(session=object()), postprocess_op=SimpleNamespace(box_thresh=.5))
        propagation_scan._rapid_rows(image)
        self.reader.text_det.postprocess_op.box_thresh = .6
        propagation_scan._rapid_rows(image)
        self.reader.text_det.limit_side_len = 512
        propagation_scan._rapid_rows(image)
        self.reader.text_det.session.session = object()
        propagation_scan._rapid_rows(image)
        self.assertEqual(self.reader.calls, 4)

    def test_cache_is_bounded_and_thread_local(self):
        for value in range(12):
            propagation_scan._rapid_rows(Image.new("RGB", (300, 200), (value, 0, 0)))
        self.assertEqual(len(propagation_scan._RAW_ROW_CACHE.state[2]), 8)
        image = Image.new("RGB", (300, 200), (11, 0, 0))
        before = self.reader.calls
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(propagation_scan._rapid_rows, image).result()
        self.assertEqual(self.reader.calls, before + 1)


if __name__ == "__main__":
    unittest.main()
