"""Offline, source-backed Rite of the Nameless modifier choices.

The catalog records published rows, including raw untranslated rows; it does not
claim every row is currently eligible to roll. ``rite:`` catalog IDs and
``logger:rite:`` semantic stat IDs are this application's identifiers, never
claimed game IDs. There are no effect, density or roll-weight calculations here.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parent.parent / "ritual_rites_catalog.json"
KINDS = frozenset(("local", "proliferation", "bounty"))


@lru_cache(maxsize=1)
def _bundled_catalog() -> dict:
    """Load and cache bundled Rite choices after checking their schema, kinds and unique IDs."""
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data.get("affixes")
    if data.get("schema_version") != 1 or not isinstance(rows, list):
        raise ValueError("Unsupported bundled Rite modifier catalog")
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or row.get("kind") not in KINDS
                or not row.get("id") or not row.get("text")
                or row["id"] in ids):
            raise ValueError("Invalid bundled Rite modifier catalog")
        ids.add(row["id"])
    return data


def catalog() -> dict:
    """Return isolated catalog data without network access or OCR initialization."""
    return deepcopy(_bundled_catalog())


def affixes() -> tuple[dict, ...]:
    """Return isolated modifier choices in the published source order."""
    return tuple(deepcopy(_bundled_catalog()["affixes"]))


def affix(affix_id: str) -> dict | None:
    """Look up a bundled ID, returning a copy so callers cannot alter defaults."""
    return next((deepcopy(row) for row in _bundled_catalog()["affixes"]
                 if row["id"] == affix_id), None)


def _positive_count(value: Any) -> int:
    """Require a canonical positive integer count, rejecting booleans and lossy coercions."""
    if isinstance(value, bool):
        raise ValueError("Rite modifier count must be a positive integer")
    try:
        count = int(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Rite modifier count must be a positive integer") from exc
    if count < 1 or str(value).strip() != str(count):
        raise ValueError("Rite modifier count must be a positive integer")
    return count


def _custom_affix(value: dict) -> dict:
    """Give user-entered wording a stable identity and provenance without inferring game stats."""
    text = value.get("text", value.get("description", ""))
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A custom Rite modifier needs a description")
    text = text.strip()
    kind = value.get("kind", "local")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Unknown Rite modifier kind")
    inherited = value.get("inherited_text", "")
    if not isinstance(inherited, str):
        raise ValueError("Inherited Rite modifier text must be text")
    inherited = inherited.strip()
    if kind == "proliferation" and not inherited:
        inherited = text.removeprefix("Foretold Proliferation:").strip()
    if kind != "proliferation":
        inherited = ""
    # Typed future modifiers retain their wording without invented stat values.
    identity = json.dumps([kind, text, inherited], ensure_ascii=False,
                          separators=(",", ":"))
    return {
        "id": "custom:rite:" + sha256(identity.encode("utf-8")).hexdigest()[:24],
        "text": text, "kind": kind, "inherited_text": inherited,
        "stat_id": None, "value": None, "unit": "", "custom": True,
        "provenance": {"source": "user_entered", "source_urls": [],
                       "source_verified": None, "source_raw_description": text,
                       "source_raw_stat": None},
    }


def normalize_affixes(values: list | tuple | None) -> list[dict]:
    """Canonicalize selected IDs/counts and preserve explicitly typed modifiers.

    A known ID always supplies the bundled text, kind and stat data; only its
    positive occurrence ``count`` is editable. Unknown explicit catalog IDs fail
    instead of silently becoming new choices. A custom row supplies ``text`` (or
    ``description``), optional ``kind`` and optional ``inherited_text``. Custom
    effects carry no inferred numeric/game-stat data. Selection order and repeat
    rows are retained so callers can model their own map and Rite boundaries.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("Rite modifiers must be a list")
    known = {row["id"]: row for row in _bundled_catalog()["affixes"]}
    normalized = []
    for value in values:
        if isinstance(value, str):
            value = {"id": value}
        if not isinstance(value, dict):
            raise ValueError("Each Rite modifier must be an ID or an object")
        count = _positive_count(value.get("count", 1))
        selected_id = value.get("id")
        if selected_id is not None and not isinstance(selected_id, str):
            raise ValueError("Rite modifier ID must be text")
        if selected_id in known:
            row = deepcopy(known[selected_id])
        elif selected_id and not str(selected_id).startswith("custom:rite:"):
            raise ValueError("Unknown Rite modifier catalog ID")
        else:
            row = _custom_affix(value)
        row["count"] = count
        normalized.append(row)
    return normalized
