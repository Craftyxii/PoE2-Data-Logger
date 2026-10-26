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
        """Prepare read-only channel FFT blocks using OpenCV's 40x40 template geometry."""
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
        """Prepare source and squared-source transforms shared by masked RGB SSD queries."""
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
        """Build source FFTs and fixed-mask normalization once for grayscale query correlation."""
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


class _ShiftRanking:
    """Rank 48x48 atlas candidates over the 9x9 translations the inventory ranking reads.

    The masked RGB squared difference at one translation is
    sum(mask^2 * source^2) - 2 * sum(mask^2 * query * source) + norm. A float32
    matrix product estimates it for every candidate and translation, with a
    proven error bound; candidates that could rank among the best families are
    then computed exactly as OpenCV does (float64 sums rounded to float32 per
    channel, float32 channel sums, addWeighted), so their values are the same.
    """

    SHIFTS = tuple((dy, dx) for dy in range(9) for dx in range(9))
    # Rounding bound of a float32 dot product over 6912 nonnegative terms, with margin.
    GAMMA = 4 * 6912 * 2.0 ** -24 * 1.01

    def __init__(self, source):
        """Lay out each candidate's pixels and squared pixels as rows, channel by channel."""
        count = source.shape[0] // 48
        blocks = np.moveaxis(source.reshape(count, 48, 48, 3), 3, 1)
        squares = np.moveaxis(np.multiply(source, source).reshape(count, 48, 48, 3), 3, 1)
        self.values = np.ascontiguousarray(blocks.reshape(count, 3 * 2304))
        self.squares = np.ascontiguousarray(squares.reshape(count, 3 * 2304))
        self.square_sums = np.ascontiguousarray(
            squares.astype(np.float64).sum(axis=1).reshape(count, 2304).astype(np.float32))
        self.count = count
        self.nbytes = self.values.nbytes + self.squares.nbytes + self.square_sums.nbytes

    @classmethod
    def _kernels(cls, plane):
        """Place a 40x40 plane at each translation of a 48x48 block, one row per translation."""
        out = np.zeros((len(cls.SHIFTS), 48, 48), dtype=plane.dtype)
        for index, (dy, dx) in enumerate(cls.SHIFTS):
            out[index, dy:dy + 40, dx:dx + 40] = plane
        return out.reshape(len(cls.SHIFTS), 2304)

    def _exact(self, rows, template, squared_mask, norm):
        """Masked squared differences of the chosen candidates in OpenCV's arithmetic order."""
        mask_kernel = self._kernels(squared_mask).astype(np.float64)
        cross = squared = None
        for channel in range(3):
            columns = slice(channel * 2304, (channel + 1) * 2304)
            plane = self._kernels(template[:, :, channel]).astype(np.float64)
            products = (self.values[rows, columns].astype(np.float64) @ plane.T).astype(np.float32)
            powers = (self.squares[rows, columns].astype(np.float64) @ mask_kernel.T).astype(np.float32)
            cross = products if cross is None else cv2.add(cross, products)
            squared = powers if squared is None else cv2.add(squared, powers)
        return cv2.addWeighted(cross, -2, squared, 1, norm)

    def minima(self, query, mask, families, keep=8):
        """Return each candidate's least error over the translations.

        Every candidate that can affect the best ``keep`` families gets OpenCV's
        exact value; any other gets a lower bound above all of theirs.
        """
        repeated = np.repeat(mask[:, :, None], 3, axis=2)
        squared_mask = np.multiply(mask, mask)
        norm = cv2.norm(np.multiply(query, repeated), cv2.NORM_L2SQR)
        template = np.multiply(query, squared_mask[:, :, None])
        planes = np.concatenate([self._kernels(template[:, :, channel]) for channel in range(3)], axis=1)
        cross = (self.values @ planes.T).astype(np.float64)
        squared = (self.square_sums @ self._kernels(squared_mask).T).astype(np.float64)
        bound = self.GAMMA * (squared + 2 * cross) + 1 + 1e-5 * norm
        lower = (squared - 2 * cross + norm - bound).min(axis=1)
        order = np.argsort(lower, kind="stable")
        minima = lower.copy()
        best = {}
        for start in range(0, self.count, 16):
            rows = order[start:start + 16]
            if len(best) >= keep:
                limit = sorted(best.values())[keep - 1]
                rows = rows[lower[rows] <= limit]
                if not len(rows):
                    break
            values = self._exact(rows, template, squared_mask, norm).min(axis=1)
            for row, value in zip(rows, values):
                minima[row] = value
                best[families[row]] = min(best.get(families[row], np.inf), float(value))
        return minima


_SHIFT_BANKS = {}


def shift_minima(source, query, mask, families, *, keep=8):
    """Least masked RGB error per 48-row atlas candidate over its 9x9 translations.

    The same values as ``match(...)[indices].min()`` for every candidate that can
    rank among the best ``keep`` families. Returns None when the inputs are not
    the immutable atlas and finite float32 query this was proven for.
    """
    if not _eligible(source, query, mask, cv2.TM_SQDIFF) or len(families) != source.shape[0] // 48:
        return None
    with _LOCK:
        for key in [key for key, (reference, _) in _SHIFT_BANKS.items() if reference() is None]:
            del _SHIFT_BANKS[key]
        entry = _SHIFT_BANKS.get(id(source))
        if entry is None or entry[0]() is not source:
            entry = (weakref.ref(source), _ShiftRanking(source))
            _SHIFT_BANKS[id(source)] = entry
        bank = entry[1]
    return bank.minima(query, mask, families, keep)


