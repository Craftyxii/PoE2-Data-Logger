from __future__ import annotations

import json
from pathlib import Path
import struct
import sys

from PyInstaller.archive.readers import CArchiveReader


def pe_machine(path):
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
                 'PoE2_Data_Logger.core.service',
                 'PoE2_Data_Logger.platform.hotkey',
                 'PoE2_Data_Logger.ocr.opened_scan',
                 'PoE2_Data_Logger.ocr.item_ocr',
                 'PoE2_Data_Logger.ocr.propagation_scan',
                 'PoE2_Data_Logger.core.workbook_export'):
        if name not in module_archive.toc:
            raise ValueError(f'Launcher is missing application module: {name}')
        module_archive.extract(name)
    runtime = executable.parent / '_internal'
    native_files = 0
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
        for path in runtime.rglob('*'):
            if path.suffix.casefold() in ('.exe', '.dll', '.pyd') and path.is_file():
                if pe_machine(path) != 0x8664:
                    raise ValueError(f'Incompatible Windows binary: {path.relative_to(runtime).as_posix()}')
                native_files += 1
    return {'options': options, 'python_version': version,
            'python_library': '_internal/' + library,
            'application_modules': len(module_archive.toc), 'files_checked': check_files,
            'native_files_checked': native_files}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: verify_build.py path/to/PoE2-Data-Logger.exe')
    print(json.dumps(verify(sys.argv[1]), indent=2))
