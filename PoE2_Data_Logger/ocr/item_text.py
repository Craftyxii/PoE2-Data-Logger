"""Parse copied or recognized waystone/tablet text into editable proposals.

Screen OCR is anchored to a tooltip title and may retry uncertain tablet
percentage rows; returned fields still require review."""

from __future__ import annotations

import math
import re

import numpy as np

from PoE2_Data_Logger.core import ocr_sensitivity
from PoE2_Data_Logger.ocr.affix_capture import looks_like_modifier, numeric_token_complete


MAX_ITEM_TEXT = 30000
_SEPARATOR = re.compile(r"^-{5,}$")
_MOD_LABEL = re.compile(r"^\{\s*(?:Prefix|Suffix|Implicit|Enchant|Corrupted|Scourge|Unique).*?\}$", re.I)
_PROPERTY = re.compile(r"^(?:Item Level|Waystone Tier|Revives Available|Waystone Drop Chance|"
                       r"Item Rarity|Monster Rarity|Monster Pack Size|Pack Size|Effectiveness|"
                       r"Monster Effectiveness|Rare Monsters|Uses Remaining|Tablet|Rarity|Item Class)\s*:", re.I)
_FOOTER = re.compile(r"^(?:Can be used|Waystones can|Right click|Shift click|Press Alt|"
                     r"Corrupted$|Mirrored$|Unidentified$|Note:)", re.I)
_WAYSTONE_USAGE = re.compile(r"^(?:Can be used|Waystones can)\b", re.I)
_PERCENT = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_TABLET_TITLE = re.compile(r"^(?:[A-Za-z' -]+\s+)?Tab[li1]ets?(?:\s+\d+)?$", re.I)
_WAYSTONE_SUBJECT = re.compile(
    r"^(?:Players?|Monsters?|Map|Area|Rare|Magic|Unique|Boss|All|Your|"
    r"Breaches?|Rituals?|Chests?|Strongboxes?)\b", re.I)
_WRAP_END = re.compile(r"\b(?:an?|in|of|to|from|with|and|for)$", re.I)


def _number(line):
    """Read one complete percentage without accepting suffixes of malformed or extra rolls."""
    value_text = line.split(":", 1)[-1]
    matches = list(_PERCENT.finditer(value_text))
    if len(matches) != 1:
        return None
    match = matches[0]
    remaining = value_text[:match.start()] + value_text[match.end():]
    if (re.search(r"[\d%+-]", remaining) or
            not numeric_token_complete(value_text, match)):
        return None
    n = float(match.group(1).replace(",", "."))
    return int(n) if n.is_integer() else n


def _normal_lines(text):
    """Validate the text-size limit and return stripped nonempty lines."""
    if not isinstance(text, str) or len(text) > MAX_ITEM_TEXT:
        raise ValueError("The copied item text is too long or invalid.")
    return [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]


def _item_kind(lines):
    """Identify waystones or tablets from the copied Item Class property."""
    klass = next((line.split(":", 1)[1].strip() for line in lines
                  if line.lower().startswith("item class:")), "")
    if re.search(r"waystones?", klass, re.I):
        return "waystone"
    if re.search(r"tablets?", klass, re.I):
        return "tablet"
    return None


def _modifier_lines(lines, kind):
    """Collect at most 80 modifier lines after the item-specific property boundary.

    Skip separators, labels and properties, and stop at recognized usage/footer prose.
    """
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


def _unique_tablet_lines(lines):
    """Bound a copied unique tablet's modifiers between its implicit and its flavour text.

    Separators split the tooltip into sections. Header, property, implicit and
    footer sections are dropped; the last remaining section is flavour text.
    Return the modifier lines and whether that structure was unambiguous.
    """
    sections, current = [], []
    for line in lines:
        if _SEPARATOR.match(line):
            sections.append(current)
            current = []
        else:
            current.append(line)
    sections.append(current)
    body = [section for section in sections if section and not any(
        _PROPERTY.match(line) or _is_footer(line) or re.search(r"\(implicit\)$", line, re.I) or
        re.match(r"^(?:Adds|Requires)\b", line, re.I) for line in section)]
    labelled = [section for section in body if any(_MOD_LABEL.match(line) for line in section)]
    mods = labelled or body[:-1]
    return [line for section in mods for line in section if not _MOD_LABEL.match(line)][:80], bool(mods)


