"""OCR runtime preferences shared by the app's cached recognition workers.

The active value is fixed for this process before scanners start. Saving a
different preference never rebuilds models or changes an in-flight scan.
"""
from __future__ import annotations

import threading


DEFAULT_THREADS = 2
THREAD_OPTIONS = (1, 2, 4, 6)
_META_KEY = "ocr_cpu_threads"
_FAST_KEY = "ocr_fast_recognition"
_ACTIVE_THREADS = None
_ACTIVE_LOCK = threading.Lock()
# Fast recognition reaches the same readings by quicker arithmetic. Each worker job
# carries the preference saved when its scan was taken; off runs the original path.
_FAST = None


def saved_threads():
    """Read the SQLite thread preference, falling back for invalid values or types."""
    from PoE2_Data_Logger.core import logger_store as logger

    with logger._connect() as db:
        try:
            count = logger._meta(db, _META_KEY, DEFAULT_THREADS)
        except (TypeError, ValueError):
            # An invalid stored preference must not prevent OCR startup.
            count = DEFAULT_THREADS
    return count if type(count) is int and count in THREAD_OPTIONS else DEFAULT_THREADS


def active_threads():
    """Freeze the process thread count once under a lock for shared OCR sessions."""
    global _ACTIVE_THREADS
    with _ACTIVE_LOCK:
        if _ACTIVE_THREADS is None:
            _ACTIVE_THREADS = saved_threads()
        return _ACTIVE_THREADS


def saved_fast_recognition():
    """Read the Fast recognition preference: on unless it was switched off."""
    from PoE2_Data_Logger.core import logger_store as logger

    with logger._connect() as db:
        return logger._meta(db, _FAST_KEY, True) is not False


def save_fast_recognition(enabled):
    """Persist Fast recognition for the next scans."""
    if type(enabled) is not bool:
        raise ValueError("Fast recognition must be on or off.")
    from PoE2_Data_Logger.core import logger_store as logger

    with logger._connect() as db:
        logger._set_meta(db, _FAST_KEY, enabled)
    return enabled


def fast_recognition():
    """Whether this scan uses Fast recognition (set per job in a worker)."""
    global _FAST
    if _FAST is None:
        _FAST = saved_fast_recognition()
    return _FAST


def save_threads(count):
    """Persist an allowed future thread count after freezing this process's active count."""
    if type(count) is not int or count not in THREAD_OPTIONS:
        raise ValueError("CPU OCR threads must be 1, 2, 4 or 6.")
    from PoE2_Data_Logger.core import logger_store as logger

    # Freeze this process even when called before its first OCR session exists.
    active_threads()
    with logger._connect() as db:
        logger._set_meta(db, _META_KEY, count)
    return count
