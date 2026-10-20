"""OCR runtime preferences shared by the app's cached recognition workers.

The active value is fixed for this process before scanners start. Saving a
different preference never rebuilds models or changes an in-flight scan.
"""
from __future__ import annotations

import threading


DEFAULT_THREADS = 2
THREAD_OPTIONS = (1, 2, 4, 6)
_META_KEY = "ocr_cpu_threads"
DEFAULT_GPU_LABELS = False
_GPU_META_KEY = "ocr_gpu_labels"
_ACTIVE_THREADS = None
_ACTIVE_GPU_LABELS = None
_ACTIVE_LOCK = threading.Lock()


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


def saved_gpu_labels():
    """Read the optional Currency/Ritual GPU preference, accepting only a saved boolean."""
    from PoE2_Data_Logger.core import logger_store as logger

    with logger._connect() as db:
        try:
            enabled = logger._meta(db, _GPU_META_KEY, DEFAULT_GPU_LABELS)
        except (TypeError, ValueError):
            enabled = DEFAULT_GPU_LABELS
    return enabled if type(enabled) is bool else DEFAULT_GPU_LABELS


def active_gpu_labels():
    """Freeze the process GPU preference before scanners start; later changes need restart."""
    global _ACTIVE_GPU_LABELS
    with _ACTIVE_LOCK:
        if _ACTIVE_GPU_LABELS is None:
            _ACTIVE_GPU_LABELS = saved_gpu_labels()
        return _ACTIVE_GPU_LABELS


def save_gpu_labels(enabled):
    """Persist the next startup's optional GPU setting without changing an active worker."""
    if type(enabled) is not bool:
        raise ValueError("GPU OCR must be on or off.")
    from PoE2_Data_Logger.core import logger_store as logger

    active_gpu_labels()
    with logger._connect() as db:
        logger._set_meta(db, _GPU_META_KEY, enabled)
    return enabled