def _is_footer(line):
    """Recognize usage, inspection and terminal item-status text that ends modifiers."""
    key = re.sub(r"[^a-z0-9]", "", line.lower())
    return bool(_FOOTER.match(line) or key.startswith(("canbeused", "inspect", "togglechat")))


def _waystone_modifiers(lines):
    """Join syntactically supported wraps and hold uncertain row boundaries instead of counting extra mods."""
    merged, uncertain = [], False
    for line in lines:
        independent = bool(_PERCENT.search(line) or _WAYSTONE_SUBJECT.match(line))
        if merged and _WRAP_END.search(merged[-1]) and not independent:
            merged[-1] += " " + line
        else:
            if not independent or merged and _WRAP_END.search(merged[-1]):
                uncertain = True
            merged.append(line)
    if merged and _WRAP_END.search(merged[-1]):
        uncertain = True
    return merged, uncertain


def _unmodified_normal_waystone(lines, mods, fields, uncertain):
    """Prove an empty Normal tooltip from its rarity, tier title and complete usage boundary."""
    rarities = [line.split(":", 1)[1].strip().casefold() for line in lines
                if re.match(r"^Rarity\s*:", line, re.I)]
    title = next((i for i, line in enumerate(lines)
                  if re.fullmatch(r"Waystone\s*\(Tier\s*\d{1,2}\)", line, re.I)), None)
    if (rarities != ["normal"] or title is None or mods or uncertain or
            any(re.fullmatch(r"Unidentified", line, re.I) for line in lines) or
            any(fields.get(key) not in (None, 0) for key in
                ("waystone", "item_rarity", "monster_rarity", "pack_size", "effectiveness"))):
        return False
    title_tier = int(re.search(r"\d{1,2}", lines[title]).group())
    tier_properties = [re.fullmatch(r"Waystone Tier\s*:\s*(\d{1,2})", line, re.I)
                       for line in lines if re.match(r"^Waystone Tier\s*:", line, re.I)]
    percentage_properties = [line for line in lines if re.match(
        r"^(?:Waystone Drop Chance|Item Rarity|Monster Rarity|(?:Monster )?Pack Size|"
        r"(?:Monster )?Effectiveness)\s*:", line, re.I)]
    if (fields.get("tier") != title_tier or
            any(match is None or int(match.group(1)) != title_tier for match in tier_properties) or
            any(_number(line) != 0 for line in percentage_properties)):
        return False
    end = next((i for i in range(title + 1, len(lines)) if _WAYSTONE_USAGE.match(lines[i])), None)
    return end is not None and all(
        _SEPARATOR.match(line) or _PROPERTY.match(line) or
        re.fullmatch(r"Corrupted|Mirrored", line, re.I)
        for line in lines[title + 1:end])


