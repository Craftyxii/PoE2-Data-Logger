"""Persist per-scan OCR strictness and derive confidence gates without changing map data.

Fifty reproduces each scanner's existing thresholds. Lower values admit more
tentative matches; higher values require stronger evidence. Scanners receive
captured preferences explicitly so an in-flight scan cannot change policy.
"""

DEFAULT = 50
SCAN_TYPES = (
    ("seed", "Seed / rune bar"),
    ("remnant", "Remnant recipes"),
    ("propagation", "Propagation"),
    ("waystone", "Waystone / map modifiers"),
    ("tablet", "Tablet modifiers"),
    ("currency", "Currency inventory"),
    ("ritual", "Ritual rewards"),
)
_KEYS = {kind: kind + "_ocr_strictness" for kind, _label in SCAN_TYPES}


def validate(level):
    """Require an integer slider value from zero through one hundred without coercion."""
    if type(level) is not int or not 0 <= level <= 100:
        raise ValueError("OCR strictness must be a whole number from 0 to 100.")
    return level


def saved_values():
    """Read each scan preference, falling back to current behavior for corrupt values."""
    from PoE2_Data_Logger.core import logger_store as logger

    values = {}
    with logger._connect() as db:
        for kind, key in _KEYS.items():
            try:
                values[kind] = validate(logger._meta(db, key, DEFAULT))
            except (TypeError, ValueError):
                values[kind] = DEFAULT
    return values


def save_values(values):
    """Atomically persist all scan preferences without creating map commits."""
    from PoE2_Data_Logger.core import logger_store as logger

    if not isinstance(values, dict) or set(values) != set(_KEYS):
        raise ValueError("Choose OCR strictness for every scan type.")
    values = {kind: validate(values[kind]) for kind in _KEYS}
    with logger._connect() as db:
        for kind, key in _KEYS.items():
            logger._set_meta(db, key, values[kind])
    return values


def review_threshold(base, level, spread):
    """Shift an evidence floor linearly, keeping unit confidence scores within zero to one."""
    level = validate(level)
    if level == DEFAULT:
        return base
    threshold = base + (level - DEFAULT) / DEFAULT * spread
    return min(1.0, max(0.0, threshold)) if 0 <= base <= 1 else threshold


def clear_threshold(base, level, spread):
    """Apply the same strictness direction to automatic acceptance evidence floors."""
    return review_threshold(base, level, spread)
