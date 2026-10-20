"""Reuse immutable artwork transforms in the pinned OpenCV masked matchers.

Adapted from OpenCV 4.10.0's crossCorr/matchTemplateMask numerical order:
https://github.com/opencv/opencv/blob/4.10.0/modules/imgproc/src/templmatch.cpp
Copyright (C) 2000, Intel Corporation. The complete notice and provenance are
bundled in third_party/opencv_matching. No candidates or translations are pruned.
"""
from collections import OrderedDict
import threading
import weakref

import cv2
import numpy as np


_CACHE = OrderedDict()
_LOCK = threading.RLock()
_MAX_BYTES = 160 * 1024 * 1024
_MAX_BUILD_BYTES = 320 * 1024 * 1024
_MAX_BANKS = 2


class _Correlation:
    """Cache reference FFTs, preserving OpenCV's block sizes and channel rounding."""

    def __init__(self, source):
        height, width = source.shape[:2]
        self.channels = 1 if source.ndim == 2 else source.shape[2]
        self.shape = height - 39, width - 39
        # For the 48-wide padded atlases and 40-wide templates, crossCorr's
        # minBlockSize=256 and blockScale=4.5 give these exact block dimensions.
        self.dh = cv2.getOptimalDFTSize(min(217, self.shape[0]) + 39)
        self.dw = cv2.getOptimalDFTSize(self.shape[1] + 39)
        self.bh = min(self.dh - 39, self.shape[0])
        self.blocks = []
        for y in range(0, self.shape[0], self.bh):
            height = min(self.bh, self.shape[0] - y)
            spectra = []
            for channel in range(self.channels):
                plane = source if self.channels == 1 else source[:, :, channel]
                data = np.zeros((self.dh, self.dw), dtype=np.float64)
                data[:height + 39, :48] = plane[y:y + height + 39]
                cv2.dft(data, data, nonzeroRows=height + 39)
                data.flags.writeable = False
                spectra.append(data)
            self.blocks.append((y, height, tuple(spectra)))
        self.nbytes = sum(data.nbytes for _, _, planes in self.blocks for data in planes)

    def run(self, template):
        """Correlate using cached source transforms and fresh query transforms."""
        channels = 1 if template.ndim == 2 else template.shape[2]
        query = []
        for channel in range(channels):
            data = np.zeros((self.dh, self.dw), dtype=np.float64)
            data[:40, :40] = template if channels == 1 else template[:, :, channel]
            cv2.dft(data, data, nonzeroRows=40)
            query.append(data)
        result = np.empty(self.shape, dtype=np.float32)
        for y, height, spectra in self.blocks:
            target = result[y:y + height]
            for channel, spectrum in enumerate(spectra):
                product = cv2.mulSpectrums(spectrum, query[channel if channels > 1 else 0],
                                          0, conjB=True)
                cv2.dft(product, product, flags=cv2.DFT_INVERSE | cv2.DFT_SCALE,
                        nonzeroRows=height)
                # crossCorr converts each channel before adding it to the result.
                plane = product[:height, :self.shape[1]].astype(np.float32)
                if channel == 0:
                    target[:] = plane
                else:
                    cv2.add(target, plane, target)
        return result


class _SquaredDifference:
    """Reuse reference and squared-reference FFTs for RGB weighted SSD."""

    def __init__(self, source, _mask):
        self.source = _Correlation(source)
        self.squared = _Correlation(np.multiply(source, source))
        self.nbytes = self.source.nbytes + self.squared.nbytes

    def run(self, query, mask):
        """Keep the masked float32 products and final OpenCV expression order."""
        repeated = np.repeat(mask[:, :, None], 3, axis=2)
        squared_mask = np.multiply(mask, mask)
        norm = cv2.norm(np.multiply(query, repeated), cv2.NORM_L2SQR)
        squared = self.squared.run(squared_mask)
        cross = self.source.run(np.multiply(query, squared_mask[:, :, None]))
        return cv2.addWeighted(cross, -2, squared, 1, norm)


class _GrayCorrelation:
    """Reuse reference-only correlations and normalization for the fixed gray mask."""

    def __init__(self, source, mask):
        self.source = _Correlation(source)
        self.mask = mask.astype(np.float32, copy=True)
        self.mask.flags.writeable = False
        self.total = float(cv2.sumElems(self.mask)[0])
        self.image_mask = self.source.run(self.mask)
        # This route accepts only a binary mask: M*M == M. Both correlations
        # in OpenCV's normalizer are therefore exactly the same float32 array.
        squared = _Correlation(np.multiply(source, source)).run(self.mask)
        scaled = cv2.multiply(self.image_mask, 1 / self.total)
        adjusted = cv2.scaleAdd(self.image_mask, -2, self.image_mask)
        self.normalizer = cv2.sqrt(cv2.add(squared, cv2.multiply(scaled, adjusted)))
        self.image_mask.flags.writeable = self.normalizer.flags.writeable = False
        self.nbytes = (self.source.nbytes + self.mask.nbytes + self.image_mask.nbytes +
                       self.normalizer.nbytes)

    def run(self, query, _mask):
        """Recompute all query-dependent terms without changing normalization rounding."""
        mean = float(cv2.sumElems(cv2.multiply(self.mask, query))[0]) / self.total
        centered = cv2.subtract(query, mean)
        masked = cv2.multiply(self.mask, centered)
        template = cv2.multiply(self.mask, masked)
        result = self.source.run(template)
        correction = cv2.multiply(self.image_mask,
                                  float(cv2.sumElems(template)[0]) / self.total)
        result = cv2.subtract(result, correction)
        norm = cv2.norm(masked, cv2.NORM_L2)
        # MatExpr materializes norm_imgx * norm_templx with convertTo: its
        # float32 scale is intentional. cv2.multiply(array, double) differs.
        return cv2.divide(result, self.normalizer * np.float32(norm))


