"""Synthetic PE/archive tests exercising frozen-build validation without launching a Windows installer."""

from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyInstaller.archive.readers import CArchiveReader

from tools.verify_build import pe_machine, verify


MODULES = (
    'PoE2_Data_Logger.ui.native_desktop',
    'PoE2_Data_Logger.core.logger_store',
    'PoE2_Data_Logger.core.service',
    'PoE2_Data_Logger.platform.hotkey',
    'PoE2_Data_Logger.ocr.opened_scan',
    'PoE2_Data_Logger.ocr.item_ocr',
    'PoE2_Data_Logger.ocr.propagation_scan',
    'PoE2_Data_Logger.ocr.runehelper_ocr',
    'PoE2_Data_Logger.core.catalog_repairs',
    'PoE2_Data_Logger.core.currency_display',
    'PoE2_Data_Logger.core.ocr_runtime',
    'PoE2_Data_Logger.core.ocr_sensitivity',
    'PoE2_Data_Logger.core.review_learning',
    'PoE2_Data_Logger.ui.currency_counter',
    'PoE2_Data_Logger.core.workbook_export',
)


def windows_binary(machine=0x8664):
    """Construct a minimal PE image with configurable machine architecture."""
    data = bytearray(640)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, machine, 1)
    struct.pack_into('<H', data, 148, 240)
    struct.pack_into('<H', data, 152, 0x20B)
    struct.pack_into('<II', data, 408, 128, 512)
    return data