def parse_item_text(text, affixes=()):
    """Parse supported copied items into reviewable fields, holding ambiguous observed waystone rolls."""
    lines = _normal_lines(text)
    kind = _item_kind(lines)
    if not kind:
        return None
    mods = _modifier_lines(lines, kind)
    if kind == "tablet":
        from PoE2_Data_Logger.ocr.item_ocr import parse_tablet

        unique = any(re.fullmatch(r"Rarity\s*:\s*Unique", line, re.I) for line in lines)
        bounded = False
        if unique:
            bounded_mods, bounded = _unique_tablet_lines(lines)
            mods = bounded_mods if bounded else mods
        proposals = parse_tablet([{"text": line, "score": 1.0} for line in mods], affixes, flags=bounded)
        result = {"kind": kind, "mods": mods, "matches": proposals["matches"],
                  "uncertain": proposals["uncertain"], "status": "review"}
        return {**result, "unique": True, "bounded": bounded} if unique else result

    tier_line = next((line for line in lines if line.lower().startswith("waystone tier:")), "")
    tier_match = re.search(r"\b(\d{1,2})\b", tier_line)
    if not tier_match:
        tier_match = next((match for line in lines[:8]
                           if (match := re.search(r"Waystone\s*\(Tier\s*(\d{1,2})\)", line, re.I))), None)
    tier = int(tier_match.group(1)) if tier_match else None
    fields, uncertain = {"tier": tier}, []
    for key, pattern in (("waystone", r"^Waystone Drop Chance\s*:"),
                         ("item_rarity", r"^Item Rarity\s*:"),
                         ("monster_rarity", r"^Monster Rarity\s*:"),
                         ("pack_size", r"^(?:Monster )?Pack Size\s*:"),
                         ("effectiveness", r"^(?:Monster )?Effectiveness\s*:")):
        property_line = next((line for line in lines if re.search(pattern, line, re.I)), None)
        fields[key] = _number(property_line) if property_line is not None else None
        if property_line is not None and fields[key] is None:
            uncertain.append(property_line)
    explicit = [line for line in mods if "(enchant)" not in line.lower()]
    fields["map_mods"] = len(explicit) if mods else None
    normal_unmodified = _unmodified_normal_waystone(lines, mods, fields, uncertain)
    if normal_unmodified:
        fields["waystone"] = fields["map_mods"] = 0
    base = next((i for i, line in enumerate(lines[:8])
                 if re.search(r"Waystone\s*\(Tier", line, re.I)), None)
    name = lines[base - 1] if base is not None and base >= 1 else ""
    if normal_unmodified and (_PROPERTY.match(name) or _SEPARATOR.match(name)):
        name = ""
    result = {"kind": kind, "fields": fields, "mods": mods, "name": name, "uncertain": uncertain,
              "status": "review"}
    if normal_unmodified:
        result["_normal_unmodified"] = True
    return result


def _unique_tablet_rows(rows, body_rows, affixes, flavour):
    """Cut a unique tablet's modifier rows at its flavour text and rejoin short wrapped endings.

    A row joins the one above only when it is short and number-free, the row
    above is not already a known modifier, and its first word could not have
    fit on that full-width line. Return the rows and whether flavour bounded them.
    """
    from PoE2_Data_Logger.ocr.item_ocr import _affix_match

    end = next((i for i, row in enumerate(rows) if flavour(row)), None)
    section = rows if end is None else rows[:end]
    widths = [row["right"] - row["x"] for row in body_rows
              if all(key in row for key in ("x", "right"))]
    width = max(widths, default=0)
    joined = []
    for row in section:
        previous = joined[-1] if joined else None
        text = row["text"]
        if (previous and width and all(key in row and key in previous for key in ("x", "right")) and
                not re.search(r"\d", text) and (len(text.split()) <= 3 or row["right"] - row["x"] <= width * .35) and
                _affix_match(previous["text"], affixes)[0] is None):
            first = text.split()[0] if text.split() else ""
            word = (row["right"] - row["x"]) * (len(first) + 1) / max(1, len(text))
            if previous["right"] - previous["x"] + word > width:
                previous["text"] += " " + text
                previous["score"] = min(float(previous.get("score", 1)), float(row.get("score", 1)))
                previous["right"] = max(previous["right"], row["right"])
                previous["x"] = min(previous["x"], row["x"])
                if "bottom" in row:
                    previous["bottom"] = row["bottom"]
                continue
        joined.append(row)
    return joined, end is not None