def _eligible(source, query, mask, method):
    """Restrict reuse to immutable, finite catalog atlases and the pinned CPU kernel."""
    if (cv2.__version__ != "4.10.0" or not isinstance(source, np.ndarray) or
            not isinstance(query, np.ndarray) or not isinstance(mask, np.ndarray) or
            source.ndim not in (2, 3) or source.dtype != np.float32 or source.flags.writeable or
            not source.flags.c_contiguous or source.shape[0] < 48 or
            source.shape[0] % 48 or source.shape[1] != 48 or
            query.dtype != np.float32 or mask.shape != (40, 40) or
            not np.isfinite(query).all() or not np.isfinite(mask).all()):
        return False
    if method == cv2.TM_SQDIFF:
        return (source.ndim == 3 and source.shape[2] == 3 and query.shape == (40, 40, 3)
                and mask.dtype == np.float32 and float(mask.min()) >= 0
                and float(mask.max()) <= 1)
    return (method == cv2.TM_CCOEFF_NORMED and source.ndim == 2 and
            mask.dtype in (np.dtype("uint8"), np.dtype("float32")) and
            query.shape == (40, 40) and bool(np.any(mask)) and
            bool(np.all((mask == 0) | (mask == 1))))


def match(source, query, method, *, mask):
    """Use bounded shared reference arithmetic, falling back to original OpenCV.

    No scan image or recognition decision is retained here. Weak source identity
    prevents stale reuse after atlas replacement. The first call checks the whole
    response against OpenCV on this backend, and returns that original response.
    """
    if not _eligible(source, query, mask, method):
        return cv2.matchTemplate(source, query, method, mask=mask)
    backend = (cv2.useOptimized(), cv2.ipp.useIPP(), cv2.ipp.useIPP_NotExact(),
               cv2.getNumThreads())
    key = (id(source), method, mask.tobytes() if method == cv2.TM_CCOEFF_NORMED else b"",
           backend)
    with _LOCK:
        for previous in [old for old, (reference, _) in _CACHE.items() if reference() is None]:
            del _CACHE[previous]
        entry = _CACHE.get(key)
        if entry is not None and entry[0]() is source:
            _CACHE.move_to_end(key)
            prepared = entry[1]
        else:
            prepared = None
            expected = None
            # Reject oversized banks before allocating their FFTs. Include both
            # squared and RGB channel banks; gray construction temporarily needs
            # a second bank, discarded after the invariant normalizer is ready.
            rows = source.shape[0] - 39
            blocks = (rows + 216) // 217
            estimate = blocks * 256 * 48 * 8 * (6 if source.ndim == 3 else 2)
            # Reserve squared pixels, correlation outputs and construction
            # spectra before allocation, without evicting a normal RGB/gray
            # pair merely because the gray constructor needs a temporary bank.
            workspace = estimate + 2 * source.nbytes + 5 * rows * 9 * 4
            while (_CACHE and workspace + sum(bank.nbytes for _, bank in _CACHE.values()
                                              if bank) > _MAX_BUILD_BYTES):
                _CACHE.popitem(last=False)
            if (estimate <= _MAX_BYTES and workspace <= _MAX_BUILD_BYTES and
                    np.isfinite(source).all()):
                try:
                    prepared = (_SquaredDifference if method == cv2.TM_SQDIFF else
                                _GrayCorrelation)(source, mask)
                    actual = prepared.run(query, mask)
                    expected = cv2.matchTemplate(source, query, method, mask=mask)
                    if not np.array_equal(actual, expected, equal_nan=True):
                        prepared = None
                except (cv2.error, MemoryError):
                    prepared = None
            _CACHE[key] = (weakref.ref(source), prepared)
            while (len(_CACHE) > _MAX_BANKS or
                   sum(bank.nbytes for _, bank in _CACHE.values() if bank) > _MAX_BYTES):
                _CACHE.popitem(last=False)
            if expected is not None:
                return expected
    if prepared is None:
        return cv2.matchTemplate(source, query, method, mask=mask)
    try:
        return prepared.run(query, mask)
    except (cv2.error, MemoryError):
        # A failed warm computation must not consume the reading. Disable this
        # bank while allowing another active reader to finish its own buffers.
        with _LOCK:
            if _CACHE.get(key, (None, None))[1] is prepared:
                _CACHE[key] = (weakref.ref(source), None)
        entry = None
        del prepared
        return cv2.matchTemplate(source, query, method, mask=mask)