def prepare_shifts(source):
    """Build the translation bank for an atlas at startup."""
    mask = np.ones((40, 40), np.float32)
    if _eligible_source(source, mask, cv2.TM_SQDIFF):
        shift_minima(source, np.full((40, 40, 3), 26, np.float32), mask,
                     tuple(range(source.shape[0] // 48)))


def _eligible_source(source, mask, method):
    """Restrict preparation to immutable catalog atlases and the pinned CPU kernel."""
    if (cv2.__version__ != "4.10.0" or not isinstance(source, np.ndarray) or
            not isinstance(mask, np.ndarray) or source.ndim not in (2, 3) or
            source.dtype != np.float32 or source.flags.writeable or
            not source.flags.c_contiguous or source.shape[0] < 48 or
            source.shape[0] % 48 or source.shape[1] != 48 or
            mask.shape != (40, 40) or not np.isfinite(mask).all()):
        return False
    if method == cv2.TM_SQDIFF:
        return (source.ndim == 3 and source.shape[2] == 3 and
                mask.dtype == np.float32 and float(mask.min()) >= 0 and
                float(mask.max()) <= 1)
    return (method == cv2.TM_CCOEFF_NORMED and source.ndim == 2 and
            mask.dtype in (np.dtype("uint8"), np.dtype("float32")) and bool(np.any(mask)) and
            bool(np.all((mask == 0) | (mask == 1))))


def _eligible(source, query, mask, method):
    """Check query constraints separately so startup never needs a synthetic query."""
    return (_eligible_source(source, mask, method) and isinstance(query, np.ndarray) and
            query.dtype == np.float32 and np.isfinite(query).all() and
            query.shape == ((40, 40, 3) if method == cv2.TM_SQDIFF else (40, 40)))


def _key(source, mask, method):
    """Share source, mask and backend identity between preparation and real matching."""
    backend = (cv2.useOptimized(), cv2.ipp.useIPP(), cv2.ipp.useIPP_NotExact(),
               cv2.getNumThreads())
    return (id(source), method, mask.tobytes() if method == cv2.TM_CCOEFF_NORMED else b"",
            backend)


def _prepared(source, mask, method, key):
    """Find or build the existing bounded bank while the caller holds _LOCK."""
    for previous in [old for old, (reference, _) in _CACHE.items() if reference() is None]:
        del _CACHE[previous]
    entry = _CACHE.get(key)
    if entry is not None and entry[0]() is source:
        _CACHE.move_to_end(key)
        return entry[1]
    prepared = None
    # Reject oversized banks before allocating their FFTs. Include both
    # squared and RGB channel banks; gray construction temporarily needs
    # a second bank, discarded after the invariant normalizer is ready.
    rows = source.shape[0] - 39
    blocks = (rows + 216) // 217
    estimate = blocks * 256 * 48 * 8 * (6 if source.ndim == 3 else 2)
    # Reserve squared pixels, correlation outputs and construction spectra
    # without evicting the RGB/gray pair for gray's temporary constructor bank.
    workspace = estimate + 2 * source.nbytes + 5 * rows * 9 * 4
    while (_CACHE and workspace + sum(bank.nbytes for _, bank in _CACHE.values()
                                      if bank) > _MAX_BUILD_BYTES):
        _CACHE.popitem(last=False)
    if (estimate <= _MAX_BYTES and workspace <= _MAX_BUILD_BYTES and
            np.isfinite(source).all()):
        try:
            prepared = (_SquaredDifference if method == cv2.TM_SQDIFF else
                        _GrayCorrelation)(source, mask)
            # A reference-only build cannot prove the query arithmetic. Leave
            # the original whole-response check for the first actual reading.
            prepared.verified = False
        except (cv2.error, MemoryError):
            prepared = None
    _CACHE[key] = (weakref.ref(source), prepared)
    while (len(_CACHE) > _MAX_BANKS or
           sum(bank.nbytes for _, bank in _CACHE.values() if bank) > _MAX_BYTES):
        _CACHE.popitem(last=False)
    return prepared


def prepare(source, method, *, mask):
    """Prepare reference-only arithmetic in the same bounded bank used by match.

    Return whether the bank is available. No query or recognition result is
    created or retained; the first real match still proves exact backend parity.
    Mutable/unsupported sources and allocation limits simply leave OpenCV active.
    """
    if not _eligible_source(source, mask, method):
        return False
    key = _key(source, mask, method)
    with _LOCK:
        prepared = _prepared(source, mask, method, key)
        return prepared is not None and _CACHE.get(key, (None, None))[1] is prepared


def match(source, query, method, *, mask):
    """Use bounded shared reference arithmetic, falling back to original OpenCV.

    No scan image or recognition decision is retained here. Weak source identity
    prevents stale reuse after atlas replacement. The first actual query checks
    the whole response against OpenCV, even when startup prepared the reference.
    """
    if not _eligible(source, query, mask, method):
        return cv2.matchTemplate(source, query, method, mask=mask)
    key = _key(source, mask, method)
    with _LOCK:
        prepared = _prepared(source, mask, method, key)
        if prepared is not None and not prepared.verified:
            expected = None
            try:
                actual = prepared.run(query, mask)
                expected = cv2.matchTemplate(source, query, method, mask=mask)
                if np.array_equal(actual, expected, equal_nan=True):
                    prepared.verified = True
                else:
                    prepared = None
            except (cv2.error, MemoryError):
                prepared = None
            if prepared is None:
                _CACHE[key] = (weakref.ref(source), None)
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
        del prepared
        return cv2.matchTemplate(source, query, method, mask=mask)