def parse_screen_tooltip(ocr_rows, affixes=(), *, normal_header_start=None, flavour=None):
    """Find a waystone/tablet title and parse its bounded tooltip body.

    Waystones need a drop-chance boundary or a complete explicitly Normal tooltip;
    tablets merge likely wrapped modifiers and retain OCR rows alongside proposals.
    ``flavour`` recognizes unique-coloured rows: a unique tablet title enables
    number-free modifiers, bounded by the flavour text that follows them.
    """
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
    if kind == "waystone":
        header_start = max(0, index - 2)
        if type(normal_header_start) is int and 0 <= normal_header_start < index:
            header_start = min(header_start, normal_header_start)
        rarity = [line for line in lines[header_start:index] if re.match(r"^Rarity\s*:", line, re.I)]
        usage = next((i for i in range(index + 1, len(lines)) if _WAYSTONE_USAGE.match(lines[i])), None)
        if (rarity and usage is not None and
                not any(re.fullmatch(r"Unidentified", line, re.I) for line in lines)):
            normal = parse_item_text("\n".join(["Item Class: Waystones",
                                                *lines[header_start:usage + 1]]), affixes)
            if normal and normal.get("_normal_unmodified"):
                normal["source"] = "screen OCR"
                return normal
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
        merged, uncertain_boundaries = _waystone_modifiers(mods)
        name = lines[index - 1] if index else ""
        tier = re.search(r"Waystone\s*\(Tier\s*(\d+)\)", body[0], re.I)
        props = ["Item Class: Waystones", name, body[0],
                 f"Waystone Tier: {tier.group(1)}", *body[1:first_mod], "Item Level: 0", *merged]
        parsed = parse_item_text("\n".join(props), affixes)
        if parsed and uncertain_boundaries:
            parsed["fields"]["map_mods"] = None
    else:
        start = next((i + 1 for i, line in enumerate(body)
                      if re.search(r"uses?\s*remaining\b", line, re.I)), None)
        if start is None:
            start = next((i for i, line in enumerate(body[1:], 1)
                          if looks_like_modifier(line) and not _PROPERTY.match(line)), None)
        if start is None:
            return None
        from PoE2_Data_Logger.ocr.item_ocr import merge_tablet_lines, parse_tablet

        rows = []
        for offset, line in enumerate(body[start:], index + start):
            if _PROPERTY.match(line) or _SEPARATOR.match(line):
                continue
            source = ocr_rows[offset]
            row = dict(source) if isinstance(source, dict) else {"score": 1.0}
            row["text"] = line.strip()
            rows.append(row)
        title = ocr_rows[index]
        unique = bool(flavour and isinstance(title, dict) and flavour(title))
        bounded = False
        if unique:
            rows, bounded = _unique_tablet_rows(rows, [row for row in ocr_rows[index:end] if isinstance(row, dict)],
                                                affixes, flavour)
        rows = merge_tablet_lines(rows)
        # Every row a flavour boundary encloses is a modifier, read or held.
        rows = [row for row in rows if bounded or looks_like_modifier(row["text"])]
        proposals = parse_tablet(rows, affixes, flags=bounded)
        if not proposals["matches"] and not proposals["uncertain"]:
            return None
        if unique and not bounded:
            proposals["status"] = "review"
        parsed = {"kind": kind, "mods": [row["text"] for row in rows],
                  **proposals, "ocr_rows": rows, **({"unique": True, "bounded": bounded} if unique else {})}
    if parsed:
        parsed["source"] = "screen OCR"
    return parsed


def _tooltip_visual_row(row, parts):
    """Rebuild one original visual row from its retained component boxes."""
    retained = sorted((dict(part) for part in parts), key=lambda part: part.get("x", 0))
    if "parts" not in row:
        return retained[0]
    visible = {**row, "parts": retained,
               "text": " ".join(part["text"] for part in retained),
               "score": min(float(part.get("score", row.get("score", 1))) for part in retained)}
    for key, aggregate in (("x", min), ("y", min), ("right", max), ("bottom", max)):
        if all(key in part for part in retained):
            visible[key] = aggregate(part[key] for part in retained)
    return visible


