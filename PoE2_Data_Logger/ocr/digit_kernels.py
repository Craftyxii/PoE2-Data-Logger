"""Accelerate pure arithmetic kernels of the pinned upstream digit reader.

The vendor file remains unchanged. Its threshold sweep, digit assembly,
gap filling and confidence decisions still execute in JavaScript. A different
vendor revision or an unsupported native input uses the original functions.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache

import cv2
import numpy as np
from scipy import ndimage


SOURCE_SHA256 = "9ade6207266aae5bac0f14c46a154694ed33e8f4ddd543c802218badca5ce5f0"
_SLIDE_HEADER = "  function slideMatch(strip, tmpl, dyLo, dyHi, minInkFrac) {"
_OPENING_HEADER = "  function greyOpening(sub, k) {"
_COLLECT_HEADER = "  function collect(strip, templates, thresh, P) {"

_SLIDE_HOOK = """
    try {
      const native = __poe2DigitSlide(JSON.stringify({
        strip: {data:Array.from(strip.data),w:strip.w,h:strip.h},
        tmpl: {data:Array.from(tmpl.data),w:tmpl.w,h:tmpl.h},
        lo:dyLo,hi:dyHi,frac:minInkFrac
      }));
      if (native !== null) return JSON.parse(native);
    } catch (_) { /* retain upstream matching on unsupported native inputs */ }
"""
_OPENING_HOOK = """
    try {
      const native = __poe2DigitOpening(JSON.stringify({
        sub:{data:Array.from(sub.data),w:sub.w,h:sub.h},k:k
      }));
      if (native !== null) {
        const result = JSON.parse(native);
        result.data = Uint8Array.from(result.data);
        return result;
      }
    } catch (_) { /* retain upstream opening on unsupported native inputs */ }
"""
_COLLECT_HOOK = """
    try {
      const native = __poe2DigitCollect(JSON.stringify({
        strip:{data:Array.from(strip.data),w:strip.w,h:strip.h},
        templates:Object.keys(templates).map(function(ch) {
          const t=templates[ch];
          return {ch:ch,data:Array.from(t.data),w:t.w,h:t.h};
        }),lo:P.dyLo,hi:P.dyHi,frac:P.minInkFrac,thresh:thresh
      }));
      if (native !== null) return JSON.parse(native);
    } catch (_) { /* retain upstream collection on unsupported native inputs */ }
