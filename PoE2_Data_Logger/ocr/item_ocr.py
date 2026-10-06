from __future__ import annotations

import io
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import cv2
from PIL import Image, ImageOps

from PoE2_Data_Logger.ocr.opened_scan import OCR_LOCK, _engine
from PoE2_Data_Logger.ocr import currency_ocr
from PoE2_Data_Logger.ocr.affix_capture import affix_key, affix_unit, modifier_value, looks_like_modifier


def _key(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def is_ritual_page(lines):
    text = " ".join(str(row.get("text", "")) for row in lines
                    if isinstance(row, dict) and row.get("score", 0) >= .75).casefold()
    title = bool(re.search(r"\bfavou?rs\b", text))
    tribute = bool(re.search(r"\btribute\b", text))
    controls = bool(re.search(r"\bdefer(?:red|\s+mode)?\b|\breroll\s+favou?rs\b|offer\s+tribute", text))
    return tribute and (title or controls)


def ocr_lines(image):
    if isinstance(image, (str, Path)):
        with Image.open(image) as source:
            image = source.convert("RGB")
    with OCR_LOCK:
        result = _engine()(image.convert("RGB"))
    rows = []
    if result.boxes is not None:
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if text.strip():
                rows.append({"text": text.strip(), "score": float(score),
                             "x": min(float(p[0]) for p in box),
                             "y": min(float(p[1]) for p in box),
                             "right": max(float(p[0]) for p in box),
                             "bottom": max(float(p[1]) for p in box)})
    rows.sort(key=lambda row: (row["y"], row["x"]))
    groups = []
    for row in rows:
        group = next((g for g in reversed(groups[-3:]) if abs(g["y"] - row["y"]) < 12), None)
        if group is None:
            groups.append({"y": row["y"], "parts": [row]})
        else:
            group["parts"].append(row)
    return [{"text": " ".join(part["text"] for part in sorted(g["parts"], key=lambda r: r["x"])),
             "score": min(part["score"] for part in g["parts"]),
             "x": min(part["x"] for part in g["parts"]), "y": g["y"],
             "right": max(part["right"] for part in g["parts"]),
             "bottom": max(part["bottom"] for part in g["parts"]),
             "parts": g["parts"]}
            for g in groups]


def _affix_match(text, names):
    raw = affix_key(text)
    aliases = {
        "Monsters have increased Effectiveness": "Effectiveness",
        "increased Effectiveness": "Effectiveness",
        "increased Monster Rarity": "Monster Rarity",
        "increased Pack Size": "Pack Size",
        "increased Pack Size in Map": "Pack Size",
        "increased Rarity of Items found in Map": "Rarity Of Items Found In Map",
        "increased Gold found in Map": "Increased Gold Found In Map",
        "Map has increased Monster Rarity": "Monster Rarity",
        "Map has increased number of Rare Monsters": "Rare Monster%",
        "Map has increased Magic Monsters": "Magic Monster %",
        "increased Quantity of Waystones found in Map": "Waystone %",
        "Map has increased chance to contain Essences": "Chance to Contain Essences",
        "Map contains increased number of Runic Monster Markers": "% Increased Monster Markers",
        "Expeditions have Surpassing chance to contain an additional Verisium Remnant":
            "Surpassing Chance for a additional remnant",
    }
    for full, alias in aliases.items():
        if raw == affix_key(full) and alias in names:
            return alias, 1.0
    for name in names:
        if raw and raw == affix_key(name):
            return name, 1.0
    scored = []
    for name in names:
        target = affix_key(name)
        score = SequenceMatcher(None, raw, target).ratio()
        scored.append((score, name))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < .96 or (len(scored) > 1 and scored[0][0] - scored[1][0] < .035):
        return None, scored[0][0] if scored else 0.0
    return scored[0][1], scored[0][0]


def merge_tablet_lines(lines):
    merged = []
    for source in lines:
        row = dict(source) if isinstance(source, dict) else {"text": str(source)}
        text = row["text"].strip()
        previous = merged[-1] if merged else None
        wraps = bool(previous and (re.search(r"\b(?:an?|in|of|to|from|with|contain|additional|before)$",
                                             previous["text"], re.I) or
                                   re.match(r"^additional\b", text, re.I) or
                                   text.lower() in ("map", "maps", "remnant", "remnants")))
        continuation = bool(wraps and
                            re.search(r"[A-Za-z]", text) and
                            not re.search(r"\d\s*%", text) and
                            not re.match(r"^(?:Can be used|Right click|Shift click|Item Level|"
                                         r"Uses Remaining|Corrupted|Mirrored|Unstash|Tablet|"
                                         r"The first|Map (?:has|contains)|Expeditions (?:have|are))\b", text, re.I) and
                            ("y" not in previous or "y" not in row or
                             0 <= row["y"] - previous["y"] <= 45))
        if continuation:
            previous["text"] += " " + text
            previous["score"] = min(float(previous.get("score", 1)), float(row.get("score", 1)))
            if "bottom" in row:
                previous["bottom"] = row["bottom"]
        else:
            merged.append(row)
    return merged


def parse_tablet(lines, affixes):
    found, uncertain = [], []
    for line in merge_tablet_lines(lines):
        text = line["text"] if isinstance(line, dict) else str(line)
        amount = modifier_value(text)
        if not amount:
            if looks_like_modifier(text):
                uncertain.append(text)
            continue
        name, score = _affix_match(text, affixes)
        if (name and affix_unit(name) == amount["unit"] and
                name not in [item["affix"] for item in found]):
            found.append({"affix": name, "value": amount["value"], "unit": amount["unit"],
                          "score": min(score, float(line.get("score", 1)) if isinstance(line, dict) else 1),
                          "raw": text})
        else:
            uncertain.append(text)
    return {"matches": found[:4], "uncertain": uncertain + [item["raw"] for item in found[4:]],
            "status": "ready" if found and not uncertain and len(found) <= 4 else "review"}


def inventory_cell(image, slot):
    if slot < 1 or slot > 60:
        raise ValueError("Choose an inventory slot from 1 to 60.")
    column, row = (slot - 1) % 12, (slot - 1) // 12
    left = round(column * image.width / 12)
    top = round(row * image.height / 5)
    right = round((column + 1) * image.width / 12)
    bottom = round((row + 1) * image.height / 5)
    return image.crop((left, top, right, bottom)).convert("RGB")


def inventory_grid(image):
    if image.info.get("poe2_inventory_aligned"):
        return image
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    r, g, b = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    gold = (r > g * 1.12) & (g > b * 1.08) & (r > 17) & (r < 170)
    navy = ((b > 20) & (b < 45) & (r >= 5) & (r < 25) &
            (g >= 4) & (g < 22) & (b > r * 1.4) & (r > g))
    separators = gold | navy
    horizontal, vertical = separators.mean(axis=0), separators.mean(axis=1)

    def fit(profile, count, pitches, required, max_padding=1):
        smooth = cv2.dilate(profile.astype(np.float32)[None, :],
                            np.ones((1, 3), np.uint8))[0]
        best = None
        for pitch in pitches:
            span = count * pitch
            if span > len(profile) + 3:
                continue
            starts = np.arange(-2, max(-1.5, min(len(profile) - span + 2,
                                                len(profile) * max_padding)), .5)
            places = starts[:, None] + np.arange(count + 1)[None, :] * pitch
            samples = np.interp(places, np.arange(len(smooth)), smooth, left=0, right=0)
            hits = (samples > .15).sum(axis=1)
            scores = samples[:, 1:-1].sum(axis=1) * 2 + samples[:, (0, -1)].sum(axis=1) * .2 + hits * .1
            scores[hits < required] = -1
            index = int(np.argmax(scores))
            if scores[index] >= 0 and (best is None or scores[index] > best[0]):
                best = float(scores[index]), float(starts[index]), float(pitch)
        return best

    low, high = image.width / 14.5, min(image.width / 11.8, image.height / 4.9)
    if high <= low:
        return image
    xfit = fit(horizontal, 12, np.arange(low, high + .25, .25), 8)
    if xfit is None:
        return image
    pitch = xfit[2]
    yfit = fit(vertical, 5, (pitch,), 4, .18)
    if yfit is None:
        return image
    x, y = xfit[1], yfit[1]
    box = (max(0, round(x + 1)), max(0, round(y + 1)),
           min(image.width, round(x + 12 * pitch)),
           min(image.height, round(y + 5 * yfit[2])))
    cropped = image.crop(box)
    cropped.info["poe2_inventory_aligned"] = True
    return cropped


def _stack_count(cell, read):
    corner = cell.crop((0, 0, max(8, round(cell.width * .5)),
                        max(8, round(cell.height * .3))))
    enlarged = ImageOps.expand(corner.resize((corner.width * 6, corner.height * 6),
                                             Image.Resampling.NEAREST), border=20, fill="black")
    digits = []
    for line in read(enlarged):
        match = re.fullmatch(r"\s*(\d{1,5})\s*", line["text"])
        if match and line.get("score", 1) >= .85:
            digits.append((float(line.get("score", 1)), int(match.group(1))))
    if digits:
        return max(digits)[1], False
    return 1, True


def _inventory_labels(image):
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from PoE2_Data_Logger.ocr.inventory_labels import count_crops, tier_crops, fallback_crops

    labels = {slot: {} for slot in range(1, 61)}
    patches, positions = [], []
    counts = {slot: [] for slot in labels}
    tiers = {slot: [] for slot in labels}
    for slot in labels:
        cell = inventory_cell(image, slot)
        number_crops, possible_count = count_crops(cell)
        labels[slot]["count_present"] = possible_count
        for patch in number_crops:
            patches.append(np.asarray(patch)); positions.append((slot, "count"))
        roman_crops, possible_tier = tier_crops(cell)
        labels[slot]["tier_present"] = possible_tier
        for patch in roman_crops:
            patches.append(np.asarray(patch)); positions.append((slot, "tier"))
        if not possible_count:
            labels[slot]["count"] = 1
    if not patches:
        return labels
    with OCR_LOCK:
        result = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
    for (slot, kind), raw, score in zip(positions, result.txts, result.scores):
        text = raw.strip().replace(" ", "").rstrip(".,:;")
        if kind == "count" and re.fullmatch(r"\d{1,6}", text) and float(score) >= .85:
            if 0 < int(text) <= 1000000:
                counts[slot].append((int(text), float(score)))
        elif kind == "tier" and float(score) >= .8:
            roman = text.upper().replace("L", "I").replace("1", "I")
            if roman in ("II", "III"):
                tiers[slot].append(roman)
    for slot in labels:
        readings = counts[slot]
        numbers = [value for value, score in readings if score >= .98]
        if numbers:
            value = max(dict.fromkeys(numbers), key=numbers.count)
            labels[slot]["count_candidate"] = value
            if len(numbers) >= 2 and len(set(numbers)) == 1:
                labels[slot]["count"] = value
        if tiers[slot] and len(set(tiers[slot])) == 1:
            labels[slot]["tier"] = tiers[slot][0]
    fallback_images, fallback_positions = [], []
    for slot in labels:
        if labels[slot].get("count") in (None, 1) and labels[slot]["count_present"]:
            for kind, patch in fallback_crops(inventory_cell(image, slot)):
                fallback_images.append(np.asarray(patch))
                fallback_positions.append((slot, kind))
    if fallback_images:
        with OCR_LOCK:
            result = _engine().text_rec(TextRecInput(img=fallback_images, return_word_box=False))
        alternatives = {}
        for (slot, kind), raw, score in zip(fallback_positions, result.txts, result.scores):
            text = raw.strip().replace(" ", "").rstrip(".,:;")
            if re.fullmatch(r"\d{1,6}", text) and float(score) >= .85 and 0 < int(text) <= 1000000:
                alternatives.setdefault(slot, {"mask": [], "raw": []})[kind].append(int(text))
        for slot, readings in alternatives.items():
            masked, raw = readings["mask"], readings["raw"]
            if labels[slot].get("count") == 1:
                if len(masked) >= 2 and len(set(masked)) == 1 and masked[0] != 1:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = masked[0]
                elif 11 in raw:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = 11
                elif len(raw) >= 2 and len(set(raw)) == 1 and raw[0] != 1:
                    labels[slot].pop("count")
                    labels[slot]["count_candidate"] = raw[0]
                continue
            value = max(dict.fromkeys(masked), key=masked.count) if masked else None
            if value == 1 and 11 in raw:
                value = 11
            elif len(raw) >= 2 and len(set(raw)) == 1 and raw[0] != value:
                value = raw[0]
            if value is not None:
                labels[slot]["count_candidate"] = value
    return labels


def scan_inventory_grid(image, references=(), read=None):
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    if image.width < 360 or image.height < 180 or image.width * image.height > 12_000_000:
        raise ValueError("Select the complete 12×5 inventory grid.")
    image = inventory_grid(image)
    labels = _inventory_labels(image) if read is None else None
    read = read or ocr_lines
    reader = currency_ocr.CurrencyReader()
    examples = []
    for reference in references:
        raw = reference["image"]
        if isinstance(raw, bytes):
            with Image.open(io.BytesIO(raw)) as stored:
                raw = stored.convert("RGB")
        examples.append({"name": reference["name"], "image": raw})
    found, unknown = [], []
    for slot in range(1, 61):
        cell = inventory_cell(image, slot)
        if float(np.asarray(cell, dtype=np.uint8).std(axis=(0, 1)).mean()) < 8:
            continue
        count_hint = labels[slot].get("count") if labels is not None and labels[slot]["count_present"] else None
        icon = reader.icon(cell, count_digits=len(str(count_hint)) if count_hint is not None else None)
        name = None
        score = icon.get("score", 0)
        if icon.get("family"):
            if labels is not None:
                tier = labels[slot].get("tier", "")
                if labels[slot].get("tier_present") and not tier:
                    unknown.append({"slot": slot, "candidate": " / ".join(icon["members"]),
                                    "score": round(score, 3), "reason": "check tier badge"})
                    continue
                name = currency_ocr.resolve_tier(icon["members"], cell,
                                                lambda unused, tier=tier: [{"text": tier, "score": 1}] if tier else [])
            else:
                name = currency_ocr.resolve_tier(icon["members"], cell, read)
            if not name:
                unknown.append({"slot": slot, "candidate": " / ".join(icon["members"]),
                                "score": round(score, 3), "reason": "shared icon; check tier"})
                continue
        elif examples:
            ranked = reader.examples(cell, examples)
            if ranked:
                top = ranked[0]
                runner = next((entry["score"] for entry in ranked[1:]
                               if entry["name"] != top["name"]), float("-inf"))
                if top["score"] > -1200 and top["score"] - runner > 150:
                    name = top["name"]
                    score = top["score"]
        if name:
            if labels is not None:
                generic_count = labels[slot].get("count")
                native_count = (reader.count(cell) if generic_count is None and
                                labels[slot].get("count_candidate") is None else None)
                quantity = (generic_count if generic_count is not None
                            else labels[slot].get("count_candidate") or native_count or 1)
                guessed = generic_count is None
            else:
                native_count = reader.count(cell)
                generic_count, generic_unclear = (_stack_count(cell, read) if native_count is None
                                                  else (native_count, False))
                quantity = native_count if native_count is not None else generic_count
                guessed = native_count is None and generic_unclear
            found.append({"slot": slot, "name": name, "quantity": quantity,
                          "score": round(score, 3), "count_needs_review": guessed})
        elif icon.get("all") and (icon.get("uncertain") or score >= .55):
            unknown.append({"slot": slot, "candidate": icon["all"][0]["name"],
                            "score": round(score, 3)})
    return {"items": found, "unknown": unknown, "status": "review"}


def parse_ritual(lines, omen_names):
    proposals, unmatched, anchors = [], [], []
    excluded = re.compile(r"^(?:ritual|favou?rs?|defer|reroll|tribute|purchase|refresh|remaining|"
                          r"items?|rewards?|cost|cancel|close|\d[\d, ]*)$", re.I)
    known = [(name, _key(name)) for name in omen_names]
    for line in lines:
        raw = line["text"] if isinstance(line, dict) else str(line)
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            continue
        if re.search(r"^(?:Offer Tribute to the King|Reroll Favou?rs)\b|\bDefer Mode\b", raw, re.I):
            continue
        price_only = re.fullmatch(r"(?:Tribute\s*[:x-]?\s*)?(\d{1,3}(?:,\d{3})+|\d{2,7})"
                                  r"(?:\s*Tribute)?", raw, re.I)
        if price_only:
            y = line.get("y") if isinstance(line, dict) else None
            if y is not None:
                previous = [(y - anchor_y, index) for index, anchor_y in anchors
                            if 0 < y - anchor_y <= 110 and proposals[index]["tribute"] is None]
                if previous:
                    proposals[min(previous)[1]]["tribute"] = int(price_only.group(1).replace(",", ""))
                    continue
            unmatched.append(raw)
            continue
        stack = re.search(r"\s+[x×]\s*(\d{1,6})(?=\s|$)", raw, re.I)
        quantity = int(stack.group(1)) if stack else 1
        priced = (raw[:stack.start()] + raw[stack.end():]).strip() if stack else raw
        amount = re.search(r"(?:\bTribute\s*[:x-]?\s*|\s+)(\d{1,3}(?:,\d{3})+|\d{1,7})\s*$", priced, re.I)
        tribute = int(amount.group(1).replace(",", "")) if amount else None
        title = priced[:amount.start()].strip(" -·:") if amount else priced
        if quantity < 1:
            unmatched.append(raw)
            continue
        if excluded.fullmatch(title) or len(title) < 4 or len(title) > 160 or not re.search("[A-Za-z]", title):
            continue
        score = float(line.get("score", 1)) if isinstance(line, dict) else 1.0
        if score < .55:
            unmatched.append(raw)
            continue
        is_omen = bool(re.search(r"\b[O0]men\b", title, re.I))
        if is_omen:
            ranked = sorted(((SequenceMatcher(None, _key(title), key).ratio(), name)
                             for name, key in known), reverse=True)
            best = ranked[0] if ranked else (0.0, title)
            next_score = ranked[1][0] if len(ranked) > 1 else 0.0
            name = best[1] if best[0] >= .75 and best[0] - next_score >= .035 else title
            category = "Omen"
            name_match = best[0] if name == best[1] else 0.0
        elif re.fullmatch(r"[A-Za-z][A-Za-z' -]{3,70}", title) and score >= .72:
            name, category = title, "Item"
            name_match = 1.0
        else:
            unmatched.append(raw)
            continue
        proposals.append({"category": category, "name": name, "quantity": quantity,
                          "tribute": tribute, "source": raw, "score": round(score, 2),
                          "name_match": round(name_match, 3)})
        if isinstance(line, dict) and line.get("y") is not None:
            anchors.append((len(proposals) - 1, line["y"]))
    return {"items": proposals[:100], "unmatched": unmatched + [r["text"] for r in lines[100:]],
            "raw_text": "\n".join(r["text"] if isinstance(r, dict) else str(r) for r in lines),
            "status": "review"}


def deferred_markers(image):
    with Image.open(Path(__file__).resolve().parent.parent / "deferred_marker.png") as reference:
        marker = np.asarray(reference.convert("L"))
    gray = np.asarray(image.convert("L"))
    scale = 1.0
    found = []
    for factor in (.67, .85, 1.0, 1.25, 1.5, 2.0):
        size = max(8, round(marker.shape[0] * factor * scale))
        if size >= min(gray.shape):
            continue
        sample = cv2.resize(marker, (size, size), interpolation=cv2.INTER_AREA)
        scores = cv2.matchTemplate(gray, sample, cv2.TM_CCOEFF_NORMED)
        for _ in range(100):
            _, score, _, point = cv2.minMaxLoc(scores)
            if score < .86:
                break
            x, y = point
            actual_x, actual_y, unit = x / scale, y / scale, size / scale
            if not any(abs(actual_x - old["x"]) < unit and abs(actual_y - old["y"]) < unit for old in found):
                found.append({"x": actual_x, "y": actual_y, "size": unit, "score": score,
                              "box": (round(actual_x - 1.85 * unit), round(actual_y - 2 * unit),
                                      round(actual_x + unit), round(actual_y + unit))})
            scores[max(0, y - size):y + size + 1, max(0, x - size):x + size + 1] = -1
    return sorted(found, key=lambda item: (item["y"], item["x"]))


def ritual_totals(lines, image=None):
    tribute = None
    rerolls = None
    header = None
    for row in lines:
        if float(row.get("score", 0)) < .8:
            continue
        match = re.search(r"(\d[\d,]*)\s*Tribute\b", row["text"], re.I)
        if match:
            tribute = int(match.group(1).replace(",", ""))
            header = row
            break
    if header:
        for row in lines:
            if (re.fullmatch(r"\d{1,2}", row["text"].strip()) and row.get("score", 0) >= .85 and
                    row.get("right", 0) < header["x"] and
                    abs(row.get("y", 0) - header["y"]) <= max(25, header["bottom"] - header["y"])):
                rerolls = int(row["text"].strip())
        if rerolls is None and image is not None:
            from rapidocr.ch_ppocr_rec.typings import TextRecInput
            height = header["bottom"] - header["y"]
            x, y = header["x"] - height * 7.02, header["y"] + height * .2
            left, top = round(x), round(y)
            crop = image.crop((left, top, left + round(height * .57), top + round(height * .8))).convert("RGB")
            pixels = np.asarray(crop)
            bright = (pixels[:, :, 0] > 160) & (pixels[:, :, 1] > 160) & (pixels[:, :, 2] > 140)
            mask = Image.fromarray(np.where(bright, 255, 0).astype(np.uint8)).convert("RGB")
            patches = [np.asarray(ImageOps.expand(patch.resize((patch.width * 6, patch.height * 6)), border=15, fill="black"))
                       for patch in (crop, mask)]
            with OCR_LOCK:
                read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
            numbers = [int(text) for text, score in zip(read.txts, read.scores)
                       if re.fullmatch(r"\d{1,2}", text) and score >= .85]
            if len(numbers) == 2 and numbers[0] == numbers[1]:
                rerolls = numbers[0]
            if rerolls is None:
                width = header["right"] - header["x"]
                x, y = header["x"] - width * 1.33, header["y"] + height * .1
                crop = image.crop((round(x), round(y), round(x + width * .13),
                                   round(y + height * 1.05))).convert("RGB")
                pixels = np.asarray(crop)
                bright = (pixels[:, :, 0] > 160) & (pixels[:, :, 1] > 160) & (pixels[:, :, 2] > 140)
                mask = Image.fromarray(np.where(bright, 255, 0).astype(np.uint8)).convert("RGB")
                patches = [np.asarray(ImageOps.expand(patch.resize((patch.width * 6, patch.height * 6)),
                                                     border=15, fill="black")) for patch in (crop, mask)]
                with OCR_LOCK:
                    read = _engine().text_rec(TextRecInput(img=patches, return_word_box=False))
                numbers = [int(text) for text, score in zip(read.txts, read.scores)
                           if re.fullmatch(r"\d{1,2}", text) and score >= .85]
                if len(numbers) == 2 and numbers[0] == numbers[1]:
                    rerolls = numbers[0]
    return {"tribute_available": tribute, "rerolls_remaining": rerolls}


def scan_ritual_page(image, omen_names, references=()):
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    rows = ocr_lines(image)
    result = parse_ritual(rows, omen_names)
    result.update(ritual_totals(rows, image))
    markers = deferred_markers(image)
    omens = [item for item in result["items"] if item["category"] == "Omen"]
    supplied = list(references)
    supplied_names = {reference.get("name") for reference in supplied}
    references = supplied + [reference for reference in currency_ocr.omen_references()
                             if reference["name"] not in supplied_names]
    if not isinstance(image, Image.Image):
        with Image.open(image) as source:
            image = source.convert("RGB")
    scale = min(1.0, 1100 / max(image.size))
    shown = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if scale < 1:
        shown = cv2.resize(shown, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    existing = {item["name"] for item in omens}
    candidates = []
    for reference in list(references)[:128]:
        name = reference.get("name")
        if name in existing or name not in omen_names:
            continue
        raw = reference.get("image")
        try:
            with Image.open(io.BytesIO(raw)) as stored:
                icon = np.asarray(stored.convert("RGB"), dtype=np.uint8)
        except (TypeError, ValueError, OSError):
            continue
        if min(icon.shape[:2]) < 20 or float(icon.std()) < 12:
            continue
        best = 0.0
        location = None
        for factor in (.8, 1.0, 1.25):
            width, height = round(icon.shape[1] * scale * factor), round(icon.shape[0] * scale * factor)
            if min(width, height) < 16 or width >= shown.shape[1] or height >= shown.shape[0]:
                continue
            sample = cv2.resize(icon, (width, height), interpolation=cv2.INTER_AREA)
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(shown, sample, cv2.TM_CCOEFF_NORMED))
            if score > best:
                best = score
                location = (point[0] / scale, point[1] / scale, width / scale, height / scale)
            if best >= .97:
                break
        if best >= .965:
            candidates.append((best, name, location))
    matched_markers = set()
    for score, name, location in sorted(candidates, reverse=True)[:20]:
        if name not in existing:
            x, y, width, height = location
            nearby = [index for index, marker in enumerate(markers)
                      if x - width * .25 <= marker["x"] <= x + width * 1.25 and
                         y - height * .25 <= marker["y"] <= y + height * 1.25]
            matched_markers.update(nearby)
            result["items"].append({"category": "Omen", "name": name, "quantity": 1,
                                    "tribute": None, "source": f"icon reference {score:.2f}",
                                    "score": round(score, 2), "deferred": bool(nearby),
                                    "needs_review": True})
            existing.add(name)
    for index, marker in enumerate(markers):
        if index not in matched_markers:
            result["items"].append({"category": "Omen", "name": "Deferred omen", "quantity": 1,
                                    "tribute": None, "source": "Deferred marker; name not resolved",
                                    "score": round(marker["score"], 2), "deferred": True,
                                    "needs_review": True})
    result["deferred_count"] = len(markers)
    return result