def _scaled_rarity_headers(rows, anchor, center):
    """Find title-aligned rarity witnesses within three observed text heights.

    This supplements the existing pixel margin only for coherent Waystone
    headers; another item's intervening text cannot supply a Normal rarity.
    """
    if ("bottom" not in anchor or
            not re.search(r"Waystone\s*\(Tier\s*\d+\)", anchor["text"], re.I)):
        return []
    height = anchor["bottom"] - anchor["y"]
    if not math.isfinite(height) or height <= 0:
        return []
    alignment = max(height, (anchor["right"] - anchor["x"]) * .15)
    radius = max(160, anchor["right"] - anchor["x"])

    def aligned(part):
        """Require finite OCR bounds and center alignment with the Waystone title."""
        return (all(key in part and math.isfinite(part[key]) for key in ("x", "y", "right", "bottom")) and
                abs((part["x"] + part["right"]) / 2 - center) <= alignment)

    visual_rows = []
    for row in rows:
        retained = [part for part in row.get("parts", [row])
                    if abs((part.get("x", center) + part.get("right", center)) / 2 - center) <= radius]
        if retained:
            visual_rows.append(_tooltip_visual_row(row, retained))
    headers = []
    for row in visual_rows:
        if (not aligned(row) or not re.match(r"^Rarity\s*:", row["text"], re.I) or
                not .5 * height <= row["bottom"] - row["y"] <= 1.5 * height or
                row["bottom"] > anchor["y"] or anchor["y"] - row["y"] > 3 * height):
            continue
        if any(aligned(other) and row["y"] < other["y"] < anchor["y"] and
               not re.match(r"^Rarity\s*:", other["text"], re.I) and
               not _SEPARATOR.fullmatch(other["text"].strip()) for other in visual_rows):
            continue
        headers.extend(row.get("parts", [row]))
    return headers


