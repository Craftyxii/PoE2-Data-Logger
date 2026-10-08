"""Stage related export files together and recover their previous contents.

The filesystem cannot replace two files atomically.  Keeping recovery copies
until every replacement succeeds prevents a locked second spreadsheet from
    leaving the first export silently updated on its own.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping


class ExportWriteError(OSError):
    """An export failed, possibly with recovery copies that must be retained."""


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Cleanup must not mask the save failure or remove a recovery copy.
        pass


def _stage_bytes(destination: Path, data: bytes) -> Path:
    descriptor, filename = tempfile.mkstemp(
        prefix=".PoE2_Data_Export_", suffix=".tmp", dir=destination.parent)
    staged = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except BaseException:
        _remove_temporary(staged)
        raise
    return staged


def _backup_file(destination: Path) -> Path:
    descriptor, filename = tempfile.mkstemp(
        prefix=".PoE2_Data_Export_", suffix=".recovery", dir=destination.parent)
    os.close(descriptor)
    backup = Path(filename)
    try:
        shutil.copy2(destination, backup)
    except BaseException:
        _remove_temporary(backup)
        raise
    return backup


def write_export_files(files: Mapping[Path, bytes], *, replace: bool = True) -> None:
    """Write all files, restoring previous files if any replacement fails.

    Data and recovery copies are fully written and closed before any target is
    replaced.  A recovery copy is never deleted if restoring that file fails;
    the raised error names the copy so the user can recover it after unlocking
    the destination.  This protects ordinary save failures, not power loss or
    other applications editing the same files concurrently.
    """
    if not files:
        raise ValueError("Choose at least one export destination.")
    destinations = []
    identities = set()
    for supplied, data in files.items():
        destination = Path(supplied).absolute()
        identity = os.path.normcase(str(destination.resolve()))
        if identity in identities:
            raise ValueError("Related exports must use different destination files.")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Export contents must be bytes.")
        identities.add(identity)
        destinations.append((destination, data))

    staged = {}
    backups = {}
    changed = []
    retained = set()
    try:
        for destination, data in destinations:
            staged[destination] = _stage_bytes(destination, data)
        for destination, _ in destinations:
            if not replace:
                # Reserve each new name exclusively before replacing it with
                # the staged bytes. Another export cannot claim the same name.
                with destination.open("xb"):
                    pass
                changed.append(destination)
            elif destination.exists() or destination.is_symlink():
                backups[destination] = _backup_file(destination)
        for destination, _ in destinations:
            os.replace(staged[destination], destination)
            if replace:
                changed.append(destination)
    except BaseException as error:
        failures = []
        for destination in reversed(changed):
            backup = backups.get(destination)
            try:
                if backup is not None:
                    os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError as rollback_error:
                if backup is not None:
                    retained.add(backup)
                    failures.append(f"Recovery copy for {destination}: {backup} ({rollback_error})")
                else:
                    failures.append(f"Could not remove incomplete export {destination}: {rollback_error}")
        if not isinstance(error, Exception):
            raise
        if failures:
            outcome = "Some previous exports could not be restored. " + "; ".join(failures)
        elif changed:
            outcome = "Previous exports were restored."
        else:
            outcome = "No export files were replaced."
        raise ExportWriteError(f"Could not save export files: {error}. {outcome}") from error
    finally:
        for temporary in (*staged.values(), *backups.values()):
            if temporary not in retained:
                _remove_temporary(temporary)
