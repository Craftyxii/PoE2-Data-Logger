"""Source and resource groups for PyInstaller."""

CORE_SOURCES = (
    "logger_store.py",
    "store.py",
    "service.py",
    "auto_commit.py",
    "reference_pack.py",
    "ritual_catalog.py",
    "workbook_export.py",
)

SCANNER_SOURCES = (
    "affix_capture.py",
    "currency_ocr.py",
    "inventory_labels.py",
    "item_ocr.py",
    "item_text.py",
    "opened_scan.py",
    "runehelper_ocr.py",
    "scan.py",
    "prototype.py",
    "cv_eval.py",
    "glyph_eval.py",
)

DESKTOP_SOURCES = (
    "native_desktop.py",
    "hotkey.py",
    "hover_copy.py",
    "live_watch.py",
    "region_select.py",
)

APP_SOURCES = CORE_SOURCES + SCANNER_SOURCES + DESKTOP_SOURCES

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
