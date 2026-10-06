from __future__ import annotations

import re
from affix_capture import looks_like_modifier


MAX_ITEM_TEXT = 30000
_SEPARATOR = re.compile(r"^-{5,}$")
_MOD_LABEL = re.compile(r"^\{\s*(?:Prefix|Suffix|Implicit|Enchant|Corrupted|Scourge).*?\}$", re.I)
_PROPERTY = re.compile(r"^(?:Item Level|Waystone Tier|Revives Available|Waystone Drop Chance|"
                       r"Item Rarity|Monster Rarity|Monster Pack Size|Pack Size|Effectiveness|"
                       r"Monster Effectiveness|Rare Monsters|Uses Remaining|Tablet|Rarity|Item Class)\s*:", re.I)
_FOOTER = re.compile(r"^(?:Can be used|Waystones can|Right click|Shift click|Press Alt|"
                     r"Corrupted$|Mirrored$|Unidentified$|Note:)", re.I)
_PERCENT = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_TABLET_TITLE = re.compile(r"^(?:[A-Za-z' -]+\s+)?Tab[li1]ets?(?:\s+\d+)?$", re.I)


def _number(line):
    match = _PERCENT.search(line)
    if not match:
        return None
    n = float(match.group(1).replace(",", "."))
    return int(n) if n.is_integer() else n


def _normal_lines(text):
    if not isinstance(text, str) or len(text) > MAX_ITEM_TEXT:
        raise ValueError("The copied item text is too long or invalid.")
    return [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]


def _item_kind(lines):
    klass = next((line.split(":", 1)[1].strip() for line in lines
                  if line.lower().startswith("item class:")), "")
    if re.search(r"waystones?", klass, re.I):
        return "waystone"
    if re.search(r"tablets?", klass, re.I):
        return "tablet"
    return None


def _modifier_lines(lines, kind):
    if kind == "waystone":
        start = next((i + 1 for i, line in enumerate(lines)
                      if line.lower().startswith("item level:")), None)
        if start is None:
            return []
    else:
        start = next((i for i, line in enumerate(lines)
                      if looks_like_modifier(line) and not _PROPERTY.match(line)), None)
        if start is None:
            return []
    result = []
    for line in lines[start:]:
        if _is_footer(line):
            break
        if (_SEPARATOR.match(line) or _MOD_LABEL.match(line) or _PROPERTY.match(line)
                or re.search(r"uses?\s*remaining\b", line, re.I)):
            continue
        if kind == "tablet" and ("expedition to a map" in line.lower()
                                 or line.lower() == "tablet"):
            continue
        result.append(line)
    return result[:80]


def _is_footer(line):
    key = re.sub(r"[^a-z0-9]", "", line.lower())
    return bool(_FOOTER.match(line) or key.startswith(("canbeused", "inspect", "togglechat")))


def parse_item_text(text, affixes=()):
    lines = _normal_lines(text)
    kind = _item_kind(lines)
    if not kind:
        return None
    mods = _modifier_lines(lines, kind)
    if kind == "tablet":
        from item_ocr import parse_tablet

        proposals = parse_tablet([{"text": line, "score": 1.0} for line in mods], affixes)
        return {"kind": kind, "mods": mods, "matches": proposals["matches"],
                "uncertain": proposals["uncertain"], "status": "review"}

    tier_line = next((line for line in lines if line.lower().startswith("waystone tier:")), "")
    tier_match = re.search(r"\b(\d{1,2})\b", tier_line)
    if not tier_match:
        tier_match = next((match for line in lines[:8]
                           if (match := re.search(r"Waystone\s*\(Tier\s*(\d{1,2})\)", line, re.I))), None)
    tier = int(tier_match.group(1)) if tier_match else None
    fields = {"tier": tier}
    for key, pattern in (("waystone", r"^Waystone Drop Chance\s*:"),
                         ("item_rarity", r"^Item Rarity\s*:"),
                         ("monster_rarity", r"^Monster Rarity\s*:"),
                         ("pack_size", r"^(?:Monster )?Pack Size\s*:"),
                         ("effectiveness", r"^(?:Monster )?Effectiveness\s*:")):
        fields[key] = next((_number(line) for line in lines if re.search(pattern, line, re.I)), None)
    explicit = [line for line in mods if "(enchant)" not in line.lower()]
    fields["map_mods"] = len(explicit) if mods else None
    base = next((i for i, line in enumerate(lines[:8])
                 if re.search(r"Waystone\s*\(Tier", line, re.I)), None)
    name = lines[base - 1] if base is not None and base >= 1 else ""
    return {"kind": kind, "fields": fields, "mods": mods, "name": name,
            "status": "review"}


