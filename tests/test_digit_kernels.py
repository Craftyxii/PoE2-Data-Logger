"""Differential checks against untouched vendor arithmetic and count decisions.

Rendered cells use bundled captured glyphs/artwork, not new capture-accuracy
evidence. No test changes the eight-threshold sweep or review policy.
"""

import hashlib
import json
import unittest
from unittest.mock import patch

import numpy as np
import quickjs
from PIL import Image, ImageFilter

from PoE2_Data_Logger.ocr import currency_ocr, digit_kernels


class DigitKernelTests(unittest.TestCase):
    """Keep native arithmetic bit-for-bit equivalent to upstream results."""

    @classmethod
    def setUpClass(cls):
        """Load independent original and production readers once without count caches."""
        cls.source = (currency_ocr.ROOT / "digit-reader.js").read_text(encoding="utf-8")
        cls.original = cls.context(cls.source)
        cls.accelerated = cls.context(cls.source, accelerate=True)
        cls.glyphs = json.loads((currency_ocr.ROOT / "digit-templates.json").read_text())["templates"]
        cls.production = currency_ocr.CurrencyReader()
        entries = json.loads((currency_ocr.ROOT / "inventory-icons.json").read_text())["icons"]
        cls.artwork = {item["family"]: Image.fromarray(
            np.asarray(item["rgba"], dtype=np.uint8).reshape(40, 40, 4), "RGBA")
            for item in entries if item["family"] in ("divine-orb", "prismatic-alloy")}

    @staticmethod
    def context(source, accelerate=False):
        """Expose vendor functions and the same raw count entry point in a fresh context."""
        context = quickjs.Context()
        context.eval("var module = {exports:{}};")
        executable = digit_kernels.source_with_native_kernels(context, source) if accelerate else source
        # Expose the lexical collection helper only inside these differential
        # contexts; the bundled source file and production exports stay intact.
        context.eval(executable.replace("    otsu, crop,", "    collect, otsu, crop,"))
        bank = (currency_ocr.ROOT / "digit-templates.json").read_text(encoding="utf-8")
        context.eval("var DigitReader=module.exports; var DIGITS=DigitReader.templatesFromJSON(" + bank + ");")
        context.eval("""function trial(s) {
          var p=JSON.parse(s);
          var value=DigitReader.valueChannelDesatMax(p.data,p.w,p.h);
          return JSON.stringify(DigitReader.readCellAdaptive(
            value,p.w,p.h,p.cx,p.cy,DIGITS,DigitReader.DEFAULTS,p.scale));
        }
        function slide(s) {var p=JSON.parse(s); return JSON.stringify(
          DigitReader.slideMatch(p.strip,p.tmpl,p.lo,p.hi,p.frac));}
        function opening(s) {var p=JSON.parse(s); var r=DigitReader.greyOpening(p.sub,p.k);
          return JSON.stringify({data:Array.from(r.data),w:r.w,h:r.h});}
        function collection(s) {var p=JSON.parse(s), templates={};
          p.templates.forEach(function(t) {templates[t.ch]={data:Uint8Array.from(t.data),w:t.w,h:t.h};});
          return JSON.stringify(DigitReader.collect(p.strip,templates,p.thresh,
            {dyLo:p.lo,dyHi:p.hi,minInkFrac:p.frac}));}
        """)
        return context

    @staticmethod
    def pixels(array):
        """Serialize a small integer plane using the vendor data layout."""
        return {"data": array.ravel().tolist(), "w": array.shape[1], "h": array.shape[0]}

    def test_binary_match_windows_bounds_ink_floors_and_first_ties(self):
        """Compare full x/dy/score records, including zero masks and clipped windows."""
        rng = np.random.default_rng(618)
        original, accelerated = self.original.get("slide"), self.accelerated.get("slide")
        for index in range(160):
            h, w = int(rng.integers(7, 30)), int(rng.integers(2, 38))
            th, tw = int(rng.integers(7, 17)), int(rng.integers(1, 14))
            source = (rng.random((h, w)) < rng.choice([0, .1, .4, .8, 1])).astype(np.uint8)
            glyph = (rng.random((th, tw)) < rng.choice([0, .1, .4, .8, 1])).astype(np.uint8)
            payload = json.dumps({"strip": self.pixels(source), "tmpl": self.pixels(glyph),
                                  "lo": -2, "hi": 3, "frac": .45})
            with self.subTest(index=index):
                self.assertEqual(json.loads(accelerated(payload)), json.loads(original(payload)))
        tied = json.dumps({"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                           "tmpl": self.pixels(np.ones((10, 3), dtype=np.uint8)),
                           "lo": -2, "hi": 3, "frac": .45})
        self.assertTrue(all(row["dy"] == -2 for row in json.loads(accelerated(tied))))

    def test_grayscale_opening_reflects_edges_including_singleton_axes(self):
        """Compare every output pixel, including even-k behavior of the original loops."""
        rng = np.random.default_rng(702)
        original, accelerated = self.original.get("opening"), self.accelerated.get("opening")
        for index in range(60):
            h, w = int(rng.integers(1, 25)), int(rng.integers(1, 36))
            source = rng.integers(0, 256, (h, w), dtype=np.uint8)
            payload = json.dumps({"sub": self.pixels(source), "k": int(rng.integers(1, 9))})
            with self.subTest(index=index):
                self.assertEqual(json.loads(accelerated(payload)), json.loads(original(payload)))

    def test_collection_preserves_template_key_order_scores_and_threshold_boundaries(self):
        """Compare complete collected candidates, including JS numeric-key ordering and ties."""
        rng = np.random.default_rng(310)
        original, accelerated = self.original.get("collection"), self.accelerated.get("collection")
        for index in range(50):
            source = (rng.random((24, 34)) < rng.choice([0, .2, .5, 1])).astype(np.uint8)
            templates = []
            for name in ("z", "7", "2", "a"):
                height, width = int(rng.integers(7, 16)), int(rng.integers(1, 12))
                glyph = (rng.random((height, width)) < rng.choice([0, .3, 1])).astype(np.uint8)
                templates.append({**self.pixels(glyph), "ch": name})
            payload = json.dumps({"strip": self.pixels(source), "templates": templates,
                                  "lo": -2, "hi": 3, "frac": .45,
                                  "thresh": float(rng.choice([0, .45, .76, 1]))})
            with self.subTest(index=index):
                self.assertEqual(json.loads(accelerated(payload)), json.loads(original(payload)))
        payload = json.dumps({"strip": self.pixels(np.ones((24, 5), dtype=np.uint8)),
                              "templates": [{**self.pixels(np.ones((10, 3), dtype=np.uint8)), "ch": name}
                                            for name in ("z", "7", "2", "a")],
                              "lo": -2, "hi": 3, "frac": .45, "thresh": 1})
        result = json.loads(accelerated(payload))
        self.assertEqual(result, json.loads(original(payload)))
        self.assertEqual([row["ch"] for row in result[::3]], ["2", "7", "z", "a"])
        with patch.object(digit_kernels, "collect_matches", wraps=digit_kernels.collect_matches) as kernel:
            self.assertEqual(json.loads(accelerated(payload)), result)
        kernel.assert_called_once()

    def test_collection_failure_and_unsupported_inputs_retain_original_collection(self):
        """Do not lose candidates if batched matching is unavailable or too large."""
        value = {"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                 "templates": [{**self.pixels(np.ones((10, 3), dtype=np.uint8)), "ch": "7"}],
                 "lo": -2, "hi": 3, "frac": .45, "thresh": .76}
        payload = json.dumps(value)
        with patch.object(digit_kernels, "collect_matches", side_effect=RuntimeError("native unavailable")):
            self.assertEqual(self.accelerated.get("collection")(payload), self.original.get("collection")(payload))
        value["strip"] = self.pixels(np.ones((24, 130), dtype=np.uint8))
        payload = json.dumps(value)
        self.assertEqual(self.accelerated.get("collection")(payload), self.original.get("collection")(payload))

    def test_collection_cache_preserves_template_order_and_filter_metadata(self):
        """Reuse only exact arithmetic inputs, with ordering and score gates in the key."""
        digit_kernels._cached_native_json.cache_clear()
        self.addCleanup(digit_kernels._cached_native_json.cache_clear)
        value = {"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                 "templates": [{**self.pixels(np.ones((10, 3), dtype=np.uint8)), "ch": name}
                               for name in ("a", "b")],
                 "lo": -2, "hi": 3, "frac": .45, "thresh": .76}
        with patch.object(digit_kernels, "collect_matches", wraps=digit_kernels.collect_matches) as kernel:
            first = digit_kernels._native_json(json.dumps(value), "collect")
            self.assertIs(digit_kernels._native_json(json.dumps(value), "collect"), first)
            kernel.assert_called_once()
            value["templates"].reverse()
            reordered = digit_kernels._native_json(json.dumps(value), "collect")
            self.assertNotEqual(first, reordered)
            value["thresh"] = 1
            self.assertIsNotNone(digit_kernels._native_json(json.dumps(value), "collect"))
            value["templates"][0]["data"][0] = 0
            self.assertIsNotNone(digit_kernels._native_json(json.dumps(value), "collect"))
            self.assertEqual(kernel.call_count, 4)

    def test_unknown_vendor_revision_is_not_instrumented(self):
        """An unrecognized file must retain the upstream implementation unchanged."""
        changed = self.source + "\n// different upstream revision\n"
        context = unittest.mock.Mock()
        self.assertEqual(digit_kernels.source_with_native_kernels(context, changed), changed)
        context.add_callable.assert_not_called()

    def test_verified_source_calls_native_kernels_without_changing_the_vendor_file(self):
        """Prove differential success exercises the hooks rather than only JS fallback."""
        self.assertEqual(hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
                         digit_kernels.SOURCE_SHA256)
        source = np.ones((24, 10), dtype=np.uint8)
        payload = json.dumps({"strip": self.pixels(source),
                              "tmpl": self.pixels(np.ones((10, 3), dtype=np.uint8)),
                              "lo": -2, "hi": 3, "frac": .45})
        with patch.object(digit_kernels, "slide_match", wraps=digit_kernels.slide_match) as kernel:
            self.accelerated.get("slide")(payload)
        kernel.assert_called_once()
        self.assertIsNotNone(digit_kernels._native_json(payload, "slide"))
        payload = json.dumps({"sub": self.pixels(source * 127), "k": 5})
        with patch.object(digit_kernels, "grey_opening", wraps=digit_kernels.grey_opening) as kernel:
            self.accelerated.get("opening")(payload)
        kernel.assert_called_once()
        self.assertIsNotNone(digit_kernels._native_json(payload, "opening"))
        self.assertEqual((currency_ocr.ROOT / "digit-reader.js").read_text(encoding="utf-8"), self.source)

    def test_arithmetic_cache_keys_all_inputs_and_current_kernel_and_is_bounded(self):
        """Repeated threshold strips share arithmetic without sharing a count decision."""
        digit_kernels._cached_native_json.cache_clear()
        self.addCleanup(digit_kernels._cached_native_json.cache_clear)
        value = {"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                 "tmpl": self.pixels(np.ones((10, 3), dtype=np.uint8)),
                 "lo": -2, "hi": 3, "frac": .45}
        payload = json.dumps(value)
        with patch.object(digit_kernels, "slide_match", wraps=digit_kernels.slide_match) as kernel:
            first = digit_kernels._native_json(payload, "slide")
            self.assertIs(digit_kernels._native_json(payload, "slide"), first)
            kernel.assert_called_once()
            for field, changed in (("lo", -1), ("hi", 2), ("frac", .55)):
                alternate = {**value, field: changed}
                self.assertIsNotNone(digit_kernels._native_json(json.dumps(alternate), "slide"))
            alternate = json.loads(payload)
            alternate["strip"]["data"][0] = 0
            self.assertIsNotNone(digit_kernels._native_json(json.dumps(alternate), "slide"))
            alternate = json.loads(payload)
            alternate["tmpl"]["data"][0] = 0
            self.assertIsNotNone(digit_kernels._native_json(json.dumps(alternate), "slide"))
            self.assertEqual(kernel.call_count, 6)
        # Replacing a kernel cannot keep a prior callable's cached result.
        with patch.object(digit_kernels, "slide_match", wraps=digit_kernels.slide_match) as replacement:
            self.assertEqual(digit_kernels._native_json(payload, "slide"), first)
            replacement.assert_called_once()
        for index in range(130):
            value["frac"] = index / 256
            self.assertIsNotNone(digit_kernels._native_json(json.dumps(value), "slide"))
        self.assertEqual(digit_kernels._cached_native_json.cache_info().currsize, 128)
        before = digit_kernels._cached_native_json.cache_info()
        unsupported = json.dumps({**value, "unused": "\u2603"}, ensure_ascii=False)
        self.assertIsNone(digit_kernels._native_json(unsupported, "slide"))
        self.assertIsNone(digit_kernels._native_json("x" * 25001, "slide"))
        self.assertEqual(digit_kernels._cached_native_json.cache_info(), before)

    def test_transient_kernel_failure_is_not_cached(self):
        """A native failure falls back now without disabling later valid arithmetic."""
        digit_kernels._cached_native_json.cache_clear()
        self.addCleanup(digit_kernels._cached_native_json.cache_clear)
        value = {"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                 "tmpl": self.pixels(np.ones((10, 3), dtype=np.uint8)),
                 "lo": -2, "hi": 3, "frac": .45}
        expected = digit_kernels.slide_match(value["strip"], value["tmpl"], -2, 3, .45)
        with patch.object(digit_kernels, "slide_match", side_effect=[RuntimeError("transient"), expected]) as kernel:
            payload = json.dumps(value)
            self.assertIsNone(digit_kernels._native_json(payload, "slide"))
            self.assertEqual(json.loads(digit_kernels._native_json(payload, "slide")), expected)
            self.assertEqual(json.loads(digit_kernels._native_json(payload, "slide")), expected)
            self.assertEqual(kernel.call_count, 2)

    def test_unsupported_native_input_and_kernel_failure_use_original_functions(self):
        """Unsupported planes and native errors retain JS fallback, rather than dropping evidence."""
        payload = json.dumps({"strip": self.pixels(np.ones((24, 10), dtype=np.uint8)),
                              "tmpl": self.pixels(np.ones((10, 3), dtype=np.uint8)),
                              "lo": -2, "hi": 3, "frac": .45})
        expected = self.original.get("slide")(payload)
        with patch.object(digit_kernels, "slide_match", side_effect=RuntimeError("native unavailable")):
            self.assertEqual(self.accelerated.get("slide")(payload), expected)
        wide = np.ones((24, 130), dtype=np.uint8)
        unsupported = json.loads(payload)
        unsupported["strip"] = self.pixels(wide)
        unsupported = json.dumps(unsupported)
        self.assertEqual(self.accelerated.get("slide")(unsupported), self.original.get("slide")(unsupported))
        image = json.dumps({"sub": self.pixels(np.arange(80, dtype=np.uint8).reshape(8, 10)), "k": 5})
        with patch.object(digit_kernels, "grey_opening", side_effect=RuntimeError("native unavailable")):
            self.assertEqual(self.accelerated.get("opening")(image), self.original.get("opening")(image))
        for malformed in ("{", "null", "[]", "{}", "x" * 25001):
            self.assertIsNone(digit_kernels._native_json(malformed, "slide"))

    def cell(self, family, number, scale=1, shade=255):
        """Render recorded glyph shapes over bundled artwork at several capture sizes."""
        cell = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
        cell.alpha_composite(self.artwork[family])
        mask = Image.new("L", cell.size)
        x, baseline = 2, 15
        for digit in str(number):
            glyph = self.glyphs[digit]
            ink = np.asarray(glyph["data"], dtype=np.uint8).reshape(glyph["h"], glyph["w"])
            mask.paste(Image.fromarray(ink * 255), (x, baseline - glyph["h"]))
            x += glyph["w"] + 1
        cell.paste((0, 0, 0), mask=mask.filter(ImageFilter.MaxFilter(3)))
        cell.paste((shade, shade, shade), mask=mask)
        extent = round(54 * scale)
        return cell.convert("RGB").resize((extent, extent), Image.Resampling.LANCZOS)

    @staticmethod
    def payload(cell):
        """Pass the same bounded pixel size and anchors as the production count adapter."""
        cell = cell.copy()
        cell.thumbnail((96, 96), Image.Resampling.LANCZOS)
        return json.dumps({**currency_ocr.CurrencyReader._pixels(cell),
                           "cx": int(cell.width * .27), "cy": int(cell.height * .25),
                           "scale": max(.5, min(cell.size) / 40)}, separators=(",", ":"))

    def test_production_count_sweep_matches_original_for_rendered_artwork_and_scales(self):
        """Preserve complete text/confidence/winning floor for 7/74 and other counts."""
        original = self.original.get("trial")
        cases = [(family, number, scale, shade)
                 for family in self.artwork for number in (1, 7, 10, 14, 74, 783)
                 for scale in (.75, 1, 2) for shade in (170, 255)]
        for family, number, scale, shade in cases:
            payload = self.payload(self.cell(family, number, scale, shade))
            with self.subTest(family=family, number=number, scale=scale, shade=shade):
                self.assertEqual(json.loads(self.production.read_count(payload)), json.loads(original(payload)))
        # Real 74 remains a valid count; no number-specific cap or 7 preference.
        for number in (7, 74):
            cell = self.cell("divine-orb", number)
            self.assertEqual(self.production.count(cell), number)


if __name__ == "__main__":
    unittest.main()
