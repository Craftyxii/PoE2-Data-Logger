"""CPU OCR preference shared by the app's cached ONNX sessions.

The active value is fixed for this process before scanners start. Saving a
different preference never rebuilds models or changes an in-flight scan.
"""
from __future__ import annotations

import threading


DEFAULT_THREADS = 2
THREAD_OPTIONS = (1, 2, 4, 6)
_META_KEY = "ocr_cpu_threads"
_ACTIVE_THREADS = None
_ACTIVE_LOCK = threading.Lock()


def saved_threads():
    from PoE2_Data_Logger.core import logger_store as logger

    with logger._connect() as db:
        try:
            count = logger._meta(db, _META_KEY, DEFAULT_THREADS)
        except (TypeError, ValueError):
            # An invalid stored preference must not prevent OCR startup.
            count = DEFAULT_THREADS
    return count if type(count) is int and count in THREAD_OPTIONS else DEFAULT_THREADS


def active_threads():
    global _ACTIVE_THREADS
    with _ACTIVE_LOCK:
        if _ACTIVE_THREADS is None:
            _ACTIVE_THREADS = saved_threads()
        return _ACTIVE_THREADS


def save_threads(count):
    if type(count) is not int or count not in THREAD_OPTIONS:
        raise ValueError("CPU OCR threads must be 1, 2, 4 or 6.")
    from PoE2_Data_Logger.core import logger_store as logger

    # Freeze this process even when called before its first OCR session exists.
    active_threads()
    with logger._connect() as db:
        logger._set_meta(db, _META_KEY, count)
    return count
