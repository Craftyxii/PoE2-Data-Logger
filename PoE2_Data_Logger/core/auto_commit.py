"""Gate automatic remnant logging on opened OCR evidence and pending scan context.

Candidate checks resolve the observed reward order against the recipe catalog;
the final write receives the pending, opened and optional seed context guards.
"""

from __future__ import annotations

import re
import math

from PoE2_Data_Logger.core import logger_store as logger


def _family(label):
    """Parse only a complete "Family N" label; return None for other labels."""
    match = re.fullmatch(r"Family (\d+)", str(label or ""))
    return int(match.group(1)) if match else None


def _score(value):
    """Coerce a confidence value to a finite fraction, using zero for invalid scores."""
    try:
        score = float(value)
        return score if math.isfinite(score) and 0 <= score <= 1 else 0.0
    except (TypeError, ValueError):
        return 0.0


def candidate(opened, seed=None, resolver=logger.resolve):
    """Require one family, icon-derived sockets and confident ordered opened rewards.

    Reject contradictory seed evidence and require the catalog resolver to match
    the observed reward prefix before returning commit arguments.
    """
    def review(reason):
        """Return the manual-review reason in the common non-ready result shape."""
        return {"ready": False, "reason": reason}

    if not isinstance(opened, dict):
        return review("Scan an opened remnant to save it automatically.")
    family = _family(opened.get("family"))
    if (opened.get("status") != "Review the opened rewards before logging." or
            opened.get("can_use") is not True or family is None or
            opened.get("candidates") != [family]):
        return review("The opened remnant needs manual review.")
    sockets = opened.get("sockets")
    if (type(sockets) is not int or sockets not in range(3, 11) or
            opened.get("socket_source") != "opened icons" or
            sockets != opened.get("recipe_sockets")):
        return review("The opened socket count needs manual review.")
    gap = opened.get("first_line_gap")
    if type(gap) is not int or not 30 <= gap <= 90:
        return review("The first opened reward line needs manual review.")
    lines = opened.get("opened_recipes")
    if (not isinstance(lines, list) or not lines or
            any(not isinstance(line, dict) or not line.get("recipe") or
                _score(line.get("ocr_score")) < .8 or
                _score(line.get("match_score")) < .95 for line in lines)):
        return review("Review the opened reward lines before saving.")
    if isinstance(seed, dict):
        seed_family = _family(seed.get("family"))
        seed_sockets = seed.get("sockets")
        if (seed_family is not None and seed.get("candidates") == [seed_family] and
                seed_family != family) or (type(seed_sockets) is int and
                                          seed_sockets in range(3, 11) and seed_sockets != sockets):
            return review("The two scans disagree; review this remnant before saving.")
    first, second = opened.get("first_recipe"), opened.get("next_recipe")
    if first != lines[0]["recipe"] or (len(lines) > 1 and second != lines[1]["recipe"]):
        return review("The opened reward order needs manual review.")
    if len(lines) == 1 and second:
        return review("The single opened reward needs manual review.")
    try:
        resolved = resolver(first, second, family)
    except (ValueError, TypeError):
        return review("The recipe could not be resolved automatically.")
    rows = resolved.get("rows") or []
    if (resolved.get("status") != "ready" or resolved.get("family") != family or
            not rows or rows[0]["sockets"] != sockets or len(lines) > len(rows) or
            [line["recipe"] for line in lines] != [row["recipe"] for row in rows[:len(lines)]]):
        return review("The opened rewards do not match a verified family sequence.")
    return {"ready": True, "first": first, "next": second, "family": family}


def commit(opened, seed=None, scan_id=None):
    """Check the auto-commit toggle and pending IDs before a guarded remnant write.

    Pass the observed contexts to commit_remnant so its write can reject stale
    evidence; return validation failures as reasons for manual review.
    """
    with logger._connect() as db:
        enabled = bool(logger._meta(db, "settings").get("auto_commit", False))
        pending = logger._meta(db, "ocr_pending")
    if not enabled:
        return {"committed": False, "reason": "Auto-commit is off."}
    if not pending or not isinstance(opened, dict) or any(
        opened.get(key) != pending[key] for key in ("remnant_id", "map_id", "expedition_id")
    ):
        return {"committed": False, "reason": "The opened scan is not the current remnant."}
    if isinstance(seed, dict) and any(
        seed.get(key) != pending[key] for key in ("remnant_id", "map_id", "expedition_id")
    ):
        return {"committed": False, "reason": "The two scans belong to different remnants."}
    result = candidate(opened, seed)
    if not result["ready"]:
        return {"committed": False, "reason": result["reason"]}
    try:
        saved = logger.commit_remnant(result["first"], result["next"], result["family"],
                                      scan_id, expected_pending=pending, expected_context=opened,
                                      seed_context=seed if isinstance(seed, dict) else None)
    except ValueError as error:
        return {"committed": False, "reason": str(error)}
    return {"committed": True, **saved}