def read_screen_tooltip(image, affixes=(), ocr_rows=None, strictness=ocr_sensitivity.DEFAULT):
    """Read or reuse OCR rows, isolate the tooltip and retry unclear tablet percentages.

    The captured scan-type preference sets retry and review confidence floors.
    A dictionary supports automatic waystone/tablet routing from the same capture.
    Percentage syntax and affix constraints remain mandatory at every setting.
    """
    from PoE2_Data_Logger.ocr.item_ocr import ocr_lines
    rows = [dict(row) for row in ocr_rows] if ocr_rows is not None else ocr_lines(image)
    parts = [dict(part) for row in rows for part in row.get("parts", [row])]
    normal_header_start = None
    anchor = next((row for row in parts if _TABLET_TITLE.fullmatch(row["text"].strip()) or
                   re.search(r"Waystone\s*\(Tier\s*\d+\)", row["text"], re.I)), None)
    if anchor and all(key in anchor for key in ("x", "right", "y")):
        center = (anchor["x"] + anchor["right"]) / 2
        radius = max(160, anchor["right"] - anchor["x"])
        headers = _scaled_rarity_headers(rows, anchor, center)
        isolated = []
        for row in rows:
            retained = [part for part in row.get("parts", [row])
                        if (part.get("y", anchor["y"]) >= anchor["y"] - 60 or part in headers) and
                        abs((part.get("x", center) + part.get("right", center)) / 2 - center) <= radius]
            if not retained:
                continue
            # Parts exclude unrelated UI; their original visual row remains
            # the modifier boundary, even when OCR emitted the right half first.
            isolated.append(_tooltip_visual_row(row, retained))
        rows = isolated
        rows.sort(key=lambda row: (row.get("y", 0), row.get("x", 0)))
        # Only geometry-verified header context may extend Normal proof past
        # its existing two rows; retain every coherent conflict before separators.
        normal_header_start = next((i for i, row in enumerate(rows)
                                    if re.match(r"^Rarity\s*:", row["text"], re.I) and
                                    any(part in headers for part in row.get("parts", [row]))), None)
    def flavour(row):
        """Recognize unique-coloured title and flavour rows that carry OCR geometry."""
        return all(key in row for key in ("x", "y", "right", "bottom")) and _unique_ink(image, row)

    parsed = parse_screen_tooltip(rows, affixes, normal_header_start=normal_header_start, flavour=flavour)
    level = ocr_sensitivity.validate(strictness.get(parsed["kind"], ocr_sensitivity.DEFAULT)
                                    if isinstance(strictness, dict) and parsed else
                                    ocr_sensitivity.DEFAULT if isinstance(strictness, dict) else strictness)
    retry_floor = ocr_sensitivity.clear_threshold(.94, level, .14)
    review_floor = ocr_sensitivity.clear_threshold(.9, level, .1)
    if parsed and parsed["kind"] == "tablet":
        changed = False
        for row in rows:
            ambiguous = re.search(r"\b(?=[A-Za-z0-9]{1,3}\s*%)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,3}\s*%", row["text"])
            if not ambiguous and float(row.get("score", 1)) >= retry_floor:
                continue
            if not all(key in row for key in ("x", "y", "right", "bottom")):
                continue
            if _rollless_ink(image, row):
                continue
            box = (max(0, int(row["x"]) - 8), max(0, int(row["y"]) - 2),
                   min(image.width, int(row["right"]) + 8), min(image.height, int(row["bottom"]) + 2))
            crop = image.crop(box)
            crop = crop.resize((crop.width * 3, crop.height * 3))
            retry = ocr_lines(crop)
            if not any(_PERCENT.search(item["text"]) and item["score"] >= retry_floor for item in retry):
                crop = image.crop((max(0, int(row["x"]) - 8), max(0, int(row["y"])),
                                   min(image.width, int(row["right"]) + 8), min(image.height, int(row["bottom"]))))
                retry = ocr_lines(crop.resize((crop.width * 3, crop.height * 3)))
            retry = [item for item in retry if _PERCENT.search(item["text"]) and item["score"] >= retry_floor]
            if len(retry) == 1 and _PERCENT.search(retry[0]["text"]) and retry[0]["score"] >= retry_floor:
                row["text"], row["score"] = retry[0]["text"], retry[0]["score"]
                changed = True
            normalized = re.sub(r"(?<!\w)(?=[0-9Oo]*\d)[0-9Oo]{1,4}(?=\s*%)",
                                lambda match: match.group().replace("O", "0").replace("o", "0"), row["text"])
            if normalized != row["text"]:
                row["text"] = normalized
                changed = True
        if changed:
            parsed = parse_screen_tooltip(rows, affixes, normal_header_start=normal_header_start, flavour=flavour)
        if parsed and any(float(row.get("score", 1)) < review_floor for row in parsed.get("ocr_rows", [])):
            parsed["uncertain"] = list(dict.fromkeys(parsed.get("uncertain", []) +
                                        [row["text"] for row in parsed["ocr_rows"] if float(row.get("score", 1)) < review_floor]))
    if parsed:
        parsed.setdefault("ocr_rows", rows)
        if level != ocr_sensitivity.DEFAULT:
            parsed["_ocr_strictness"] = level
    return parsed


_RITE_LABEL = re.compile(r"^FORETOLD\s+(BOUNTY|PROLIFERATION)\s*:", re.I)
_RITE_SUBJECT = re.compile(r"FORETOLD\s+(?:BOUNTY|PROLIFERATION)|RITUAL\s+ALTARS|\bRITUALS?\s+CONTAIN", re.I)


def _rite_key(text):
    """Compare Rite wording across OCR spacing, case and a/an/one/1 variants."""
    return "".join("1" if word in ("a", "an", "one") else word
                   for word in re.findall(r"[a-z0-9]+", text.lower()))


def _flavour_row(image, row):
    """Recognize the orange flavour text, whose ink is far redder than modifier text."""
    box = tuple(round(row[key]) for key in ("x", "y", "right", "bottom"))
    pixels = np.asarray(image.convert("RGB").crop(box)).reshape(-1, 3)
    ink = pixels[pixels.max(axis=1) > 90]
    return bool(len(ink)) and float(np.median(ink[:, 0])) > float(np.median(ink[:, 2])) * 1.4


