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
from functools import lru_cache
from pathlib import Path

import numpy as np
import cv2
import quickjs
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "third_party" / "currency_overlay"
_READERS = threading.local()


class _ExampleBank:
    def __init__(self, owner, signature, references, order):
        self.owner = id(owner)
        self.signature = signature
        self.references = references
        self.order = order

    def __bool__(self):
        return bool(self.references)


def get_reader():
    if not hasattr(_READERS, "reader"):
        _READERS.reader = CurrencyReader()
    return _READERS.reader


def catalog_version():
    digest = hashlib.sha256()
    for filename in ("currency-icons.json", "inventory-icons.json", "inventory-reference-variants.json"):
        path = ROOT / filename
        if path.exists():
            digest.update(filename.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def catalog_names():
    names = set()
    for filename in ("currency-icons.json", "inventory-icons.json"):
        if (ROOT / filename).exists():
            names.update(name for item in json.loads((ROOT / filename).read_text(encoding="utf-8"))["icons"]
                         if not item.get("ignored") for name in item["members"])
    return sorted(names)


@lru_cache(maxsize=1)
def omen_references():
    references = []
    entries = json.loads((ROOT / "inventory-icons.json").read_text(encoding="utf-8"))["icons"]
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


class CurrencyReader:
    def __init__(self):
        self._rank_cache = OrderedDict()
        self._scaled_candidates = {}
        self._example_atlases = OrderedDict()
        self._example_rank_cache = OrderedDict()
        self._example_thread = threading.get_ident()
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
        self.context.eval((ROOT / "digit-reader.js").read_text(encoding="utf-8"))
        self.context.eval("var DigitReader = module.exports;")
        self.context.eval("var DIGITS = DigitReader.templatesFromJSON(" +
                          (ROOT / "digit-templates.json").read_text(encoding="utf-8") + ");")
        self.context.eval("function readCountJSON(s) { var p = JSON.parse(s); var v = DigitReader.valueChannelDesatMax(p.data,p.w,p.h); "
                          "return JSON.stringify(DigitReader.readCellAdaptive(v,p.w,p.h,p.cx,p.cy,DIGITS,DigitReader.DEFAULTS,p.scale)); }")
        self.read_count = self.context.get("readCountJSON")

    @staticmethod
    def _pixels(image: Image.Image):
        rgba = image.convert("RGBA")
        return {"data": np.asarray(rgba, dtype=np.uint8).reshape(-1).tolist(),
                "w": rgba.width, "h": rgba.height}

    def _inventory_js(self, payload):
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

    def icon(self, image: Image.Image, count_digits=None):
        pending = None
        pixels = np.asarray(image.convert("RGB"))
        dx, dy = max(1, round(image.width * .18)), max(1, round(image.height * .18))
        inside = pixels[dy:-dy, dx:-dx] if min(image.size) > 12 else pixels
        if float(np.percentile(inside, 95)) < 32:
            return {"family": None, "members": [], "score": 0, "margin": 0, "all": [],
                    "empty": True}
        if self.inventory_match:
            ranked = self.inventory_ranked(image, calibrated=True, count_digits=count_digits)
            if ranked:
                top = ranked[0]
                margin = top["score"] - ranked[1]["score"] if len(ranked) > 1 else float("inf")
                clear_match = ((top["score"] > -3000 and margin > 300) or
                               (top["score"] > -3200 and margin > 1500))
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
                        "uncertain": top["score"] > -8000}
                if top["name"] in self.inventory_ignored:
                    pending["all"] = [{"name": "Unrecognized item"}]
                    pending["uncertain"] = True
                    return pending
                if top["score"] <= -8000:
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
                    result.get("score", 0) >= .65 and result.get("margin", 0) >= .15):
                return pending
        if result.get("family") and (result["score"] < .50 or result["margin"] < .12):
            result["family"], result["members"] = None, []
        return result

    def inventory_ranked(self, image, calibrated=False, count_digits=None):
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
        cache_key = (calibrated, count_width, rgb.tobytes())
        cached = self._rank_cache.get(cache_key)
        if cached is not None:
            self._rank_cache.move_to_end(cache_key)
            return [dict(row) for row in cached]
        distances = np.square(rgb - [26, 26, 40]).sum(axis=2)
        weights = np.where(distances > 900, 1, np.where(distances > 144, .2, 0)).astype(np.float32)
        if calibrated:
            weights[:15, :count_width] = 0
        else:
            weights[:13, :13] = 0
        total = float(weights.sum())
        if total <= 0:
            return []
        mask = np.sqrt(weights)
        best = {}
        candidates = self.calibrated_candidates if calibrated else self.inventory_candidates
        if calibrated:
            errors = cv2.matchTemplate(self.calibrated_atlas, rgb, cv2.TM_SQDIFF, mask=mask)
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
                if family not in self._scaled_candidates:
                    self._scaled_candidates[family] = tuple(
                        _inventory_candidate(self.calibrated_rgba[family], scale) for scale in (.9, 1.12, 1.25))
                for candidate in self._scaled_candidates[family]:
                    errors = cv2.matchTemplate(candidate, rgb, cv2.TM_SQDIFF, mask=mask)
                    best[family] = max(best[family], -max(0, float(np.min(errors))) / total)
        ranked = sorted(({"name": family, "score": score} for family, score in best.items()),
                        key=lambda row: row["score"], reverse=True)[:3]
        self._rank_cache[cache_key] = tuple(dict(row) for row in ranked)
        if len(self._rank_cache) > 128:
            self._rank_cache.popitem(last=False)
        return ranked

    def prepare_examples(self, references):
        if threading.get_ident() != self._example_thread:
            raise ValueError("Icon reference readers belong to their worker thread.")
        if isinstance(references, _ExampleBank):
            if references.owner != id(self):
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
        return _ExampleBank(self, signature.digest(),
                            tuple((raw, tuple(names)) for raw, names in grouped.items()), order)

    def _example_atlas(self, references):
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
        candidate = np.frombuffer(raw, dtype=np.uint8).reshape(40, 40, 3)
        padded = np.pad(candidate, ((4, 4), (4, 4), (0, 0)), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, (40, 40), axis=(0, 1))
        difference = windows.transpose(0, 1, 3, 4, 2)[shifts[:, 0], shifts[:, 1]].astype(np.float64) - rgb
        errors = (np.square(difference).sum(axis=3) * weights.astype(np.float64)).sum(axis=(1, 2))
        return -float(errors.min()) / total

    def examples(self, image: Image.Image, references):
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
        image = image.copy()
        image.thumbnail((96, 96), Image.Resampling.LANCZOS)
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
        if not results:
            return None
        best = max(results)
        if len(results) > 1 and results[0][1] != results[1][1] and abs(results[0][0] - results[1][0]) < .08:
            return None
        return best[1]


def resolve_tier(members, cell: Image.Image, read_text):
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
