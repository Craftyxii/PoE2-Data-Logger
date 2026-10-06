"""Source and resource groups for PyInstaller."""

CORE_SOURCES = (
    "core/logger_store.py",
    "core/store.py",
    "core/service.py",
    "core/auto_commit.py",
    "core/reference_pack.py",
    "core/ritual_catalog.py",
    "core/workbook_export.py",
)

OCR_SOURCES = (
    "ocr/affix_capture.py",
    "ocr/currency_ocr.py",
    "ocr/inventory_labels.py",
    "ocr/item_ocr.py",
    "ocr/item_text.py",
    "ocr/opened_scan.py",
    "ocr/runehelper_ocr.py",
    "ocr/scan.py",
    "ocr/prototype.py",
    "ocr/cv_eval.py",
    "ocr/glyph_eval.py",
)

UI_SOURCES = (
    "ui/native_desktop.py",
    "ui/region_select.py",
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
DIRECTORY_RESOURCES = ("fonts", "region_examples", "third_party")