def _ink(image, row):
    """Return the median colour of a row's bright text pixels, or None without ink."""
    box = tuple(round(row[key]) for key in ("x", "y", "right", "bottom"))
    pixels = np.asarray(image.convert("RGB").crop(box)).reshape(-1, 3)
    ink = pixels[pixels.max(axis=1) > 90]
    return tuple(float(np.median(ink[:, channel])) for channel in range(3)) if len(ink) else None


def _unique_ink(image, row):
    """Recognize the orange unique title and flavour ink, unlike yellow rare titles or gold."""
    colour = _ink(image, row)
    return colour is not None and colour[0] > colour[2] * 1.4 and colour[1] < colour[0] * .7


def _rollless_ink(image, row):
    """Recognize grey usage text and orange title or flavour text, which never carry modifier rolls."""
    colour = _ink(image, row)
    return colour is not None and (max(colour) <= min(colour) * 1.1 or
                                   colour[0] > colour[2] * 1.4 and colour[1] < colour[0] * .7)


def read_rite_tooltip(ocr_rows, image):
    """Return Rite of the Nameless modifiers from a hovered atlas map tooltip, else None.

    Modifier lines sit between the Biome line and the orange flavour text. A
    wrapped line rejoins its modifier until a catalog modifier completes, a
    Foretold label starts the next one, or the previous line ended with room
    for the next word. Unknown wording is kept as a custom modifier.
    """
    from PoE2_Data_Logger.core import ritual_rites_catalog

    texts = [" ".join(row["text"].split()) for row in ocr_rows]
    biome = next((index for index, text in enumerate(texts) if re.match(r"^BIOME\s*:", text, re.I)), None)
    if biome is None or not any(_RITE_SUBJECT.search(text) for text in texts[biome + 1:]):
        return None
    lines = []
    for row, text in zip(ocr_rows[biome + 1:], texts[biome + 1:]):
        if re.match(r"^REQUIRES\b", text, re.I) or _flavour_row(image, row):
            break
        lines.append((row, text))
    if not lines:
        return None
    catalog = {_rite_key(row["text"]): row for row in ritual_rites_catalog.affixes()}
    casing = {word.lower(): word for row in catalog.values()
              for word in re.findall(r"[A-Za-z']+", row["text"]) if word[0].isupper()}
    width = max(row["right"] - row["x"] for row, _ in lines)
    blocks = []
    for index, (row, text) in enumerate(lines):
        if blocks and not _RITE_LABEL.match(text):
            current = blocks[-1]
            previous = lines[index - 1][0]
            first = text.split()[0]
            word = (row["right"] - row["x"]) * (len(first) + 1) / max(1, len(text))
            # A word that fit at the end of the previous line would be there.
            wrapped = previous["right"] - previous["x"] + word > width
            if (wrapped and _rite_key(current) not in catalog and
                    not re.match(r"^FORETOLD\s+BOUNTY", current, re.I)):
                blocks[-1] = current + " " + text
                continue
        blocks.append(text)
    affixes = []
    for block in blocks:
        known = catalog.get(_rite_key(block))
        if known is not None:
            entry = {"id": known["id"]}
        else:
            # The tooltip font is small caps; restore catalog capitalization.
            label = _RITE_LABEL.match(block)
            bounty = label is not None and label.group(1).upper() == "BOUNTY"
            words = []
            for word in block.split():
                core = word.strip(":,.")
                cased = casing.get(core.lower(), core.lower())
                if bounty and len(words) > 1 and cased.islower() and (len(words) == 2 or cased not in ("of", "the")):
                    # Bounty rewards are item names, written in title case.
                    cased = cased[:1].upper() + cased[1:]
                words.append(word.replace(core, cased) if core else word)
            text = " ".join(words)
            entry = {"text": text[:1].upper() + text[1:],
                     "kind": label.group(1).lower() if label else "local"}
        same = next((row for row in affixes if row.get("id", row.get("text")) == entry.get("id", entry.get("text"))), None)
        if same is not None:
            same["count"] += 1
        else:
            affixes.append({**entry, "count": 1})
    return {"kind": "rite", "map_name": texts[0].title() if biome else "", "affixes": affixes}
