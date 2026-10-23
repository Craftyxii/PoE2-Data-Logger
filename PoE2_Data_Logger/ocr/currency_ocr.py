"""Use POE2-VibeTools' offline currency icon and count readers verbatim.

The JavaScript is loaded in an embedded QuickJS context, without Electron, a
browser, a local server, or any price/network calls. The inventory-grid crop
and review policy are logger adapters around the upstream recognizers.
"""
from __future__ import annotations

import json
import hashlib
import io
import re
import weakref
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import numpy as np
import cv2
import quickjs
from PIL import Image
from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr import artwork_cache, digit_kernels

ROOT = Path(__file__).resolve().parent.parent / "third_party" / "currency_overlay"
_READERS = threading.local()


@dataclass(frozen=True)
class _ExampleBank:
    """Keep prepared reference pixels, stable name order and the owning reader’s identity."""
    owner: object
    signature: bytes
    references: tuple
    order: object

    def __bool__(self):
        """Report whether the prepared bank contains any pixel references."""
        return bool(self.references)


def get_reader():
    """Reuse one CurrencyReader per worker thread so embedded JavaScript state is not shared."""
    if not hasattr(_READERS, "reader"):
        _READERS.reader = CurrencyReader()
    return _READERS.reader


def catalog_version():
    """Hash available icon/variant catalogs to invalidate catalog-dependent cached results."""
    digest = hashlib.sha256()
    for filename in ("currency-icons.json", "inventory-icons.json", "inventory-reference-variants.json"):
        path = ROOT / filename
        if path.exists():
            digest.update(filename.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def catalog_names():
    """List distinct names from non-ignored bundled currency and inventory families."""
    names = set()
    for filename in ("currency-icons.json", "inventory-icons.json"):
        path = ROOT / filename
        if path.exists():
            stat = path.stat()
            names.update(_catalog_file_names(str(path), stat.st_mtime_ns, stat.st_size))
    return sorted(names)


@lru_cache(maxsize=2)
def _catalog_file_names(path, modified, size):
    """Cache one catalog's member names; its timestamp and size invalidate the 10 MB parse."""
    return frozenset(name for item in json.loads(Path(path).read_text(encoding="utf-8"))["icons"]
                     if not item.get("ignored") for name in item["members"])


def omen_references():
    """Render cached Omen artwork over the inventory background as PNG reference bytes."""
    path = ROOT / "inventory-icons.json"
    stat = path.stat()
    return _omen_references(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _omen_references(path, modified, size):
    """Invalidate bundled Omen reference pixels with their source catalog."""
    references = []
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["icons"]
    for entry in entries:
        names = [name for name in entry["members"] if name.startswith("Omen of ")]
        if entry.get("ignored") or not names:
            continue
        art = Image.fromarray(np.asarray(entry["rgba"], dtype=np.uint8).reshape(40, 40, 4), "RGBA")
        background = Image.new("RGBA", art.size, (26, 26, 40, 255))
        background.alpha_composite(art)
        output = io.BytesIO()
        background.convert("RGB").save(output, format="PNG")
        references.extend({"name": name, "image": output.getvalue()} for name in names)
    return tuple(references)


@lru_cache(maxsize=2)
def _inventory_assets(path, modified, size):
    """Cache catalog members and padded icon candidates with a masked count corner.

    File timestamps and size participate in the cache key supplied by callers.
    """
    inventory = json.loads(Path(path).read_text(encoding="utf-8"))["icons"]
    candidates, rgba_bank = [], []
    for item in inventory:
        rgba = np.asarray(item["rgba"], dtype=np.float32).reshape(40, 40, 4)
        alpha = rgba[:, :, 3:4] / 255
        rgb = np.floor(rgba[:, :, :3] * alpha + np.array([26, 26, 40]) * (1 - alpha) + .5)
        rgb[:13, :13] = [26, 26, 40]
        candidate = cv2.copyMakeBorder(rgb.astype(np.float32), 4, 4, 4, 4, cv2.BORDER_REPLICATE)
        candidate.flags.writeable = False
        candidates.append((item["family"], candidate))
        rgba_bank.append({"family": item["family"], "rgba": item["rgba"]})
    return inventory, candidates, rgba_bank


def _inventory_candidate(rgba, scale=1.0):
    """Center scaled RGBA artwork on the 40×40 inventory backing and pad for alignment."""
    pixels = np.asarray(rgba, dtype=np.uint8).reshape(40, 40, 4)
    art = Image.fromarray(pixels, "RGBA")
    if scale != 1:
        width = round(40 * scale)
        art = art.resize((width, width), Image.Resampling.LANCZOS)
    background = Image.new("RGBA", (40, 40), (26, 26, 40, 255))
    background.alpha_composite(art, ((40 - art.width) // 2, (40 - art.height) // 2))
    rgb = np.asarray(background.convert("RGB"), dtype=np.float32).copy()
    result = cv2.copyMakeBorder(rgb, 4, 4, 4, 4, cv2.BORDER_REPLICATE)
    result.flags.writeable = False
    return result


@lru_cache(maxsize=2)
def _calibrated_inventory_assets(path, modified, size, variants_path, variants_modified):
    """Combine catalog artwork with allowed reference variants in one cached matching atlas."""
    entries = json.loads(Path(path).read_text(encoding="utf-8"))["icons"]
    candidates = [(entry["family"], _inventory_candidate(entry["rgba"])) for entry in entries]
    if variants_modified:
        allowed = {entry["family"] for entry in entries}
        for entry in json.loads(Path(variants_path).read_text(encoding="utf-8"))["examples"]:
            if entry["family"] in allowed:
                for scale in (.96, 1.0, 1.04):
                    candidates.append((entry["family"], _inventory_candidate(entry["rgba"], scale)))
    atlas = np.concatenate([candidate for _, candidate in candidates], axis=0)
    atlas.flags.writeable = False
    candidates = tuple((family, atlas[index * 48:(index + 1) * 48])
                       for index, (family, _) in enumerate(candidates))
    return candidates, {entry["family"]: entry["rgba"] for entry in entries}, atlas


def _inventory_query(image, calibrated, count_digits):
    """Normalize one exact masked artwork query and retain its existing cache identity."""
    rgb = np.asarray(image.convert("RGB").resize((40, 40), Image.Resampling.LANCZOS),
                     dtype=np.float32).copy()
    background = ((rgb.max(axis=2) < 28) |
                  ((rgb[:, :, 0] < 28) & (rgb[:, :, 1] < 28) & (rgb[:, :, 2] <= 45) &
                   (rgb[:, :, 2] >= rgb[:, :, 0]) & (rgb[:, :, 2] >= rgb[:, :, 1])))
    rgb[background] = [26, 26, 40]
    count_width = min(40, 6 + 7 * count_digits) if type(count_digits) is int and count_digits > 0 else 26
    if calibrated:
        rgb[:15, :count_width] = [26, 26, 40]
    else:
        rgb[:13, :13] = [26, 26, 40]
    rgb[27:, 27:] = [26, 26, 40]
    distances = np.square(rgb - [26, 26, 40]).sum(axis=2)
    weights = np.where(distances > 900, 1, np.where(distances > 144, .2, 0)).astype(np.float32)
    if calibrated:
        weights[:15, :count_width] = 0
    else:
        weights[:13, :13] = 0
    # The masked kernel ignores only zero-weight pixels, not the other colors.
    cache_key = calibrated, weights.tobytes(), rgb[weights > 0].tobytes()
    return rgb, weights, cache_key


def _inventory_empty(image):
    """Apply the icon reader's existing dark-cell gate before numerical ranking."""
    pixels = np.asarray(image.convert("RGB"))
    dx, dy = max(1, round(image.width * .18)), max(1, round(image.height * .18))
    inside = pixels[dy:-dy, dx:-dx] if min(image.size) > 12 else pixels
    return float(np.percentile(inside, 95)) < 32


def _scaled_inventory_candidates(state, family):
    """Build each family's existing scale-refinement bank once on its owning state."""
    if family not in state._scaled_candidates:
        state._scaled_candidates[family] = tuple(
            _inventory_candidate(state.calibrated_rgba[family], scale) for scale in (.9, 1.12, 1.25))
    return state._scaled_candidates[family]


def _rank_inventory(state, rgb, weights, calibrated):
    """Run unchanged catalog arithmetic using only artwork assets and private scale state."""
    total = float(weights.sum())
    if total <= 0:
        return []
    mask = np.sqrt(weights)
    best = {}
    candidates = state.calibrated_candidates if calibrated else state.inventory_candidates
    if calibrated:
        errors = artwork_cache.match(state.calibrated_atlas, rgb, cv2.TM_SQDIFF, mask=mask)
        indices = np.arange(len(candidates))[:, None] * 48 + np.arange(9)
        minima = errors[indices].min(axis=(1, 2))
    else:
        minima = [cv2.matchTemplate(candidate, rgb, cv2.TM_SQDIFF, mask=mask).min()
                  for _, candidate in candidates]
    for (family, _), error in zip(candidates, minima):
        score = -max(0, float(error)) / total
        best[family] = max(best.get(family, float("-inf")), score)
    if calibrated:
        shortlist = sorted(best, key=best.get, reverse=True)[:8]
        for family in shortlist:
            for candidate in _scaled_inventory_candidates(state, family):
                errors = cv2.matchTemplate(candidate, rgb, cv2.TM_SQDIFF, mask=mask)
                best[family] = max(best[family], -max(0, float(np.min(errors))) / total)
    return sorted(({"name": family, "score": score} for family, score in best.items()),
                  key=lambda row: row["score"], reverse=True)[:3]


def _cache_inventory_rank(reader, key, ranked):
    """Publish copied numeric rankings on the owning reader's serial thread only."""
    reader._rank_cache[key] = tuple(dict(row) for row in ranked)
    reader._rank_cache.move_to_end(key)
    if len(reader._rank_cache) > 128:
        reader._rank_cache.popitem(last=False)


class _CatalogRankState:
    """Own only private scale preparation and shared read-only catalog assets, never JavaScript."""

    def __init__(self, reader):
        """Keep the numerical ranking inputs without retaining the reader or its context."""
        self.calibrated_candidates = reader.calibrated_candidates
        self.calibrated_rgba = reader.calibrated_rgba
        self.calibrated_atlas = reader.calibrated_atlas
        self._scaled_candidates = dict(reader._scaled_candidates)


class CurrencyReader:
    """Own offline JavaScript recognizers and bounded image-matching caches for one worker.

    Prepared example banks are checked against both the reader and creating
    thread; get_reader supplies thread-local instances for normal use.
    """
    def __init__(self):
        """Load offline recognizers/catalogs into a memory-limited context and prepare icon assets."""
        self._rank_cache = OrderedDict()
        self._scaled_candidates = {}
        self._example_atlases = OrderedDict()
        self._example_rank_cache = OrderedDict()
        # These banks contain only normalized local references, never a scan's
        # recognition or review decisions. A replaced worker starts empty.
        self._learned_reference_banks = OrderedDict()
        self._learned_reference_bytes = 0
        self._count_caches = threading.local()
        self._example_thread = threading.get_ident()
        self._example_owner = object()
        self.context = quickjs.Context()
        self.context.set_memory_limit(128 * 1024 * 1024)
        self.context.eval("var module = {exports:{}};")
        self.context.eval((ROOT / "currency-reader.js").read_text(encoding="utf-8"))
        self.context.eval("var CurrencyReader = module.exports;")
        self.context.eval("var BANK = " + (ROOT / "currency-icons.json").read_text(encoding="utf-8") + ";")
        self.context.eval("function identifyJSON(s) { return JSON.stringify(CurrencyReader.identify(JSON.parse(s), BANK)); }")
        self.identify = self.context.get("identifyJSON")
        self.context.eval("module = {exports:{}};")
        self.context.eval((ROOT / "icon-matcher.js").read_text(encoding="utf-8"))
        self.context.eval("var IconMatcher = module.exports;")
        self.inventory_match = None
        self.inventory_candidates = []
        if (ROOT / "inventory-icons.json").exists():
            self._inventory_js_ready = False
            self.inventory_match = lambda payload, owner=weakref.proxy(self): owner._inventory_js(payload)
            bank_path = ROOT / "inventory-icons.json"
            stat = bank_path.stat()
            inventory, self.inventory_candidates, self._inventory_rgba = _inventory_assets(
                str(bank_path), stat.st_mtime_ns, stat.st_size)
            self.inventory_members = {item["family"]: item["members"] for item in inventory}
            self.inventory_ignored = {item["family"] for item in inventory if item.get("ignored")}
            variants_path = ROOT / "inventory-reference-variants.json"
            self.calibrated_candidates, self.calibrated_rgba, self.calibrated_atlas = _calibrated_inventory_assets(
                str(bank_path), stat.st_mtime_ns, stat.st_size, str(variants_path),
                variants_path.stat().st_mtime_ns if variants_path.exists() else 0)
        self.context.eval("function examplesJSON(s) { var p = JSON.parse(s); var ranked = IconMatcher.match(p.cell, "
                          "p.refs.map(function(r) { return {name:r.name, rgb:r.rgb}; })).ranked; "
                          "var names=Object.create(null), distinct=[]; "
                          "for(var i=0;i<ranked.length;i++) { if(!names[ranked[i].name]) { "
                          "names[ranked[i].name]=true; distinct.push(ranked[i]); } } "
                          "return JSON.stringify(distinct.slice(0,3)); }")
        self.match_examples = self.context.get("examplesJSON")
        self.context.eval("module = {exports:{}};")
        digit_source = (ROOT / "digit-reader.js").read_text(encoding="utf-8")
        self.context.eval(digit_kernels.source_with_native_kernels(self.context, digit_source))
        self.context.eval("var DigitReader = module.exports;")
        self.context.eval("var DIGITS = DigitReader.templatesFromJSON(" +
                          (ROOT / "digit-templates.json").read_text(encoding="utf-8") + ");")
        self.context.eval("function readCountJSON(s) { var p = JSON.parse(s); var v = DigitReader.valueChannelDesatMax(p.data,p.w,p.h); "
                          "return JSON.stringify(DigitReader.readCellAdaptive(v,p.w,p.h,p.cx,p.cy,DIGITS,DigitReader.DEFAULTS,p.scale)); }")
        self.read_count = self.context.get("readCountJSON")

    @staticmethod
    def _pixels(image: Image.Image):
        """Serialize a PIL image as flat RGBA pixels and dimensions for JavaScript readers."""
        rgba = image.convert("RGBA")
        return {"data": np.asarray(rgba, dtype=np.uint8).reshape(-1).tolist(),
                "w": rgba.width, "h": rgba.height}

    def _inventory_js(self, payload):
        """Lazily prepare the legacy inventory matcher and return its top three distinct families."""
        if not self._inventory_js_ready:
            self.context.eval("var INVENTORY_BANK = " + json.dumps(self._inventory_rgba, separators=(",", ":")) + ";")
            self.context.eval("var INVENTORY_REFS = INVENTORY_BANK.map(function(r) { "
                              "return {name:r.family, f:IconMatcher.prepCandidate(r.rgba)}; });")
            self.context.eval("function inventoryJSON(s) { var cell=JSON.parse(s); "
                              "for(var p=0;p<cell.length;p+=3) { "
                              "var x=(p/3)%40,y=Math.floor(p/120); "
                              "if((x>=27 && y>=27) || Math.max(cell[p],cell[p+1],cell[p+2])<28 || "
                              "(cell[p]<28 && cell[p+1]<28 && cell[p+2]<=45 && cell[p+2]>=cell[p] && cell[p+2]>=cell[p+1])) { "
                              "cell[p]=26;cell[p+1]=26;cell[p+2]=40; } } "
                              "var ranked=IconMatcher.match(cell,INVENTORY_REFS).ranked; "
                              "var distinct=[]; var seen={}; for(var i=0;i<ranked.length;i++) { "
                              "if(!seen[ranked[i].name]) { seen[ranked[i].name]=true; distinct.push(ranked[i]); } } "
                              "return JSON.stringify(distinct.slice(0,3)); }")
            self._inventory_js_ready = True
        return self.context.get("inventoryJSON")(payload)

    def icon(self, image: Image.Image, count_digits=None, strictness=ocr_sensitivity.DEFAULT, score_allowance=0):
        """Identify an icon, separating empty, ignored and uncertain inventory matches.

        A weighted match needs score > -3000 with margin > 300, score > -3200 with
        margin > 1500, or score > -3600 with margin > 2500; fallback recognition
        must also pass its score/margin gates. ``score_allowance`` offsets only the
        score floors, for callers that blank part of the art before matching.
        Strictness adjusts identity confidence and candidate admission around those
        defaults. Count and tier checks retain their separate evidence requirements.
        """
        strictness = ocr_sensitivity.validate(strictness)
        clear = lambda base, spread: ocr_sensitivity.clear_threshold(base, strictness, spread)
        suggestion = ocr_sensitivity.review_threshold(-8000, strictness, 2000)
        pending = None
        if _inventory_empty(image):
            return {"family": None, "members": [], "score": 0, "margin": 0, "all": [],
                    "empty": True}
        if self.inventory_match:
            ranked = self.inventory_ranked(image, calibrated=True, count_digits=count_digits)
            if ranked:
                top = ranked[0]
                margin = top["score"] - ranked[1]["score"] if len(ranked) > 1 else float("inf")
                # A lower score needs a larger lead: count digits and tier
                # badges on small cells cost score but not the lead.
                score = top["score"] + score_allowance
                clear_match = ((score > clear(-3000, 800) and margin > clear(300, 200)) or
                               (score > clear(-3200, 800) and margin > clear(1500, 500)) or
                               (score > clear(-3600, 800) and margin > clear(2500, 500)))
                if top["name"] in self.inventory_ignored and clear_match:
                    return {"family": None, "members": [], "score": 0,
                            "margin": margin, "all": [], "ignored": True,
                            "candidate": " / ".join(self.inventory_members[top["name"]]),
                            "shared_icon": len(self.inventory_members[top["name"]]) > 1}
                if clear_match:
                    return {"family": top["name"], "members": self.inventory_members[top["name"]],
                            "score": max(0, 1 + top["score"] / 8000), "margin": margin,
                            "method": "inventory", "all": []}
                pending = {"family": None, "members": [],
                        "score": max(0, 1 + top["score"] / 8000), "margin": margin,
                        "all": [{"name": " / ".join(self.inventory_members[top["name"]])}],
                        "uncertain": top["score"] > suggestion}
                if top["name"] in self.inventory_ignored:
                    pending["all"] = [{"name": "Unrecognized item"}]
                    pending["uncertain"] = True
                    return pending
                if top["score"] <= suggestion:
                    if strictness != ocr_sensitivity.DEFAULT:
                        pending["all"] = []
                    pending["uncertain"] = True
                    return pending
        icon = image.copy()
        if min(icon.size) >= 35:
            dx, dy = max(1, round(icon.width * .025)), max(1, round(icon.height * .025))
            icon = icon.crop((dx, dy, icon.width - dx, icon.height - dy))
            pixels = np.array(icon.convert("RGB"))
            corner = pixels[:max(1, round(icon.height * .30)), :max(1, round(icon.width * .65))]
            corner[:] = np.median(pixels[-3:, :], axis=(0, 1))
            icon = Image.fromarray(pixels)
        icon.thumbnail((72, 72), Image.Resampling.LANCZOS)
        result = json.loads(self.identify(json.dumps(self._pixels(icon), separators=(",", ":"))))
        if pending is not None:
            agrees = bool(set(result.get("members", [])) &
                          set(self.inventory_members[top["name"]]))
            if not (agrees and result.get("family") and
                    result.get("score", 0) >= clear(.65, .15) and result.get("margin", 0) >= clear(.15, .08)):
                return pending
        if result.get("family") and (result["score"] < clear(.50, .20) or result["margin"] < clear(.12, .08)):
            result["family"], result["members"] = None, []
        return result

    def inventory_ranked(self, image, calibrated=False, count_digits=None):
        """Rank distinct icon families by weighted pixel error with translation tolerance.

        Normalize backing, mask the count corner using an optional digit-width hint
        and omit the tier corner; calibrated candidates also refine artwork scale.
        """
        rgb, weights, cache_key = _inventory_query(image, calibrated, count_digits)
        cached = self._rank_cache.get(cache_key)
        if cached is not None:
            self._rank_cache.move_to_end(cache_key)
            return [dict(row) for row in cached]
        ranked = _rank_inventory(self, rgb, weights, calibrated)
        if float(weights.sum()) > 0:
            _cache_inventory_rank(self, cache_key, ranked)
        return ranked

    def prepare_artwork(self):
        """Prepare the same reference-only catalog assets used by calibrated scans.

        Query masks remain capture-specific; the RGB spectra depend only on the
        immutable source atlas. Its first actual query still proves backend parity.
        """
        if not getattr(self, "inventory_match", None):
            return
        for family in self.calibrated_rgba:
            _scaled_inventory_candidates(self, family)
        artwork_cache.prepare(self.calibrated_atlas, cv2.TM_SQDIFF,
                              mask=np.ones((40, 40), np.float32))

    def prepare_examples(self, references):
        """Normalize and deduplicate references into a bank owned by this reader and thread.

        Preserve first-seen name order and a content signature for repeat ranking.
        """
        if threading.get_ident() != self._example_thread:
            raise ValueError("Icon reference readers belong to their worker thread.")
        if isinstance(references, _ExampleBank):
            if references.owner is not self._example_owner:
                raise ValueError("Prepared icon references belong to a different reader.")
            return references
        grouped = {}
        order = {}
        signature = hashlib.sha256()
        for reference in references:
            pixels = np.asarray(reference["image"].convert("RGB").resize(
                (40, 40), Image.Resampling.LANCZOS), dtype=np.uint8).copy()
            pixels[:13, :13] = [26, 26, 40]
            raw = pixels.tobytes()
            name = reference["name"]
            order.setdefault(name, len(order))
            names = grouped.setdefault(raw, [])
            if name not in names:
                names.append(name)
                encoded = name.encode("utf-8", "surrogatepass")
                signature.update(len(encoded).to_bytes(8, "little"))
                signature.update(encoded)
                signature.update(raw)
        return _ExampleBank(self._example_owner, signature.digest(),
                            tuple((raw, tuple(names)) for raw, names in grouped.items()),
                            MappingProxyType(order))

    def reference_bank(self, key, build):
        """Reuse an immutable prepared local bank within bounded worker memory.

        The adapter supplies a complete content/metadata key and a builder that
        returns (prepared examples, kinds, footprint). Different readers and
        threads cannot share a bank, even when their reference content matches.
        """
        if threading.get_ident() != self._example_thread:
            raise ValueError("Icon reference readers belong to their worker thread.")
        cached = self._learned_reference_banks.get(key)
        if cached is not None:
            self._learned_reference_banks.move_to_end(key)
            return cached[0]
        value = build()
        examples, kinds, _ = value
        size = 256 + sum(128 + len(name.encode("utf-8", "surrogatepass")) +
                         len(str(kind).encode("utf-8", "surrogatepass"))
                         for name, kind in kinds.items())
        if isinstance(examples, _ExampleBank):
            size += sum(len(raw) + 128 + sum(
                128 + len(name.encode("utf-8", "surrogatepass")) for name in names)
                        for raw, names in examples.references)
        else:
            # Ritual's original-size fallback artwork shares this same bounded
            # reference LRU rather than retaining a second set of icon banks.
            size += sum(128 + len(name.encode("utf-8", "surrogatepass")) + image.nbytes
                        for name, image in examples)
        # An unusually large import remains usable without retaining its bank.
        maximum = 16 * 1024 * 1024
        if size <= maximum:
            while self._learned_reference_banks and (
                    len(self._learned_reference_banks) >= 24 or
                    self._learned_reference_bytes + size > maximum):
                _, (_, removed) = self._learned_reference_banks.popitem(last=False)
                self._learned_reference_bytes -= removed
            self._learned_reference_banks[key] = value, size
            self._learned_reference_bytes += size
        return value

    def _example_atlas(self, references):
        """Cache a padded vertical atlas of example pixels for batched alignment searches."""
        digest = hashlib.sha256()
        for raw, _ in references:
            digest.update(raw)
        key = len(references), digest.digest()
        cached = self._example_atlases.get(key)
        if cached is not None:
            self._example_atlases.move_to_end(key)
            return cached
        atlas = np.concatenate([cv2.copyMakeBorder(
            np.frombuffer(raw, dtype=np.uint8).reshape(40, 40, 3).astype(np.float32),
            4, 4, 4, 4, cv2.BORDER_REPLICATE) for raw, _ in references], axis=0)
        atlas.flags.writeable = False
        self._example_atlases[key] = atlas
        if len(self._example_atlases) > 8:
            self._example_atlases.popitem(last=False)
        return atlas

    @staticmethod
    def _exact_example_score(rgb, raw, weights, total, shifts):
        """Recompute the best weighted error at shortlisted shifts using float64 arithmetic."""
        candidate = np.frombuffer(raw, dtype=np.uint8).reshape(40, 40, 3)
        padded = np.pad(candidate, ((4, 4), (4, 4), (0, 0)), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, (40, 40), axis=(0, 1))
        difference = windows.transpose(0, 1, 3, 4, 2)[shifts[:, 0], shifts[:, 1]].astype(np.float64) - rgb
        errors = (np.square(difference).sum(axis=3) * weights.astype(np.float64)).sum(axis=(1, 2))
        return -float(errors.min()) / total

    def examples(self, image: Image.Image, references):
        """Return the three best distinct example names with stable ordering for ties.

        Use batched alignment matching, then refine near-leading candidates before
        caching ranks; a returned name is a candidate until caller thresholds accept it.
        """
        bank = self.prepare_examples(references)
        if not bank:
            return []
        rgb = np.asarray(image.convert("RGB").resize((40, 40), Image.Resampling.LANCZOS),
                         dtype=np.float32).copy()
        rgb[:13, :13] = [26, 26, 40]
        key = bank.signature, rgb.tobytes()
        cached = self._example_rank_cache.get(key)
        if cached is not None:
            self._example_rank_cache.move_to_end(key)
            return [dict(row) for row in cached]
        distances = np.square(rgb - [26, 26, 40]).sum(axis=2)
        weights = np.where(distances > 900, 1, np.where(distances > 144, .2, 0)).astype(np.float32)
        weights[:13, :13] = 0
        total = float(weights.sum(dtype=np.float64))
        if total <= 0:
            return []
        mask = np.sqrt(weights)
        best = {}
        scored = []
        for start in range(0, len(bank.references), 128):
            batch = bank.references[start:start + 128]
            errors = cv2.matchTemplate(self._example_atlas(batch), rgb, cv2.TM_SQDIFF, mask=mask)
            rows = np.arange(len(batch))[:, None] * 48 + np.arange(9)
            alignment_errors = errors[rows]
            minima = alignment_errors.min(axis=(1, 2))
            for (raw, names), error, alignments in zip(batch, minima, alignment_errors):
                score = -max(0, float(error)) / total
                shifts = np.argwhere(alignments <= error + total * 16)
                scored.append((score, raw, names, shifts))
                for name in names:
                    best[name] = max(best.get(name, float("-inf")), score)
        cutoff = sorted(best.values(), reverse=True)[min(2, len(best) - 1)] - 16
        exact = {}
        for score, raw, names, shifts in sorted(scored, key=lambda entry: entry[0], reverse=True):
            if score < cutoff or all(exact.get(name) == 0 for name in names):
                continue
            score = self._exact_example_score(rgb, raw, weights, total, shifts)
            for name in names:
                exact[name] = max(exact.get(name, float("-inf")), score)
        ranked = sorted(({"name": name, "score": score} for name, score in exact.items()),
                        key=lambda row: (-row["score"], bank.order[row["name"]]))[:3]
        self._example_rank_cache[key] = tuple(dict(row) for row in ranked)
        if len(self._example_rank_cache) > 128:
            self._example_rank_cache.popitem(last=False)
        return ranked

    def count(self, image: Image.Image):
        """Return a positive one-to-five-digit native count only at confidence .76 or higher.

        Use a bounded cell thumbnail and the expected upper-left count anchor;
        callers retain review responsibility for clipping or conflicting readings.
        """
        from PoE2_Data_Logger.ocr.inventory_labels import complete_count_glyphs

        glyph_count = complete_count_glyphs(image)
        image = image.copy()
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
        # Cache only this independent reader's exact input, including failed
        # readings. Never reuse an icon match or a caller's quantity decision.
        state = getattr(self._count_caches, "state", None)
        if state is None or state[0] is not self.read_count:
            state = (self.read_count, OrderedDict())
            self._count_caches.state = state
        cache = state[1]
        rgba = image.convert("RGBA")
        key = (rgba.size, rgba.tobytes())
        if key in cache:
            cache.move_to_end(key)
            value = cache[key]
            return value if value is None or len(str(value)) >= (glyph_count or 0) else None
        size = image.size
        scale = max(.5, min(size) / 40)
        pixels = self._pixels(image)
        results = []
        for cx, cy in ((int(size[0] * .27), int(size[1] * .25)),):
            trial = json.loads(self.read_count(json.dumps({**pixels, "cx": cx, "cy": cy,
                                                           "scale": scale}, separators=(",", ":"))))
            if (re.fullmatch(r"\d{1,5}", trial.get("text", "")) and
                    trial.get("conf", 0) >= .76 and int(trial["text"]) > 0):
                results.append((float(trial["conf"]), int(trial["text"])))
        value = max(results)[1] if results else None
        if len(results) > 1 and results[0][1] != results[1][1] and abs(results[0][0] - results[1][0]) < .08:
            value = None
        cache[key] = value
        if len(cache) > 128:
            cache.popitem(last=False)
        return value if value is None or len(str(value)) >= (glyph_count or 0) else None


_ICON_IMPLEMENTATION = CurrencyReader.icon
_RANK_IMPLEMENTATION = CurrencyReader.inventory_ranked


def prefetch_catalog_ranks(reader, requests, workers=None):
    """Prepare exact numeric ranks for one inventory, leaving all identity/review work serial.

    Each worker owns its scale dictionary and receives immutable query pixels;
    it has no QuickJS context, learned references, scan result or shared rank LRU.
    Results are published in input order only after every task succeeds. This
    optional preparation never replaces an overridden reader implementation.
    """
    if (type(reader) is not CurrencyReader or not reader.inventory_match or
            getattr(reader.icon, "__func__", None) is not _ICON_IMPLEMENTATION or
            getattr(reader.inventory_ranked, "__func__", None) is not _RANK_IMPLEMENTATION):
        return 0
    if workers is None:
        from PoE2_Data_Logger.core import ocr_runtime
        workers = ocr_runtime.active_threads()
    if type(workers) is not int or workers not in (2, 4, 6):
        return 0
    pool = None
    try:
        requests = tuple(requests)
        # The caller supplies one captured 12x5 inventory, never a scan queue.
        if not 2 <= len(requests) <= 60:
            return 0
        pending = OrderedDict()
        wanted = OrderedDict()
        for image, count_digits in requests:
            if _inventory_empty(image):
                continue
            rgb, weights, key = _inventory_query(image, True, count_digits)
            if key in wanted or not np.any(weights):
                continue
            cached = reader._rank_cache.get(key)
            wanted[key] = tuple(dict(row) for row in cached) if cached is not None else None
            if cached is not None:
                continue
            rgb.flags.writeable = weights.flags.writeable = False
            pending[key] = (key, rgb, weights)
        if len(pending) < 2:
            return 0
        shared = _CatalogRankState(reader)
        local = threading.local()
        states = []
        states_lock = threading.Lock()

        def rank(query):
            """Use the same numerical kernel with thread-private preparation only."""
            if not hasattr(local, "state"):
                local.state = _CatalogRankState(shared)
                with states_lock:
                    states.append(local.state)
            key, rgb, weights = query
            return key, _rank_inventory(local.state, rgb, weights, True)

        pool = ThreadPoolExecutor(max_workers=min(workers, len(pending)),
                                  thread_name_prefix="CurrencyArt")
        results = list(pool.map(rank, pending.values()))
    except (cv2.error, MemoryError, RuntimeError):
        # No partial publication: the serial icon path still has this capture
        # and can perform its ordinary comparisons if preparation fails.
        return 0
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    for state in states:
        for family, candidates in state._scaled_candidates.items():
            reader._scaled_candidates.setdefault(family, candidates)
    prepared = dict(results)
    # Refresh requested hits alongside new ranks. Otherwise a full prior LRU
    # could evict a later slot's old hit while publishing this scan's misses.
    for key, cached in wanted.items():
        _cache_inventory_rank(reader, key, cached if cached is not None else prepared[key])
    return len(results)


def resolve_tier(members, cell: Image.Image, read_text):
    """Resolve shared artwork from recognized II/III badges or a unique unprefixed base name.

    Other tier-like text remains unresolved rather than selecting the base variant.
    """
    if len(members) == 1:
        return members[0]
    cell = cell.copy()
    cell.thumbnail((96, 96), Image.Resampling.LANCZOS)
    right = cell.crop((cell.width // 2, 0, cell.width, cell.height))
    lines = read_text(right.resize((right.width * 4, right.height * 4)))
    text = " ".join(line["text"] for line in lines
                    if float(line.get("score", 1)) >= .65).upper()
    tier = "perfect" if re.search(r"\bIII\b", text) else "greater" if re.search(r"\bII\b", text) else None
    if tier:
        options = [name for name in members if name.lower().startswith(tier + " ")]
        return options[0] if len(options) == 1 else None
    if re.search(r"\b(?:IV|VI|VII|VIII|IX|X|GREATER|PERFECT|LESSER)\b", text):
        return None
    base = [name for name in members if not re.match(r"^(?:Greater|Perfect|Lesser)\b", name, re.I)]
    return base[0] if len(base) == 1 else None
