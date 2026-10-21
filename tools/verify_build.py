"""Inspect a frozen Windows launcher, embedded application modules and bundled native runtime files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

from PyInstaller.archive.readers import CArchiveReader


UNIQUE_CATALOG_SHA256 = '4e71e5f8b74218dfcceab69592ffd7cceb3081fc4665b5d1bc7c7219f6de8a7e'


def verify_unique_catalog(directory):
    """Require the reviewed catalog, its credits and every original artwork file."""
    directory = Path(directory)
    manifest = directory / 'manifest.json'
    if not manifest.is_file():
        raise ValueError('Missing bundled unique catalog: manifest.json')
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != UNIQUE_CATALOG_SHA256:
        raise ValueError('Bundled unique catalog manifest integrity check failed.')
    notice = directory / 'NOTICE.txt'
    if not notice.is_file() or notice.stat().st_size == 0:
        raise ValueError('Missing bundled unique catalog credits: NOTICE.txt')
    checked = set()
    for row in json.loads(raw)['items']:
        name = row['icon']
        if name in checked:
            continue
        path = directory / name
        if not path.is_file():
            raise ValueError(f'Missing bundled unique artwork: {name}')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != row['image_sha256']:
            raise ValueError(f'Bundled unique artwork integrity check failed: {name}')
        checked.add(name)
    return len(checked)


def pe_machine(path):
    """Validate PE headers, section bounds and file completeness, then return the executable
    machine architecture.
    """
    with Path(path).open('rb') as stream:
        data = stream.read(65536)
        size = stream.seek(0, 2)
    if data[:2] != b'MZ' or len(data) < 64:
        raise ValueError(f'Invalid Windows executable: {Path(path).name}')
    offset = struct.unpack_from('<I', data, 60)[0]
    if offset + 94 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError(f'Invalid PE header: {Path(path).name}')
    sections = struct.unpack_from('<H', data, offset + 6)[0]
    optional_size = struct.unpack_from('<H', data, offset + 20)[0]
    table = offset + 24 + optional_size
    if not sections or table + sections * 40 > len(data):
        raise ValueError(f'Invalid PE section table: {Path(path).name}')
    for index in range(sections):
        raw_size, raw_offset = struct.unpack_from('<II', data, table + index * 40 + 16)
        if raw_size and raw_offset + raw_size > size:
            raise ValueError(f'Truncated Windows binary: {Path(path).name}')
    return struct.unpack_from('<H', data, offset + 4)[0]


def verify(executable, check_files=True):
    """Inspect the PyInstaller archive and, when requested, require the bundled Python 3.12 x64
    runtime, compatible native libraries, isolated OCR workers and local export modules.
    """
    executable = Path(executable)
    archive = CArchiveReader(str(executable))
    options = archive.options
    directory = [option.split(' ', 1)[1] for option in options
                 if option.startswith('pyi-contents-directory ')]
    if directory != ['_internal']:
        raise ValueError('Launcher must use the bundled _internal runtime directory.')
    with executable.open('rb') as stream:
        stream.seek(archive._end_offset - archive._COOKIE_LENGTH)
        cookie = struct.unpack(archive._COOKIE_FORMAT, stream.read(archive._COOKIE_LENGTH))
    version = cookie[4]
    library = cookie[5].rstrip(b'\0').decode('ascii')
    if version != 312 or library != 'python312.dll':
        raise ValueError('Launcher must use the bundled Python 3.12 runtime.')
    if '__main__' not in archive.toc or 'PYZ.pyz' not in archive.toc:
        raise ValueError('Launcher is missing the application entry point or module archive.')
    module_archive = archive.open_embedded_archive('PYZ.pyz')
    for name in ('PoE2_Data_Logger.ui.native_desktop',
                 'PoE2_Data_Logger.core.logger_store',
                 'PoE2_Data_Logger.core.analysis_context',
                 'PoE2_Data_Logger.core.analysis_context_data',
                 'PoE2_Data_Logger.core.analysis_context_rules',
                 'PoE2_Data_Logger.core.atlas_effect_snapshot',
                 'PoE2_Data_Logger.core.workbook_references',
                 'PoE2_Data_Logger.core.undo_store',
                 'PoE2_Data_Logger.core.service',
                 'PoE2_Data_Logger.platform.hotkey',
                 'PoE2_Data_Logger.ocr.opened_scan',
                 'PoE2_Data_Logger.ocr.item_ocr',
                 'PoE2_Data_Logger.ocr.unique_items',
                 'PoE2_Data_Logger.ocr.propagation_scan',
                 'PoE2_Data_Logger.ocr.runehelper_ocr',
                 'PoE2_Data_Logger.core.catalog_repairs',
                 'PoE2_Data_Logger.core.currency_display',
                 'PoE2_Data_Logger.core.ocr_runtime',
                 'PoE2_Data_Logger.core.ocr_workers',
                 'PoE2_Data_Logger.core.ocr_sensitivity',
                 'PoE2_Data_Logger.core.review_learning',
                 'PoE2_Data_Logger.ui.currency_counter',
                 'PoE2_Data_Logger.core.workbook_export'):
        if name not in module_archive.toc:
            raise ValueError(f'Launcher is missing application module: {name}')
        module_archive.extract(name)
    runtime = executable.parent / '_internal'
    native_files = 0
    unique_artwork_files = 0
    if check_files:
        if pe_machine(executable) != 0x8664:
            raise ValueError('The application must be a 64-bit Windows executable.')
        for name in (library, 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll'):
            path = runtime / name
            if not path.is_file() or pe_machine(path) != 0x8664:
                raise ValueError(f'Missing or incompatible bundled DLL: {name}')
        required = [runtime / 'base_library.zip',
                    runtime / 'PySide6/plugins/platforms/qwindows.dll']
        if not required[-1].is_file():
            required[-1] = runtime / 'PySide6/Qt/plugins/platforms/qwindows.dll'
        for path in required:
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'Missing runtime file: {path.relative_to(executable.parent).as_posix()}')
        unique_artwork_files = verify_unique_catalog(runtime / 'PoE2_Data_Logger' / 'unique_item_assets')
        for path in runtime.rglob('*'):
            if path.suffix.casefold() in ('.exe', '.dll', '.pyd') and path.is_file():
                if pe_machine(path) != 0x8664:
                    raise ValueError(f'Incompatible Windows binary: {path.relative_to(runtime).as_posix()}')
                native_files += 1
    return {'options': options, 'python_version': version,
            'python_library': '_internal/' + library,
            'application_modules': len(module_archive.toc), 'files_checked': check_files,
            'native_files_checked': native_files,
            'unique_artwork_files_checked': unique_artwork_files}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: verify_build.py path/to/PoE2-Data-Logger.exe')
    print(json.dumps(verify(sys.argv[1]), indent=2))