def parse_screen_tooltip(ocr_rows, affixes=()):
    lines = [row["text"].strip() if isinstance(row, dict) else str(row).strip()
             for row in ocr_rows]
    anchor = next(((i, "waystone") for i, line in enumerate(lines)
                   if re.search(r"Waystone\s*\(Tier\s*\d+\)", line, re.I)), None)
    if anchor is None:
        anchor = next(((i, "tablet") for i, line in enumerate(lines)
                       if len(line) <= 80 and _TABLET_TITLE.fullmatch(line)), None)
    if anchor is None:
        return None
    index, kind = anchor
    end = next((i for i in range(index + 1, len(lines)) if _is_footer(lines[i])), len(lines))
    if end - index < 3:
        return None
    body = lines[index:end]
    if kind == "waystone":
        first_mod = next((i + 1 for i, line in enumerate(body)
                          if re.match(r"^Waystone Drop Chance\s*:", line, re.I)), None)
        if first_mod is None:
            return None
        mods = body[first_mod:]
        while mods and _SEPARATOR.match(mods[0]):
            mods.pop(0)
        if mods and mods[-1].lower() == "corrupted":
            mods.pop()
        merged = []
        for line in mods:
            if merged and len(line.split()) <= 2 and not _PERCENT.search(line) and len(merged[-1]) > 40:
                merged[-1] += " " + line
            else:
                merged.append(line)
        name = lines[index - 1] if index else ""
        tier = re.search(r"Waystone\s*\(Tier\s*(\d+)\)", body[0], re.I)
        props = ["Item Class: Waystones", name, body[0],
                 f"Waystone Tier: {tier.group(1)}", *body[1:first_mod], "Item Level: 0", *merged]
        parsed = parse_item_text("\n".join(props), affixes)
    else:
        start = next((i + 1 for i, line in enumerate(body)
                      if re.search(r"uses?\s*remaining\b", line, re.I)), None)
        if start is None:
            start = next((i for i, line in enumerate(body[1:], 1)
                          if looks_like_modifier(line) and not _PROPERTY.match(line)), None)
        if start is None:
            return None
        from item_ocr import merge_tablet_lines, parse_tablet

        rows = []
        for offset, line in enumerate(body[start:], index + start):
            if _PROPERTY.match(line) or _SEPARATOR.match(line):
                continue
            source = ocr_rows[offset]
            row = dict(source) if isinstance(source, dict) else {"score": 1.0}
            row["text"] = line.strip()
            rows.append(row)
        rows = merge_tablet_lines(rows)
        rows = [row for row in rows if looks_like_modifier(row["text"])]
        proposals = parse_tablet(rows, affixes)
        if not proposals["matches"] and not proposals["uncertain"]:
            return None
        parsed = {"kind": kind, "mods": [row["text"] for row in rows],
                  **proposals, "ocr_rows": rows}
    if parsed:
        parsed["source"] = "screen OCR"
    return parsed


def read_screen_tooltip(image, affixes=(), ocr_rows=None):
    from item_ocr import ocr_lines
    rows = [dict(row) for row in ocr_rows] if ocr_rows is not None else ocr_lines(image)
    parts = [dict(part) for row in rows for part in row.get("parts", [row])]
    anchor = next((row for row in parts if _TABLET_TITLE.fullmatch(row["text"].strip()) or
                   re.search(r"Waystone\s*\(Tier\s*\d+\)", row["text"], re.I)), None)
    if anchor and all(key in anchor for key in ("x", "right", "y")):
        center = (anchor["x"] + anchor["right"]) / 2
        radius = max(160, anchor["right"] - anchor["x"])
        rows = [row for row in parts if row.get("y", anchor["y"]) >= anchor["y"] - 60 and
                abs((row.get("x", center) + row.get("right", center)) / 2 - center) <= radius]
        rows.sort(key=lambda row: (row.get("y", 0), row.get("x", 0)))
    parsed = parse_screen_tooltip(rows, affixes)
    if parsed and parsed["kind"] == "tablet":
        changed = False
        for row in rows:
            ambiguous = re.search(r"\b(?=[A-Za-z0-9]{1,3}\s*%)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,3}\s*%", row["text"])
            if not ambiguous and float(row.get("score", 1)) >= .94:
                continue
            if not all(key in row for key in ("x", "y", "right", "bottom")):
                continue
            box = (max(0, int(row["x"]) - 8), max(0, int(row["y"]) - 2),
                   min(image.width, int(row["right"]) + 8), min(image.height, int(row["bottom"]) + 2))
            crop = image.crop(box)
            crop = crop.resize((crop.width * 3, crop.height * 3))
            retry = ocr_lines(crop)
            if not any(_PERCENT.search(item["text"]) and item["score"] >= .94 for item in retry):
                crop = image.crop((max(0, int(row["x"]) - 8), max(0, int(row["y"])),
                                   min(image.width, int(row["right"]) + 8), min(image.height, int(row["bottom"]))))
                retry = ocr_lines(crop.resize((crop.width * 3, crop.height * 3)))
            retry = [item for item in retry if _PERCENT.search(item["text"]) and item["score"] >= .94]
            if len(retry) == 1 and _PERCENT.search(retry[0]["text"]) and retry[0]["score"] >= .94:
                row["text"], row["score"] = retry[0]["text"], retry[0]["score"]
                changed = True
            normalized = re.sub(r"(?<!\w)(?=[0-9Oo]*\d)[0-9Oo]{1,4}(?=\s*%)",
                                lambda match: match.group().replace("O", "0").replace("o", "0"), row["text"])
            if normalized != row["text"]:
                row["text"] = normalized
                changed = True
        if changed:
            parsed = parse_screen_tooltip(rows, affixes)
        if parsed and any(float(row.get("score", 1)) < .9 for row in parsed.get("ocr_rows", [])):
            parsed["uncertain"] = list(dict.fromkeys(parsed.get("uncertain", []) +
                                        [row["text"] for row in parsed["ocr_rows"] if float(row.get("score", 1)) < .9]))
    if parsed:
        parsed.setdefault("ocr_rows", rows)
    return parsed