"""


def _pixels(value, *, binary=False, template=False):
    """Validate bounded integer pixels before allocating native work arrays."""
    if not isinstance(value, dict):
        raise ValueError("Pixel input must be an object.")
    width, height, data = value.get("w"), value.get("h"), value.get("data")
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= (32 if template else 128) or
            not 1 <= height <= (32 if template else 64) or
            not isinstance(data, list) or len(data) != width * height):
        raise ValueError("Unsupported digit-kernel dimensions.")
    maximum = 1 if binary else 255
    if any(type(pixel) is not int or not 0 <= pixel <= maximum for pixel in data):
        raise ValueError("Unsupported digit-kernel pixels.")
    return np.asarray(data, dtype=np.uint8).reshape(height, width)


def _validate_search(dy_low, dy_high, minimum_ink):
    """Limit native search work without changing supported upstream arguments."""
    if (type(dy_low) is not int or type(dy_high) is not int or
            not -64 <= dy_low <= dy_high <= 64 or dy_high - dy_low > 32 or
            type(minimum_ink) not in (int, float) or
            not math.isfinite(minimum_ink) or not 0 <= minimum_ink <= 1):
        raise ValueError("Unsupported digit-kernel search.")


def _slide_arrays(source, glyph, dy_low, dy_high, minimum_ink):
    """Evaluate validated binary planes with exact integer IoU and first-dy ties."""
    height, width = source.shape
    glyph_height, glyph_width = glyph.shape
    if glyph_width > width or glyph_height > height:
        return []
    offsets = np.arange(dy_low, dy_high + 1)
    starts = height // 2 + offsets - glyph_height // 2
    valid = (starts >= 0) & (starts + glyph_height <= height)
    offsets, starts = offsets[valid], starts[valid]
    if not len(offsets):
        return []
    # Validated binary pixels keep the integral below 8192 and each window
    # sum/intersection at most 1024. Integer-valued products and sums are also
    # exact in ndimage's double accumulator; int32 output avoids uint8 overflow.
    # Unlike FFT correlation this needs no rounding tolerance or score changes.
    integral = cv2.integral(source, sdepth=cv2.CV_32S)
    ys = starts[:, None]
    xs = np.arange(width - glyph_width + 1)[None, :]
    ink = (integral[ys + glyph_height, xs + glyph_width] -
           integral[ys, xs + glyph_width] - integral[ys + glyph_height, xs] +
           integral[ys, xs])
    glyph_ink = int(glyph.sum())
    correlated = ndimage.correlate(source, glyph, output=np.int32)
    # The default center is floor(size/2), including even-sized glyphs. Only
    # complete windows are sampled, so correlate's boundary mode has no effect.
    intersection = correlated[ys + glyph_height // 2, xs + glyph_width // 2]
    union = ink + glyph_ink - intersection
    scores = np.divide(intersection, union, out=np.zeros_like(intersection, dtype=np.float64),
                       where=union > 0)
    scores[ink < minimum_ink * glyph_ink] = 0
    # argmax returns the first maximum, matching the upstream strict > update
    # while offsets and output x positions retain their ascending order.
    best = scores.argmax(axis=0)
    return [{"x": x, "dy": int(offsets[best[x]]), "score": float(scores[best[x], x])}
            for x in range(width - glyph_width + 1) if scores[best[x], x] > 0]


def slide_match(strip, template, dy_low, dy_high, minimum_ink):
    """Match binary windows with exact integer IoU and upstream first-dy ties."""
    source = _pixels(strip, binary=True)
    glyph = _pixels(template, binary=True, template=True)
    _validate_search(dy_low, dy_high, minimum_ink)
    return _slide_arrays(source, glyph, dy_low, dy_high, minimum_ink)


def collect_matches(strip, templates, dy_low, dy_high, minimum_ink, threshold):
    """Batch pure matching while retaining JS template/x order and score filtering."""
    source = _pixels(strip, binary=True)
    _validate_search(dy_low, dy_high, minimum_ink)
    if (not isinstance(templates, list) or len(templates) > 64 or
            type(threshold) not in (int, float) or not math.isfinite(threshold) or
            not 0 <= threshold <= 1):
        raise ValueError("Unsupported digit-kernel collection.")
    result = []
    for template in templates:
        if (not isinstance(template, dict) or not isinstance(template.get("ch"), str) or
                not 1 <= len(template["ch"]) <= 32):
            raise ValueError("Unsupported digit-kernel template name.")
        glyph = _pixels(template, binary=True, template=True)
        for match in _slide_arrays(source, glyph, dy_low, dy_high, minimum_ink):
            if match["score"] >= threshold:
                result.append({"x": match["x"], "ch": template["ch"],
                               "score": match["score"], "tw": template["w"]})
    return result


def grey_opening(sub, size):
    """Use integer erosion/dilation with the upstream edge-repeating reflection."""
    source = _pixels(sub)
    if type(size) is not int or not 1 <= size <= 15:
        raise ValueError("Unsupported digit-kernel opening.")
    # The JS implementation loops -floor(k/2)..floor(k/2), even for even k.
    extent = 2 * (size // 2) + 1
    kernel = np.ones((extent, extent), dtype=np.uint8)
    result = cv2.dilate(cv2.erode(source, kernel, borderType=cv2.BORDER_REFLECT),
                        kernel, borderType=cv2.BORDER_REFLECT)
    return {"data": result.ravel().tolist(), "w": sub["w"], "h": sub["h"]}


@lru_cache(maxsize=128)
def _cached_native_json(payload, operation, kernel):
    """Reuse exact pure-kernel results, including the current callable's identity.

    The same binary strip often survives adjacent thresholds. Retaining only
    its immutable arithmetic result leaves the original threshold votes and
    count decisions independent. Successful inputs/outputs are at most 25/35
    thousand ASCII bytes, bounding retained strings below eight MiB.
    """
    value = json.loads(payload)
    if operation == "slide":
        result = kernel(value["strip"], value["tmpl"], value["lo"], value["hi"], value["frac"])
    elif operation == "collect":
        result = kernel(value["strip"], value["templates"], value["lo"], value["hi"],
                        value["frac"], value["thresh"])
    else:
        result = kernel(value["sub"], value["k"])
    encoded = json.dumps(result, separators=(",", ":"), allow_nan=False)
    if len(encoded) > 35000 or not encoded.isascii():
        raise ValueError("Unsupported digit-kernel result size.")
    return encoded


def _native_json(payload, operation):
    """Fall back to JavaScript for malformed inputs or native execution failures."""
    try:
        if not isinstance(payload, str) or len(payload) > 25000 or not payload.isascii():
            return None
        kernel = {"slide": slide_match, "collect": collect_matches, "opening": grey_opening}.get(operation)
        return None if kernel is None else _cached_native_json(payload, operation, kernel)
    except Exception:
        # Keeping the original kernel available is deliberate: native failure
        # must never manufacture or discard a count reading.
        return None


def source_with_native_kernels(context, source):
    """Hook only the verified vendor revision; unknown source executes unchanged."""
    if (hashlib.sha256(source.encode("utf-8")).hexdigest() != SOURCE_SHA256 or
            source.count(_SLIDE_HEADER) != 1 or source.count(_OPENING_HEADER) != 1 or
            source.count(_COLLECT_HEADER) != 1):
        return source
    try:
        context.add_callable("__poe2DigitSlide", lambda payload: _native_json(payload, "slide"))
        context.add_callable("__poe2DigitOpening", lambda payload: _native_json(payload, "opening"))
        context.add_callable("__poe2DigitCollect", lambda payload: _native_json(payload, "collect"))
    except Exception:
        return source
    return source.replace(_SLIDE_HEADER, _SLIDE_HEADER + _SLIDE_HOOK).replace(
        _OPENING_HEADER, _OPENING_HEADER + _OPENING_HOOK).replace(
        _COLLECT_HEADER, _COLLECT_HEADER + _COLLECT_HOOK)
