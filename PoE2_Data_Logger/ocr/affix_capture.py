from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_NUMBER = re.compile(r"(?<![\d.])[+-]?\d+(?:[.,]\d+)?\s*%?")
_PERCENT = re.compile(r"(?<!\w)[+-]?\d+(?:[.,]\d+)?\s*%")
_ROLL_RANGE = re.compile(r"\(\s*[+-]?\d+(?:[.,]\d+)?\s*%?\s*[-–—]\s*[+-]?\d+(?:[.,]\d+)?\s*%?\s*\)")


def affix_name(raw):
    text = re.sub(r"(?i)\ban?\s+additional\b", "additional", _ROLL_RANGE.sub("", str(raw)))
    text = _NUMBER.sub(" ", text).replace("#", " ").replace("%", " ")
    return re.sub(r"\s+", " ", text).strip(" .,+-")


def affix_key(raw):
    text = affix_name(raw).lower()
    for noun in ("essence", "spirit", "modifier", "boss", "chest", "shrine", "strongbox"):
        text = re.sub(rf"\b{noun}(?:es|s)\b", noun, text)
    return re.sub(r"[^a-z0-9]", "", text)


@lru_cache(maxsize=1)
def affix_catalog():
    data = json.loads((Path(__file__).resolve().parent.parent / "affix_catalog.json").read_text(encoding="utf-8"))
    return data["affixes"]


@lru_cache(maxsize=512)
def affix_unit(name):
    key = affix_key(name)
    for item in affix_catalog():
        if affix_key(item["name"]) == key:
            return item["unit"]
    if re.search(r"\bseconds?\b", name, re.I):
        return "seconds"
    if re.search(r"\b(?:chance|increased|reduced|more|less|gain|slower|sooner)\b", name, re.I):
        return "%"
    if re.search(r"\badditional\b|\bextra\b|\bfirst unearthed\b", name, re.I):
        return "count"
    return "%"


def modifier_value(raw):
    text = _ROLL_RANGE.sub("", str(raw)).strip()
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    if (not text or len(text) > 250 or "usesremaining" in compact or
            compact.startswith(("canbeused", "rightclick", "shiftclick", "itemlevel",
                                "waystonetier", "adds", "inspect", "toggle"))):
        return None
    percentages = list(_PERCENT.finditer(text))
    if percentages:
        if len(percentages) != 1:
            return None
        value = float(percentages[0].group().replace("%", "").replace(",", ".").strip())
        unit = "%"
    else:
        if "%" in text:
            return None
        amounts = list(_NUMBER.finditer(text))
        if len(amounts) == 1 and re.search(r"\b(?:additional|extra|seconds?|times|first)\b", text, re.I):
            value = float(amounts[0].group().replace(",", "."))
            unit = "seconds" if re.search(r"\bseconds?\b", text, re.I) else "count"
        elif not amounts and re.search(r"\ban? additional\b|\bthe first unearthed\b", text, re.I):
            value, unit = 1, "count"
        else:
            return None
    if not 0 <= value <= 9999:
        return None
    if unit != "%" and not float(value).is_integer():
        return None
    return {"value": int(value) if float(value).is_integer() else value,
            "unit": unit, "name": affix_name(text)}


def looks_like_modifier(raw):
    text = str(raw).strip()
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    if "usesremaining" in compact or compact.startswith(("adds", "canbeused", "itemlevel", "inspect")):
        return False
    return bool("%" in text or re.search(r"\b(?:additional|extra|seconds?|first unearthed)\b", text, re.I))


def new_affix_names(uncertain, known, ocr_rows=None):
    known_keys = {affix_key(name) for name in known}
    confidence = {}
    if ocr_rows is not None:
        for row in ocr_rows:
            confidence[row["text"].strip()] = float(row.get("score", 0))
    names = []
    for raw in uncertain:
        if not isinstance(raw, str) or len(raw) > 220:
            continue
        if ocr_rows is not None and confidence.get(raw.strip(), 0) < .94:
            continue
        if not re.fullmatch(r"[\w\s%+.,'’()/–—-]+", raw, re.UNICODE):
            continue
        parsed = modifier_value(raw)
        if parsed is None:
            continue
        name = parsed["name"]
        if (not 10 <= len(name) <= 120 or len(name.split()) < 2 or
                not re.search(r"[A-Za-z]{3}", name) or affix_key(name) in known_keys):
            continue
        names.append(name)
        known_keys.add(affix_key(name))
    return names[:10]


def tablet_random_mod_counts(config):
    key = affix_key("Map has additional random Modifiers")
    values = [0] * 4
    for i, pair in enumerate(config.get("tablet_affixes", [])):
        if i // 4 >= int(config.get("tablets_used", 1)):
            break
        if affix_key(pair.get("affix", "")) == key:
            amount = float(pair.get("value") or 0)
            if amount.is_integer() and 0 <= amount <= 20:
                values[i // 4] += int(amount)
    if not config.get("tablet_random_mods_derived") and not any(values):
        for i in range(min(int(config.get("plus_two_tablets", 0)), int(config.get("tablets_used", 1)))):
            values[i] = 2
    return values
