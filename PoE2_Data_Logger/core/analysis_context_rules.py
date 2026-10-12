"""Verified linear display rules for the Atlas analysis context.

These rules translate raw source constants into the catalog's displayed units.
They do not decide whether an effect applies, combine different stats, evaluate
per-monster/per-tablet conditions, or simulate the game's stacking and caps.
"""

from __future__ import annotations

from functools import lru_cache
import gzip
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from string import Formatter

from PoE2_Data_Logger.core.atlas_catalog import ATLAS_DIR, catalog


def _canonical(value):
    """Use stable source identities regardless of dictionary insertion order."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plain_text(text):
    """Remove glossary markup while retaining its displayed mechanical wording."""
    # Keep the displayed part of game glossary links, exactly as the catalog
    # builder does; their internal link IDs are not units or effect formulas.
    return re.sub(r"\[([^\[\]]+)\]", lambda match: match.group(1).split("|")[-1], text).strip()


def _valid_bound(value):
    """Accept finite numeric translation bounds while rejecting boolean constants."""
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)
                             and math.isfinite(value))


def _single_placeholder(text):
    """Require one plain numeric placeholder, excluding coupled or formatted fields."""
    if text.count("{0}") != 1:
        return False
    try:
        fields = [(name, specification, conversion)
                  for _, name, specification, conversion in Formatter().parse(text)
                  if name is not None]
    except ValueError:
        return False
    return fields == [("0", "", None)]


def _display_unit(template):
    """Read the interpolated value's unit, rather than an unrelated literal cap."""
    # Other numbers in an effect (for example a literal 100% minimum reward)
    # must not determine the unit of its one interpolated value.
    suffix = template.split("{0}", 1)[1]
    if suffix.startswith("%"):
        return "%"
    if re.match(r"\s*seconds?\b", suffix, re.I):
        return "seconds"
    if re.match(r"\s*(?:(?:additional|extra|Reactivation|Rare|Magic|Normal|Unique)\s+)*"
                r"(?:Monsters?|Packs?|Modifiers?|Runes?|Rooms?|Wombs?|Shrines?|Strongboxes?|"
                r"Chests?|Breaches?|Abysses?)\b", suffix, re.I):
        return "count"
    if re.match(r"\s*to (?:maximum|minimum) (?:Prefixes|Suffixes|(?:Monster )?Modifiers)\b",
                suffix, re.I):
        return "count"
    return "catalog display units"


def _rules_from_inputs(inputs, expected_hashes):
    """Select eligible rules only when their complete pinned input identity matches.

    Translation min/max ranges select a wording branch, not game conditions.
    First eligible entry for a stat/range wins in the source's priority order.
    """
    if not isinstance(inputs, dict):
        return []
    hashes = inputs.get("sources")
    if not isinstance(hashes, dict) or not hashes or hashes != expected_hashes:
        return []
    source_hashes_json = _canonical(hashes)
    rules = []
    seen = set()
    for entry in inputs.get("translations") or []:
        ids = entry.get("ids")
        if (entry.get("hidden") or not isinstance(ids, list) or len(ids) != 1
                or not isinstance(ids[0], str) or not ids[0]):
            continue
        for english in entry.get("English") or []:
            conditions = english.get("condition")
            formats = english.get("format")
            if (not isinstance(conditions, list) or len(conditions) != 1
                    or not isinstance(conditions[0], dict)
                    or formats not in (["#"], ["+#"])):
                continue
            condition = conditions[0]
            if (set(condition) - {"min", "max", "negated"}
                    or condition.get("negated") not in (None, False)):
                continue
            minimum, maximum = condition.get("min"), condition.get("max")
            if (not _valid_bound(minimum) or not _valid_bound(maximum)
                    or minimum is not None and maximum is not None and minimum > maximum):
                continue
            original = english.get("string")
            if not isinstance(original, str):
                continue
            template = _plain_text(original)
            if not _single_placeholder(template):
                continue
            handlers = english.get("index_handlers") or [[]]
            if (not isinstance(handlers, list) or len(handlers) != 1
                    or not isinstance(handlers[0], list)):
                continue
            divisor, multiplier = 1, 1
            supported = True
            for handler in handlers[0]:
                if handler == "negate":
                    multiplier *= -1
                elif handler in ("divide_by_one_hundred", "divide_by_one_hundred_2dp_if_required"):
                    divisor *= 100
                elif handler == "milliseconds_to_seconds_2dp_if_required":
                    divisor *= 1000
                else:
                    supported = False
                    break
            if not supported or divisor not in (1, 100, 1000):
                continue
            key = (ids[0], minimum, maximum)
            if key in seen:
                continue
            seen.add(key)
            rule = {"source_hashes_json": source_hashes_json,
                    "stat_id": ids[0], "min_raw": minimum, "max_raw": maximum,
                    "divisor": divisor, "multiplier": multiplier,
                    "template": template, "unit": _display_unit(template),
                    "format": formats[0]}
            rule["rule_key"] = "atlas-linear-" + hashlib.sha256(
                _canonical(rule).encode("utf-8")).hexdigest()
            rules.append(rule)
    return rules


@lru_cache(maxsize=1)
def _bundled_rules():
    """Load optional display metadata; unavailable translations leave frozen raw facts usable."""
    try:
        with gzip.open(Path(ATLAS_DIR) / "sources/inputs.json.gz", "rt", encoding="utf-8") as handle:
            inputs = json.load(handle)
    except (OSError, ValueError) as error:
        logging.getLogger(__name__).warning("Atlas context translations unavailable: %s", error)
        return ()
    expected = catalog().get("provenance", {}).get("input_hashes")
    return tuple(_rules_from_inputs(inputs, expected))


def linear_stat_rules() -> list[dict]:
    """Return independent rule dictionaries for verified pinned Atlas source data.

    A frozen catalog must match ``source_hashes_json`` before a rule is used.
    The caller evaluates the raw branch bounds and displays
    ``raw_value * multiplier / divisor`` using the template/format.
    These are source/display totals, not final applied game bonuses.
    """
    return [dict(rule) for rule in _bundled_rules()]
