"""Declare the modules and data assets that the frozen desktop build must carry.

The PyInstaller spec compares APP_SOURCES against the package's Python files;
new modules must be listed here so packaging cannot silently omit them.
"""

CORE_SOURCES = (
    "core/logger_store.py",
    "core/store.py",
    "core/service.py",
    "core/auto_commit.py",
    "core/reference_pack.py",
    "core/ritual_catalog.py",
    "core/workbook_export.py",
    "core/atlas_catalog.py",
    "core/export_files.py",
    "core/catalog_repairs.py",
    "core/currency_display.py",
    "core/ocr_runtime.py",
    "core/ocr_sensitivity.py",
    "core/review_learning.py",
)

OCR_SOURCES = (
    "ocr/affix_capture.py",
    "ocr/currency_ocr.py",
    "ocr/inventory_labels.py",
    "ocr/item_ocr.py",
    "ocr/item_text.py",
    "ocr/ritual_grid.py",
    "ocr/opened_scan.py",
    "ocr/propagation_scan.py",
    "ocr/propagation_marks.py",
    "ocr/runehelper_ocr.py",
    "ocr/scan.py",
    "ocr/prototype.py",
    "ocr/cv_eval.py",
    "ocr/glyph_eval.py",
)

UI_SOURCES = (
    "ui/native_desktop.py",
    "ui/region_select.py",
    "ui/atlas_settings.py",
    "ui/currency_counter.py",
)

PLATFORM_SOURCES = (
    "platform/hotkey.py",
    "platform/hover_copy.py",
    "platform/live_watch.py",
)

PACKAGE_SOURCES = (
    "__init__.py",
    "__main__.py",
    "core/__init__.py",
    "ocr/__init__.py",
    "ui/__init__.py",
    "platform/__init__.py",
)

# Package markers and each functional layer share one completeness check.
APP_SOURCES = PACKAGE_SOURCES + CORE_SOURCES + OCR_SOURCES + UI_SOURCES + PLATFORM_SOURCES

DATABASE_RESOURCES = (
    "bootstrap.json.gz",
    "catalog.json",
    "affix_catalog.json",
)

SCANNER_RESOURCES = (
    "socket_model.joblib",
    "glyphs.npz",
    "book_bronze.png",
    "book_purple.png",
    "deferred_marker.png",
)

DESKTOP_RESOURCES = (
    "power_rune.ico",
    "brand_logo.jpg",
    "IN_APP_README.md",
    "CRAFTYXII_ASSETS_LICENSE.txt",
    "AFFIX_CATALOG_NOTICE.txt",
)

FILE_RESOURCES = DATABASE_RESOURCES + SCANNER_RESOURCES + DESKTOP_RESOURCES
# These directories retain their relative layout for runtime asset lookups.
DIRECTORY_RESOURCES = ("fonts", "region_examples", "third_party", "atlas", "ocr/ritual_assets", "rune_icons")