class BuildVerificationTests(unittest.TestCase):
    """Check frozen launcher modules, runtime layout and native binary validation."""
    def setUp(self):
        """Create a synthetic Windows package and mock its PyInstaller archive reader."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.executable = self.root / 'PoE2-Data-Logger.exe'
        self.runtime = self.root / '_internal'
        self.runtime.mkdir()
        self.module_archive = Mock()
        self.module_archive.toc = dict.fromkeys(MODULES)
        self.archive = Mock()
        self.archive.options = ['pyi-contents-directory _internal']
        self.archive.toc = {'__main__': None, 'PYZ.pyz': None}
        self.archive._COOKIE_LENGTH = CArchiveReader._COOKIE_LENGTH
        self.archive._COOKIE_FORMAT = CArchiveReader._COOKIE_FORMAT
        self.archive.open_embedded_archive.return_value = self.module_archive
        self.write_launcher()
        for name in ('python312.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
                     'PySide6/plugins/platforms/qwindows.dll'):
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(windows_binary())
        (self.runtime / 'base_library.zip').write_bytes(b'bundled standard library')
        reader_patch = patch('tools.verify_build.CArchiveReader', return_value=self.archive)
        self.reader = reader_patch.start()
        self.addCleanup(reader_patch.stop)

    def write_launcher(self, version=312, library=b'python312.dll', machine=0x8664):
        """Write a launcher PE with the chosen Python runtime cookie and architecture."""
        cookie = struct.pack(CArchiveReader._COOKIE_FORMAT,
                             CArchiveReader._COOKIE_MAGIC_PATTERN, 88, 0, 0,
                             version, library)
        self.executable.write_bytes(windows_binary(machine) + cookie)
        self.archive._end_offset = self.executable.stat().st_size

    def test_current_package_layout_passes_and_modules_are_extracted(self):
        """Verify the current package validates and extracts all required application modules."""
        result = verify(self.executable)

        self.assertEqual(result['python_version'], 312)
        self.assertEqual(result['python_library'], '_internal/python312.dll')
        self.assertTrue(result['files_checked'])
        self.assertEqual(result['native_files_checked'], 4)
        self.archive.open_embedded_archive.assert_called_once_with('PYZ.pyz')
        self.assertEqual([call.args[0] for call in self.module_archive.extract.call_args_list],
                         list(MODULES))

    def test_missing_entry_point_or_module_archive_is_rejected(self):
        """Verify launchers missing the entry point or embedded module archive fail validation."""
        for name in ('__main__', 'PYZ.pyz'):
            with self.subTest(name=name):
                self.archive.toc = {'__main__': None, 'PYZ.pyz': None}
                del self.archive.toc[name]
                with self.assertRaisesRegex(ValueError, 'application entry point or module archive'):
                    verify(self.executable)

    def test_old_entry_point_is_rejected(self):
        """Verify the obsolete unqualified desktop entry point fails validation."""
        self.archive.toc = {'native_desktop': None, 'PYZ.pyz': None}

        with self.assertRaisesRegex(ValueError, 'application entry point'):
            verify(self.executable)

    def test_each_missing_application_module_is_rejected(self):
        """Verify every required application module is individually enforced."""
        for name in MODULES:
            with self.subTest(name=name):
                self.module_archive.toc = dict.fromkeys(MODULES)
                del self.module_archive.toc[name]
                with self.assertRaises(ValueError) as raised:
                    verify(self.executable)
                self.assertEqual(str(raised.exception), f'Launcher is missing application module: {name}')

    def test_unqualified_modules_are_rejected(self):
        """Verify basename-only modules cannot satisfy qualified application requirements."""
        self.module_archive.toc = dict.fromkeys(name.rsplit('.', 1)[-1] for name in MODULES)

        with self.assertRaisesRegex(ValueError, 'missing application module'):
            verify(self.executable)

    def test_damaged_module_propagates_extraction_error(self):
        """Verify archive extraction failures propagate through build verification."""
        self.module_archive.extract.side_effect = ValueError('damaged module')

        with self.assertRaisesRegex(ValueError, 'damaged module'):
            verify(self.executable)

    def test_wrong_python_runtime_is_rejected(self):
        """Verify launcher cookies require both the Python 3.12 version and library name."""
        for version, library in ((311, b'python312.dll'), (312, b'python311.dll')):
            with self.subTest(version=version, library=library):
                self.write_launcher(version=version, library=library)
                with self.assertRaisesRegex(ValueError, 'bundled Python 3.12 runtime'):
                    verify(self.executable)

    def test_wrong_runtime_directory_is_rejected(self):
        """Verify the archive declares the required _internal runtime directory."""
        self.archive.options = ['pyi-contents-directory .']

        with self.assertRaisesRegex(ValueError, 'bundled _internal runtime directory'):
            verify(self.executable)

    def test_wrong_launcher_architecture_is_rejected(self):
        """Verify 32-bit launchers fail the 64-bit Windows architecture check."""
        self.write_launcher(machine=0x14C)

        with self.assertRaisesRegex(ValueError, '64-bit Windows executable'):
            verify(self.executable)

    def test_missing_or_incompatible_runtime_dll_is_rejected(self):
        """Verify absent or wrong-architecture Python DLLs fail validation."""
        path = self.runtime / 'python312.dll'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or incompatible bundled DLL: python312.dll'):
            verify(self.executable)

        path.write_bytes(windows_binary(machine=0x14C))
        with self.assertRaisesRegex(ValueError, 'Missing or incompatible bundled DLL: python312.dll'):
            verify(self.executable)

    def test_missing_or_empty_runtime_file_is_rejected(self):
        """Verify required runtime files must exist with nonempty contents."""
        path = self.runtime / 'base_library.zip'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing runtime file: _internal/base_library.zip'):
            verify(self.executable)

        path.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'Missing runtime file: _internal/base_library.zip'):
            verify(self.executable)

    def test_qt_platform_plugin_alternate_directory_is_supported(self):
        """Verify the alternate PySide6 Qt plugin directory is accepted."""
        source = self.runtime / 'PySide6/plugins/platforms/qwindows.dll'
        target = self.runtime / 'PySide6/Qt/plugins/platforms/qwindows.dll'
        target.parent.mkdir(parents=True)
        source.rename(target)

        self.assertEqual(verify(self.executable)['native_files_checked'], 4)

    def test_incompatible_native_extension_is_rejected(self):
        """Verify bundled native extensions with incompatible architecture are rejected."""
        (self.runtime / 'scanner.pyd').write_bytes(windows_binary(machine=0x14C))

        with self.assertRaisesRegex(ValueError, 'Incompatible Windows binary: scanner.pyd'):
            verify(self.executable)

    def test_archive_only_check_still_validates_modules_and_runtime_cookie(self):
        """Verify archive-only mode checks modules and cookie while skipping runtime files."""
        for path in self.runtime.rglob('*'):
            if path.is_file():
                path.unlink()

        result = verify(self.executable, check_files=False)

        self.assertFalse(result['files_checked'])
        self.assertEqual(result['native_files_checked'], 0)
        self.assertEqual(self.module_archive.extract.call_count, len(MODULES))


class WindowsBinaryTests(unittest.TestCase):
    """Check PE machine extraction and rejection of malformed or truncated images."""
    def setUp(self):
        """Create a temporary path for synthetic runtime DLL fixtures."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'runtime.dll'

    def test_valid_windows_binary_machine_is_returned(self):
        """Verify machine extraction returns the 64-bit PE architecture value."""
        self.path.write_bytes(windows_binary())

        self.assertEqual(pe_machine(self.path), 0x8664)

    def test_corrupt_dos_header_is_rejected(self):
        """Verify a non-executable DOS header fails PE validation."""
        self.path.write_bytes(b'not an executable')

        with self.assertRaisesRegex(ValueError, 'Invalid Windows executable'):
            pe_machine(self.path)

    def test_corrupt_pe_header_is_rejected(self):
        """Verify an invalid PE signature is rejected."""
        data = windows_binary()
        data[128:132] = b'BAD!'
        self.path.write_bytes(data)

        with self.assertRaisesRegex(ValueError, 'Invalid PE header'):
            pe_machine(self.path)

    def test_section_extending_beyond_file_is_rejected(self):
        """Verify section data extending past the file end is rejected."""
        self.path.write_bytes(windows_binary()[:-1])

        with self.assertRaisesRegex(ValueError, 'Truncated Windows binary'):
            pe_machine(self.path)

    def test_truncated_section_table_is_rejected(self):
        """Verify a truncated PE section table is rejected."""
        self.path.write_bytes(windows_binary()[:420])

        with self.assertRaisesRegex(ValueError, 'Invalid PE section table'):
            pe_machine(self.path)


if __name__ == '__main__':
    unittest.main()
