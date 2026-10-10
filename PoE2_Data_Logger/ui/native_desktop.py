"""Qt desktop orchestration for map forms, independent scan reviews and local persistence.

Worker results cross Qt signals before touching controls. Capture ownership and
session generations keep delayed work attached to the map that requested it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QLockFile, QObject, QPointF, QRect, QSignalBlocker, QSize, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QAbstractScrollArea, QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QFrame, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import reference_pack
from PoE2_Data_Logger.core import review_learning
from PoE2_Data_Logger.core import ocr_runtime, ocr_sensitivity
from PoE2_Data_Logger.ocr.affix_capture import new_affix_names, affix_unit, modifier_value
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.core import service
from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.ui.region_select import RegionEditor, ScanRegionsPage
from PoE2_Data_Logger.ui.atlas_settings import AtlasSettingsPage
from PoE2_Data_Logger.ui.currency_counter import SessionCurrencyCounter
from PoE2_Data_Logger.core.currency_display import icon_png
from PoE2_Data_Logger.core.workbook_export import export_xlsx
from PoE2_Data_Logger.core.export_files import write_export_files


HERE = Path(__file__).resolve().parent.parent
WINDOW_TITLE = "PoE2 Data Logger 1.3.2.7 Beta"
DISCORD_INVITE = "https://discord.gg/bE758BqSQj"
DEFAULT_REFERENCE_FOLDER = (Path(sys.executable).resolve().parent / "Databases"
                            if getattr(sys, "frozen", False) else
                            Path.home() / "Documents" / "PoE2 Data Logger" / "Databases")
APP_STYLE = """
QWidget { background:#101113; color:#EEEAE3; font-family:"Segoe UI","DejaVu Sans"; font-size:12px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background:#101113; }
QLabel { background:transparent; }
QWidget#sidebar { background:#08090B; border-right:1px solid #332A1E; }
QLabel[role="eyebrow"] { color:#CBA96F; font-size:10px; font-weight:700; }
QLabel[role="subheading"], QLabel[role="note"] { color:#BCB7AE; }
QFrame#statCard { background:#1C1C1E; border:1px solid #40372C; border-radius:9px; }
QFrame#statCard QLabel { background:transparent; border:0; }
QGroupBox { background:#1C1C1E; border:1px solid #3D352C; border-radius:9px;
            margin-top:6px; padding:10px 12px 10px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:16px; padding:1px 5px;
                    color:#F4EFE7; background:#1C1C1E; font-weight:700; }
QLabel[role="message"] { background:#2A241D; color:#EEE7DB; border:1px solid #69553A; padding:9px; border-radius:5px; }
QLabel[role="success"] { background:#302716; color:#FBE3B2; padding:9px; border:1px solid #9B722A; border-radius:5px; font-weight:600; }
QLabel[role="error"] { background:#3A2221; color:#FBE8E5; padding:9px; border:1px solid #A56059; border-radius:5px; }
QLineEdit, QComboBox, QTextEdit, QSpinBox { background:#111214; color:#F2EEE8; border:1px solid #655747;
                                             border-radius:5px; padding:7px; min-height:20px; selection-background-color:#895C16; }
QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QSpinBox:focus { border:1px solid #E5AA32; }
QComboBox QAbstractItemView { background:#1C1C1E; color:#F2EEE8; selection-background-color:#704D18; }
QPushButton { background:#282521; color:#F3EFE8; border:1px solid #6C5942;
              border-radius:5px; padding:8px 12px; font-weight:600; }
QPushButton:hover { background:#3B3125; border-color:#BD8D37; }
QPushButton:pressed { background:#51401F; }
QPushButton:disabled { background:#222225; color:#817D76; border-color:#393534; }
QPushButton[role="primary"] { background:#E0A321; color:#14100A; border-color:#F0BA42; }
QPushButton[role="primary"]:hover { background:#F3B536; }
QPushButton[role="primary"]:disabled { background:#463B29; color:#9A8A70; border-color:#554834; }
QPushButton[role="danger"] { background:#4D2927; color:#F8E8E4; border-color:#A66D67; }
QPushButton[role="nav"] { background:transparent; color:#BDB9B1; border:0; border-radius:0;
                           border-left:3px solid transparent; text-align:left; padding:11px 15px; }
QPushButton[role="nav"]:hover { background:#201B16; color:#FFFFFF; }
QPushButton[role="nav"][active="true"] { background:#322417; color:#FFFFFF; border-left:3px solid #E1A321; }
QCheckBox { spacing:8px; background:transparent; }
QTabWidget::pane { border:0; }
QTableWidget, QListWidget { background:#151517; color:#EEEAE3; border:1px solid #4B4032; border-radius:5px;
                            alternate-background-color:#222022; gridline-color:#41382E;
                            selection-background-color:#75511E; selection-color:#FFFFFF; }
QHeaderView::section { background:#30271E; color:#EEEAE3; border:1px solid #554331; padding:6px; }
QTableCornerButton::section { background:#30271E; border:1px solid #554331; }
QMenuBar, QMenu, QStatusBar { background:#101113; color:#EEEAE3; }
QMenu::item:selected { background:#705020; }
QScrollBar:vertical { background:#131313; width:11px; margin:0; }
QScrollBar::handle:vertical { background:#705C41; min-height:26px; border-radius:5px; margin:2px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QSlider::groove:horizontal { background:#463B29; height:6px; border-radius:3px; }
QSlider::sub-page:horizontal { background:#E0A321; border-radius:3px; }
QSlider::handle:horizontal { background:#F5C364; border:1px solid #CBA96F; width:16px; margin:-6px 0; border-radius:8px; }
"""


def line(placeholder=""):
    """Create an empty text field with placeholder guidance."""
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    return widget


def paragraph(text):
    """Wrap explanatory text inside its section instead of widening the whole scroll page."""
    widget = QLabel(text)
    widget.setWordWrap(True)
    return widget


def combo(values, selected=None):
    """Build choices with separate display labels and stored values, then select a default."""
    widget = QComboBox()
    for value in values:
        if isinstance(value, tuple):
            widget.addItem(str(value[1]), value[0])
        else:
            widget.addItem(str(value), value)
    if selected is not None:
        select(widget, selected)
    return widget


def select(widget, value):
    """Select by stored value or label while suppressing change callbacks."""
    index = widget.findData(value)
    if index < 0:
        index = widget.findText(str(value))
    with QSignalBlocker(widget):
        widget.setCurrentIndex(max(0, index))


def value(widget):
    """Read a choice's stored value or a text field's trimmed input."""
    return widget.currentData() if isinstance(widget, QComboBox) else widget.text().strip()


def button(text, callback, role=None):
    """Connect a button's click signal to its action and optional style role."""
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    widget.clicked.connect(callback)
    return widget


def message(text=""):
    """Create a wrapping status label and mark initial text as guidance."""
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setProperty("role", "message")
    widget.setProperty("guidance", bool(text))
    return widget


def set_message(widget, content, role="message"):
    """Replace guidance with a visible status message and refresh its role styling."""
    widget.setText(content)
    if widget.property("guidance"):
        widget.setProperty("guidance", False)
        widget.show()
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def clear_waystone_read(result):
    """Require complete waystone fields and the captured OCR strictness evidence floor."""
    fields = result.get("fields") or {}
    mods = result.get("mods") or []
    if (fields.get("tier") not in (15, 16) or fields.get("waystone") is None or
            fields.get("map_mods") is None or fields["map_mods"] != len(mods) or
            not 1 <= len(mods) <= 10):
        return False
    if result.get("source") == "screen OCR":
        strictness = ocr_sensitivity.validate(result.get("_ocr_strictness", ocr_sensitivity.DEFAULT))
        if strictness == 100:
            return False
        floor = ocr_sensitivity.clear_threshold(.9, strictness, .1)
        rows = result.get("ocr_rows") or []
        return bool(rows) and all(float(row.get("score", 0)) >= floor for row in rows
                                  if row.get("text", "").strip())
    return True


def clear_currency_read(result):
    """Accept currency reads only when every named stack has a positive integer count and no
    review flags.
    """
    items = result.get("items") or []
    return result.get("_ocr_strictness", ocr_sensitivity.DEFAULT) != 100 and bool(items) and not result.get("unknown") and all(
        item.get("name") and type(item.get("quantity")) is int and
        item["quantity"] >= 1 and not item.get("count_needs_review") and
        not item.get("name_needs_review") for item in items)


def currency_review_rank(item):
    """Group accepted names/counts, held named or candidate matches, then unidentified slots."""
    if str(item.get("name") or "").strip():
        return 1 if item.get("count_needs_review") or item.get("name_needs_review") else 0
    candidate = str(item.get("candidate") or "").strip()
    return 1 if candidate.casefold() not in {
        "", "unknown", "unidentified", "unresolved", "unknown item", "unidentified item", "unrecognized item"
    } else 2


def header_counter(identifier):
    """Display a readable map/remnant number while storage retains its full linked ID."""
    match = re.fullmatch(r"[MR](\d+)", str(identifier or ""))
    return f"#{int(match.group(1))}" if match else "—"


def unresolved_ritual_name(name):
    """Recognize blank and placeholder reward names that still need identification."""
    return str(name or "").strip().casefold() in {
        "", "unknown", "unidentified", "unresolved", "deferred omen", "deferred item", "deferred reward",
        "unidentified reward", "unknown reward", "unresolved reward",
        "unknown item", "unidentified item", "unknown omen", "unidentified omen"}


def clear_ritual_read(result, omen_names):
    """Gate Ritual auto-save on complete totals, nonoverlapping grid coverage and confident
    validated rewards.
    """
    strictness = ocr_sensitivity.validate(result.get("_ocr_strictness", ocr_sensitivity.DEFAULT))
    if strictness == 100:
        return False
    floor = ocr_sensitivity.clear_threshold(.94, strictness, .14)
    name_floor = ocr_sensitivity.clear_threshold(.9, strictness, .1)
    items = result.get("items") or []
    if (not items or any(result.get(flag) for flag in
            ("unmatched", "unknown", "coverage_uncertain", "needs_review", "unresolved_count",
             "tribute_needs_review", "rerolls_needs_review", "totals_needs_review"))):
        return False
    if result.get("reward_count") is not None and result["reward_count"] != len(items):
        return False
    if result.get("grid_detected"):
        if type(result.get("grid_reward_count")) is not int:
            return False
        footprints = [tuple(item.get("grid_slots") or []) for item in items if item.get("grid_slots")]
        slots = [slot for footprint in footprints for slot in footprint]
        if (any(type(slot) is not int or not 1 <= slot <= 120 for slot in slots) or
                len(set(footprints)) != result["grid_reward_count"] or len(slots) != len(set(slots))):
            return False
    for item in items:
        try:
            score = float(item.get("score", 0))
            name_match = float(item.get("name_match", 0))
        except (TypeError, ValueError):
            return False
        if (item.get("category") not in ("Omen", "Item") or not isinstance(item.get("name"), str) or
                unresolved_ritual_name(item.get("name")) or
                len(str(item["name"])) > 200 or
                (item["category"] == "Omen" and (item["name"] not in omen_names or not name_floor <= name_match <= 1)) or
                not floor <= score <= 1 or
                type(item.get("quantity")) is not int or not 1 <= item["quantity"] <= 1000000 or
                type(item.get("tribute")) is not int or not 0 <= item["tribute"] <= 1000000000 or
                type(item.get("deferred", False)) is not bool or
                any(item.get(flag) for flag in ("needs_review", "unresolved", "name_needs_review",
                    "count_needs_review", "deferred_needs_review", "deferred_uncertain")) or
                str(item.get("source", "")).startswith("icon reference") or
                re.search(r"\b[x×]\s*\d+\b", str(item.get("source", "")), re.I)):
            return False
    return True


class IconCropCanvas(QWidget):
    """Preview an image and retain a drag rectangle for extracting one reference icon."""
    def __init__(self, image):
        """Scale the preview to fit the crop dialog and initialize an empty selection."""
        super().__init__()
        ratio = min(1.0, 760 / image.width, 480 / image.height)
        self.setFixedSize(round(image.width * ratio), round(image.height * ratio))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.picture = QPixmap()
        self.picture.loadFromData(buffer.getvalue())
        self.selection = QRect()
        self.origin = None

    def paintEvent(self, event):
        """Paint the preview with an outline around the selected crop rectangle."""
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.picture)
        if not self.selection.isNull():
            painter.setPen(QPen(QColor("#E8AB30"), 2))
            painter.drawRect(self.selection)

    def mousePressEvent(self, event):
        """Start a crop rectangle at a left-button press."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.selection = QRect(self.origin, self.origin)
            self.update()

    def mouseMoveEvent(self, event):
        """Clamp the dragged crop rectangle to the displayed image."""
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        """Finalize the crop rectangle and end the drag."""
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.origin = None
            self.update()

    def crop(self, image):
        """Convert the displayed selection to source-image coordinates, rejecting tiny crops."""
        area = self.selection.normalized()
        if area.width() < 12 or area.height() < 12:
            raise ValueError("Drag a box around one icon before saving the example.")
        box = (round(area.left() * image.width / self.width()),
               round(area.top() * image.height / self.height()),
               round((area.right() + 1) * image.width / self.width()),
               round((area.bottom() + 1) * image.height / self.height()))
        return image.crop(box)


class Tasks(QObject):
    """Carry worker completions and capture notifications across Qt thread boundaries."""
    completed = Signal(str, object, object)
    hide_overlay = Signal(object)
    scan_ready = Signal()


class WorkspaceTabs(QTabWidget):
    """Size the selected page without reserving the largest unrelated tab's wrapped height."""

    def heightForWidth(self, width):
        """Let scroll pages fill available space and use only the active standalone page's height."""
        page = self.currentWidget()
        if page is None or isinstance(page, QScrollArea):
            return self.minimumHeight()
        margins = self.contentsMargins()
        height = page.heightForWidth(max(0, width - margins.left() - margins.right()))
        if height < 0:
            height = page.minimumSizeHint().height()
        return max(self.minimumHeight(), height + margins.top() + margins.bottom())


class LoggerWindow(QMainWindow):
    """Coordinate map-scoped UI drafts, scan review and persistence through the logger service."""
    def __init__(self):
        """Create controls and map-scoped review drafts, then wire capture and GUI handoffs."""
        super().__init__()
        self.state = logger.get_state()
        if not self.state["current_map_id"]:
            self.state = logger.start_map()
        self._ocr_active_threads = ocr_runtime.active_threads()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runeshape-ui")
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self._task_done)
        self.mode = service.HOTKEY.status()["mode"]
        self.images = {"seed": None, "opened": None}
        self.results = {"seed": None, "opened": None}
        self.current_file = {"seed": "", "opened": ""}
        self.saved_scan = self._saved_scan_capture = None
        self._seed_readings = []
        self._seed_loading = False
        self._both_seed_context = None
        self._both_link = None
        self._both_approved = False
        self._both_last_opened = None
        self.resolved = None
        self._pending_tasks = {}
        self._chain_save_pending = None
        self._task_id = 0
        self._latest_hotkey = 0
        self._latest_overlay = service.HOTKEY.status().get("overlay_sequence", 0)
        with logger._connect() as db:
            self._overlay_enabled = bool(logger._meta(db, "hud_overlay", False))
            opacity = logger._meta(db, "hud_overlay_opacity", 100)
        self._overlay_opacity = max(20, min(100, opacity)) if type(opacity) is int else 100
        self._overlay_opacity_timer = QTimer(self)
        self._overlay_opacity_timer.setSingleShot(True)
        self._overlay_opacity_timer.setInterval(200)
        self._overlay_opacity_timer.timeout.connect(lambda: self.run(self._save_overlay_opacity))
        self._overlay_revealed = False
        self._overlay_visible = False
        self._overlay_auto_review = False
        self._overlay_window_transition = False
        self._overlay_activation_pending = False
        self._overlay_activation_deadline = 0.0
        self._overlay_restore_repaint_pending = False
        self._overlay_review_token = 0
        self._overlay_capture_restore = None
        self._saved_badge = None
        self._commit_badge_timer = QTimer(self)
        self._commit_badge_timer.setSingleShot(True)
        self._commit_badge_timer.setInterval(8000)
        self._commit_badge_timer.timeout.connect(self._clear_commit_badge)
        self._capturing_hotkey = False
        self._editing_regions = False
        self._region_selection_pending = False
        self._region_selection_token = 0
        self._region_editor = None
        self._closed = False
        self._wheel_field = None
        self._inventory_capture = None
        self._inventory_capture_context = None
        self._inventory_reading = None
        self._ritual_reading = None
        self._ritual_capture_context = None
        self._remnant_reading = None
        self._propagation_reading = None
        self._manual_propagation_context = None
        self._propagation_review_active = False
        self._chain_context = None
        self._chain_drafts = {}
        self._chain_scan_number = 0
        self._chain_save_request = None
        self._accepted_propagation_requests = set()
        self._saved_chain_view_key = None
        self._saved_chain_edit_context = None
        self._saved_chain_correction_drafts = {}
        self._ritual_hash = None
        self._currency_catalog_loaded = False
        self.pending_review_kind = None
        self._held_remnant_review = None
        self._failed_review = False
        self._pending_tablet_slot = None
        self._pending_currency_map = None
        self._pending_currency_phase = None
        self._pending_ritual_map = None
        self.tablet_raw_mods = [[] for _ in range(4)]
        self._extra_waystone_mods = []
        self._form_map_context = None
        self._waystone_form_baseline = self._counts_form_baseline = None
        self._tablet_form_baseline = self._active_master_baseline = self._perk_form_baseline = None
        self._perk_drafts = {}
        self._export_folder_baseline = None
        for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            QFontDatabase.addApplicationFont(str(HERE / "fonts" / font))
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(QIcon(str(HERE / "power_rune.ico")))
        self.resize(1280, 860)
        self.setStyleSheet(APP_STYLE)
        self._build()
        self.overlay_escape = QShortcut(QKeySequence("Escape"), self)
        self.overlay_escape.activated.connect(self.hide_overlay)
        self.overlay_escape.setEnabled(self._overlay_enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowOpacity(1.0)
        self.tasks.hide_overlay.connect(self._hide_overlay_capture)
        service.HOTKEY.before_capture = self._prepare_overlay_capture
        self.refresh()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.poll)
        self._poll.start(150)
        self.tasks.scan_ready.connect(self.poll, Qt.ConnectionType.QueuedConnection)
        self._scan_ready_callback = self.tasks.scan_ready.emit
        service.HOTKEY.on_event = self._scan_ready_callback
        QApplication.instance().installEventFilter(self)

    def _submit(self, label, function, done=None):
        """Run work in the pool and dispatch completion through Qt with its session generation."""
        if self._closed:
            return
        self._task_id += 1
        key = str(self._task_id)
        self._pending_tasks[key] = (done, logger.session_generation())
        future = self.pool.submit(function)
        def finished(item):
            """Emit a worker result or exception for the GUI thread unless the window has
            closed.
            """
            if self._closed:
                return
            try:
                self.tasks.completed.emit(key, item.result(), None)
            except Exception as error:
                self.tasks.completed.emit(key, None, error)
        future.add_done_callback(finished)
        self.statusBar().showMessage(label)

    def _task_done(self, key, result, error):
        """Handle a worker's Qt completion on the GUI thread, ignoring retired sessions and
        reporting failures.
        """
        if self._closed:
            return
        pending = self._pending_tasks.pop(key, None)
        if pending is None:
            return
        callback, generation = pending
        if generation != logger.session_generation():
            return
        self.statusBar().clearMessage()
        if error:
            failed = getattr(callback, "_scan_failure", None)
            if failed and not failed(error):
                return
            self.error(str(error))
        elif callback:
            try:
                callback(result)
            except Exception as problem:
                self.error(str(problem))

    def _submit_scan(self, label, function, done, kind, capture):
        """Wrap an asynchronous scan with capture-specific failure handling before submitting
        it.
        """
        field = {"currency": "_inventory_reading", "ritual": "_ritual_reading",
                 "remnant": "_remnant_reading", "seed": "_remnant_reading"}[kind]
        def completed(result):
            """Deliver a scan result and preserve capture identity when its UI callback fails."""
            active = getattr(self, field) is capture
            try:
                done(result)
            except Exception as error:
                self._scan_failed(kind, capture, error, reading_finished=active)
                raise
        completed._scan_failure = lambda error: self._scan_failed(kind, capture, error)
        self._submit(label, function, completed)

    def _scan_failed(self, kind, capture, error, reading_finished=False):
        """Retire the matching failed scan and hold an error review that cannot be committed."""
        field = {"currency": "_inventory_reading", "ritual": "_ritual_reading",
                 "remnant": "_remnant_reading", "seed": "_remnant_reading"}[kind]
        reading = getattr(self, field)
        if (reading is not capture and not (reading_finished and reading is None) or
                self.pending_review_kind != kind):
            return False
        setattr(self, field, None)
        if kind == "currency":
            self._inventory_capture_context = None
            self._pending_currency_map = self._pending_currency_phase = None
        elif kind == "ritual":
            self._ritual_capture_context = None
            self._pending_ritual_map = None
        else:
            logger.discard_ocr_id()
            self.results[kind if kind == "seed" else "opened"] = None
            self.resolved = None
            for widget in (self.first_recipe, self.next_recipe):
                with QSignalBlocker(widget):
                    widget.clear()
            self.recipe_table.hide()
            if kind == "seed":
                self._clear_seed_queue()
        summary = f"Scan failed: {error}. Scan again or reject this reading."
        self._review_pending(kind, summary, False)
        self._failed_review = True
        set_message(self.review_summary, summary, "error")
        return True

    def run(self, action):
        """Execute a UI action and show exceptions through the window's error feedback."""
        try:
            return action()
        except Exception as error:
            self.error(str(error))
            return None

    def error(self, text):
        """Show an error on the current supported page and temporarily in the status bar."""
        if hasattr(self, "tabs"):
            current = self.tabs.currentIndex()
            if current == 0 and hasattr(self, "scan_status"):
                set_message(self.scan_status, text, "error")
                self.scan_status.show()
            elif current == 2 and hasattr(self, "tablet_scan_status"):
                set_message(self.tablet_scan_status, text, "error")
            elif current == 4 and hasattr(self, "inventory_status"):
                set_message(self.inventory_status, text, "error")
            elif current == 8 and hasattr(self, "reference_status"):
                set_message(self.reference_status, text, "error")
        self.statusBar().showMessage(text, 8000)

    def note(self, text, success=False):
        """Display a temporary status-bar notice for a completed UI action."""
        self.statusBar().showMessage(text, 6000)

    def open_discord(self):
        """Open the community invite with the system URL handler and report launch failure."""
        if not QDesktopServices.openUrl(QUrl(DISCORD_INVITE)):
            self.error("Could not open Discord in your default browser.")

    def _page(self):
        """Create a scroll page with compact section gaps while preserving control hit targets."""
        outer = QScrollArea()
        outer.setFrameShape(QFrame.Shape.NoFrame)
        outer.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 9, 12, 16)
        layout.setSpacing(8)
        outer.setWidget(body)
        return outer, layout

    def _group(self, title, parent):
        """Add a titled section with tight content gaps and normal-sized controls."""
        group = QGroupBox(title)
        content = QVBoxLayout(group)
        content.setSpacing(6)
        parent.addWidget(group)
        return content

    def _build(self):
        """Assemble navigation, map headers and pages, then connect their actions and menu
        entries.
        """
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(7, 10, 12, 14)
        nav.setSpacing(3)
        brand = QHBoxLayout()
        logo = QToolButton()
        logo.setObjectName("brandLogo")
        logo.setToolTip("Join CraftyXII's Discord (opens your browser)")
        logo.setAccessibleName("Join CraftyXII's Discord")
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        logo.setStyleSheet("QToolButton { background:transparent; border:1px solid transparent; padding:1px; }"
                           "QToolButton:focus { border-color:#E5AA32; }")
        logo.clicked.connect(self.open_discord)
        image = QPixmap(str(HERE / "brand_logo.jpg"))
        if not image.isNull():
            logo.setIcon(QIcon(image.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)))
        logo.setIconSize(QSize(50, 50))
        logo.setFixedSize(54, 54)
        brand.addWidget(logo)
        identity = QVBoxLayout()
        name = QLabel("PoE2 Data Logger")
        name.setStyleSheet("font-size:13px;font-weight:700;color:#FFFFFF;")
        identity.addWidget(name)
        creator = QLabel("CRAFTYXII")
        creator.setProperty("role", "eyebrow")
        identity.addWidget(creator)
        brand.addLayout(identity)
        brand.addStretch()
        nav.addLayout(brand)
        nav.addSpacing(22)
        self.nav_buttons = []
        for i, label in enumerate(("Review", "Expedition", "Map / Tablets", "Atlas Masters",
                                   "Currency", "Data export")):
            item = button(label, lambda checked=False, index=i: self.tabs.setCurrentIndex(index), "nav")
            self.nav_buttons.append(item)
            nav.addWidget(item)
        nav.addStretch()
        preferences = QLabel("SETTINGS")
        preferences.setProperty("role", "eyebrow")
        nav.addWidget(preferences)
        scan_nav = button("Scan settings", lambda: self.tabs.setCurrentIndex(6), "nav")
        self.nav_buttons.append(scan_nav)
        nav.addWidget(scan_nav)
        regions_nav = button("Scan regions", lambda: self.tabs.setCurrentIndex(7), "nav")
        self.nav_buttons.append(regions_nav)
        nav.addWidget(regions_nav)
        database_nav = button("Database Import/Export", lambda: self.tabs.setCurrentIndex(8), "nav")
        self.nav_buttons.append(database_nav)
        nav.addWidget(database_nav)
        ocr_nav = button("OCR Sensitivity", lambda: self.tabs.setCurrentIndex(13), "nav")
        ocr_nav.setProperty("page_index", 13)
        self.nav_buttons.append(ocr_nav)
        nav.addWidget(ocr_nav)
        atlas_nav = button("Atlas / Character Settings", lambda: self.tabs.setCurrentIndex(12), "nav")
        atlas_nav.setProperty("page_index", 12)
        self.nav_buttons.append(atlas_nav)
        nav.addWidget(atlas_nav)
        navigation = QScrollArea()
        navigation.setFrameShape(QFrame.Shape.NoFrame)
        navigation.setWidgetResizable(True)
        navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        navigation.setFixedWidth(224)
        sidebar.setMinimumWidth(0)
        sidebar.setMaximumWidth(16777215)
        navigation.setWidget(sidebar)
        root.addWidget(navigation)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(24, 10, 16, 4)
        main_layout.setSpacing(10)
        self._header_horizontal_inset = (navigation.width() + main_layout.contentsMargins().left()
                                         + main_layout.contentsMargins().right())
        top = QGridLayout()
        self._header_layout = top
        self._header_arrangement = None
        heading = QVBoxLayout()
        self.page_title = QLabel("Review")
        self.page_title.setStyleSheet("font-size:24px;font-weight:700;color:#FFFFFF;")
        self.page_title.setWordWrap(True)
        self.page_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.page_subtitle = QLabel("Check the latest scan, then Approve to save it.")
        self.page_subtitle.setProperty("role", "subheading")
        self.page_subtitle.setWordWrap(True)
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        self._header_heading = heading
        self._build_kill_controls(top, compact=True)
        top.removeWidget(self.kill_counts_group)
        self.header_map_id = QLabel("—")
        self.header_remnant_id = QLabel("—")
        self.header_expedition = combo([1, 2])
        self.header_expedition.setMinimumWidth(145)
        identifiers = QHBoxLayout()
        identifiers.setSpacing(14)
        self._header_identifier_captions = []
        self._header_identifier_columns = []
        for label, field in (("MAP #", self.header_map_id),
                             ("REMNANT #", self.header_remnant_id),
                             ("EXPEDITION #", self.header_expedition)):
            column = QVBoxLayout()
            column.setSpacing(3)
            caption = QLabel(label)
            caption.setProperty("role", "eyebrow")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._header_identifier_captions.append(caption)
            column.addWidget(caption)
            if isinstance(field, QLabel):
                field.setMinimumWidth(96)
                field.setAlignment(Qt.AlignmentFlag.AlignCenter)
                field.setAccessibleName(label)
            column.addWidget(field)
            identifiers.addLayout(column)
            self._header_identifier_columns.append(column)
        self._header_identifiers = identifiers
        self.header_complete_chain_button = button(
            "Complete chain", lambda: self.run(self.complete_chain), "primary")
        self.header_complete_chain_button.setEnabled(False)
        # Compact headers share caption/number rows so unused height cannot separate them.
        self._compact_header_status = QWidget()
        self._compact_header_grid = QGridLayout(self._compact_header_status)
        self._compact_header_grid.setContentsMargins(0, 0, 0, 0)
        self._compact_header_grid.setHorizontalSpacing(12)
        self._compact_header_grid.setVerticalSpacing(3)
        self._compact_header_status.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        actions = QHBoxLayout()
        actions.addWidget(button("Undo new map", lambda: self.run(self.undo_map)))
        actions.addWidget(button("+ New map", lambda: self.run(self.finish_map), "primary"))
        self._header_actions = actions
        main_layout.addLayout(top)
        self._arrange_header()
        cards = QHBoxLayout()
        self.stat_values = [QLabel("—") for _ in range(4)]
        for number in self.stat_values:
            number.setStyleSheet("font-size:17px;font-weight:700;color:#FFFFFF;")
        self.header_biome = combo(logger.BIOMES)
        self.header_city_type = combo(logger.CITY_TYPES)
        self.header_ocean = QCheckBox("Ocean")
        self.header_irradiated = QCheckBox("Irradiated")
        self.header_deli = QCheckBox("Deli")
        self.header_wisp = QCheckBox("Wisp")
        for field in (self.header_biome, self.header_city_type):
            field.setStyleSheet("font-size:15px;font-weight:600;")
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for index, label in enumerate(("BIOME", "MAP OPTIONS", "AREA LEVEL", "CITY TYPE")):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 8, 10, 8)
            card_layout.setSpacing(1)
            caption = QLabel(label)
            caption.setProperty("role", "eyebrow")
            card_layout.addWidget(caption)
            if index == 1:
                toggles = QGridLayout()
                toggles.setVerticalSpacing(3)
                for number, field in enumerate((self.header_ocean, self.header_irradiated,
                                                 self.header_deli, self.header_wisp)):
                    toggles.addWidget(field, number // 2, number % 2)
                toggles.setColumnStretch(2, 1)
                card_layout.addLayout(toggles)
            else:
                card_layout.addWidget((self.header_biome, None, self.stat_values[2],
                                       self.header_city_type)[index])
            cards.addWidget(card, 1)
        main_layout.addLayout(cards)
        self.tabs = WorkspaceTabs()
        self.tabs.tabBar().hide()
        # Scroll pages manage their own content bounds. Standalone page
        # minimums are applied on navigation so the workspace can scroll them.
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        main_layout.addWidget(self.tabs, 1)
        self._build_review()
        self._build_expedition()
        self._build_settings()
        self._build_masters()
        self._build_inventory()
        self._build_ritual()
        self._build_data()
        self._build_scan_settings()
        self.scan_regions = ScanRegionsPage(self)
        self.tabs.addTab(self.scan_regions, "Scan regions")
        self.scan_regions.saved.connect(self._regions_saved)
        self.scan_regions.select_in_game.connect(self.select_region_in_game)
        self._build_reference_database()
        self._build_readme()
        self._build_license()
        self._build_disclaimer()
        self.atlas_settings_page = AtlasSettingsPage(self)
        self.atlas_settings_page.saved.connect(lambda data: self.run(lambda: self.save_atlas_settings(data)))
        self.tabs.addTab(self.atlas_settings_page, "Atlas / Character Settings")
        self._build_ocr_sensitivity()
        self.header_expedition.currentIndexChanged.connect(
            lambda: self.run(lambda: self.set_expedition(self.header_expedition)))
        for key, widgets in self._map_tag_controls().items():
            for widget in widgets:
                changed = widget.toggled if isinstance(widget, QCheckBox) else widget.currentIndexChanged
                changed.connect(lambda _, key=key, widget=widget:
                                self.run(lambda: self._save_map_tag(key, widget)))
        self.tabs.currentChanged.connect(self._page_changed)
        self._page_changed(0)
        self._apply_developer_mode()
        workspace = QScrollArea()
        workspace.setFrameShape(QFrame.Shape.NoFrame)
        workspace.setWidgetResizable(True)
        workspace.setWidget(main)
        root.addWidget(workspace, 1)
        self.setCentralWidget(central)
        menu = self.menuBar().addMenu("File")
        menu.addAction("Save Export CSV as…", lambda: self.run(lambda: self.save_as("csv")))
        menu.addAction("Save Export XLSX as…", lambda: self.run(lambda: self.save_as("xlsx")))
        menu.addAction("Save database backup as…", lambda: self.run(lambda: self.save_as("backup")))
        self.help_menu = self.menuBar().addMenu("Help")
        for label, index in (("README", 9), ("Licenses", 10), ("Disclaimer", 11)):
            self.help_menu.addAction(label, lambda checked=False, index=index: self.tabs.setCurrentIndex(index))

    def _arrange_header(self):
        """Pack small-window counters beside Complete while retaining the wide header layout."""
        if not hasattr(self, "_header_actions"):
            return
        # Keep long page headings from wrapping excessively beside the wider counters and actions.
        compact = self.width() < 1600
        if self._header_arrangement is None or self._header_arrangement[0] != compact:
            # Balance map/remnant labels with smaller numbers; expedition keeps its existing caption size.
            for counter in (self.header_map_id, self.header_remnant_id):
                counter.setStyleSheet(f"font-size:{41 if compact else 54}px;font-weight:700;color:#FFFFFF;")
            for index, caption in enumerate(self._header_identifier_captions):
                caption_size = (14 if compact else 17) if index < 2 else (12 if compact else 14)
                caption.setStyleSheet(f"font-size:{caption_size}px;font-weight:700;color:#D5B36C;")
        fields = (self.header_map_id, self.header_remnant_id, self.header_expedition)
        # Keep growing map/remnant numbers legible instead of compressing their text at narrow widths.
        narrow = self.width() < 1150
        status_width = self.header_complete_chain_button.sizeHint().width() + 24
        status_width += sum(max(field.minimumWidth(), field.sizeHint().width(), caption.sizeHint().width())
                            for field, caption in zip(fields[:2], self._header_identifier_captions[:2]))
        if not narrow:
            status_width += max(self.header_expedition.minimumWidth(), self.header_expedition.sizeHint().width(),
                                self._header_identifier_captions[2].sizeHint().width()) + 12
        stacked = (compact and status_width + self.kill_counts_group.minimumWidth()
                   + self._header_layout.spacing() > self.width() - self._header_horizontal_inset)
        arrangement = (compact, narrow, stacked)
        if self._header_arrangement == arrangement:
            return
        self._header_arrangement = arrangement
        layout = self._header_layout
        for item in (self._header_heading, self._header_identifiers, self._header_actions):
            layout.removeItem(item)
        layout.removeWidget(self.kill_counts_group)
        layout.removeWidget(self.header_complete_chain_button)
        layout.removeWidget(self._compact_header_status)
        for column, caption, field in zip(self._header_identifier_columns,
                                          self._header_identifier_captions, fields):
            column.removeWidget(caption)
            column.removeWidget(field)
            self._compact_header_grid.removeWidget(caption)
            self._compact_header_grid.removeWidget(field)
        self._compact_header_grid.removeWidget(self.header_complete_chain_button)
        for column in range(5):
            layout.setColumnStretch(column, 0)
        if compact:
            status = self._compact_header_grid
            status.addWidget(self.header_complete_chain_button, 1, 0, Qt.AlignmentFlag.AlignCenter)
            for index in range(2):
                status.addWidget(self._header_identifier_captions[index], 0, index + 1,
                                 Qt.AlignmentFlag.AlignCenter)
                status.addWidget(fields[index], 1, index + 1, Qt.AlignmentFlag.AlignCenter)
            if arrangement[1]:
                status.addWidget(self._header_identifier_captions[2], 2, 0, Qt.AlignmentFlag.AlignCenter)
                status.addWidget(self.header_expedition, 2, 1, 1, 2)
            else:
                status.addWidget(self._header_identifier_captions[2], 0, 3, Qt.AlignmentFlag.AlignCenter)
                status.addWidget(self.header_expedition, 1, 3, Qt.AlignmentFlag.AlignCenter)
            layout.addLayout(self._header_heading, 0, 0)
            layout.addLayout(self._header_actions, 0, 1, Qt.AlignmentFlag.AlignRight)
            layout.addWidget(self.kill_counts_group, 1, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(self._compact_header_status, 2 if stacked else 1,
                             0 if stacked else 1, 1, 2 if stacked else 1,
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._compact_header_status.show()
            layout.setColumnStretch(0, 1)
        else:
            for column, caption, field in zip(self._header_identifier_columns,
                                              self._header_identifier_captions, fields):
                column.addWidget(caption)
                column.addWidget(field)
            self._compact_header_status.hide()
            layout.addLayout(self._header_heading, 0, 0)
            layout.addWidget(self.kill_counts_group, 0, 1, Qt.AlignmentFlag.AlignLeft)
            layout.addWidget(self.header_complete_chain_button, 0, 2, Qt.AlignmentFlag.AlignCenter)
            layout.addLayout(self._header_identifiers, 0, 3)
            layout.addLayout(self._header_actions, 0, 4)
            layout.setColumnStretch(2, 1)
            layout.setColumnStretch(3, 1)

    def resizeEvent(self, event):
        """Reflow the shared header when a resize crosses its readable single-row width."""
        super().resizeEvent(event)
        self._arrange_header()

    def _page_changed(self, index):
        """Restore page opacity/capture behavior and keep standalone controls scrollable."""
        if self._end_hotkey_capture() and index != 7:
            service.HOTKEY.start()
        if index != 0:
            self._overlay_auto_review = False
            self._overlay_revealed = False
            self.setWindowOpacity(1.0)
            self._overlay_review_token += 1
        if hasattr(self, "scan_regions"):
            if index == 7 and not self._editing_regions:
                service.HOTKEY._unregister()
                self._editing_regions = True
            elif index != 7 and self._editing_regions:
                self._editing_regions = False
                service.HOTKEY.start()
        titles = ("Review", "Expedition", "Map / Tablets", "Atlas Masters", "Currency",
                  "Data export", "Scan settings", "Scan regions", "Database Import/Export", "README", "Licenses", "Disclaimer",
                  "Atlas / Character Settings", "OCR Sensitivity")
        subtitles = ("Check the latest scan, then Approve to save it.",
                     "Set the expedition and log its propagation chain.",
                     "Read a waystone or tablet, then review and save the map setup.",
                     "Choose the active master and its perks.",
                     "See all tracked currency types and their session totals.",
                     "Save the local data and manage your databases.",
                     "Set the hotkeys and scan behaviour.",
                     "Adjust the labelled capture boxes on a game screenshot.",
                     "Label examples and share your OCR reference database.",
                     "", "", "", "Set atlas passives, choose their effects and enter gear item rarity.",
                     "Adjust OCR Strictness separately for each scan type.")
        self.page_title.setText(titles[index])
        self.page_subtitle.setText(subtitles[index])
        self.page_subtitle.setVisible(bool(subtitles[index]))
        page = self.tabs.currentWidget()
        minimum = page.minimumSizeHint()
        if isinstance(page, QScrollArea):
            self.tabs.setMinimumSize(0, 0)
        else:
            self.tabs.setMinimumSize(max(0, minimum.width()), max(0, minimum.height()))
        for i, item in enumerate(self.nav_buttons):
            target = item.property("page_index")
            item.setProperty("active", "true" if (i if target is None else target) == index else "false")
            item.style().unpolish(item)
            item.style().polish(item)

    def _build_readme(self):
        """Display the bundled application README as Markdown with external links enabled."""
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(12, 18, 12, 22)
        self.readme_view = QTextBrowser()
        self.readme_view.setObjectName("readmeView")
        self.readme_view.setReadOnly(True)
        self.readme_view.setOpenExternalLinks(True)
        self.readme_view.setStyleSheet(
            "QTextBrowser { background:#1C1C1E; border:1px solid #3D352C; "
            "border-radius:9px; padding:18px; font-size:14px; }")
        self.readme_view.setMarkdown((HERE / "IN_APP_README.md").read_text(encoding="utf-8"))
        content.addWidget(self.readme_view)
        self.tabs.addTab(page, "README")

    def _build_license(self):
        """Build a license selector and read-only viewer for bundled notices."""
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(12, 18, 12, 22)
        self.license_picker = combo([
            ("CRAFTYXII_ASSETS_LICENSE.txt", "CraftyXII branding"),
            ("third_party/runehelper/LICENSE.txt", "RuneHelper"),
            ("third_party/currency_overlay/LICENSE", "Currency overlay"),
            ("third_party/currency_overlay/EXILED-EXCHANGE-2-LICENSE.txt", "Exiled Exchange 2"),
            ("fonts/LICENSE.txt", "Fonts"),
            ("AFFIX_CATALOG_NOTICE.txt", "Affix catalog"),
            ("atlas/NOTICE.txt", "PoE2 atlas data and artwork"),
        ])
        self.license_view = QTextBrowser()
        self.license_view.setObjectName("licenseView")
        self.license_view.setReadOnly(True)
        self.license_view.setStyleSheet(
            "QTextBrowser { background:#1C1C1E; border:1px solid #3D352C; "
            "border-radius:9px; padding:18px; font-size:14px; }")
        self.license_picker.currentIndexChanged.connect(self._show_license)
        content.addWidget(self.license_picker)
        content.addWidget(self.license_view)
        self._show_license()
        self.tabs.addTab(page, "Licenses")

    def _show_license(self):
        """Load the selected bundled license notice into the text viewer."""
        text = (HERE / value(self.license_picker)).read_text(encoding="utf-8")
        self.license_view.setPlainText(text)

    def _build_disclaimer(self):
        """Display the bundled product's third-party affiliation disclaimer."""
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(12, 18, 12, 22)
        self.disclaimer_view = QTextBrowser()
        self.disclaimer_view.setReadOnly(True)
        self.disclaimer_view.setStyleSheet(
            "QTextBrowser { background:#1C1C1E; border:1px solid #3D352C; "
            "border-radius:9px; padding:18px; font-size:14px; }")
        self.disclaimer_view.setPlainText(
            "PoE2 Data Logger is an independent third-party tool.\n\n"
            "This product is not affiliated with or endorsed by Grinding Gear Games in any way.")
        content.addWidget(self.disclaimer_view)
        self.tabs.addTab(page, "Disclaimer")

    def _review_pending(self, kind, summary, can_commit=True, rows=None, *, preserve_chain_draft=False):
        """Show controls for this scan, optionally retaining accepted chain drafts."""
        if kind not in ("remnant", "seed"):
            self._require_remnant_review_finished()
        self._failed_review = False
        if kind == "remnant" and self.recipe_table.property("savedRemnant"):
            self._render_recipe_rows([])
            with QSignalBlocker(self.recipe_family):
                select(self.recipe_family, "")
            set_message(self.recipe_status, "Resolve an observed recipe before manual saving.")
            self.scan_status.hide()
        if kind != "propagation":
            self._propagation_reading = None
        if kind not in ("remnant", "seed"):
            self._remnant_reading = None
        if kind != "tablet":
            self._pending_tablet_slot = None
        if kind != "currency":
            self._inventory_reading = None
            self._pending_currency_map = self._pending_currency_phase = None
        if kind != "ritual":
            self._ritual_reading = None
            self._pending_ritual_map = None
        self.pending_review_kind = kind
        self._held_remnant_review = None
        if kind != "propagation" and not preserve_chain_draft:
            self._discard_chain_review_draft()
        self._set_chain_review_active(kind == "propagation")
        self.review_kind.setText({"remnant": "Remnant", "seed": "Visible remnants",
                                  "waystone": "Waystone", "tablet": "Tablet",
                                  "currency": "Currency inventory", "ritual": "Ritual rewards",
                                  "propagation": "Propagation scan"}[kind])
        self.review_kind.setProperty("scanKind", kind)
        self._arrange_review_preview()
        set_message(self.review_summary, summary)
        self.found_table.setRowCount(0)
        for label, detail, check in rows or []:
            index = self.found_table.rowCount()
            self.found_table.insertRow(index)
            for column, text in enumerate((label, detail, check)):
                self.found_table.setItem(index, column, QTableWidgetItem(str(text)))
        self.found_table.setVisible(kind in ("waystone", "tablet", "propagation") and
                                    self.found_table.rowCount() > 0)
        self.found_label.setVisible((kind in ("waystone", "tablet", "propagation") and self.found_table.rowCount() > 0) or
                                    (kind == "currency" and self.inventory_table.rowCount() > 0) or
                                    (kind == "ritual" and self.ritual_table.rowCount() > 0))
        self.approve_scan_button.setEnabled(can_commit)
        self._review_controls(kind)
        self.review_edit_button.setVisible(kind in ("waystone", "tablet"))
        self.manual_remnant_button.setVisible(kind in ("remnant", "seed"))
        self.manual_remnant_button.setText("Enter remnant manually")
        self.currency_review_group.setVisible(kind == "currency")
        self.ritual_review_group.setVisible(kind == "ritual")
        self.scan_status.setVisible(kind in ("remnant", "seed") and bool(self.scan_status.text()))
        self.review_group.setVisible(kind in ("remnant", "seed") and
                                     bool(self.results["opened"] or self.results["seed"]))
        self.seed_fields_widget.setVisible(kind == "seed" and bool(self._seed_readings))
        self.seed_table.setVisible(bool(self._seed_readings) and (kind == "seed" or
                                  kind == "remnant" and self.mode == "both"))
        self.seed_rewards.setVisible(kind == "seed" and bool(self._seed_readings))
        self.use_opened_button.setVisible(kind == "remnant" and bool(self.results["opened"]))
        self.remnant_log_group.setVisible(kind == "remnant")
        self.tabs.setCurrentIndex(0)

        self._overlay_review_token += 1
        token = self._overlay_review_token
        QTimer.singleShot(0, lambda: self._reveal_review_overlay(token))

    def _review_controls(self, kind):
        """Expose the displayed scan's actions and required correction fields."""
        if hasattr(self, "developer_mode"):
            self._apply_developer_mode()
        self.approve_scan_button.setVisible(kind is not None and kind != "propagation")
        self.reject_scan_button.setVisible(kind is not None)
        if kind != "currency":
            self.approve_scan_button.setToolTip("")
        self.review_clear_tablets_button.setVisible(kind == "tablet")
        if hasattr(self, "manual_propagation_button"):
            self._update_manual_propagation_shortcut(kind)
        if not hasattr(self, "ritual_table"):
            return
        self.inventory_phase.setEnabled(kind != "currency" or
                                        self._inventory_reading is None and self.approve_scan_button.isEnabled())
        for review_kind, table, actions in (
                ("currency", self.inventory_table, (self.inventory_add_row_button,)),
                ("ritual", self.ritual_table, (self.ritual_add_row_button, self.ritual_remove_row_button))):
            editable = kind == review_kind and self.approve_scan_button.isEnabled()
            table.setEditTriggers(
                QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.AnyKeyPressed
                if editable else QTableWidget.EditTrigger.NoEditTriggers)
            for action in actions:
                action.setEnabled(editable)
            for row in range(table.rowCount()):
                if review_kind == "currency":
                    name_editor = table.cellWidget(row, 1)
                    if name_editor:
                        name_editor.setEnabled(editable)
                    controls = table.cellWidget(row, 3)
                    if controls:
                        controls.setEnabled(editable)
                else:
                    deferred = table.item(row, 5)
                    if deferred:
                        flags = deferred.flags()
                        deferred.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable if editable else
                                          flags & ~Qt.ItemFlag.ItemIsUserCheckable)
        ritual_editable = kind == "ritual" and self.approve_scan_button.isEnabled()
        self.ritual_tribute.setReadOnly(not ritual_editable)
        self.ritual_rerolls.setReadOnly(not ritual_editable)

    def _review_saved(self, kind, number):
        """Close only the matching review after a commit and clear its activity-specific
        capture ownership.
        """
        if self.pending_review_kind != kind:
            return
        self.pending_review_kind = None
        if kind != "propagation":
            self._discard_chain_review_draft()
        self._set_chain_review_active(kind == "propagation")
        if self._overlay_auto_review:
            self.hide_overlay()
        if kind == "tablet":
            self._pending_tablet_slot = None
        elif kind == "currency":
            self._inventory_reading = None
            self._pending_currency_map = self._pending_currency_phase = None
        elif kind == "ritual":
            self._ritual_reading = None
            self._pending_ritual_map = None
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.review_edit_button.hide()
        set_message(self.review_summary, f"✓ {self.review_kind.text()} saved · Scan Commit #{number}.", "success")

    def edit_review_settings(self):
        """Open Map / Tablets so the current waystone or tablet draft can be edited."""
        self.tabs.setCurrentIndex(2)

    def approve_review(self):
        """Dispatch review approval without promoting pending currency rows to accepted items."""
        kind = self.pending_review_kind
        if kind in ("seed", "remnant"):
            return self.approve_remnant_scan()
        return self.commit_review()

    def reject_review(self):
        """Cancel and clear the active non-remnant review, restoring saved form state without
        committing it.
        """
        self._failed_review = False
        kind = self.pending_review_kind
        if kind == "propagation":
            self._cancel_pending_chain_save()
        if kind in ("seed", "remnant"):
            return self.reject_remnant_scan()
        if kind is None:
            return
        service.HOTKEY.cancel_capture(modes=("propagation",) if kind == "propagation" else None)
        self._remnant_reading = None
        if kind == "waystone":
            self._waystone_form_baseline = None
        elif kind == "tablet":
            self._clear_tablet_form_dirty(self._pending_tablet_slot, clear_capacity=True)
        self.pending_review_kind = None
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
        self._pending_currency_phase = None
        self._inventory_reading = self._ritual_reading = None
        self._propagation_reading = None
        if kind == "propagation":
            self._clear_manual_propagation()
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.currency_review_group.hide()
        self.ritual_review_group.hide()
        self.found_table.hide()
        self.found_label.hide()
        self.review_edit_button.hide()
        self.refresh()
        set_message(self.review_summary, "Scan rejected; no entry was saved.")
        if self._overlay_auto_review:
            self.hide_overlay()

    def commit_review(self):
        """Validate and route the current review to its activity's persistence action."""
        if self._failed_review:
            raise ValueError("This scan failed. Scan again or reject this reading before saving.")
        kind = self.pending_review_kind
        if kind == "seed":
            return self.commit_seed_review()
        if kind == "remnant":
            if not self.resolved or self.resolved["status"] != "ready":
                if value(self.first_recipe):
                    self.resolve_recipe()
                else:
                    self.use_opened()
            return self.commit_remnant()
        if kind == "waystone":
            logger._number(value(self.waystone), "Waystone %", 0, 999)
            logger._integer(value(self.map_mods), "Map Mods", 0, 100)
            return self.save_map_settings()
        if kind == "tablet":
            number = self._pending_tablet_slot or int(value(self.tablet_scan_number))
            start = (number - 1) * 4
            if not any(value(field) for field in self.tablet_affixes[start:start + 4]):
                raise ValueError("No tablet affixes to commit. Check the captured image or enter an affix on Map / Tablets.")
            return self.save_tablets()
        if kind == "currency":
            return self.save_inventory()
        if kind == "ritual":
            return self.save_ritual()
        if kind == "propagation":
            return self._append_propagation()
        raise ValueError("There is no scan waiting to commit.")

    def _build_review(self):
        """Build scan-specific review controls and reachable per-recipe manual rune entry."""
        page, content = self._page()
        self.tabs.addTab(page, "Review")
        latest = self._group("SCAN REVIEW", content)
        self._review_page_layout = content
        self._review_latest_layout = latest
        self.review_kind = QLabel("Nothing waiting for review")
        self.review_kind.setStyleSheet("font-size:16px;font-weight:700;")
        latest.addWidget(self.review_kind)
        self.preview = QLabel("The latest scan preview appears here.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(180)
        self.preview.setMaximumHeight(260)
        self.preview.setStyleSheet("background:#121214;border:1px solid #4B4032;color:#BCB7AE;")
        latest.addWidget(self.preview)
        self.preview.hide()
        self.review_summary = message("Scan an activity to see its image and detected items here.")
        latest.addWidget(self.review_summary)
        self.review_items_host = QWidget()
        self.review_items_content = QVBoxLayout(self.review_items_host)
        self.review_items_content.setContentsMargins(0, 0, 0, 0)
        self.review_items_content.setSpacing(8)
        latest.addWidget(self.review_items_host)
        self.found_label = QLabel("ITEMS FOUND")
        self.found_label.setProperty("role", "eyebrow")
        self.review_items_content.addWidget(self.found_label)
        self.found_label.hide()
        self.found_table = QTableWidget(0, 3)
        self.found_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.found_table.setHorizontalHeaderLabels(["Found", "Reading", "Check"])
        self.found_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.found_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.found_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.found_table.verticalHeader().setDefaultSectionSize(38)
        self.found_table.setMinimumHeight(240)
        self.found_table.setMaximumHeight(340)
        self.review_items_content.addWidget(self.found_table)
        self.found_table.hide()
        self.scan_status = message("")
        latest.addWidget(self.scan_status)
        self.scan_status.hide()
        self.review_group = QWidget()
        review = QVBoxLayout(self.review_group)
        review.setContentsMargins(0, 0, 0, 0)
        self.review_items_content.addWidget(self.review_group)
        self.review_group.hide()
        self.opened_lines = QListWidget()
        self.opened_lines.hide()
        self.seed_table = QTableWidget(0, 5)
        self.seed_table.setHorizontalHeaderLabels(["Use", "Remnant", "Visible seed", "Family", "Check"])
        self.seed_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.seed_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.seed_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.seed_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (0, 1, 3, 4):
            self.seed_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.seed_table.verticalHeader().setDefaultSectionSize(38)
        self.seed_table.setMinimumHeight(180)
        self.seed_table.setMaximumHeight(340)
        self.seed_table.currentCellChanged.connect(lambda row, *_: self._select_seed(row))
        self.seed_table.itemChanged.connect(lambda *_: self._seed_commit_enabled())
        review.addWidget(self.seed_table)
        self.seed_table.hide()
        self.seed_fields_widget = QWidget()
        seed_form = QHBoxLayout(self.seed_fields_widget)
        seed_form.setContentsMargins(0, 0, 0, 0)
        self.seed_sockets = line("Sockets")
        self.seed_slot = line("P3")
        self.seed_rune = line("Power")
        self.seed_family = combo([("", "Unresolved")])
        for label, widget in (("Sockets", self.seed_sockets), ("Visible slot", self.seed_slot),
                              ("Seed rune", self.seed_rune), ("Family", self.seed_family)):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(widget)
            seed_form.addLayout(column)
        review.addWidget(self.seed_fields_widget)
        self.seed_rewards = QLabel("")
        self.seed_rewards.setWordWrap(True)
        review.addWidget(self.seed_rewards)
        for field in (self.seed_sockets, self.seed_slot, self.seed_rune):
            field.textEdited.connect(lambda *_: self._seed_fields_changed())
        self.seed_family.currentIndexChanged.connect(lambda *_: self._seed_fields_changed(family_only=True))
        review_buttons = QHBoxLayout()
        self.use_opened_button = button("Use opened rewards", lambda: self.run(self.use_opened))
        review_buttons.addWidget(self.use_opened_button)
        review_buttons.addWidget(button("Discard pending scan", lambda: self.run(self.discard_scan)))
        review_buttons.addStretch()
        review.addLayout(review_buttons)
        self.remnant_log_group = QWidget()
        remnant = QVBoxLayout(self.remnant_log_group)
        remnant.setContentsMargins(0, 0, 0, 0)
        self.review_items_content.addWidget(self.remnant_log_group)
        self.remnant_log_group.hide()
        inputs = QFormLayout()
        self.first_recipe = line("First reward or alias")
        self.next_recipe = line("Only if needed")
        self.recipe_family = combo([("", "Auto resolve")])
        inputs.addRow("First Recipe", self.first_recipe)
        inputs.addRow("Next Recipe", self.next_recipe)
        inputs.addRow("Family", self.recipe_family)
        remnant.addLayout(inputs)
        self.first_recipe.textChanged.connect(self._invalidate_resolution)
        self.next_recipe.textChanged.connect(self._invalidate_resolution)
        self.recipe_family.currentIndexChanged.connect(self._invalidate_resolution)
        buttons = QHBoxLayout()
        buttons.addWidget(button("Resolve recipe", lambda: self.run(self.resolve_recipe)))
        buttons.addStretch()
        remnant.addLayout(buttons)
        self.recipe_status = message("Resolve an observed recipe before manual saving.")
        self.recipe_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        remnant.addWidget(self.recipe_status)
        self.recipe_table = QTableWidget(0, 3)
        self.recipe_table.setHorizontalHeaderLabels(["Recipe / reward", "Sockets", "Rune combo"])
        self.recipe_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.recipe_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.recipe_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.recipe_table.verticalHeader().setDefaultSectionSize(42)
        self.recipe_table.setMinimumHeight(240)
        self.recipe_table.setMaximumHeight(400)
        remnant.addWidget(self.recipe_table)
        self.recipe_table.hide()
        actions = QHBoxLayout()
        self.approve_scan_button = button("Approve", lambda: self.run(self.approve_review), "primary")
        self.approve_scan_button.setEnabled(False)
        self.reject_scan_button = button("Reject", lambda: self.run(self.reject_review), "danger")
        actions.addWidget(self.approve_scan_button)
        actions.addWidget(self.reject_scan_button)
        self.review_clear_tablets_button = button("Clear tablet config", lambda: self.run(self.clear_tablets), "danger")
        actions.addWidget(self.review_clear_tablets_button)
        self.review_edit_button = button("Edit activity settings", self.edit_review_settings)
        self.review_edit_button.hide()
        actions.addWidget(self.review_edit_button)
        actions.addStretch()
        latest.addLayout(actions)
        self._review_controls(None)
        controls = QHBoxLayout()
        self.manual_remnant_button = button("Enter remnant manually", self.start_manual_remnant_review)
        controls.addWidget(self.manual_remnant_button)
        self.manual_propagation_button = button("Enter propagation manually", self.start_manual_propagation)
        controls.addWidget(self.manual_propagation_button)
        controls.addStretch()
        latest.addLayout(controls)
        self.chain_review_group = QGroupBox("PROPAGATION CHAIN")
        chain_review = QVBoxLayout(self.chain_review_group)
        self.chain_review_status = message("Scan propagation using its dedicated key, then approve the marked runes in its recipe row.")
        chain_review.addWidget(self.chain_review_status)
        self._propagation_choices = []
        self._propagation_row_inputs = []
        self._propagation_manual_requested = False
        self.propagation_recipe_table = QTableWidget(0, 3)
        self.propagation_recipe_table.setAccessibleName("Detected propagation recipes")
        self.propagation_recipe_table.setHorizontalHeaderLabels(["Recipe", "Marked runes · left to right", "Review"])
        self.propagation_recipe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.propagation_recipe_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.propagation_recipe_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.propagation_recipe_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.propagation_recipe_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.propagation_recipe_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.propagation_recipe_table.verticalHeader().setDefaultSectionSize(62)
        self.propagation_recipe_table.setMaximumHeight(260)
        chain_review.addWidget(self.propagation_recipe_table)
        self.propagation_recipe_table.hide()
        with logger._connect() as db:
            rune_names = sorted({rune.strip() for row in db.execute("SELECT combo FROM recipes")
                                 for rune in (row[0] or "").split("+")
                                 if rune.strip() and rune.strip().casefold() != "unresolved"})
        self.propagation_manual_group = QWidget()
        manual_group = QVBoxLayout(self.propagation_manual_group)
        manual_group.setContentsMargins(0, 0, 0, 0)
        self.propagation_manual_label = QLabel("Select a recipe, then enter its marked runes below.")
        self.propagation_manual_label.setWordWrap(True)
        self.propagation_manual_label.hide()
        manual_group.addWidget(self.propagation_manual_label)
        manual = QHBoxLayout()
        self.propagation_rune_inputs = []
        for caption in ("First marked rune", "Second marked rune (optional)"):
            column = QVBoxLayout()
            column.addWidget(QLabel(caption))
            field = combo([("", "")] + rune_names)
            field.setEditable(True)
            field.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            field.setAccessibleName(caption)
            field.editTextChanged.connect(self._manual_propagation_changed)
            column.addWidget(field)
            manual.addLayout(column, 1)
            self.propagation_rune_inputs.append(field)
        self.propagation_add_button = button("Save chain part", lambda: self.run(self.add_manual_propagation), "primary")
        self.propagation_add_button.setEnabled(False)
        manual.addWidget(self.propagation_add_button)
        manual_group.addLayout(manual)
        self.propagation_recipe_table.currentCellChanged.connect(self._propagation_recipe_selected)
        help_text = QLabel("Enter only the runes with three gold marks, left to right. Select a recipe row to attach it to a manual entry. "
                          "Save chain part saves one remnant to the selected expedition. "
                          "Saved runes can be corrected on Expedition.")
        help_text.setWordWrap(True)
        manual_group.addWidget(help_text)
        chain_review.addWidget(self.propagation_manual_group)
        self.propagation_manual_group.hide()
        self.chain_review_table = QTableWidget(0, 3)
        self.chain_review_table.setHorizontalHeaderLabels(["Chain part", "Rune", "Recipe"])
        self.chain_review_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked |
                                               QTableWidget.EditTrigger.AnyKeyPressed)
        self.chain_review_table.itemChanged.connect(self._edit_chain_review_rune)
        self.chain_review_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.chain_review_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.chain_review_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.chain_review_table.verticalHeader().setDefaultSectionSize(38)
        self.chain_review_table.setMinimumHeight(170)
        self.chain_review_table.setMaximumHeight(340)
        chain_review.addWidget(self.chain_review_table)
        chain_actions = QHBoxLayout()
        self.review_complete_chain_button = button("Complete chain", lambda: self.run(self.complete_chain), "primary")
        chain_actions.addWidget(self.review_complete_chain_button)
        chain_actions.addStretch()
        chain_review.insertLayout(1, chain_actions)
        self.review_items_content.insertWidget(0, self.chain_review_group)
        self.chain_review_group.hide()
        content.addStretch()

    def _build_expedition(self):
        """Build the expedition's saved-chain editor and manual propagation entry action."""
        page, content = self._page()
        self.tabs.addTab(page, "Expedition")
        self._build_chain_controls(content)
        manual = QHBoxLayout()
        self.expedition_manual_propagation_button = button(
            "Enter propagation manually", lambda: self.run(self.start_manual_propagation))
        manual.addWidget(self.expedition_manual_propagation_button)
        manual.addStretch()
        content.addLayout(manual)
        content.addStretch()

    def _build_scan_settings(self):
        """Build capture preferences, hotkeys and HUD confirmation controls."""
        page, content = self._page()
        self.tabs.addTab(page, "Scan settings")
        performance = self._group("Scan performance", content)
        thread_row = QHBoxLayout()
        thread_row.addWidget(QLabel("CPU OCR threads"))
        self._ocr_saved_threads = ocr_runtime.saved_threads()
        self.ocr_threads_select = combo(ocr_runtime.THREAD_OPTIONS, self._ocr_saved_threads)
        self.ocr_threads_select.setAccessibleName("CPU OCR threads")
        self.ocr_threads_select.currentIndexChanged.connect(lambda: self.run(self.save_ocr_threads))
        thread_row.addWidget(self.ocr_threads_select)
        thread_row.addStretch()
        performance.addLayout(thread_row)
        self.ocr_threads_status = QLabel()
        self.ocr_threads_status.setWordWrap(True)
        performance.addWidget(self.ocr_threads_status)
        self._update_ocr_threads_status()
        thread_help = QLabel("Higher counts may speed up large currency scans, but use more CPU while the game is running. "
                             "Two threads is the default.")
        thread_help.setWordWrap(True)
        performance.addWidget(thread_help)
        self.tablet_region_label = QLabel()
        self.inventory_region_label = QLabel()
        self.ritual_region_label = QLabel()
        hotkey = self._group("Scan hotkey", content)
        hotkey.addWidget(paragraph("General scan reads the hovered item or selected remnant view. Optional direct keys target one scanner."))
        keys = QGridLayout()
        keys.setHorizontalSpacing(12)
        keys.setVerticalSpacing(7)
        keys.setColumnMinimumWidth(0, 160)
        keys.setColumnMinimumWidth(1, 70)
        keys.setColumnMinimumWidth(2, 85)
        keys.setColumnMinimumWidth(3, 85)
        keys.setColumnStretch(4, 1)
        keys.addWidget(QLabel("General scan"), 0, 0)
        self.hotkey_label = QLabel("Off")
        keys.addWidget(self.hotkey_label, 0, 1)
        keys.addWidget(button("Set key", lambda: self.arm_hotkey("default")), 0, 2)
        keys.addWidget(button("Clear", lambda: self.run(lambda: self.clear_hotkey("default"))), 0, 3)
        self.direct_hotkey_labels = {}
        for index, (kind, title) in enumerate((("remnant", "Remnant"), ("waystone", "Waystone"),
                                               ("tablet", "Tablet"), ("currency", "Currency inventory"),
                                               ("ritual", "Ritual page"),
                                               ("propagation", "Propagation scan"),
                                               ("overlay", "Show / hide HUD")), 1):
            keys.addWidget(QLabel(title), index, 0)
            label = QLabel("Off")
            self.direct_hotkey_labels[kind] = label
            keys.addWidget(label, index, 1)
            keys.addWidget(button("Set key", lambda checked=False, target=kind: self.arm_hotkey(target)), index, 2)
            keys.addWidget(button("Clear", lambda checked=False, target=kind:
                                  self.run(lambda: self.clear_hotkey(target))), index, 3)
        hotkey.addLayout(keys)
        propagation_help = QLabel("Propagation uses only its own key. Each accepted scan counts one detonated remnant "
                                  "and saves its runes to the selected expedition. "
                                  "Every propagation scan requires manual review; recipe Approve saves its runes directly. "
                                  "Complete chain finishes the chain and advances the expedition number.")
        propagation_help.setWordWrap(True)
        hotkey.addWidget(propagation_help)
        overlay = self._group("HUD overlay", content)
        self.overlay_checkbox = QCheckBox("Enable HUD overlay")
        self.overlay_checkbox.setChecked(self._overlay_enabled)
        self.overlay_checkbox.toggled.connect(lambda checked: self.run(lambda: self.set_overlay_enabled(checked)))
        overlay.addWidget(self.overlay_checkbox)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("HUD opacity"))
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(20, 100)
        self.overlay_opacity_slider.setPageStep(5)
        self.overlay_opacity_slider.setValue(self._overlay_opacity)
        self.overlay_opacity_slider.setAccessibleName("Overlay opacity")
        self.overlay_opacity_label = QLabel(f"{self._overlay_opacity}%")
        self.overlay_opacity_label.setMinimumWidth(44)
        self.overlay_opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.overlay_opacity_slider.valueChanged.connect(self.set_overlay_opacity)
        opacity_row.addWidget(self.overlay_opacity_slider, 1)
        opacity_row.addWidget(self.overlay_opacity_label)
        overlay.addLayout(opacity_row)
        overlay_help = QLabel("Use the Show / hide HUD shortcut to open Review over the game at the selected opacity. "
                              "Held scans open an opaque confirmation window automatically; "
                              "saving the review or pressing Esc returns to the game. Move and resize the HUD normally. "
                              "Use borderless or windowed game mode.")
        overlay_help.setWordWrap(True)
        overlay.addWidget(overlay_help)
        self.overlay_toggle_button = button("Show / hide HUD", self.toggle_overlay)
        self.overlay_toggle_button.setEnabled(self._overlay_enabled)
        overlay.addWidget(self.overlay_toggle_button)
        self.auto_all_checkbox = QCheckBox("Auto-commit successful OCR scans for all activities")
        self.auto_all_checkbox.toggled.connect(
            lambda checked: self.run(lambda: self.set_auto_all(checked)))
        hotkey.addWidget(self.auto_all_checkbox)
        guidance = QLabel("Clear remnant, waystone, tablet, currency and Ritual reads save immediately. "
                          "Incomplete or uncertain reads stay open for review.")
        guidance.setWordWrap(True)
        hotkey.addWidget(guidance)
        self.auto_commit_checkbox = QCheckBox("Auto-save clear remnant scans")
        self.auto_commit_checkbox.toggled.connect(
            lambda checked: self.run(lambda: self.set_auto_commit(checked)))
        hotkey.addWidget(self.auto_commit_checkbox)
        self.auto_tablet_checkbox = QCheckBox("Auto-save tablets 1–4 in scan order")
        self.auto_tablet_checkbox.toggled.connect(
            lambda checked: self.run(lambda: self.set_auto_tablets(checked)))
        hotkey.addWidget(self.auto_tablet_checkbox)
        self.auto_tablet_next = QLabel("")
        hotkey.addWidget(self.auto_tablet_next)
        mode = self._group("Screenshot and hotkey mode", content)
        row = QHBoxLayout()
        self.mode_select = combo([("opened", "Opened remnant only"), ("seed", "Visible seed only"),
                                  ("both", "Both — confirm seed with opened remnant")], self.mode)
        self.mode_select.currentIndexChanged.connect(self.change_mode)
        row.addWidget(QLabel("Remnant view"))
        row.addWidget(self.mode_select)
        row.addStretch()
        mode.addLayout(row)
        mode.addWidget(paragraph("Hover a waystone or tablet and press its scan hotkey."
                              " Results open on Review."))
        mode.addWidget(QLabel("Each scan is triggered by a hotkey press."))
        self.tablet_scan_number = combo([1, 2, 3, 4])
        self.tablet_scan_number.hide()
        self.tablet_scan_number.currentIndexChanged.connect(self.show_tablet_raw)
        content.addStretch()

    def _build_ocr_sensitivity(self):
        """Build per-scan strictness sliders independently of capture and map settings."""
        page, content = self._page()
        self.tabs.addTab(page, "OCR Sensitivity")
        sensitivity = self._group("OCR Strictness", content)
        self._ocr_sensitivity_saved = ocr_sensitivity.saved_values()
        self.ocr_sensitivity_sliders = {}
        self.ocr_sensitivity_labels = {}
        for kind, title in ocr_sensitivity.SCAN_TYPES:
            row = QHBoxLayout()
            caption = QLabel(title)
            caption.setMinimumWidth(225)
            row.addWidget(caption)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setPageStep(5)
            slider.setValue(self._ocr_sensitivity_saved[kind])
            slider.setAccessibleName(f"{title} OCR Strictness")
            slider.setToolTip(
                "0: looser matches. 50: current defaults. Propagation always needs manual approval."
                if kind == "propagation" else
                "0: looser matches. 50: current defaults. 100: manual confirmation for OCR.")
            self.ocr_sensitivity_sliders[kind] = slider
            number = QLabel(str(slider.value()))
            number.setMinimumWidth(30)
            self.ocr_sensitivity_labels[kind] = number
            slider.valueChanged.connect(lambda level, key=kind: self.run(lambda: self.save_ocr_sensitivity(key, level)))
            row.addWidget(slider, 1)
            row.addWidget(number)
            sensitivity.addLayout(row)
        sensitivity_help = QLabel("Lower strictness accepts more tentative matches automatically. "
                                  "50 preserves the current settings for each scan type. "
                                  "At 100, OCR results require manual confirmation. "
                                  "Propagation always needs manual approval at every setting; its slider adjusts recognition only. "
                                  "Missing counts, conflicting recipes and incomplete scans still need review. "
                                  "Changes apply to the next scan.")
        sensitivity_help.setWordWrap(True)
        sensitivity.addWidget(sensitivity_help)
        self.ocr_sensitivity_reset = button("Reset OCR Strictness to defaults", self.reset_ocr_sensitivity)
        sensitivity.addWidget(self.ocr_sensitivity_reset)
        content.addStretch()

    def _restore_ocr_sensitivity(self, values):
        """Synchronize saved slider values and labels without emitting additional preference writes."""
        self._ocr_sensitivity_saved = dict(values)
        for kind, slider in self.ocr_sensitivity_sliders.items():
            with QSignalBlocker(slider):
                slider.setValue(values[kind])
            self.ocr_sensitivity_labels[kind].setText(str(values[kind]))

    def save_ocr_sensitivity(self, kind, level):
        """Persist one slider change, restoring the last saved controls if the write fails."""
        previous = dict(self._ocr_sensitivity_saved)
        try:
            saved = ocr_sensitivity.save_values({**previous, kind: level})
        except Exception:
            self._restore_ocr_sensitivity(previous)
            raise
        self._restore_ocr_sensitivity(saved)

    def reset_ocr_sensitivity(self):
        """Restore every scan's current-behavior default atomically, leaving pending scans unchanged."""
        self.run(lambda: self._restore_ocr_sensitivity(
            ocr_sensitivity.save_values({kind: ocr_sensitivity.DEFAULT for kind in self.ocr_sensitivity_sliders})))

    def _update_ocr_threads_status(self):
        """Compare the saved CPU-thread preference with the active OCR runtime and explain
        restart requirements.
        """
        saved = value(self.ocr_threads_select)
        text = f"Using {self._ocr_active_threads} CPU OCR threads. "
        if saved != self._ocr_active_threads:
            text += f"Saved: {saved} threads; restart the app to apply."
        else:
            text += "Changes take effect after restarting the app."
        self.ocr_threads_status.setText(text)

    def save_ocr_threads(self):
        """Persist the OCR-thread choice, restoring the prior selection if validation fails."""
        try:
            self._ocr_saved_threads = ocr_runtime.save_threads(value(self.ocr_threads_select))
        except Exception:
            select(self.ocr_threads_select, self._ocr_saved_threads)
            self._update_ocr_threads_status()
            raise
        self._update_ocr_threads_status()

    def _build_reference_database(self):
        """Build labeled references and reference-only import, export and reset controls."""
        page, content = self._page()
        self.tabs.addTab(page, "Database Import/Export")
        examples = self._group("LABEL ICON SCREENSHOTS", content)
        instructions = QLabel("Select a screenshot, drag a box around one icon, and label it. "
                              "Currency and Item examples are used for inventory scans; Omen examples can help "
                              "review Ritual pages when the text is unclear.")
        instructions.setWordWrap(True)
        instructions.setProperty("role", "note")
        examples.addWidget(instructions)
        row = QHBoxLayout()
        self.reference_kind = combo([("omen", "Omen"), ("currency", "Currency"), ("item", "Item")])
        self.reference_name = combo([])
        self.reference_name.setEditable(True)
        self.reference_name.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Long catalog names must not force the capture and sharing controls off a compact page.
        self.reference_name.setMinimumContentsLength(12)
        self.reference_name.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(QLabel("Type"))
        row.addWidget(self.reference_kind)
        row.addWidget(QLabel("Name"))
        row.addWidget(self.reference_name, 1)
        row.addWidget(button("Add icon screenshot…", lambda: self.run(self.add_reference_screenshot), "primary"))
        examples.addLayout(row)
        self.reference_kind.currentIndexChanged.connect(self.refresh_reference_names)
        examples.addWidget(button("Add remnant seed screenshot…", lambda: self.run(self.add_remnant_reference)))
        self.reference_list = QListWidget()
        self.reference_list.setMaximumHeight(190)
        examples.addWidget(self.reference_list)
        examples.addWidget(button("Remove selected example", lambda: self.run(self.remove_reference_example)))

        sharing = self._group("SHARE OCR DATABASE", content)
        description = QLabel("The pack includes Family and Recipe DB, aliases, visible seeds, reviewed rune "
                             "screenshots and glyphs, tablet affixes, Atlas perk names, Currency, Omen and Item "
                             "names, and labeled icon examples. Logs, IDs and personal settings are excluded.")
        description.setWordWrap(True)
        description.setProperty("role", "note")
        sharing.addWidget(description)
        folder_row = QHBoxLayout()
        with logger._connect() as db:
            saved_folder = logger._meta(db, "reference_export_folder", "")
        self.reference_folder = line("Reference export folder")
        self.reference_folder.setText(saved_folder or str(DEFAULT_REFERENCE_FOLDER))
        folder_row.addWidget(self.reference_folder, 1)
        folder_row.addWidget(button("Choose folder…", self.choose_reference_folder))
        sharing.addLayout(folder_row)
        controls = QHBoxLayout()
        controls.addWidget(button("Save reference pack to folder", lambda: self.run(self.export_reference_pack), "primary"))
        controls.addWidget(button("Import reference pack…", lambda: self.run(self.import_reference_pack)))
        controls.addStretch()
        sharing.addLayout(controls)
        # Keep the reset action on its own row so compact windows retain readable reference controls.
        self.reference_reset_button = button("Reset to defaults", lambda: self.run(self.reset_reference_database))
        self.reference_reset_button.setAccessibleName("Reset OCR references to defaults")
        sharing.addWidget(self.reference_reset_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.reference_replace = QCheckBox("Update matching family, recipe and seed records on import")
        sharing.addWidget(self.reference_replace)
        self.reference_status = message("")
        sharing.addWidget(self.reference_status)
        content.addStretch()
        self.refresh_reference_names()
        self.refresh_reference_examples()

    def _build_settings(self):
        """Build editable map and tablet forms, keeping scan-filled values pending until saved."""
        page, content = self._page()
        self.tabs.addTab(page, "Map / Tablets")
        map_box = self._group("Map settings", content)
        self.waystone = line("Waystone %")
        self.tier = combo([(15, "T15 · base 79"), (16, "T16 · base 80")])
        self.map_mods = line("Map Mods")
        self.map_ocr_overrides = QWidget()
        grid = QGridLayout(self.map_ocr_overrides)
        for i, (name, widget) in enumerate((("Tier", self.tier), ("Map Mods", self.map_mods))):
            grid.addWidget(QLabel(name), 0, i)
            grid.addWidget(widget, 1, i)
        map_box.addWidget(self.map_ocr_overrides)
        stats = QHBoxLayout()
        self.item_rarity = line("Item Rarity %")
        self.monster_rarity = line("Monster Rarity %")
        self.pack_size = line("Pack Size %")
        self.effectiveness = line("Effectiveness %")
        for label, field in (("Waystone %", self.waystone),
                             ("Item Rarity %", self.item_rarity),
                             ("Monster Rarity %", self.monster_rarity),
                             ("Pack Size %", self.pack_size),
                             ("Effectiveness %", self.effectiveness)):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(field)
            stats.addLayout(column)
        map_box.addLayout(stats)
        self.waystone_name = line("Waystone name")
        map_box.addWidget(self.waystone_name)
        self.waystone_name.hide()
        affix_box = self._group("Waystone affixes · up to 10", content)
        affix_grid = QGridLayout()
        self.waystone_mod_fields = []
        for index in range(10):
            field = line("Full modifier text")
            field.setMaxLength(250)
            affix_grid.addWidget(QLabel(f"Mod {index + 1}"), index // 2, (index % 2) * 2)
            affix_grid.addWidget(field, index // 2, (index % 2) * 2 + 1)
            self.waystone_mod_fields.append(field)
        affix_box.addLayout(affix_grid)
        self.map_scan_status = message("Hovered waystone readings appear here for review.")
        map_box.addWidget(self.map_scan_status)
        traits = self._group("Map tags", content)
        tags = QHBoxLayout()
        self.aldur = combo(logger.ALDUR_AFFIXES)
        self.biome = combo(logger.BIOMES)
        self.city_type = combo(logger.CITY_TYPES)
        for label, field in (("Aldur's Affixes", self.aldur),
                             ("Biome", self.biome), ("City Type", self.city_type)):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(field)
            tags.addLayout(column)
        tags.addStretch()
        traits.addLayout(tags)
        checks = QHBoxLayout()
        self.irradiated = QCheckBox("Irradiated +1")
        self.ocean = QCheckBox("Ocean Map +1")
        self.deli = QCheckBox("Deli")
        self.wisp = QCheckBox("Wisp")
        checks.addWidget(self.irradiated)
        checks.addWidget(self.ocean)
        checks.addWidget(self.deli)
        checks.addWidget(self.wisp)
        checks.addStretch()
        traits.addLayout(checks)
        self.area = message("")
        traits.addWidget(self.area)
        for widget in (self.tier, self.irradiated, self.ocean):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self.update_area)
            else:
                widget.currentIndexChanged.connect(self.update_area)
        map_actions = QHBoxLayout()
        map_actions.addWidget(button("Save map settings", lambda: self.run(self.save_map_settings), "primary"))
        map_actions.addStretch()
        traits.addLayout(map_actions)
        tablets = self._group("Tablet config", content)
        head = QHBoxLayout()
        self.tablets_used = combo([1, 2, 3, 4])
        head.addWidget(QLabel("Tablets used"))
        head.addWidget(self.tablets_used)
        head.addStretch()
        tablets.addLayout(head)
        self.tablets_used.currentIndexChanged.connect(self.tablet_active)
        self.tablet_scan_status = message("Tablet scans fill slots 1–4 in order. Review held readings, then Approve.")
        tablets.addWidget(self.tablet_scan_status)
        self.tablet_raw_preview = QTextEdit()
        self.tablet_raw_preview.setReadOnly(True)
        self.tablet_raw_preview.setMaximumHeight(90)
        self.tablet_raw_preview.setPlaceholderText("All modifiers read from the selected tablet appear here.")
        tablets.addWidget(self.tablet_raw_preview)
        self.tablet_raw_preview.hide()
        self.tablet_groups = []
        self.tablet_affixes = []
        self.tablet_values = []
        for tablet in range(4):
            group = QGroupBox(f"Tablet {tablet + 1}")
            layout = QGridLayout(group)
            for slot in range(4):
                i = tablet * 4 + slot
                affix = combo([("", "— None —")])
                affix.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                affix.setMinimumContentsLength(24)
                amount = line("Value")
                affix.currentIndexChanged.connect(
                    lambda _, a=affix, v=amount: v.setPlaceholderText(affix_unit(value(a)) if value(a) else "Value"))
                affix.currentIndexChanged.connect(
                    lambda _, number=tablet + 1: self._tablet_review_edit_changed(number))
                amount.textChanged.connect(
                    lambda _, number=tablet + 1: self._tablet_review_edit_changed(number))
                layout.addWidget(QLabel(f"Mod {slot + 1}"), slot, 0)
                layout.addWidget(affix, slot, 1)
                layout.addWidget(amount, slot, 2)
                self.tablet_affixes.append(affix)
                self.tablet_values.append(amount)
            tablets.addWidget(group)
            self.tablet_groups.append(group)
        tablet_buttons = QHBoxLayout()
        tablet_buttons.addWidget(button("Save tablet config", lambda: self.run(self.save_tablets), "primary"))
        tablet_buttons.addWidget(button("Clear tablet config", lambda: self.run(self.clear_tablets), "danger"))
        tablet_buttons.addStretch()
        tablets.addLayout(tablet_buttons)
        add = QHBoxLayout()
        self.new_affix = line("New tablet affix")
        add.addWidget(self.new_affix, 1)
        add.addWidget(button("Add to Affix DB", lambda: self.run(self.add_affix)))
        tablets.addLayout(add)
        content.addStretch()

    def _build_masters(self):
        """Build separate controls for the active Atlas master and each master's perk
        selections.
        """
        page, content = self._page()
        self.tabs.addTab(page, "Atlas Masters")
        active = self._group("ACTIVE ATLAS MASTER", content)
        row = QHBoxLayout()
        self.master = combo(["None", "Jado", "Doryani", "Hilda"])
        self.master.setMinimumWidth(250)
        self.master.setMaximumWidth(360)
        row.addWidget(self.master)
        row.addWidget(button("Save active master", lambda: self.run(self.save_active_master), "primary"))
        row.addStretch()
        active.addLayout(row)
        perks = self._group("MASTER PERKS", content)
        self.perk_master = combo(["Jado", "Doryani", "Hilda"])
        self.perk_master.setMinimumWidth(250)
        self.perk_master.currentIndexChanged.connect(self.render_perks)
        perk_choice = QHBoxLayout()
        perk_choice.addWidget(QLabel("Configure perks for"))
        self.perk_master.setMaximumWidth(360)
        perk_choice.addWidget(self.perk_master)
        perk_choice.addStretch()
        perks.addLayout(perk_choice)
        self.perk_boxes = [combo(["None"]) for _ in range(4)]
        perk_grid = QGridLayout()
        perk_grid.setHorizontalSpacing(16)
        for i, box in enumerate(self.perk_boxes, 1):
            column = QVBoxLayout()
            column.addWidget(QLabel(f"Perk {i}"))
            column.addWidget(box)
            perk_grid.addLayout(column, (i - 1) // 2, (i - 1) % 2)
        perks.addLayout(perk_grid)
        save = QHBoxLayout()
        save.addWidget(button("Save perks", lambda: self.run(self.save_perks), "primary"))
        save.addStretch()
        perks.addLayout(save)
        content.addStretch()

    def _build_inventory(self):
        """Build session currency totals and attach the editable inventory snapshot review to
        Review.
        """
        page, content = self._page()
        self.tabs.addTab(page, "Currency")
        self.session_currency = SessionCurrencyCounter()
        self._session_currency_key = None
        self._session_currency_icons = {}
        content.addWidget(self.session_currency)
        self.inventory_status = message("")
        self.inventory_status.setParent(self)
        self.inventory_status.hide()
        self.inventory_preview = QLabel()
        self.inventory_preview.setProperty("role", "note")
        self.inventory_preview.installEventFilter(self)

        self.currency_review_group = QWidget()
        review = QVBoxLayout(self.currency_review_group)
        review.setContentsMargins(0, 0, 0, 0)
        self.review_items_content.addWidget(self.currency_review_group)
        self.currency_review_group.hide()
        self.inventory_phase = combo([("start", "Start of map"), ("end", "End of map")])
        with logger._connect() as db:
            select(self.inventory_phase, logger._meta(db, "inventory_scan_phase", "start"))
        self.inventory_phase.currentIndexChanged.connect(self._inventory_phase_changed)
        phase = QHBoxLayout()
        phase.addWidget(QLabel("Snapshot"))
        phase.addWidget(self.inventory_phase)
        self.inventory_add_row_button = button("Add review row", self.add_inventory_row)
        self.inventory_add_row_button.setEnabled(False)
        phase.addWidget(self.inventory_add_row_button)
        phase.addStretch()
        review.addLayout(phase)
        self.inventory_table = QTableWidget(0, 4)
        self.inventory_table.setHorizontalHeaderLabels(["Icon", "Currency / Item", "Count", "Review"])
        self.inventory_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.inventory_table.setColumnWidth(0, 60)
        self.inventory_table.setIconSize(QSize(48, 48))
        self.inventory_table.setColumnWidth(2, 75)
        self.inventory_table.setColumnWidth(3, 205)
        self.inventory_table.itemChanged.connect(self._inventory_row_changed)
        self.inventory_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.inventory_table.setMinimumHeight(360)
        review.addWidget(self.inventory_table)
        self.inventory_table.hide()
        review.addWidget(paragraph("Repeat scans keep their history and update this map's current totals."))
        self.currency_saved = message("Start and end snapshots show each currency's net change per map.")
        review.addWidget(self.currency_saved)
        content.addStretch()

    def _build_inventory_reference_tools(self, content):
        """Build local captured-slot icon labeling and reference maintenance controls."""
        examples = self._group("LOCAL INVENTORY ICON REFERENCES", content)
        self.inventory_reference_group = examples.parentWidget()
        examples.addWidget(paragraph("For artwork missing from the bundled catalog, choose a captured slot and label it. "
                                  "Examples stay local for later scans."))
        examples.addWidget(self.inventory_preview)
        controls = QHBoxLayout()
        self.icon_slot = QSpinBox()
        self.icon_slot.setRange(1, 60)
        self.icon_name = combo([])
        controls.addWidget(QLabel("Slot 1–60"))
        controls.addWidget(self.icon_slot)
        controls.addWidget(self.icon_name, 1)
        controls.addWidget(button("Save icon example", lambda: self.run(self.save_icon_example)))
        examples.addLayout(controls)
        add = QHBoxLayout()
        self.currency_new_name = line("New currency name")
        add.addWidget(self.currency_new_name, 1)
        add.addWidget(button("Add currency name", lambda: self.run(self.add_currency_name)))
        examples.addLayout(add)
        self.icon_list = QListWidget()
        self.icon_list.setMaximumHeight(125)
        examples.addWidget(self.icon_list)
        examples.addWidget(button("Remove selected icon example", lambda: self.run(self.remove_icon_example)))

    def _build_ritual(self):
        """Attach Ritual reward and total correction controls to Review, keeping raw OCR in debug."""
        self.ritual_status = message("")
        self.ritual_status.setParent(self)
        self.ritual_status.hide()
        self.ritual_review_group = QWidget()
        review = QVBoxLayout(self.ritual_review_group)
        review.setContentsMargins(0, 0, 0, 0)
        self.review_items_content.addWidget(self.ritual_review_group)
        self.ritual_review_group.hide()
        totals = QHBoxLayout()
        self.ritual_tribute = line("Available Tribute")
        self.ritual_rerolls = line("Rerolls remaining")
        self.ritual_tribute.setReadOnly(True)
        self.ritual_rerolls.setReadOnly(True)
        totals.addWidget(QLabel("Available Tribute"))
        totals.addWidget(self.ritual_tribute)
        totals.addWidget(QLabel("Rerolls remaining"))
        totals.addWidget(self.ritual_rerolls)
        review.addLayout(totals)
        self.ritual_table = QTableWidget(0, 7)
        self.ritual_table.setHorizontalHeaderLabels(["Type", "Omen / item name", "Quantity", "Tribute", "OCR source", "Deferred", "Review"])
        self.ritual_table.setColumnWidth(0, 90)
        self.ritual_table.setColumnWidth(2, 80)
        self.ritual_table.setColumnWidth(3, 90)
        self.ritual_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ritual_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.ritual_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.ritual_table.setIconSize(QSize(48, 48))
        self.ritual_table.verticalHeader().setDefaultSectionSize(38)
        self.ritual_table.itemChanged.connect(self._ritual_row_changed)
        self.ritual_table.setMinimumHeight(300)
        self.ritual_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        review.addWidget(self.ritual_table)
        self.ritual_table.hide()
        actions = QHBoxLayout()
        self.ritual_add_row_button = button("Add reward row", self.add_ritual_row)
        self.ritual_remove_row_button = button("Remove selected row", self.remove_ritual_row)
        self.ritual_add_row_button.setEnabled(False)
        self.ritual_remove_row_button.setEnabled(False)
        actions.addWidget(self.ritual_add_row_button)
        actions.addWidget(self.ritual_remove_row_button)
        actions.addStretch()
        review.addLayout(actions)
        review.addWidget(paragraph("Type is Omen or Item. Correct quantity and Tribute if visible; "
                                "leave Tribute blank if unreadable. Unnamed rows are rejected when you Approve. "
                                "Approved name corrections teach future icon scans."))
        self.ritual_saved = message("")
        review.addWidget(self.ritual_saved)

    def _build_chain_controls(self, content):
        """Build expedition selection, saved-step corrections and the separate uncommitted rune
        draft.
        """
        chain = self._group("PROPAGATION CHAIN", content)
        self.expedition = combo([1, 2])
        self.expedition.currentIndexChanged.connect(lambda: self.run(self.set_expedition))
        expedition_row = QHBoxLayout()
        expedition_row.addWidget(QLabel("Expedition #"))
        expedition_row.addWidget(self.expedition)
        expedition_row.addStretch()
        chain.addLayout(expedition_row)
        self.chain_note = message("Save each part to this chain. Complete chain starts the next expedition.")
        chain.addWidget(self.chain_note)
        self.chain_list = QListWidget()
        self.chain_list.setAccessibleName("Current expedition chain")
        self.chain_list.setMaximumHeight(170)
        chain.addWidget(self.chain_list)
        self.chain_list.hide()
        self.expedition_chain_table = QTableWidget(0, 3)
        self.expedition_chain_table.setAccessibleName("Saved chain rune corrections")
        self.expedition_chain_table.setHorizontalHeaderLabels(["Chain part", "First rune", "Second rune"])
        self.expedition_chain_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.expedition_chain_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.expedition_chain_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.expedition_chain_table.verticalHeader().setDefaultSectionSize(45)
        self.expedition_chain_table.setMaximumHeight(340)
        chain.addWidget(self.expedition_chain_table)
        self.expedition_save_chain_button = button("Save corrections", lambda: self.run(self.save_chain_corrections))
        chain.addWidget(self.expedition_save_chain_button)
        self.expedition_draft_widget = QWidget()
        self.rune_grid = QGridLayout(self.expedition_draft_widget)
        self.rune_grid.setContentsMargins(0, 0, 0, 0)
        self.rune_inputs = []
        chain.addWidget(self.expedition_draft_widget)
        self.add_runes(18)
        buttons = QHBoxLayout()
        self.expedition_commit_chain_button = button("Commit to chain", lambda: self.run(self.commit_chain), "primary")
        buttons.addWidget(self.expedition_commit_chain_button)
        self.expedition_complete_chain_button = button("Complete chain", lambda: self.run(self.complete_chain), "primary")
        buttons.addWidget(self.expedition_complete_chain_button)
        self.expedition_more_runes_button = button("+ More runes", lambda: self.add_runes(6))
        buttons.addWidget(self.expedition_more_runes_button)
        buttons.addStretch()
        chain.addLayout(buttons)

    def _build_kill_controls(self, content, *, compact=False):
        """Build map kill-count inputs, with an optional compact header layout."""
        kills = self._group("MAP KILLS", content)
        self.kill_counts_group = kills.parentWidget()
        self.kill_counts_group.setToolTip("+ New map saves these kill counts to the current map before starting the next one.")
        if compact:
            self.kill_counts_group.setMinimumWidth(240)
            self.kill_counts_group.setMaximumWidth(340)
            self.kill_counts_group.setStyleSheet(
                "QGroupBox { padding:9px 9px 6px; margin-top:8px; }"
                "QGroupBox::title { left:10px; font-size:10px; }"
                "QGroupBox QLabel { font-size:10px; }"
                "QGroupBox QLineEdit { padding:3px 5px; min-height:16px; font-size:11px; }"
                "QGroupBox QPushButton { padding:4px 9px; font-size:10px; }")
            kills.setContentsMargins(9, 10, 9, 6)
            kills.setSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8 if compact else 16)
        grid.setVerticalSpacing(4 if compact else 10)
        self.normal = line("Normal kills")
        self.magic = line("Magic kills")
        self.rare = line("Rare kills")
        self.unique = line("Unique kills")
        for i, (label, widget) in enumerate((("Normal kills", self.normal), ("Magic kills", self.magic),
                                            ("Rare kills", self.rare), ("Unique kills", self.unique))):
            column = QVBoxLayout()
            if compact:
                column.setSpacing(1)
                widget.setMinimumWidth(0)
                widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            column.addWidget(QLabel(label))
            column.addWidget(widget)
            grid.addLayout(column, i // 2, i % 2)
        kills.addLayout(grid)

    def _build_data(self):
        """Build exports, reset and developer tools; own raw Ritual evidence outside the review HUD."""
        page, content = self._page()
        self.tabs.addTab(page, "Data & export")
        exports = self._group("LOCAL EXPORT & BACKUP", content)
        self.counts = message("")
        exports.addWidget(self.counts)
        folder_row = QHBoxLayout()
        self.export_folder = line("Local export folder")
        folder_row.addWidget(self.export_folder, 1)
        folder_row.addWidget(button("Choose folder…", self.choose_folder))
        exports.addLayout(folder_row)
        out = QHBoxLayout()
        out.addWidget(button("Save XLSX to folder", lambda: self.run(lambda: self.write_export("xlsx")), "primary"))
        out.addWidget(button("Save CSV to folder", lambda: self.run(lambda: self.write_export("csv"))))
        out.addStretch()
        exports.addLayout(out)
        self.raw_database_export_button = button(
            "Save raw SQL database to folder", lambda: self.run(lambda: self.write_export("sqlite3")))
        self.raw_database_export_button.setToolTip(
            "Export the complete saved SQLite database, including all logs, references and settings. "
            "External screenshot files are separate.")
        exports.addWidget(self.raw_database_export_button, 0, Qt.AlignmentFlag.AlignLeft)
        reset = self._group("START A FRESH LOG", content)
        # Wrapped guidance avoids forcing the entire export page wider than a compact viewport.
        description = QLabel("Clears imported and new Export rows, maps, chains, kills, currency, Ritual pages and IDs. "
                             "Your settings, families, recipes and saved OCR references remain.")
        description.setWordWrap(True)
        reset.addWidget(description)
        reset_actions = QHBoxLayout()
        reset_actions.addWidget(button("Start fresh session / reset IDs…", lambda: self.run(self.reset_logger), "danger"))
        reset_actions.addStretch()
        reset.addLayout(reset_actions)
        self.developer_mode = QCheckBox("Developer Mode · show OCR details and database editors")
        with logger._connect() as db:
            self.developer_mode.setChecked(bool(logger._meta(db, "developer_mode", False)))
        self.developer_mode.toggled.connect(self.set_developer_mode)
        content.addWidget(self.developer_mode)
        self._build_inventory_reference_tools(content)
        diagnostics = self._group("Logger status", content)
        self.diagnostics_group = diagnostics.parentWidget()
        fields = QHBoxLayout()
        for label, index in (("CURRENT MAP", 0), ("OCR", 1), ("SCAN COMMIT", 3)):
            column = QVBoxLayout()
            caption = QLabel(label)
            caption.setProperty("role", "eyebrow")
            column.addWidget(caption)
            column.addWidget(self.stat_values[index])
            fields.addLayout(column, 1)
        diagnostics.addLayout(fields)
        raw = self._group("RITUAL OCR TEXT", content)
        self.ritual_raw_group = raw.parentWidget()
        # Keep this read-only evidence buffer available to save/reset paths while
        # its debug-page parent prevents Developer Mode exposing it on Review.
        self.ritual_raw = QTextEdit()
        self.ritual_raw.setReadOnly(True)
        self.ritual_raw.setAccessibleName("Latest Ritual raw OCR text")
        self.ritual_raw.setPlaceholderText("Raw text from the latest Ritual scan appears here.")
        self.ritual_raw.setMaximumHeight(150)
        raw.addWidget(self.ritual_raw)
        recent = self._group("RECENT REMNANTS", content)
        self.recent_group = recent.parentWidget()
        self.recent = QListWidget()
        self.recent.setMaximumHeight(160)
        recent.addWidget(self.recent)
        catalog = self._group("LOCAL DATABASES", content)
        self.catalog_group = catalog.parentWidget()
        browse = QHBoxLayout()
        self.catalog_kind = combo([("families", "Family DB"), ("recipes", "Recipe DB"),
                                   ("seed_states", "Visible seeds"), ("aliases", "Aliases"),
                                   ("affixes", "Affix DB")])
        self.catalog_query = line("Search local data")
        self.catalog_query.returnPressed.connect(lambda: self.run(self.search_catalog))
        browse.addWidget(self.catalog_kind)
        browse.addWidget(self.catalog_query, 1)
        browse.addWidget(button("Search", lambda: self.run(self.search_catalog)))
        catalog.addLayout(browse)
        self.catalog_table = QTableWidget()
        self.catalog_table.setMinimumHeight(260)
        self.catalog_table.cellDoubleClicked.connect(lambda row, col: self.run(lambda: self.load_catalog_row(row)))
        catalog.addWidget(self.catalog_table)
        editors = self._group("EDIT FAMILIES, RECIPES & VISIBLE SEEDS", content)
        self.editors_group = editors.parentWidget()
        family = QGroupBox("Family group")
        family_form = QFormLayout(family)
        self.family_pick = combo([("", "New family")])
        self.family_pick.currentIndexChanged.connect(self.load_family)
        self.family_id = line()
        self.family_top = line()
        self.family_active = QCheckBox("Active for new logging")
        self.family_recipes = QTextEdit()
        self.family_recipes.setPlaceholderText("Recipes in family order, one per line")
        for title, widget in (("Existing", self.family_pick), ("Family ID", self.family_id),
                              ("Top socket", self.family_top), ("Recipes", self.family_recipes)):
            family_form.addRow(title, widget)
        family_form.addRow(self.family_active)
        family_form.addRow(button("Save family", lambda: self.run(self.save_family)))
        editors.addWidget(family)
        recipe = QGroupBox("Recipe DB")
        recipe_form = QFormLayout(recipe)
        self.edit_recipe_name = line()
        self.edit_recipe_sockets = line()
        self.edit_recipe_combo = line("Rune + Rune + …")
        self.edit_recipe_category = line()
        self.edit_recipe_level = line()
        self.edit_recipe_source = line()
        recipe_form.addRow("Name", self.edit_recipe_name)
        recipe_form.addRow(button("Load recipe", lambda: self.run(self.load_recipe)))
        for title, widget in (("Sockets", self.edit_recipe_sockets), ("Exact rune combo", self.edit_recipe_combo),
                              ("Category", self.edit_recipe_category), ("Level band", self.edit_recipe_level),
                              ("Source", self.edit_recipe_source)):
            recipe_form.addRow(title, widget)
        recipe_form.addRow(button("Save recipe", lambda: self.run(self.save_recipe)))
        editors.addWidget(recipe)
        seed = QGroupBox("Visible seed mapping")
        seed_form = QFormLayout(seed)
        self.edit_seed_family = combo([])
        self.edit_seed_sockets = line()
        self.edit_seed_slot = line("P3")
        self.edit_seed_rune = line()
        self.edit_seed_rewards = QTextEdit()
        for title, widget in (("Family", self.edit_seed_family), ("Sockets", self.edit_seed_sockets),
                              ("Visible slot", self.edit_seed_slot), ("Seed rune", self.edit_seed_rune),
                              ("Stage rewards", self.edit_seed_rewards)):
            seed_form.addRow(title, widget)
        seed_form.addRow(button("Load stage", lambda: self.run(self.load_seed)))
        seed_form.addRow(button("Save visible seed", lambda: self.run(self.save_seed_mapping)))
        editors.addWidget(seed)
        scans = self._group("SAVED SCREENSHOTS", content)
        self.scans_group = scans.parentWidget()
        self.scans_list = QListWidget()
        self.scans_list.setMaximumHeight(170)
        scans.addWidget(self.scans_list)
        self.scans_list.itemDoubleClicked.connect(lambda item: self.run(lambda: self.load_saved_scan(item)))
        content.addStretch()

    def set_developer_mode(self, enabled):
        """Persist developer-mode visibility and immediately apply it to the controls."""
        with logger._connect() as db:
            logger._set_meta(db, "developer_mode", bool(enabled))
        self._apply_developer_mode()

    def _apply_developer_mode(self):
        """Show held waystone corrections and keep other diagnostics in developer mode."""
        enabled = self.developer_mode.isChecked()
        self.map_ocr_overrides.setVisible(enabled or self.pending_review_kind == "waystone")
        for label in self.findChildren(QLabel):
            if label.property("guidance"):
                label.setVisible(enabled)
        self.ritual_table.setColumnHidden(4, not enabled)
        self.show_tablet_raw()
        self.counts.setVisible(enabled)
        for group in (self.diagnostics_group, self.catalog_group, self.editors_group, self.scans_group,
                      self.inventory_reference_group, self.ritual_raw_group):
            group.setVisible(enabled)

    def add_runes(self, count):
        """Extend the chain draft with numbered rune fields up to the 96-rune limit."""
        start = len(self.rune_inputs)
        for i in range(start, min(start + count, 96)):
            field = line(f"Rune {i + 1}")
            self.rune_grid.addWidget(QLabel(f"Rune {i + 1}"), i // 3 * 2, i % 3)
            self.rune_grid.addWidget(field, i // 3 * 2 + 1, i % 3)
            self.rune_inputs.append(field)
            field.textChanged.connect(self._chain_fields_changed)

    def _waystone_form(self):
        """Snapshot waystone form text and hidden modifiers for detecting or preserving unsaved
        edits.
        """
        fields = (self.waystone, self.map_mods, self.item_rarity, self.monster_rarity,
                  self.pack_size, self.effectiveness, self.waystone_name, *self.waystone_mod_fields)
        return (value(self.tier), value(self.aldur), tuple(field.text() for field in fields),
                tuple(self._extra_waystone_mods))

    def _restore_waystone_form(self, draft):
        """Restore a retained waystone draft and recompute its area-level display."""
        tier, aldur, texts, extra_mods = draft
        select(self.tier, tier)
        select(self.aldur, aldur)
        fields = (self.waystone, self.map_mods, self.item_rarity, self.monster_rarity,
                  self.pack_size, self.effectiveness, self.waystone_name, *self.waystone_mod_fields)
        for field, text in zip(fields, texts):
            field.setText(text)
        self._extra_waystone_mods = list(extra_mods)
        self.waystone_name.setVisible(bool(self.waystone_name.text()))
        self.update_area()

    def _tablet_form(self):
        """Snapshot tablet capacity, affix values, units and raw modifiers for draft tracking."""
        slots = []
        for number in range(4):
            start = number * 4
            entries = tuple((value(affix), amount.text(),
                amount.property("unit") if amount.property("affix") == value(affix) else affix_unit(value(affix)))
                for affix, amount in zip(self.tablet_affixes[start:start + 4], self.tablet_values[start:start + 4]))
            slots.append((entries, tuple(self.tablet_raw_mods[number])))
        return value(self.tablets_used), tuple(slots)

    def _clear_tablet_form_dirty(self, number=None, clear_capacity=False):
        """Retire dirty tracking for all tablets or the reviewed slot so refresh can restore
        saved values.
        """
        if number is None or self._tablet_form_baseline is None:
            self._tablet_form_baseline = None
        else:
            slots = list(self._tablet_form_baseline[1])
            slots[number - 1] = None
            self._tablet_form_baseline = (None if clear_capacity else self._tablet_form_baseline[0], tuple(slots))

    def _require_remnant_review_finished(self):
        """Block other activity reviews while the logger or UI holds a pending remnant scan."""
        if self.pending_review_kind in ("remnant", "seed") or logger.get_state()["ocr_pending"]:
            raise ValueError("Save or reject the pending remnant scan before scanning another activity.")

    def refresh(self):
        """Refresh saved state while retaining edits within their original map context."""
        folder_text = self.export_folder.text()
        folder_draft = folder_text if (self._export_folder_baseline is not None and
                                       folder_text != self._export_folder_baseline) else None
        waystone_values = self._waystone_form()
        count_values = tuple(field.text() for field in (self.normal, self.magic, self.rare, self.unique))
        tablet_values = self._tablet_form()
        active_master_value = value(self.master)
        perk_values = (value(self.perk_master), tuple(value(field) for field in self.perk_boxes))
        state = logger.get_state()
        map_context = (logger.session_generation(), state["current_map_id"])
        same_map = map_context == self._form_map_context
        if not same_map:
            self._perk_drafts.clear()
            self._perk_form_baseline = None
            self._saved_chain_correction_drafts.clear()
            self._saved_chain_view_key = None
        waystone_draft = waystone_values if (
            self.pending_review_kind == "waystone" or same_map and
            self._waystone_form_baseline is not None and
            waystone_values != self._waystone_form_baseline) else None
        count_drafts = {index: text for index, text in enumerate(count_values)
                        if same_map and self._counts_form_baseline is not None and
                        text != self._counts_form_baseline[index]}
        tablet_drafts = {number: slot for number, slot in enumerate(tablet_values[1], 1)
                         if same_map and self._tablet_form_baseline is not None and
                         self._tablet_form_baseline[1][number - 1] is not None and
                         slot != self._tablet_form_baseline[1][number - 1]}
        tablet_capacity_draft = tablet_values[0] if (
            same_map and self._tablet_form_baseline is not None and
            self._tablet_form_baseline[0] is not None and
            tablet_values[0] != self._tablet_form_baseline[0]) else None
        active_master_draft = active_master_value if (
            same_map and self._active_master_baseline is not None and
            active_master_value != self._active_master_baseline) else None
        perk_draft = perk_values if (same_map and self._perk_form_baseline is not None and
                                    perk_values != self._perk_form_baseline) else None
        tablet_draft = None
        if self.pending_review_kind == "tablet" and self._pending_tablet_slot is not None:
            number = self._pending_tablet_slot
            start = (number - 1) * 4
            entries = [{"affix": value(a), "value": value(v),
                        "unit": v.property("unit") if v.property("affix") == value(a) else affix_unit(value(a))}
                       for a, v in zip(self.tablet_affixes[start:start + 4], self.tablet_values[start:start + 4])]
            tablet_draft = (number, entries, list(self.tablet_raw_mods[number - 1]), int(value(self.tablets_used)))
        self.state = state
        config = state["settings"]
        target = state.get("atlas_settings_target_map_id", "")
        self.atlas_settings_page.set_settings(config.get("atlas_settings"),
            status=f"Saved settings apply to {target}." if target else
                   "Saved atlas settings carry forward to your next map.")
        all_scans = bool(config.get("ocr_auto_commit"))
        with QSignalBlocker(self.auto_all_checkbox):
            self.auto_all_checkbox.setChecked(all_scans)
        with QSignalBlocker(self.auto_commit_checkbox):
            self.auto_commit_checkbox.setChecked(bool(config.get("auto_commit")))
        self.auto_commit_checkbox.setEnabled(not all_scans)
        auto_tablets = bool(config.get("tablet_auto_commit"))
        with QSignalBlocker(self.auto_tablet_checkbox):
            self.auto_tablet_checkbox.setChecked(auto_tablets)
        self.auto_tablet_checkbox.setEnabled(not all_scans)
        next_tablet = logger.tablet_next_slot()
        self.auto_tablet_next.setText(
            f"Next tablet: {next_tablet} of 4" if next_tablet <= 4 else
            "Four tablets saved. Clear tablet config to scan a new set.")
        self.tablet_scan_number.setEnabled(not auto_tablets)
        select(self.tablet_scan_number, min(next_tablet, 4))
        commit_count = state["scan_commit_count"]
        if self._saved_badge is not None and self._saved_badge != commit_count:
            self._saved_badge = None
        for field, text in zip(self.stat_values, (
                state["current_map_id"] or "M0001", "INACTIVE",
                str(state["area_level"]),
                f"✓ #{commit_count} SAVED" if self._saved_badge is not None else f"#{commit_count}")):
            field.setText(text)
        self.stat_values[3].setStyleSheet(
            "font-size:17px;font-weight:700;color:#F5C364;" if self._saved_badge is not None else
            "font-size:17px;font-weight:700;color:#FFFFFF;")
        self.waystone.setText(str(config["waystone"]))
        select(self.tier, config["tier"])
        self.map_mods.setText(str(config["map_mods"]))
        self._sync_map_tags(config)
        aldur = str(config["aldur"])
        if self.aldur.findData(aldur) < 0:
            self.aldur.addItem(aldur, aldur)
        select(self.aldur, aldur)
        for key, field in (("item_rarity", self.item_rarity),
                           ("monster_rarity", self.monster_rarity),
                           ("pack_size", self.pack_size),
                           ("effectiveness", self.effectiveness)):
            field.setText("" if config.get(key) is None else str(config[key]))
        self.waystone_name.setText(config.get("waystone_name", ""))
        waystone_mods = config.get("waystone_mods", [])
        for field, mod in zip(self.waystone_mod_fields, list(waystone_mods[:10]) + [""] * 10):
            field.setText(mod)
        self._extra_waystone_mods = list(waystone_mods[10:])
        self.waystone_name.setVisible(bool(self.waystone_name.text()))
        self.tablet_raw_mods = [list(mods) for mods in config.get("tablet_raw_mods", [[] for _ in range(4)])]
        self.show_tablet_raw()
        select(self.master, config["atlas_master"])
        self._active_master_baseline = value(self.master)
        if active_master_draft is not None:
            select(self.master, active_master_draft)
        self.update_area()
        select(self.perk_master, perk_values[0] if same_map else
               config["atlas_master"] if config["atlas_master"] != "None" else "Jado")
        self.render_perks()
        if perk_draft is not None:
            for field, selected in zip(self.perk_boxes, perk_draft[1]):
                select(field, selected)
        select(self.tablets_used, config["tablets_used"])
        for i, (affix, amount) in enumerate(zip(self.tablet_affixes, self.tablet_values)):
            current = config["tablet_affixes"][i]
            with QSignalBlocker(affix):
                affix.clear()
                affix.addItem("— None —", "")
                for name in state["affixes"]:
                    affix.addItem(name, name)
            select(affix, current["affix"])
            amount.setText("" if current["value"] is None else str(current["value"]))
            amount.setProperty("affix", current["affix"])
            amount.setProperty("unit", current.get("unit") or affix_unit(current["affix"]))
        self._tablet_form_baseline = self._tablet_form()
        for number, (slot, raw) in tablet_drafts.items():
            self._fill_tablet_slot(number,
                [{"affix": affix, "value": amount, "unit": unit} for affix, amount, unit in slot], raw)
        if tablet_capacity_draft is not None:
            select(self.tablets_used, tablet_capacity_draft)
        if tablet_draft is not None:
            number, entries, raw, capacity = tablet_draft
            self._fill_tablet_slot(number, entries, raw)
            select(self.tablets_used, max(capacity, number))
        self.tablet_active()
        self.show_tablet_raw()
        self._sync_header_ids(state)
        self._load_chain_context()
        kill_values = (*state["kills"], state.get("unique_kills"))
        for widget, count in zip((self.normal, self.magic, self.rare, self.unique), kill_values):
            widget.setText("" if count is None else str(count))
        self._counts_form_baseline = tuple(field.text() for field in (self.normal, self.magic, self.rare, self.unique))
        for index, text in count_drafts.items():
            (self.normal, self.magic, self.rare, self.unique)[index].setText(text)
        self._export_folder_baseline = state["export_folder"]
        self.export_folder.setText(folder_draft if folder_draft is not None else self._export_folder_baseline)
        counts = state["counts"]
        set_message(self.counts, f"{counts['historical_rows']} imported rows · {counts['new_rows']} new rows · "
                    f"{state['ritual_pages']} Ritual pages · {counts['saved_scans']} saved scans · "
                    f"{counts['rune_references']} rune references")
        self.recent.clear()
        for item in state["recent"]:
            self.recent.addItem(f"{item['remnant_id']}  ·  {item['map_id']}  ·  "
                                f"{item['first_recipe']}  ·  {item['sockets']} sockets")
        self.recent_group.setVisible(bool(state["recent"]))
        current_family = self.family_pick.currentData()
        with QSignalBlocker(self.family_pick):
            self.family_pick.clear()
            self.family_pick.addItem("New family", "")
            for family in state["families"]:
                self.family_pick.addItem(f"Family {family['id']}", family["id"])
        select(self.family_pick, current_family or "")
        with QSignalBlocker(self.edit_seed_family):
            self.edit_seed_family.clear()
            for family in state["families"]:
                self.edit_seed_family.addItem(f"Family {family['id']}", family["id"])
        with QSignalBlocker(self.recipe_family):
            selected = self.recipe_family.currentData()
            self.recipe_family.clear()
            self.recipe_family.addItem("Auto resolve", "")
            for family in state["families"]:
                if family["valid"]:
                    self.recipe_family.addItem(f"Family {family['id']}", family["id"])
        select(self.recipe_family, selected or "")
        self.load_family()
        self.scans_list.clear()
        for scan in store.list_scans()[:20]:
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(f"#{scan['id']} · {scan['file_name']} · "
                                   f"{scan['sockets']} sockets · {scan['family'] or 'unresolved'}")
            item.setData(Qt.ItemDataRole.UserRole, scan["id"])
            self.scans_list.addItem(item)
        self.refresh_hotkey()
        self.refresh_aux_regions()
        if not self._currency_catalog_loaded:
            self.refresh_currency_references()
            self._currency_catalog_loaded = True
        self.refresh_currency_summary()
        self.refresh_ritual_summary()
        self._waystone_form_baseline = self._waystone_form()
        self._form_map_context = map_context
        if waystone_draft is not None:
            self._restore_waystone_form(waystone_draft)

    def save_atlas_settings(self, data):
        """Save atlas settings, force the editor to show persisted values and display the
        commit badge.
        """
        state = logger.save_atlas_settings(data)
        self.atlas_settings_page.set_settings(state["settings"]["atlas_settings"], force=True)
        self.refresh()
        self._set_commit_badge(self.state["scan_commit_count"])
        self.note("Atlas / character settings saved.", True)

    def refresh_hotkey(self):
        """Refresh registered shortcut labels and the OCR availability indicator."""
        status = service.HOTKEY.status()
        self.hotkey_label.setText(status["combo"] or "Off")
        for kind, label in self.direct_hotkey_labels.items():
            label.setText(status["combos"].get(kind) or "Off")
        self._set_ocr_indicator()

    def _set_ocr_indicator(self):
        """Show OCR active only when a scan shortcut is registered and the focus predicate
        permits capture.
        """
        shortcuts = service.HOTKEY.status()
        scan_key = bool(shortcuts["combo"] or any(shortcuts["combos"].get(kind)
                        for kind in ("remnant", "waystone", "tablet", "currency", "ritual", "propagation")))
        active = bool(shortcuts["registered"] and scan_key)
        if active and service.HOTKEY.focused is not None:
            active = service.HOTKEY.focused()
        if self.stat_values[1].text() == ("ACTIVE" if active else "INACTIVE"):
            return
        self.stat_values[1].setText("ACTIVE" if active else "INACTIVE")
        self.stat_values[1].setStyleSheet(
            "font-size:17px;font-weight:700;color:#F5C364;" if active else
            "font-size:17px;font-weight:700;color:#AAA49B;")

    def _set_commit_badge(self, number):
        """Display a saved-commit badge and start its expiry timer."""
        if number is None:
            return
        self._saved_badge = int(number)
        self.state["scan_commit_count"] = int(number)
        self.stat_values[3].setText(f"✓ #{number} SAVED")
        self.stat_values[3].setStyleSheet("font-size:17px;font-weight:700;color:#F5C364;")
        self._commit_badge_timer.start()

    def _clear_commit_badge(self, expected=None):
        """Expire the saved badge only if its optional expected commit still matches."""
        if self._saved_badge is not None and (expected is None or self._saved_badge == expected):
            self._saved_badge = None
            self.stat_values[3].setText(f"#{self.state['scan_commit_count']}")
            self.stat_values[3].setStyleSheet("font-size:17px;font-weight:700;color:#FFFFFF;")

    def _map_tag_controls(self):
        """Map each immediately saved map tag to its header and settings controls."""
        return {"biome": (self.header_biome, self.biome),
                "city_type": (self.header_city_type, self.city_type),
                "ocean": (self.header_ocean, self.ocean),
                "irradiated": (self.header_irradiated, self.irradiated),
                "deli": (self.header_deli, self.deli),
                "wisp": (self.header_wisp, self.wisp)}

    def _sync_header_ids(self, state):
        """Display live IDs, reflow growing counters and sync expedition choices without saving them."""
        mid = state["current_map_id"]
        self.header_map_id.setText(header_counter(mid))
        self.header_map_id.setToolTip(mid or "No active map")
        # Completing a chain selects an empty expedition without resetting this map's remnant number.
        remnant = state.get("map_remnant_id") or state["current_remnant_id"]
        self.header_remnant_id.setText(header_counter(remnant))
        self.header_remnant_id.setToolTip(remnant or "No remnant recorded")
        self._arrange_header()
        current = state["settings"]["expedition"]
        for control in (self.header_expedition, self.expedition):
            with QSignalBlocker(control):
                for number in range(1, max(2, current + 1) + 1):
                    if control.findData(number) < 0:
                        control.addItem(str(number), number)
                select(control, current)
        with QSignalBlocker(self.header_expedition):
            for index in range(self.header_expedition.count()):
                number = self.header_expedition.itemData(index)
                self.header_expedition.setItemText(index,
                    f"{number} · {mid}-E{number:02d}" if mid else str(number))
        select(self.header_expedition, state["settings"]["expedition"])

    def _sync_map_tags(self, config):
        """Mirror saved map tags into both control sets with change signals blocked."""
        for key, widgets in self._map_tag_controls().items():
            for widget in widgets:
                if isinstance(widget, QCheckBox):
                    with QSignalBlocker(widget):
                        widget.setChecked(bool(config.get(key, False)))
                else:
                    select(widget, config.get(key, "None"))
        self.update_area()

    def _save_map_tag(self, key, widget):
        """Save one changed map tag immediately, synchronizing both copies or reverting on
        failure.
        """
        selected = widget.isChecked() if isinstance(widget, QCheckBox) else value(widget)
        if selected == self.state["settings"].get(key):
            return
        try:
            self.state = logger.save_settings({key: selected})
        except Exception:
            self._sync_map_tags(self.state["settings"])
            raise
        self._sync_map_tags(self.state["settings"])
        self._set_commit_badge(logger.record_commit("Map settings"))

    def update_area(self):
        """Preview area level from tier, Irradiated and Ocean, disabling uncertain waystone
        approval.
        """
        if not hasattr(self, "area"):
            return
        if value(self.tier) not in (15, 16):
            set_message(self.area, "Confirm the waystone tier to calculate its area level.")
            self.stat_values[2].setText("—")
            if self.pending_review_kind == "waystone":
                self.approve_scan_button.setEnabled(False)
            return
        level = (79 if value(self.tier) == 15 else 80) + int(self.irradiated.isChecked()) + int(self.ocean.isChecked())
        set_message(self.area, f"Calculated Area Level: {level}")
        self.stat_values[2].setText(str(level))
        if self.pending_review_kind == "waystone" and not self._failed_review:
            self.approve_scan_button.setEnabled(True)

    def tablet_active(self):
        """Show only the tablet groups within the selected capacity."""
        for i, group in enumerate(self.tablet_groups):
            group.setVisible(i < int(value(self.tablets_used)))

    def _tablet_review_edit_changed(self, number):
        """Allow approval after the user enters affixes for the scan's bound tablet slot."""
        if (self.pending_review_kind != "tablet" or self._pending_tablet_slot != number or
                self._failed_review):
            return
        start = (number - 1) * 4
        self.approve_scan_button.setEnabled(any(
            value(field) for field in self.tablet_affixes[start:start + 4]))

    def render_perks(self):
        """Preserve each master's unsaved selections while browsing other masters."""
        if self._perk_form_baseline is not None:
            previous, baseline = self._perk_form_baseline
            edited = tuple(value(field) for field in self.perk_boxes)
            if edited != baseline:
                self._perk_drafts[previous] = edited
            else:
                self._perk_drafts.pop(previous, None)
        master = value(self.perk_master)
        if not master:
            return
        choices = ["None"] + [item["name"] for item in self.state["masters"].get(master, [])]
        saved = self.state["settings"]["master_selections"].get(master, ["None"] * 4)
        for i, widget in enumerate(self.perk_boxes):
            with QSignalBlocker(widget):
                widget.clear()
                for name in choices:
                    widget.addItem(name, name)
            select(widget, saved[i])
        self._perk_form_baseline = (master, tuple(value(field) for field in self.perk_boxes))
        for field, selected in zip(self.perk_boxes, self._perk_drafts.get(master, ())):
            select(field, selected)

    def save_map_settings(self):
        """Save selected settings and report when the logged map snapshot stays frozen."""
        reviewing = self.pending_review_kind == "waystone"
        logger.save_settings({"waystone": value(self.waystone), "tier": value(self.tier),
                              "map_mods": value(self.map_mods), "irradiated": self.irradiated.isChecked(),
                              "ocean": self.ocean.isChecked(), "aldur": value(self.aldur),
                              "deli": self.deli.isChecked(), "wisp": self.wisp.isChecked(),
                              "biome": value(self.biome), "city_type": value(self.city_type),
                              "item_rarity": value(self.item_rarity),
                              "monster_rarity": value(self.monster_rarity),
                              "pack_size": value(self.pack_size),
                              "effectiveness": value(self.effectiveness),
                              "waystone_name": value(self.waystone_name),
                              "waystone_mods": [value(field) for field in self.waystone_mod_fields]
                                                + self._extra_waystone_mods})
        commit_number = logger.record_commit("Map settings")
        self._waystone_form_baseline = None
        self._review_saved("waystone", commit_number)
        self.refresh()
        self._set_commit_badge(commit_number)
        notice = self._map_setup_save_notice()
        if notice:
            set_message(self.map_scan_status, notice)
            if reviewing:
                set_message(self.review_summary, notice)
        self.note(notice or "Map settings saved.")
        return commit_number

    def _map_setup_save_notice(self):
        """Explain a settings save that cannot change an existing map snapshot."""
        map_id = self.state.get("current_map_id")
        with logger._connect() as db:
            row = db.execute("SELECT snapshot_json FROM maps WHERE map_id=?", (map_id,)).fetchone()
        if row is None:
            return ""
        recorded = json.loads(row[0])
        selected = logger._snapshot(self.state["settings"])
        keys = ("tier", "waystone", "base_map_mods", "aldur", "irradiated", "ocean", "deli", "wisp",
                "biome", "city_type", "item_rarity", "monster_rarity", "pack_size", "effectiveness",
                "waystone_name", "waystone_mods")
        if any(recorded.get(key) != selected.get(key) for key in keys):
            return (f"Settings saved, but {map_id}'s recorded setup is unchanged because it already has activity. "
                    "Scan the next map's waystone before logging its activity.")
        return ""

    def save_active_master(self):
        """Persist the selected active master and retire its unsaved form baseline."""
        logger.save_settings({"atlas_master": value(self.master)})
        commit_number = logger.record_commit("Atlas Master")
        self._active_master_baseline = None
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note("Active Atlas Master saved.", True)

    def save_perks(self):
        """Commit only the selected master's perks and retire that master's draft."""
        master = value(self.perk_master)
        selections = dict(self.state["settings"]["master_selections"])
        selections[master] = [value(item) for item in self.perk_boxes]
        logger.save_settings({"master_selections": selections})
        commit_number = logger.record_commit("Master perks", master)
        self._perk_drafts.pop(master, None)
        self._perk_form_baseline = None
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note(f"{master} perks saved.")

    def save_tablets(self):
        """Save tablet configuration, merging only a reviewed slot and advancing scan order
        when appropriate.
        """
        if self.pending_review_kind == "tablet" and self._pending_tablet_slot is None:
            raise ValueError("No tablet affixes were read. Scan the tablet again before saving this reading.")
        entries = [{"affix": value(a), "value": value(v),
                    "unit": v.property("unit") if v.property("affix") == value(a) else affix_unit(value(a))}
                   for a, v in zip(self.tablet_affixes, self.tablet_values)]
        next_slot = logger.tablet_next_slot()
        reviewed = self._pending_tablet_slot if self.pending_review_kind == "tablet" else None
        if reviewed is not None:
            if next_slot > 4:
                raise ValueError("Four tablets are already saved. Clear tablet config before scanning a new set.")
            if not any(item["affix"] for item in entries[(reviewed - 1) * 4:reviewed * 4]):
                raise ValueError("No tablet affixes to save. Check the captured image or enter an affix on Map / Tablets.")
            stored = logger.get_state()["settings"]
            merged = list(stored["tablet_affixes"])
            start = (reviewed - 1) * 4
            merged[start:start + 4] = entries[start:start + 4]
            entries = merged
            raw_mods = list(stored.get("tablet_raw_mods") or [[], [], [], []])
            raw_mods.extend([[] for _ in range(4-len(raw_mods))])
            raw_mods[reviewed - 1] = self.tablet_raw_mods[reviewed - 1]
        else:
            raw_mods = self.tablet_raw_mods
        used = max(int(value(self.tablets_used)), reviewed) if reviewed is not None else value(self.tablets_used)
        logger.save_settings({"tablets_used": used,
                              "tablet_affixes": entries,
                              "tablet_raw_mods": raw_mods},
                             confirmed_affixes=sorted({item["affix"] for item in entries
                                                       if item["affix"] and item["affix"] not in self.state["affixes"]}))
        commit_number = logger.record_commit("Tablet config")
        self._clear_tablet_form_dirty(reviewed)
        if reviewed == next_slot:
            logger.advance_tablet_slot(next_slot)
        self._review_saved("tablet", commit_number)
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note(f"Tablet {reviewed} saved." if reviewed is not None else "Tablet config saved.")
        return commit_number

    def clear_tablets(self):
        """Cancel capture, clear saved tablet configuration and retire any pending tablet
        review.
        """
        service.HOTKEY.cancel_capture()
        logger.clear_tablets()
        self._clear_tablet_form_dirty()
        self._pending_tablet_slot = None
        self.refresh()
        if self.pending_review_kind == "tablet":
            self.pending_review_kind = None
            self.approve_scan_button.setEnabled(False)
            self._review_controls(None)
            self.review_edit_button.hide()
            self.found_table.hide()
            self.found_label.hide()
            self._show_image(None)
            self.review_kind.setText("Tablet config cleared")
            set_message(self.review_summary, "Tablet slots cleared. The next tablet scan starts at Tablet 1.", "success")
        self.note("Tablet config cleared.")

    def add_affix(self):
        """Add a tablet affix to the local catalog and refresh the form choices."""
        logger.add_affix(value(self.new_affix))
        self.new_affix.clear()
        self.refresh()
        self.note("Affix added to tablet dropdowns.")

    def counts_data(self):
        """Collect kill-count text for the logger's validation and map-finalization actions."""
        return {"normal": value(self.normal), "magic": value(self.magic),
                "rare": value(self.rare), "unique": value(self.unique)}

    def finish_map(self):
        """Save current-map kills when needed, start the next map and clear map-scoped review
        drafts. Keep active scan ownership until map validation and creation succeed.
        """
        if self.state["current_map_id"] and not self.state["pending_new_map"]:
            previous = self.state["current_map_id"]
            service.dispatch("/api/finish-map", self.counts_data())
        else:
            previous = None
        state = logger.start_map()
        service.HOTKEY.cancel_capture()
        self._clear_map_review()
        self.refresh()
        self.review_kind.setText("New map")
        set_message(self.review_summary, "Waystone settings cleared. Scan or enter the new map's waystone.")
        if previous:
            self._set_commit_badge(state["scan_commit_count"])
        self.note(f"Map {state['current_map_id']} started."
                  + (" Previous map totals saved." if previous else ""), True)

    def _clear_map_review(self):
        """Discard map-specific review drafts when the map context changes."""
        self._waystone_form_baseline = self._counts_form_baseline = None
        self._tablet_form_baseline = self._active_master_baseline = self._perk_form_baseline = None
        self._perk_drafts.clear()
        self._saved_chain_correction_drafts.clear()
        self._saved_chain_view_key = None
        self.pending_review_kind = None
        self._discard_chain_review_draft()
        self._failed_review = False
        self._remnant_reading = None
        self._propagation_reading = None
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
        self._pending_currency_phase = None
        self._inventory_reading = self._ritual_reading = None
        self._inventory_capture = self._inventory_capture_context = None
        self._ritual_hash = self._ritual_capture_context = None
        self.inventory_table.setRowCount(0)
        self.ritual_table.setRowCount(0)
        self.inventory_preview.clear()
        self.ritual_raw.clear()
        self.ritual_tribute.clear()
        self.ritual_rerolls.clear()
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = self._saved_scan_capture = self.resolved = None
        for field in (self.first_recipe, self.next_recipe):
            with QSignalBlocker(field):
                field.clear()
        select(self.recipe_family, "")
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.review_edit_button.hide()
        self.found_table.hide()
        self.found_label.hide()
        self.review_group.hide()
        self.recipe_table.hide()
        self.currency_review_group.hide()
        self.ritual_review_group.hide()
        self.remnant_log_group.hide()
        self._show_image(None)

    def undo_map(self):
        """Undo an empty map and invalidate captures and reviews only when its context changes."""
        context = logger.scan_context()
        logger.undo_empty_map()
        if logger.scan_context() != context:
            service.HOTKEY.cancel_capture()
            self._clear_map_review()
            self.review_kind.setText("Nothing waiting for review")
            set_message(self.review_summary, "Map change undone. Scan the current map again.")
        self.refresh()
        self.note("Empty map undone.")

    def set_expedition(self, widget=None):
        """Save expedition selection from either control, reverting both to persisted state on
        failure.
        """
        control = self.expedition if widget is None else widget
        # Inventory and Ritual snapshots retain their capture expedition. Do
        # not let a selector change leave a visible, otherwise valid review
        # stranded behind the store's stale-context validation.
        if self.pending_review_kind in ("currency", "ritual"):
            self.state = logger.get_state()
            select(self.expedition, self.state["settings"]["expedition"])
            self._sync_header_ids(self.state)
            raise ValueError("Save or reject the current inventory or Ritual review before changing expedition.")
        try:
            logger.save_settings({"expedition": value(control)})
        except Exception:
            self.state = logger.get_state()
            select(self.expedition, self.state["settings"]["expedition"])
            self._sync_header_ids(self.state)
            raise
        self.refresh()

    def _chain_key(self, context):
        """Key a chain draft by session generation, captured map and expedition."""
        return (context["_scan_generation"], context["_capture_map_id"], context["_capture_expedition"])

    def _chain_fields_changed(self, *, defer_review=False):
        # A correction can briefly empty one rune of a scanned pair. Keep its
        # part and recipe until the whole draft is cleared or a scan replaces it.
        """Retain context-specific rune drafts and refresh review while preserving scanned part
        metadata.
        """
        if not any(value(field) for field in self.rune_inputs):
            for field in self.rune_inputs:
                field.setProperty("chainPart", None)
                field.setProperty("chainRecipe", None)
        if self._chain_context is not None:
            key = self._chain_key(self._chain_context)
            if any(value(field) for field in self.rune_inputs):
                self._chain_drafts[key] = [
                    {"rune": value(field), "part": field.property("chainPart"),
                     "recipe": field.property("chainRecipe") or ""} for field in self.rune_inputs]
            else:
                # Completed and cleared chains need no draft. Retaining their
                # empty field lists would grow memory with every expedition.
                self._chain_drafts.pop(key, None)
        if defer_review:
            # Let Qt finish closing a table editor before replacing its items.
            QTimer.singleShot(0, lambda: self._refresh_chain_review() if not self._closed else None)
        else:
            self._refresh_chain_review()

    def _load_chain_context(self):
        """Restore the active map/expedition's chain draft and retire propagation work from the
        prior context.
        """
        context = logger.scan_context()
        if self._chain_context != context:
            self._cancel_pending_chain_save()
            self._clear_manual_propagation()
            if (self._chain_context is not None and
                    self._chain_context["_scan_generation"] != context["_scan_generation"]):
                self._chain_drafts.clear()
            self._chain_context = context
            draft = self._chain_drafts.get(self._chain_key(context), [])
            if len(draft) > len(self.rune_inputs):
                self.add_runes(len(draft) - len(self.rune_inputs))
            for index, field in enumerate(self.rune_inputs):
                item = draft[index] if index < len(draft) else {}
                with QSignalBlocker(field):
                    field.setProperty("chainPart", item.get("part"))
                    field.setProperty("chainRecipe", item.get("recipe", ""))
                    field.setText(item.get("rune", ""))
            if self.pending_review_kind == "propagation":
                self._propagation_reading = None
                self.pending_review_kind = None
                self._review_controls(None)
                self.found_table.hide()
                self.found_label.hide()
        self._refresh_chain_review()

    def _chain_steps(self):
        """Group adjacent runes sharing part metadata into ordered one- or two-rune chain
        steps.
        """
        steps = []
        previous = None
        for index, field in enumerate(self.rune_inputs):
            rune = value(field)
            if not rune:
                continue
            part = field.property("chainPart") or f"manual-{index}"
            if part == previous and not steps[-1]["rune2"]:
                steps[-1]["rune2"] = rune
            else:
                steps.append({"rune1": rune, "rune2": ""})
            previous = part
        return steps

    def _set_chain_review_active(self, active):
        # A held remnant can remain pending independently while propagation
        # actions display their own review controls.
        """Display chain controls only within a propagation review."""
        self._propagation_review_active = active
        displayed = self.review_kind.property("scanKind")
        self.chain_review_group.setVisible(active and displayed == "propagation" and (
            bool(self.chain_review_table.rowCount()) or bool(self._manual_propagation_context) or
            self.pending_review_kind == "propagation" or bool(self.state.get("chain"))))

    def _discard_chain_review_draft(self):
        # Accepted scan counts and audit snapshots are already saved. Only
        # the uncommitted rune draft and its review choices are discarded.
        """Discard uncommitted rune and recipe choices while retaining already saved acceptance
        counts.
        """
        self._set_chain_review_active(False)
        self._clear_manual_propagation()
        self._chain_save_request = None
        for field in self.rune_inputs:
            with QSignalBlocker(field):
                field.clear()
                field.setProperty("chainPart", None)
                field.setProperty("chainRecipe", None)
        self._chain_fields_changed()

    def _refresh_chain_review(self):
        """Render draft parts beside saved steps and update editing, commit and completion
        eligibility.
        """
        if not hasattr(self, "chain_review_table") or not hasattr(self, "rune_inputs"):
            return
        table = self.chain_review_table
        blocker = QSignalBlocker(table)
        table.setRowCount(0)
        previous = None
        part_number = 0
        chain_parts = []
        for index, field in enumerate(self.rune_inputs):
            rune = value(field)
            if not rune:
                continue
            part = field.property("chainPart") or f"manual-{index}"
            if part != previous:
                part_number += 1
                chain_parts.append({"runes": [], "recipe": field.property("chainRecipe") or ""})
            chain_parts[-1]["runes"].append(rune)
            previous = part
            row = table.rowCount()
            table.insertRow(row)
            for column, text in enumerate((str(part_number), rune, field.property("chainRecipe") or "")):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, index)
                if column != 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, column, item)
        del blocker
        self.chain_list.clear()
        saved_chain = self.state.get("chain") or []
        for entry in saved_chain:
            self.chain_list.addItem(f"#{entry['step']}  " + " → ".join(
                filter(None, (entry['rune1'], entry['rune2']))))
        self.chain_list.setVisible(bool(saved_chain))
        self._refresh_saved_chain_editor()
        self._set_chain_review_active(self._propagation_review_active)
        table.setVisible(table.rowCount() > 0)
        can_append = bool(table.rowCount() and not self.state.get("chain_completed"))
        for field in self.rune_inputs:
            field.setEnabled(not self.state.get("chain_completed"))
        self.expedition_commit_chain_button.setEnabled(can_append)
        # Empty draft fields are not the saved chain; manual entry remains available via its shortcut.
        for control in (self.expedition_draft_widget, self.expedition_commit_chain_button,
                        self.expedition_more_runes_button):
            control.setVisible(bool(table.rowCount()))
        self._update_chain_completion_controls()
        self._manual_propagation_changed()
        expedition_id = self.state.get("current_expedition_id") or "the active expedition"
        detonated = self.state.get("detonated") or 0
        # Current chain status must remain visible when optional guidance is turned off.
        set_message(self.chain_note, f"{expedition_id} · {detonated} remnants detonated. "
                    + ("Chain completed." if self.state.get("chain_completed") else
                       f"{len(saved_chain)} saved parts. Complete chain starts the next expedition."))
        draft_summary = (f" · {part_number} reviewed parts waiting to save" if table.rowCount() else "")
        set_message(self.chain_review_status,
                    f"{expedition_id} · {detonated} remnants detonated · {len(saved_chain)} saved chain parts"
                    + draft_summary + ". "
                    "Each approved propagation scan counts one remnant. "
                    "Approve saves a recipe's marked runes to this expedition. Complete chain finishes the chain and starts the next expedition.")

    def _refresh_saved_chain_editor(self):
        """Restore saved-chain correction drafts by session, map and expedition."""
        entries = self.state.get("chain") or []
        closed = bool(self.state.get("chain_completed"))
        key = (self._chain_key(self._chain_context) if self._chain_context else None,
               tuple((entry["step"], entry["rune1"], entry["rune2"]) for entry in entries), closed)
        if key == self._saved_chain_view_key:
            return
        previous_view = self._saved_chain_view_key
        if self._saved_chain_view_key and not self._saved_chain_view_key[2]:
            baseline = {step: (rune1, rune2) for step, rune1, rune2 in self._saved_chain_view_key[1]}
            edits = {entry["step"]: entry for entry in self._saved_chain_edits()
                     if (entry["rune1"], entry["rune2"]) != baseline.get(entry["step"])}
            previous = self._saved_chain_view_key[0]
            if edits:
                self._saved_chain_correction_drafts[previous] = edits
            else:
                self._saved_chain_correction_drafts.pop(previous, None)
        current = {entry["step"]: (entry["rune1"], entry["rune2"]) for entry in entries}
        edits = {step: entry for step, entry in self._saved_chain_correction_drafts.get(key[0], {}).items()
                 if not closed and step in current and (entry["rune1"], entry["rune2"]) != current[step]}
        if edits:
            self._saved_chain_correction_drafts[key[0]] = edits
        else:
            self._saved_chain_correction_drafts.pop(key[0], None)
        self._saved_chain_view_key = key
        self._saved_chain_edit_context = dict(self._chain_context) if self._chain_context else None
        table = self.expedition_chain_table
        # Appending one accepted part must retain existing dropdowns and their correction drafts.
        append_only = bool(previous_view and previous_view[0] == key[0] and
                           not previous_view[2] and not closed and
                           key[1][:len(previous_view[1])] == previous_view[1] and
                           table.rowCount() == len(previous_view[1]))
        first_row = table.rowCount() if append_only else 0
        if not append_only:
            table.setRowCount(0)
        names = [self.propagation_rune_inputs[0].itemText(index)
                 for index in range(1, self.propagation_rune_inputs[0].count())]
        for row, entry in enumerate(entries[first_row:], first_row):
            table.insertRow(row)
            item = QTableWidgetItem(str(entry["step"]))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole, entry["step"])
            table.setItem(row, 0, item)
            shown = edits.get(entry["step"], entry)
            for column, name in ((1, shown["rune1"]), (2, shown["rune2"])):
                options = ["", *names] if column == 2 else list(names)
                if name and name not in options:
                    options.append(name)
                field = combo(options, name)
                field.setAccessibleName(f"Chain part {entry['step']} rune {column}")
                field.setEnabled(not closed)
                table.setCellWidget(row, column, field)
                field.currentIndexChanged.connect(self._chain_corrections_changed)
        table.setVisible(bool(entries))
        self._chain_corrections_changed()

    def _saved_chain_edits(self):
        """Read the saved-step correction controls with their original logger step numbers."""
        return [{"step": self.expedition_chain_table.item(row, 0).data(Qt.ItemDataRole.UserRole),
                 "rune1": self.expedition_chain_table.cellWidget(row, 1).currentText(),
                 "rune2": self.expedition_chain_table.cellWidget(row, 2).currentText()}
                for row in range(self.expedition_chain_table.rowCount())]

    def _chain_corrections_changed(self, *_):
        """Enable correction saving when saved-step edits differ from persisted open-chain
        state.
        """
        dirty = self._saved_chain_edits() != (self.state.get("chain") or [])
        self.expedition_save_chain_button.setVisible(bool(self.state.get("chain")))
        self.expedition_save_chain_button.setEnabled(dirty and not self.state.get("chain_completed"))
        self._update_chain_completion_controls()

    def _update_chain_completion_controls(self):
        """Enable completion for saved open chains without drafts, corrections or context-bound scans."""
        if not hasattr(self, "expedition_complete_chain_button"):
            return
        draft = any(value(field) for field in self.rune_inputs)
        dirty = self._saved_chain_edits() != (self.state.get("chain") or [])
        ready = bool(self.state.get("chain") and not self.state.get("chain_completed") and
                     not draft and not dirty and self.pending_review_kind in (None, "remnant", "seed") and
                     not self._manual_propagation_context)
        for field in (self.review_complete_chain_button, self.expedition_complete_chain_button,
                      self.header_complete_chain_button):
            field.setEnabled(ready)
            field.setToolTip("Finish this saved chain and advance to the next expedition." if ready else
                             "Save a chain part and finish any pending scan, draft or corrections first.")

    def save_chain_corrections(self):
        """Persist saved-step corrections against their captured context and refresh commit
        feedback.
        """
        saved = logger.update_chain_steps(self._saved_chain_edits(), expected_context=self._saved_chain_edit_context)
        self.refresh()
        if saved.get("scan_commit_number"):
            self._set_commit_badge(saved["scan_commit_number"])
        self.note(f"{saved['expedition_id']} · Chain rune corrections saved.", True)

    def _edit_chain_review_rune(self, item):
        """Copy a review-table rune correction to its backing draft field and defer table
        reconstruction.
        """
        if item.column() == 1:
            index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(index, int) and 0 <= index < len(self.rune_inputs):
                with QSignalBlocker(self.rune_inputs[index]):
                    self.rune_inputs[index].setText(item.text().strip())
                self._chain_fields_changed(defer_review=True)

    def _manual_propagation_changed(self, *_):
        """Update row approvals independently and hide fallback fields for resolved recipes."""
        if hasattr(self, "propagation_add_button"):
            try:
                self._manual_propagation_runes()
                ready = True
            except ValueError:
                ready = False
            active = bool(self._chain_context and not self._chain_context.get("_capture_map_pending") and
                          not self.state.get("chain_completed"))
            selected = self.propagation_recipe_table.currentRow()
            selected_choice = (self._propagation_choices[selected]
                               if 0 <= selected < len(self._propagation_choices) else None)
            waiting = not self._propagation_choices or (selected_choice is not None and
                                                       not selected_choice.get("denied"))
            self.propagation_add_button.setEnabled(ready and active and waiting and
                                                   self._propagation_manual_requested)
            for row, choice in enumerate(self._propagation_choices):
                actions = self.propagation_recipe_table.cellWidget(row, 2)
                if actions:
                    approve = actions.findChild(QPushButton, "approvePropagationRecipe")
                    if approve:
                        if self._propagation_row_inputs[row]:
                            try:
                                self._propagation_recipe_runes(row)
                                can_approve = True
                            except ValueError:
                                can_approve = False
                        else:
                            manual = row == selected and any(field.currentText().strip()
                                                             for field in self.propagation_rune_inputs)
                            can_approve = ready if manual else choice.get("can_use", bool(choice.get("runes")))
                        approve.setText("Approve")
                        approve.setToolTip("Save this recipe's marked runes to the selected expedition." if can_approve else
                                           "Choose the marked runes in this recipe's dropdowns, left to right."
                                           if self._propagation_row_inputs[row] else
                                           "Use Enter propagation manually to identify this recipe's marked runes.")
                        approve.setEnabled(active and not choice.get("denied") and
                                           can_approve)
            if self._propagation_choices:
                text = ("Marked runes for " + (selected_choice.get("selected_recipe") or
                                              selected_choice.get("reward_text") or "the selected recipe")
                        if selected_choice and not selected_choice.get("denied") else
                        "Select a waiting recipe, then enter its marked runes below.")
                self.propagation_manual_label.setText(text)
            self.propagation_manual_label.setVisible(bool(self._propagation_choices))
            self.propagation_manual_group.setVisible(self._propagation_manual_requested)
            self._update_manual_propagation_shortcut()
            self._update_chain_completion_controls()

    def _update_manual_propagation_shortcut(self, kind=None):
        """Hide redundant manual entry for recipe-only reviews while preserving unresolved fallback access."""
        displayed = kind or self.review_kind.property("scanKind")
        inline_only = bool(self._manual_propagation_context and self._propagation_choices and
                           len(self._propagation_row_inputs) == len(self._propagation_choices) and
                           all(self._propagation_row_inputs))
        self.manual_propagation_button.setVisible(displayed in (None, "propagation") and not inline_only)

    def _propagation_recipe_runes(self, row):
        """Read only this recipe's selected slots, requiring first then optional later second."""
        fields = self._propagation_row_inputs[row]
        first, second = [field.currentData() for field in fields]
        if first is None:
            raise ValueError("Choose the first marked rune in this recipe.")
        if second is not None and second <= first:
            raise ValueError("Choose marked runes in left-to-right recipe order.")
        return [field.currentText() for field in fields if field.currentData() is not None]

    def _build_propagation_recipe_inputs(self, row, choice):
        """Build recipe-ordered choices with local rune PNGs, preserving labels and numeric slot IDs."""
        with logger._connect() as db:
            recipe = db.execute("SELECT sockets,combo FROM recipes WHERE name=? COLLATE NOCASE",
                                (choice.get("selected_recipe") or "",)).fetchone()
        names = [rune.strip() for rune in (recipe["combo"] or "").split("+")] if recipe else []
        if (not recipe or len(names) != recipe["sockets"] or
                any(not rune or rune.casefold() == "unresolved" for rune in names)):
            return None
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(4, 4, 4, 4)
        fields = []
        for caption in ("First marked rune", "Second marked rune (optional)"):
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(QLabel("First rune" if not fields else "Second (optional)"))
            field = combo([(None, "")] + list(enumerate(names)))
            field.setIconSize(QSize(20, 20))
            for index, name in enumerate(names, 1):
                icon_name = re.sub(r"[^a-z0-9]+", "_", name.casefold())
                icon_path = HERE / "rune_icons" / f"{icon_name}.png"
                if icon_path.is_file():
                    field.setItemIcon(index, QIcon(str(icon_path)))
            field.setAccessibleName(f"{choice['selected_recipe']} · {caption}")
            field.setToolTip(caption + " · choose only runes with three gold marks.")
            # Reserve the longest recipe rune, its icon and the styled arrow
            # instead of allowing compact windows to collapse to icon-only.
            field.setMinimumWidth(max(field.fontMetrics().horizontalAdvance(name)
                                      for name in names) + field.iconSize().width() + 52)
            field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            column.addWidget(field)
            layout.addLayout(column, 1)
            fields.append(field)
        # Prefill verified readings only when their names map to this recipe's
        # ordered slots; incomplete OCR must leave the user a blank selection.
        runes = choice.get("runes") or []
        if choice.get("can_use", bool(runes)) and 1 <= len(runes) <= 2:
            positions = [position - 1 for position in choice.get("positions") or []]
            if not positions:
                previous = -1
                for rune in runes:
                    position = next((index for index, name in enumerate(names)
                                     if index > previous and name.casefold() == rune.casefold()), None)
                    if position is None:
                        break
                    positions.append(position)
                    previous = position
            if (len(positions) == len(runes) and positions == sorted(set(positions)) and
                    all(0 <= position < len(names) and names[position].casefold() == rune.casefold()
                        for position, rune in zip(positions, runes))):
                for field, position in zip(fields, positions):
                    field.setCurrentIndex(position + 1)
        self.propagation_recipe_table.setCellWidget(row, 1, controls)
        return fields

    def _propagation_recipe_selected(self, row, _column, previous_row, _previous_column):
        # Manual corrections belong to one recipe. Switching rows must not
        # apply a correction entered for a different reward.
        """Close stale fallback menus on recipe changes while retaining each row's own selections."""
        if row != previous_row:
            for field in self.propagation_rune_inputs:
                with QSignalBlocker(field):
                    field.hidePopup()
                    field.setCurrentIndex(0)
                    field.setEditText("")
        self._manual_propagation_changed()

    def _clear_manual_propagation(self):
        """Close old manual menus and clear propagation context, recipe choices and rune input
        without discarding saved chain parts.
        """
        self._manual_propagation_context = None
        self._propagation_manual_requested = False
        if not hasattr(self, "propagation_rune_inputs"):
            return
        for field in self.propagation_rune_inputs:
            with QSignalBlocker(field):
                field.hidePopup()
                field.setCurrentIndex(0)
                field.setEditText("")
        self._propagation_choices = []
        self._propagation_row_inputs = []
        self.propagation_recipe_table.setRowCount(0)
        self.propagation_recipe_table.hide()
        self._manual_propagation_changed()

    def _prepare_manual_propagation(self, result, *, manual=False):
        """Create recipe-only rune dropdowns for mandatory approval of every propagation scan."""
        self._clear_manual_propagation()
        self._manual_propagation_context = dict(result)
        self._propagation_manual_requested = manual
        self._propagation_choices = [dict(choice) for choice in result.get("choices") or []]
        if not self._propagation_choices and result.get("selected_recipe"):
            self._propagation_choices = [dict(result)]
        table = self.propagation_recipe_table
        blocker = QSignalBlocker(table)
        for index, choice in enumerate(self._propagation_choices):
            table.insertRow(index)
            table.setItem(index, 0, QTableWidgetItem(choice.get("selected_recipe") or
                                                   choice.get("reward_text") or "Unrecognized recipe"))
            runes = choice.get("runes") or []
            table.setItem(index, 1, QTableWidgetItem(" → ".join(runes) or
                                                   choice.get("status") or "Identify the marked runes manually"))
            self._propagation_row_inputs.append(self._build_propagation_recipe_inputs(index, choice))
            actions = QWidget()
            layout = QHBoxLayout(actions)
            layout.setContentsMargins(4, 4, 4, 4)
            approve = button("Approve", lambda checked=False, row=index:
                             self.run(lambda: self.approve_propagation_recipe(row)), "primary")
            approve.setObjectName("approvePropagationRecipe")
            approve.setEnabled(choice.get("can_use", bool(runes)))
            deny = button("Deny", lambda checked=False, row=index: self.deny_propagation_recipe(row), "danger")
            layout.addWidget(approve)
            layout.addWidget(deny)
            table.setCellWidget(index, 2, actions)
        for fields in self._propagation_row_inputs:
            for field in fields or []:
                field.currentIndexChanged.connect(self._manual_propagation_changed)
        table.setVisible(bool(self._propagation_choices))
        table.setMinimumHeight(min(260, 38 + 62 * len(self._propagation_choices)))
        del blocker
        selected_recipe = result.get("selected_recipe")
        if selected_recipe:
            for row, choice in enumerate(self._propagation_choices):
                if choice.get("selected_recipe") == selected_recipe:
                    table.setCurrentCell(row, 0)
                    break
        self._manual_propagation_changed()
        self._set_chain_review_active(True)

    def start_manual_propagation(self):
        """Open propagation entry without changing a held remnant's identity."""
        self._load_chain_context()
        if self._manual_propagation_context:
            self._propagation_manual_requested = True
            self._manual_propagation_changed()
            self.tabs.setCurrentIndex(0)
            self._focus_manual_propagation()
            return
        if self.pending_review_kind not in ("remnant", "seed"):
            self._review_pending("propagation", "Enter the marked runes, then save the chain part.", False)
        else:
            self._show_independent_propagation_review(
                "Enter the marked runes, then save the chain part.")
        self._prepare_manual_propagation(logger.scan_context(), manual=True)
        self.tabs.setCurrentIndex(0)

    def _show_independent_propagation_review(self, summary, raw=None):
        # Keep the pending remnant and its captured IDs intact, but give an
        # explicitly requested propagation scan its own visible controls.
        """Show propagation controls while retaining a separate remnant review."""
        if self.review_kind.property("scanKind") != "propagation":
            self._held_remnant_review = (self.pending_review_kind,
                self.review_summary.text(), self.approve_scan_button.isEnabled(), self._failed_review)
        self.review_kind.setText("Propagation scan")
        self.review_kind.setProperty("scanKind", "propagation")
        set_message(self.review_summary, summary)
        self._show_image(raw)
        for widget in (self.review_group, self.remnant_log_group, self.seed_table,
                       self.scan_status, self.found_table, self.found_label,
                       self.currency_review_group, self.ritual_review_group, self.review_edit_button):
            widget.hide()
        self._review_controls(None)
        self.approve_scan_button.setEnabled(False)
        self.manual_remnant_button.setText("Return to remnant review")
        self.manual_remnant_button.show()
        self.tabs.setCurrentIndex(0)

    def deny_propagation_recipe(self, row):
        """Reject one recipe and disable only that row's rune selectors and approval controls."""
        if 0 <= row < len(self._propagation_choices):
            self._propagation_choices[row]["denied"] = True
            self.propagation_recipe_table.cellWidget(row, 2).setEnabled(False)
            controls = self.propagation_recipe_table.cellWidget(row, 1)
            if controls:
                controls.setEnabled(False)
                controls.hide()
            self.propagation_recipe_table.item(row, 1).setText("Denied")
            if self.propagation_recipe_table.currentRow() == row:
                for field in self.propagation_rune_inputs:
                    with QSignalBlocker(field):
                        field.setCurrentIndex(0)
                        field.setEditText("")
            self._manual_propagation_changed()
            if all(choice.get("denied") for choice in self._propagation_choices):
                self._clear_manual_propagation()
                if self.pending_review_kind == "propagation":
                    self.pending_review_kind = None
                    self._propagation_reading = None
                    self._review_controls(None)
                self._refresh_chain_review()

    def approve_propagation_recipe(self, row):
        """Approve this row's rune selections and carry its own family evidence into the chain save."""
        if not self._manual_propagation_context:
            raise ValueError("Scan propagation before choosing a recipe.")
        logger.validate_scan_context(self._manual_propagation_context)
        if not 0 <= row < len(self._propagation_choices):
            raise ValueError("Select a waiting propagation recipe.")
        choice = self._propagation_choices[row]
        if choice.get("denied"):
            raise ValueError("Select a waiting propagation recipe.")
        self.propagation_recipe_table.setCurrentCell(row, 0)
        if self._propagation_row_inputs[row]:
            try:
                runes = self._propagation_recipe_runes(row)
            except ValueError as error:
                self._focus_manual_propagation()
                self.statusBar().showMessage(str(error), 15000)
                return
            result = {**self._manual_propagation_context, **choice,
                      "can_use": True, "runes": runes,
                      "selected_recipe": choice["selected_recipe"]}
            self._accept_manual_propagation(result)
            return
        manual = any(field.currentText().strip() for field in self.propagation_rune_inputs)
        if not manual and not choice.get("can_use", bool(choice.get("runes"))):
            self._focus_manual_propagation()
            return
        if manual:
            try:
                runes = self._manual_propagation_runes()
            except ValueError:
                self._focus_manual_propagation()
                return
        else:
            runes = choice["runes"]
        result = {**self._manual_propagation_context, **choice,
                  "can_use": True, "runes": runes,
                  "selected_recipe": choice["selected_recipe"]}
        self._accept_manual_propagation(result)

    def _focus_manual_propagation(self):
        """Reveal the selected row's inline selector, or the standalone manual fallback."""
        row = self.propagation_recipe_table.currentRow()
        fields = (self._propagation_row_inputs[row]
                  if 0 <= row < len(self._propagation_row_inputs) else None)
        if self._propagation_manual_requested:
            fields = None
        field = (fields or self.propagation_rune_inputs)[0]
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        page = self.tabs.widget(0)
        # Scroll both the recipe table and the outer review page so a selected
        # row's controls remain reachable even in a long detected recipe list.
        if fields:
            self.propagation_recipe_table.scrollToItem(self.propagation_recipe_table.item(row, 0))
        QTimer.singleShot(0, lambda: page.ensureWidgetVisible(field, 12, 24)
                          if not self._closed and self._manual_propagation_context else None)

    def _manual_propagation_runes(self):
        """Validate the first and optional second marked rune against catalog names and
        normalize their spelling.
        """
        runes = [field.currentText().strip() for field in self.propagation_rune_inputs]
        if not runes[0]:
            raise ValueError("Enter the first marked rune, then the optional second rune.")
        while runes and not runes[-1]:
            runes.pop()
        names = {self.propagation_rune_inputs[0].itemText(index).casefold():
                 self.propagation_rune_inputs[0].itemText(index)
                 for index in range(1, self.propagation_rune_inputs[0].count())}
        if any(rune.casefold() not in names for rune in runes):
            raise ValueError("Choose a rune name from the marked-rune lists.")
        return [names[rune.casefold()] for rune in runes]

    def add_manual_propagation(self):
        """Add manually named runes only to a waiting selected recipe."""
        context = self._manual_propagation_context or self._chain_context
        logger.validate_scan_context(context)
        row = self.propagation_recipe_table.currentRow()
        choice = self._propagation_choices[row] if 0 <= row < len(self._propagation_choices) else {}
        if self._propagation_choices and (not choice or choice.get("denied")):
            raise ValueError("Select a waiting propagation recipe before adding its runes.")
        result = {**context, "can_use": True, "runes": self._manual_propagation_runes(),
                  "selected_recipe": choice.get("selected_recipe", "")}
        self._accept_manual_propagation(result)

    def _accept_manual_propagation(self, result):
        """Save accepted manual runes while retaining any held remnant review, then retire
        propagation-only controls.
        """
        self._append_propagation(result, preserve_remnant_review=True, auto_save=True)

    def _propagation_read(self, result, raw=None):
        """Hold every propagation capture for explicit approval, regardless of OCR confidence or settings."""
        if self._chain_save_pending:
            raise ValueError("Wait for the approved chain part to save before scanning propagation again.")
        logger.validate_scan_context(result)
        runes = result.get("runes") or []
        if (not isinstance(runes, (list, tuple)) or
                any(not isinstance(rune, str) or len(rune) > 80 for rune in runes)
                or (result.get("can_use") and
                    (not 1 <= len(runes) <= 2 or any(not rune.strip() for rune in runes)))):
            raise ValueError("Scan a selected recipe with one or two clear propagation marks.")
        # Replayed callbacks for a manually accepted capture must not reopen or duplicate it.
        result.setdefault("_chain_accept_request", uuid4().hex)
        if result["_chain_accept_request"] in self._accepted_propagation_requests:
            return
        preserve_remnant = (self.pending_review_kind in ("remnant", "seed") or
                            bool(logger.get_state()["ocr_pending"]))
        self._load_chain_context()
        if preserve_remnant:
            self._show_independent_propagation_review(
                result.get("status") or "Check the selected recipe and marked runes.", raw)
            self._prepare_manual_propagation(result)
            text = "Review the recipe and marked runes, then Approve. The pending remnant remains open for review."
            set_message(self.chain_review_status, text, "info")
            self.statusBar().showMessage(text, 15000)
            self.tabs.setCurrentIndex(0)
            token = self._overlay_review_token
            QTimer.singleShot(0, lambda: self._reveal_review_overlay(token))
            return
        self._propagation_reading = dict(result)
        self._show_image(raw)
        rows = [(f"Rune {index + 1}", rune, "Left to right") for index, rune in enumerate(runes)]
        self._review_pending("propagation", "Review the recipe and marked runes, then Approve.",
                             bool(result.get("can_use") and 1 <= len(runes) <= 2), rows)
        self._prepare_manual_propagation(result)

    def _append_propagation(self, result=None, *, preserve_remnant_review=False, auto_save=False):
        """Freeze approved rune/family evidence for atomic retries, or stage an explicit draft, without advancing."""
        result = result if preserve_remnant_review else self._propagation_reading
        if ((not preserve_remnant_review and self.pending_review_kind != "propagation") or
                not result or not result.get("can_use")):
            raise ValueError("Scan a selected recipe with one or two clear propagation marks.")
        logger.validate_scan_context(result)
        logger.validate_scan_context(self._chain_context)
        runes = result.get("runes", [])
        if not 1 <= len(runes) <= 2 or any(not isinstance(rune, str) or not rune.strip() for rune in runes):
            raise ValueError("Scan a selected recipe with one or two clear propagation marks.")
        # Accepted scans persist independently of an unrelated manual draft in this expedition.
        # This flag saves explicitly reviewed parts immediately; OCR ingress never uses it.
        if auto_save:
            if self._chain_save_pending:
                return
            request_id = result.setdefault("_chain_accept_request", uuid4().hex)
            if self._manual_propagation_context is not None:
                self._manual_propagation_context.setdefault("_chain_accept_request", request_id)
            frozen = {**result, "runes": list(runes)}
            if isinstance(result.get("candidates"), (list, tuple)):
                frozen["candidates"] = list(result["candidates"])
            pending = {"result": frozen,
                       "context": dict(self._chain_context), "preserve_review": preserve_remnant_review,
                       "deadline": time.monotonic() + 10}
            self._chain_save_pending = pending
            self._retry_approved_chain_save(pending)
            return
        occupied = [index for index, field in enumerate(self.rune_inputs) if value(field)]
        start = occupied[-1] + 1 if occupied else 0
        if start + len(runes) > 96:
            raise ValueError("This chain draft already has the maximum of 96 runes.")
        saved = logger.increment_propagation_detonated(self._chain_context,
            runes=runes, recipe=result.get("selected_recipe") or "",
            request_id=result.setdefault("_chain_accept_request", uuid4().hex))
        if start + len(runes) > len(self.rune_inputs):
            self.add_runes(start + len(runes) - len(self.rune_inputs))
        self._chain_scan_number += 1
        for offset, rune in enumerate(runes):
            field = self.rune_inputs[start + offset]
            with QSignalBlocker(field):
                field.setProperty("chainPart", f"scan-{self._chain_scan_number}")
                field.setProperty("chainRecipe", result.get("selected_recipe") or "")
                field.setText(rune.strip())
        self._accepted_propagation_requests.add(result["_chain_accept_request"])
        self.state["detonated"] = saved["detonated"]
        self.state["scan_commit_count"] = saved["scan_commit_number"]
        self._set_commit_badge(saved["scan_commit_number"])
        self._propagation_review_active = True
        self._chain_fields_changed()
        if preserve_remnant_review:
            text = " → ".join(runes) + " added to the chain draft."
            if self.pending_review_kind in ("remnant", "seed") or logger.get_state()["ocr_pending"]:
                text += " The pending remnant remains open for review."
            self.statusBar().showMessage(text, 15000)
            if self.review_kind.property("scanKind") == "propagation":
                set_message(self.review_summary, text, "success")
            self.tabs.setCurrentIndex(0)
            return
        self._propagation_reading = None
        self.pending_review_kind = None
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.found_table.hide()
        self.found_label.hide()
        set_message(self.review_summary,
                    f"{result.get('selected_recipe') or 'Selected recipe'} · " + " → ".join(runes)
                    + f" added to the chain draft. {saved['expedition_id']} · {saved['detonated']} remnants detonated. "
                    "Use Commit to chain to save these reviewed parts; Complete chain finishes the expedition.", "success")
        self._overlay_review_token += 1
        token = self._overlay_review_token
        QTimer.singleShot(0, lambda: self.show_overlay() if self._overlay_enabled and
                          not self._editing_regions and not self._region_selection_pending and
                          token == self._overlay_review_token and self.tabs.currentIndex() == 0 else None)

    def commit_chain(self):
        """Save accepted parts without completing the chain or advancing its expedition."""
        preserve_remnant = self.pending_review_kind in ("remnant", "seed")
        runes = [value(field) for field in self.rune_inputs]
        while runes and not runes[-1]:
            runes.pop()
        if not runes or any(not rune for rune in runes):
            raise ValueError("Fill runes in order, starting at Rune 1.")
        names = {self.propagation_rune_inputs[0].itemText(index).casefold():
                 self.propagation_rune_inputs[0].itemText(index)
                 for index in range(1, self.propagation_rune_inputs[0].count())}
        if any(rune.casefold() not in names for rune in runes):
            raise ValueError("Correct the chain rune names before committing. Choose names from the marked-rune lists.")
        steps = [{key: names[rune.casefold()] if rune else "" for key, rune in step.items()}
                 for step in self._chain_steps()]
        fingerprint = (self._chain_key(self._chain_context),
                       tuple((step["rune1"], step["rune2"]) for step in steps))
        if not self._chain_save_request or self._chain_save_request[0] != fingerprint:
            self._chain_save_request = (fingerprint, uuid4().hex)
        saved = logger.commit_chain_draft(steps, expected_context=self._chain_context,
                                          request_id=self._chain_save_request[1])
        service.HOTKEY.cancel_capture(modes=("propagation",))
        self._propagation_reading = None
        for field in self.rune_inputs:
            field.clear()
        self._chain_save_request = None
        if self.pending_review_kind == "propagation":
            self.pending_review_kind = None
            self._review_controls(None)
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        text = (f"{saved['expedition_id']} · {len(saved['steps'])} chain parts saved in order. "
                "Scan the next part, or Complete chain when finished.")
        if not preserve_remnant or self.review_kind.property("scanKind") == "propagation":
            set_message(self.review_summary, text, "success")
        self.note(text, True)

    def _cancel_pending_chain_save(self):
        """Retire deferred acceptance on cancellation or a changed map/session, restoring its controls."""
        self._chain_save_pending = None
        if hasattr(self, "propagation_recipe_table"):
            self.propagation_recipe_table.setEnabled(True)

    def _retry_approved_chain_save(self, pending):
        """Retry a frozen, explicitly approved part without waiting for SQLite locks on the GUI thread."""
        if self._closed or self._chain_save_pending is not pending:
            return
        result = pending["result"]
        try:
            if time.monotonic() >= pending["deadline"]:
                raise TimeoutError("The database is busy. The chain part was not saved; Approve again.")
            saved = logger.accept_propagation_part(pending["context"], runes=result["runes"],
                recipe=result.get("selected_recipe") or "", request_id=result["_chain_accept_request"],
                family=result.get("family"), family_candidates=result.get("candidates"), wait_for_lock=False)
        except sqlite3.OperationalError as error:
            if (getattr(error, "sqlite_errorcode", 0) & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                self._cancel_pending_chain_save()
                raise
            self.propagation_recipe_table.setEnabled(False)
            self.statusBar().showMessage("Saving the approved chain part; waiting for the database.")
            QTimer.singleShot(50, lambda: self.run(lambda: self._retry_approved_chain_save(pending)))
            return
        except Exception:
            self._cancel_pending_chain_save()
            raise
        self._cancel_pending_chain_save()
        self._finish_approved_chain_save(result, saved, pending["preserve_review"])

    def _finish_approved_chain_save(self, result, saved, preserve_review):
        """Show a completed save while retaining any newer independent review and its typed corrections."""
        request_id = result["_chain_accept_request"]
        self._accepted_propagation_requests.add(request_id)
        owned = any(reading and reading.get("_chain_accept_request") == request_id
                    for reading in (self._manual_propagation_context, self._propagation_reading))
        if owned:
            self._propagation_reading = None
            if self.pending_review_kind == "propagation":
                self.pending_review_kind = None
                self._review_controls(None)
                self.found_table.hide()
                self.found_label.hide()
                self.approve_scan_button.setEnabled(False)
            self._clear_manual_propagation()
            self._propagation_review_active = True
        self._refresh_after_chain_save()
        self._set_commit_badge(saved["scan_commit_number"])
        text = (f"{result.get('selected_recipe') or 'Selected recipe'} · " + " → ".join(result["runes"]) +
                f" saved to {saved['expedition_id']}. Scan the next part, or Complete chain when finished.")
        self.statusBar().showMessage(text, 15000)
        if owned and self.review_kind.property("scanKind") == "propagation":
            set_message(self.review_summary, text, "success")
        if owned and not preserve_review:
            self._overlay_review_token += 1
            token = self._overlay_review_token
            QTimer.singleShot(0, lambda: self.show_overlay() if self._overlay_enabled and
                              not self._editing_regions and not self._region_selection_pending and
                              token == self._overlay_review_token and self.tabs.currentIndex() == 0 else None)

    def _refresh_after_chain_save(self):
        """Refresh saved chain state and counters without rebuilding unrelated pages or overwriting drafts."""
        state = logger.get_state()
        context = (logger.session_generation(), state["current_map_id"])
        # A first save can create the map; changed settings still need the complete synchronization.
        if context != self._form_map_context or state["settings"] != self.state["settings"]:
            self.refresh()
            return
        self.state = state
        self._sync_header_ids(state)
        self._load_chain_context()
        counts = state["counts"]
        set_message(self.counts, f"{counts['historical_rows']} imported rows · {counts['new_rows']} new rows · "
                    f"{state['ritual_pages']} Ritual pages · {counts['saved_scans']} saved scans · "
                    f"{counts['rune_references']} rune references")

    def complete_chain(self):
        """Finish the saved chain and advance the expedition once while retaining separate reviews."""
        if any(value(field) for field in self.rune_inputs):
            raise ValueError("Commit the reviewed parts to the chain before completing it.")
        if self.pending_review_kind == "propagation" or self._manual_propagation_context:
            raise ValueError("Approve or deny the waiting propagation scan before completing the chain.")
        # Other scan captures retain the expedition token until their review is resolved.
        if self.pending_review_kind not in (None, "remnant", "seed"):
            raise ValueError("Finish the pending scan review before completing the chain.")
        if self._saved_chain_edits() != (self.state.get("chain") or []):
            raise ValueError("Save the rune corrections before completing the chain.")
        preserve_remnant = self.pending_review_kind in ("remnant", "seed")
        saved = logger.complete_chain(expected_context=self._chain_context)
        service.HOTKEY.cancel_capture(modes=("propagation",))
        self._propagation_reading = None
        self._discard_chain_review_draft()
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        text = f"{saved['expedition_id']} · Chain completed. {saved['next_expedition_id']} is ready."
        if not preserve_remnant or self.review_kind.property("scanKind") == "propagation":
            set_message(self.review_summary, text, "success")
        self.note(text, True)

    def save_counts(self):
        """Save map kill counts, clear their draft baseline and refresh the commit badge."""
        data = self.counts_data()
        saved = logger.save_counts(data["normal"], data["magic"], data["rare"], unique=data["unique"])
        self._counts_form_baseline = None
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        self.note("Map kill counts saved.", True)

    def _end_hotkey_capture(self):
        """Release keyboard capture and restore the overlay Escape shortcut when key selection
        ends.
        """
        if not self._capturing_hotkey:
            return False
        self._capturing_hotkey = False
        self.releaseKeyboard()
        self.overlay_escape.setEnabled(self._overlay_enabled)
        return True

    def arm_hotkey(self, target="default"):
        """Toggle key selection, temporarily unregistering shortcuts and grabbing keyboard
        input for the target.
        """
        if self._end_hotkey_capture():
            service.HOTKEY.start()
            self.refresh_hotkey()
            self.note("Hotkey selection cancelled.")
            return
        if service.HOTKEY.status()["supported"]:
            service.HOTKEY._unregister()
        self._capturing_hotkey = target
        self.overlay_escape.setEnabled(False)
        self.grabKeyboard()
        action = "show / hide HUD" if target == "overlay" else f"{target} scan"
        self.note(f"Press a key or combination for {action}. Click Set key again to cancel.")

    def clear_hotkey(self, target="default"):
        """Clear a target shortcut and restore registration state if an active key-selection
        update fails.
        """
        capturing = self._end_hotkey_capture()
        try:
            if target == "default":
                service.HOTKEY.configure("")
            else:
                service.HOTKEY.configure_for(target, "")
        except Exception:
            if capturing:
                self.run(service.HOTKEY.start)
            self.refresh_hotkey()
            raise
        self.refresh_hotkey()
        action = "Show / hide HUD" if target == "overlay" else f"{target.title()} scan"
        self.note(f"{action} shortcut cleared.")

    def set_overlay_opacity(self, percent):
        """Apply configured transparency only to a manually revealed HUD."""
        self._overlay_opacity = max(20, min(100, int(percent)))
        self.overlay_opacity_label.setText(f"{self._overlay_opacity}%")
        self.setWindowOpacity(self._overlay_opacity / 100
                              if self._overlay_enabled and self._overlay_revealed
                              and not self._overlay_auto_review else 1.0)
        self._overlay_opacity_timer.start()

    def _save_overlay_opacity(self):
        """Persist the debounced HUD opacity preference independently of current window
        opacity.
        """
        with logger._connect() as db:
            logger._set_meta(db, "hud_overlay_opacity", self._overlay_opacity)

    def set_overlay_enabled(self, enabled):
        """Configure overlay behavior without making normal pages transparent."""
        if enabled and service.HOTKEY.status()["supported"] and not service.HOTKEY.status()["combos"].get("overlay"):
            try:
                service.HOTKEY.configure_for("overlay", "Ctrl+Shift+H")
            except ValueError:
                with QSignalBlocker(self.overlay_checkbox):
                    self.overlay_checkbox.setChecked(self._overlay_enabled)
                raise
        with logger._connect() as db:
            logger._set_meta(db, "hud_overlay", bool(enabled))
        self._overlay_enabled = bool(enabled)
        self._overlay_auto_review = False
        self._overlay_revealed = False
        self._overlay_activation_pending = False
        self._overlay_review_token += 1
        visible, minimized = self.isVisible(), self.isMinimized()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowOpacity(1.0)
        if visible:
            self.showMinimized() if minimized else self.show()
        elif not self._overlay_enabled:
            self.showNormal()
        self.overlay_escape.setEnabled(self._overlay_enabled)
        self.overlay_toggle_button.setEnabled(self._overlay_enabled)
        with QSignalBlocker(self.overlay_checkbox):
            self.overlay_checkbox.setChecked(self._overlay_enabled)
        self.refresh_hotkey()
        self.note("HUD overlay enabled." if enabled else "HUD overlay disabled.", True)

    def toggle_overlay(self):
        """Toggle the enabled HUD between an interactive Review window and hidden/minimized
        state.
        """
        if not self._overlay_enabled:
            return
        if self.isVisible() and not self.isMinimized():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self, automatic=False):
        """Reveal an interactive HUD; automatic confirmations stay opaque."""
        if not self._overlay_enabled:
            return
        self._overlay_revealed = True
        self._overlay_auto_review = automatic
        self._overlay_activation_pending = not self.isActiveWindow()
        # A denied Windows focus request must not suppress a later taskbar activation.
        self._overlay_activation_deadline = time.monotonic() + .15
        self.tabs.setCurrentIndex(0)
        # Confirmation needs a normal interactive window. Keep transparency
        # for the manually revealed HUD, and preserve maximized geometry when
        # a capture hides and restores the window.
        self._overlay_window_transition = True
        try:
            self.setWindowOpacity(1.0 if automatic else self._overlay_opacity / 100)
            if self.windowState() & Qt.WindowState.WindowMaximized:
                self.showMaximized()
            else:
                self.showNormal()
            self.raise_()
            self.activateWindow()
        finally:
            self._overlay_window_transition = False

    def _restore_application_view(self):
        """Restore opaque app painting after an external activation or native minimized-window restore."""
        self._overlay_activation_pending = False
        if self._overlay_revealed or self._overlay_auto_review:
            self._overlay_review_token += 1
            self._overlay_capture_restore = None
        self._overlay_revealed = False
        self._overlay_auto_review = False
        self.setWindowOpacity(1.0)
        if not self._overlay_restore_repaint_pending and self.isVisible() and not self.isMinimized():
            self._overlay_restore_repaint_pending = True
            QTimer.singleShot(0, self._repaint_restored_application)

    def _repaint_restored_application(self):
        """Invalidate the restored client area after Windows and Qt finish their native state transition."""
        self._overlay_restore_repaint_pending = False
        if self._closed or not self.isVisible() or self.isMinimized():
            return
        self.update()
        if self.centralWidget() is not None:
            self.centralWidget().update()

    def hide_overlay(self):
        """Return to the game and restore normal application opacity."""
        if self._overlay_enabled:
            if self._end_hotkey_capture():
                service.HOTKEY.start()
            self._overlay_review_token += 1
            self._overlay_revealed = False
            self._overlay_auto_review = False
            self._overlay_activation_pending = False
            self.setWindowOpacity(1.0)
            hotkey = service.HOTKEY.status()
            if (not self._editing_regions and hotkey["supported"] and
                    hotkey["registered"] and hotkey["combos"].get("overlay")):
                self.hide()
            else:
                self.showMinimized()

    def _hide_overlay_capture(self, ready):
        """Hide the visible HUD on the GUI thread, record restoration context and acknowledge
        the capture worker.
        """
        try:
            # A timed-out worker no longer owns this queued hide request.
            if ready is not None and ready.is_set():
                return
            if (self._overlay_enabled and not self._closed and self.isVisible() and
                    not self.isMinimized()):
                context = logger.scan_context()
                self.hide_overlay()
                self._overlay_capture_restore = (self._overlay_review_token, context)
        finally:
            if ready is not None:
                ready.set()

    def _prepare_overlay_capture(self):
        """Before capture, request a GUI-thread hide and wait briefly so the HUD does not enter
        the screenshot.
        """
        self._overlay_capture_restore = None
        if not self._overlay_enabled or not self._overlay_visible:
            return
        if QThread.currentThread() == self.thread():
            self._hide_overlay_capture(None)
            return
        ready = threading.Event()
        self.tasks.hide_overlay.emit(ready)
        if not ready.wait(.75):
            ready.set()
            raise ValueError("The HUD is busy. Hide it and try the scan again.")

    def _restore_overlay_capture_failure(self):
        """Restore the HUD after capture failure only if its token and map/expedition context
        remain current.
        """
        captured = self._overlay_capture_restore
        self._overlay_capture_restore = None
        if captured is None:
            return
        token, context = captured
        if (self._overlay_enabled and not self._closed and not self._editing_regions and
                not self._region_selection_pending and token == self._overlay_review_token and
                context == logger.scan_context() and
                (not self.isVisible() or self.isMinimized())):
            # An error has no pending review, so automatic review visibility
            # would immediately hide it again on the next poll.
            self.show_overlay()

    def _reveal_review_overlay(self, token):
        """Reveal an opaque held-review window only after scan work finishes and its reveal
        token still matches.
        """
        if (self._overlay_enabled and not self._closed and not self._editing_regions and
                not self._region_selection_pending and self.tabs.currentIndex() == 0 and
                all(reading is None for reading in (self._inventory_reading, self._ritual_reading, self._remnant_reading)) and
                token == self._overlay_review_token and self.pending_review_kind):
            self.show_overlay(automatic=True)

    def refresh_reference_names(self):
        """Reload reference names for the selected item kind, retaining an existing valid
        selection.
        """
        if not hasattr(self, "reference_name"):
            return
        previous = self.reference_name.currentText().strip()
        names = {"omen": logger.ritual_names, "currency": logger.currency_names,
                 "item": logger.item_names}[value(self.reference_kind)]()
        with QSignalBlocker(self.reference_name):
            self.reference_name.clear()
            self.reference_name.addItems(names)
            self.reference_name.setCurrentText(previous if previous in names else "")

    def refresh_reference_examples(self):
        """List stored and review-learned icon examples with database IDs attached for removal."""
        if not hasattr(self, "reference_list"):
            return
        from PySide6.QtWidgets import QListWidgetItem
        self.reference_list.clear()
        for kind, entries in (("Omen", logger.omen_icons()),
                              ("Currency", logger.currency_icons()), ("Item", logger.item_icons()),
                              ("Review", logger.review_icons())):
            for entry in entries:
                label = entry["kind"].title() if kind == "Review" else kind
                row = QListWidgetItem(f"{label} · {entry['name']}" + (" · learned in review" if kind == "Review" else ""))
                picture = QPixmap()
                picture.loadFromData(entry["image"])
                row.setIcon(QIcon(picture.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation)))
                row.setData(Qt.ItemDataRole.UserRole, (kind.lower(), entry["id"]))
                self.reference_list.addItem(row)

    def add_reference_screenshot(self):
        """Crop a bounded screenshot into a labeled local reference and refresh the relevant
        catalogs.
        """
        kind = value(self.reference_kind)
        name = self.reference_name.currentText().strip()
        if not name:
            raise ValueError("Enter the Omen, currency or item name before adding a screenshot.")
        path, _ = QFileDialog.getOpenFileName(self, "Choose a screenshot containing one icon",
                                              str(Path.home()), "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        if Path(path).stat().st_size > 16_000_000:
            raise ValueError("Screenshot exceeds 16 MB.")
        with Image.open(path) as source:
            if source.width * source.height > 12_000_000:
                raise ValueError("Screenshot exceeds 12 megapixels.")
            image = source.convert("RGB")
        dialog = QDialog(self)
        dialog.setWindowTitle("Select one icon")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Drag a box around the icon, then select Save crop."))
        canvas = IconCropCanvas(image)
        layout.addWidget(canvas)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            crop = canvas.crop(image)
        finally:
            dialog.deleteLater()
        if kind == "omen":
            logger.add_ritual_name(name)
            number = logger.save_omen_icon(name, crop)
        elif kind == "item":
            logger.add_item_name(name)
            number = logger.save_item_icon(name, crop)
        else:
            logger.add_currency_item(name)
            number = logger.save_currency_icon(name, crop)
        self.refresh_reference_names()
        self.refresh_reference_examples()
        self.refresh_currency_references()
        set_message(self.reference_status, f"✓ Saved {kind} icon example #{number} for {name}.", "success")
        self.note(f"Saved {kind} icon example for {name}.", True)

    def add_remnant_reference(self):
        """Switch to visible-seed mode and open an image scan for creating a remnant reference."""
        self.tabs.setCurrentIndex(0)
        self.mode = "seed"
        select(self.mode_select, "seed")
        service.HOTKEY.set_mode("seed")
        self.scan_file()

    def remove_reference_example(self):
        """Delete the selected icon example from its owning reference table and refresh
        displayed catalogs.
        """
        item = self.reference_list.currentItem()
        if not item:
            raise ValueError("Select an example to remove.")
        kind, number = item.data(Qt.ItemDataRole.UserRole)
        if kind == "review":
            logger.delete_review_icon(number)
        elif kind == "omen":
            logger.delete_omen_icon(number)
        elif kind == "item":
            logger.delete_item_icon(number)
        else:
            logger.delete_currency_icon(number)
        self.refresh_reference_examples()
        self.refresh_currency_references()
        set_message(self.reference_status, "✓ Icon example removed.", "success")

    def export_reference_pack(self):
        """Persist the destination and export a reference ZIP without replacing older packs."""
        default = DEFAULT_REFERENCE_FOLDER
        if Path(value(self.reference_folder)) == default:
            default.mkdir(parents=True, exist_ok=True)
        folder = logger.save_reference_export_folder(value(self.reference_folder))
        set_message(self.reference_status, "Exporting OCR references…")
        self._submit("Exporting OCR references…", lambda: reference_pack.save_export_file(folder),
                     lambda result: self._reference_exported(result))

    def _reference_exported(self, result):
        """Show successful reference-export size and path after worker completion."""
        set_message(self.reference_status,
                    f"✓ Reference database exported · {result['bytes']:,} bytes · {result['path']}",
                    "success")
        self.note("Reference database exported.", True)

    def choose_reference_folder(self):
        """Choose and immediately persist the reference-pack export directory."""
        folder = QFileDialog.getExistingDirectory(self, "Choose reference export folder",
                                                  value(self.reference_folder) or str(Path.home()))
        if folder:
            self.reference_folder.setText(logger.save_reference_export_folder(folder))

    def import_reference_pack(self):
        """Choose a reference ZIP and import it in the pool using the selected replacement
        policy.
        """
        path, _ = QFileDialog.getOpenFileName(self, "Import OCR reference database",
                                              value(self.reference_folder) or str(Path.home()),
                                              "Reference pack (*.zip)")
        if not path:
            return
        replace = self.reference_replace.isChecked()
        set_message(self.reference_status, "Importing OCR references…")
        self._submit("Importing OCR references…",
                     lambda: reference_pack.import_pack(path, replace),
                     self._reference_imported)

    def _reference_imported(self, counts):
        """Refresh affected catalogs after import and display the new or updated record counts."""
        self.refresh()
        self.refresh_currency_references()
        self.refresh_reference_names()
        self.refresh_reference_examples()
        total = sum(counts.values())
        set_message(self.reference_status,
                    f"✓ Reference database imported · {total} new or updated records · "
                    f"{counts['scans']} remnant screenshots · {counts['currency_icons']} currency icons · "
                    f"{counts['omen_icons']} Omen icons · {counts['item_icons']} Item icons · "
                    f"{counts.get('review_icon_examples', 0)} learned review icons.", "success")
        self.note("Reference database imported and ready for the next scan.", True)

    def reset_reference_database(self):
        """Confirm a reference-only reset, then reload catalogs while retaining logged activities and settings."""
        if self._pending_tasks:
            raise ValueError("Wait for the current scan, import or export to finish before resetting references.")
        answer = QMessageBox.question(
            self, "Reset OCR references to defaults",
            "Restore the built-in OCR references and remove user-added names, labeled icons, "
            "learned examples and custom reference changes?\n\n"
            "Logged maps, items, currency, activities, IDs and personal settings are kept. "
            "Save a reference pack first if you want to keep your custom references.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        # The confirmation runs a nested event loop; recheck workers before replacing reference rows.
        if self._pending_tasks:
            raise ValueError("Wait for the current scan, import or export to finish before resetting references.")
        reference_pack.reset_to_defaults()
        self.refresh()
        self.refresh_currency_references()
        self.refresh_reference_names()
        self.refresh_reference_examples()
        set_message(self.reference_status, "✓ Default OCR references restored. Recorded logs and settings kept.",
                    "success")
        self.note("OCR references reset to defaults.", True)

    def set_auto_commit(self, enabled):
        """Persist the remnant auto-save preference without changing other activity settings."""
        self.state = logger.save_settings({"auto_commit": enabled})
        self.note("Clear opened scans will save automatically." if enabled else
                  "Opened scans will wait for manual review before saving.", True)

    def set_auto_all(self, enabled):
        """Persist the all-activity auto-save choice together with remnant and tablet
        preferences.
        """
        self.state = logger.save_settings({"ocr_auto_commit": enabled,
                                           "auto_commit": enabled,
                                           "tablet_auto_commit": enabled})
        self.refresh()
        self.note("Clear OCR scans will save across all activities." if enabled else
                  "Map, currency and Ritual scans will wait for review.", True)

    def set_auto_tablets(self, enabled):
        """Persist ordered tablet auto-save and refresh its slot and review controls."""
        self.state = logger.save_settings({"tablet_auto_commit": enabled})
        self.refresh()
        self.note("Clear tablet scans will save in order from Tablet 1 to 4." if enabled else
                  "Tablet scans will wait for manual review before saving.", True)

    def eventFilter(self, source, event):
        """Track HUD visibility and external restore, protect wheel edits, capture shortcut keys
        and select preview slots.
        """
        if source is self and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._overlay_visible = event.type() == QEvent.Type.Show
        if source is self and self._overlay_enabled:
            if (event.type() == QEvent.Type.WindowActivate or
                    event.type() == QEvent.Type.ActivationChange and self.isActiveWindow()):
                if (self._overlay_window_transition or
                        self._overlay_activation_pending and time.monotonic() <= self._overlay_activation_deadline):
                    # Qt can send both activation event types for the same native focus request.
                    pass
                else:
                    self._restore_application_view()
            elif ((event.type() == QEvent.Type.WindowDeactivate or
                   event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow()) and
                  not self._overlay_window_transition):
                self._overlay_activation_pending = False
            elif (event.type() == QEvent.Type.WindowStateChange and not self._overlay_window_transition and
                  (self.isMinimized() or event.oldState() & Qt.WindowState.WindowMinimized)):
                self._restore_application_view()
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.Hide, QEvent.Type.Wheel):
            field = source if isinstance(source, QWidget) else None
            while field is not None and not isinstance(field, (QComboBox, QAbstractSpinBox, QSlider)):
                field = field.parentWidget()
            editable = isinstance(source, (QComboBox, QAbstractSpinBox, QLineEdit, QSlider))
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(source, QWidget):
                self._wheel_field = field if event.button() == Qt.MouseButton.LeftButton else None
            elif event.type() == QEvent.Type.Hide and source is field and field is self._wheel_field:
                self._wheel_field = None
            elif event.type() == QEvent.Type.Wheel and editable and field:
                focused = QApplication.focusWidget()
                if (field is not self._wheel_field or
                        not (focused is field or (focused and field.isAncestorOf(focused)))):
                    parent = field.parentWidget()
                    while parent is not None and not isinstance(parent, QAbstractScrollArea):
                        parent = parent.parentWidget()
                    if parent is not None:
                        viewport = parent.viewport()
                        forwarded = QWheelEvent(
                            QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                            event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                            event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                            event.source(), event.pointingDevice())
                        QApplication.sendEvent(viewport, forwarded)
                        event.accept()
                        return True
                    event.ignore()
                    return True
        if self._capturing_hotkey and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
                return True
            target = self._capturing_hotkey
            self._end_hotkey_capture()
            modifiers = event.modifiers()
            if event.nativeVirtualKey():
                from PoE2_Data_Logger.platform.hotkey import virtual_key_name
                name = virtual_key_name(event.nativeVirtualKey())
            elif modifiers & Qt.KeyboardModifier.KeypadModifier:
                keypad = {Qt.Key.Key_Plus: "NumAdd", Qt.Key.Key_Minus: "NumSubtract",
                          Qt.Key.Key_Asterisk: "NumMultiply", Qt.Key.Key_Slash: "NumDivide",
                          Qt.Key.Key_Period: "NumDecimal", Qt.Key.Key_Enter: "Enter"}
                name = f"Num{chr(key)}" if Qt.Key.Key_0 <= key <= Qt.Key.Key_9 else keypad.get(key)
            elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
                name = f"F{key - Qt.Key.Key_F1 + 1}"
            elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                name = chr(key)
            else:
                names = {Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Tab: "Tab",
                         Qt.Key.Key_Backtab: "Tab", Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter",
                         Qt.Key.Key_Pause: "Pause", Qt.Key.Key_CapsLock: "CapsLock",
                         Qt.Key.Key_Escape: "Escape", Qt.Key.Key_Space: "Space",
                         Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown",
                         Qt.Key.Key_End: "End", Qt.Key.Key_Home: "Home", Qt.Key.Key_Left: "Left",
                         Qt.Key.Key_Up: "Up", Qt.Key.Key_Right: "Right", Qt.Key.Key_Down: "Down",
                         Qt.Key.Key_Print: "PrintScreen", Qt.Key.Key_Insert: "Insert", Qt.Key.Key_Delete: "Delete",
                         Qt.Key.Key_Menu: "Menu", Qt.Key.Key_NumLock: "NumLock", Qt.Key.Key_ScrollLock: "ScrollLock"}
                name = names.get(key) or (chr(key) if 0x20 <= key <= 0x7E else None)
            if not name:
                service.HOTKEY.start()
                self.error("This key could not be captured. Choose another keyboard key.")
                return True
            prefix = [label for flag, label in ((Qt.KeyboardModifier.ControlModifier, "Ctrl"),
                                                 (Qt.KeyboardModifier.AltModifier, "Alt"),
                                                 (Qt.KeyboardModifier.ShiftModifier, "Shift"),
                                                 (Qt.KeyboardModifier.MetaModifier, "Win"))
                      if modifiers & flag]
            shortcut = "+".join(prefix + [name])
            try:
                if target == "default":
                    service.HOTKEY.configure(shortcut)
                else:
                    service.HOTKEY.configure_for(target, shortcut)
            except Exception as error:
                self.run(service.HOTKEY.start)
                self.refresh_hotkey()
                self.error(str(error))
                return True
            self.refresh_hotkey()
            assigned = (service.HOTKEY.status()["combo"] if target == "default" else
                        service.HOTKEY.status()["combos"].get(target))
            if assigned:
                self.note(f"{target.title()} scan shortcut: {assigned}", True)
            return True
        if source is getattr(self, "inventory_preview", None) and event.type() == QEvent.Type.MouseButtonPress:
            if self._inventory_capture is not None and self.inventory_preview.width() > 0:
                column = min(11, int(event.position().x() * 12 / self.inventory_preview.width()))
                row = min(4, int(event.position().y() * 5 / self.inventory_preview.height()))
                self.icon_slot.setValue(row * 12 + column + 1)
                return True
        return super().eventFilter(source, event)

    def change_mode(self):
        """Change remnant view mode, invalidate both-view linkage and redisplay any reading for
        the chosen view.
        """
        previous = self.mode
        self.mode = value(self.mode_select)
        if previous != self.mode:
            self._both_link = None
            self._both_approved = False
        self.run(lambda: service.HOTKEY.set_mode(self.mode))
        self.refresh_hotkey()
        self._show_image(self.images["opened" if self.mode == "both" else self.mode])
        result = self.results["opened" if self.mode == "both" else self.mode]
        self.review_group.setVisible(bool(result))
        if result:
            actual = result.get("mode", "opened" if self.mode == "both" else self.mode)
            self.show_result(actual, result, self.images[actual])

    def scan_file(self):
        """Validate an image file and submit a context-bound remnant scan with deferred ID
        assignment.
        """
        filename, _ = QFileDialog.getOpenFileName(self, "Test an image", str(Path.home()),
                                                  "Images (*.png *.jpg *.jpeg)")
        if not filename:
            return
        if Path(filename).stat().st_size > service.MAX_UPLOAD:
            raise ValueError("Screenshot is over 16 MB.")
        raw = Path(filename).read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        service._image(encoded)
        mode = self.mode
        context = logger.scan_context()
        sensitivity = ocr_sensitivity.saved_values()
        service.HOTKEY.cancel_capture(modes=("seed", "opened", "both"))
        capture = object()
        self._remnant_reading = capture
        self._review_pending("seed" if mode == "seed" else "remnant", "Reading remnant image…", False)
        self.current_file[mode] = Path(filename).name
        self.images[mode] = raw
        if mode == "seed":
            self.saved_scan = self._saved_scan_capture = None
        self._show_image(raw)
        set_message(self.scan_status, "Reading image…")
        self.scan_status.show()
        with logger._connect() as db:
            number = logger._meta(db, "current_map_number", 0)
            if not number or logger._meta(db, "pending_new_map", False):
                number += 1
            map_id = logger._map_id(number)
        self._submit_scan("Reading image…",
                     lambda: service.dispatch(f"/api/scan?mode={mode}",
                                              {"image": encoded, "map_id": map_id,
                                               "scan_generation": context["_scan_generation"],
                                               "scan_context": context, "defer_ocr_id": True,
                                               "ocr_strictness": sensitivity}),
                     lambda result: self._scan_done(mode, result, raw, capture),
                     "seed" if mode == "seed" else "remnant", capture)

    def _scan_done(self, mode, result, raw, capture=None):
        """Ignore superseded capture completions, show the returned remnant view and consider
        eligible auto-save.
        """
        if capture is not None and capture is not self._remnant_reading:
            return
        self._remnant_reading = None
        mode = result.get("mode", mode)
        self.show_result(mode, result, raw)
        if mode == "opened":
            self.maybe_auto_commit(result)
        elif mode == "seed":
            self.maybe_auto_commit_seeds()

    def _show_image(self, raw):
        """Display a scaled scan preview or hide it when bytes are absent or invalid."""
        self._arrange_review_preview()
        if not raw:
            self.preview.setPixmap(QPixmap())
            self.preview.hide()
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(raw):
            self.preview.setPixmap(pixmap.scaled(1100, self.preview.maximumHeight(), Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
            self.preview.show()
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.hide()

    def _arrange_review_preview(self):
        """Give inventory and Ritual approvals more space by compacting their preview and heading."""
        compact = self.review_kind.property("scanKind") in ("currency", "ritual")
        self.preview.setMinimumHeight(126 if compact else 180)
        self.preview.setMaximumHeight(182 if compact else 260)
        self._review_page_layout.setContentsMargins(12, 9, 12, 16)
        self._review_latest_layout.setSpacing(5 if compact else 6)
        self._review_latest_layout.parentWidget().setStyleSheet(
            "QGroupBox { margin-top:6px; padding-top:10px; }" if compact else "")
        picture = self.preview.pixmap()
        if compact and picture is not None and picture.height() > self.preview.maximumHeight():
            self.preview.setPixmap(picture.scaled(1100, self.preview.maximumHeight(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _show_review_capture(self, picture):
        """Encode a captured PIL image as PNG for the shared review preview."""
        output = io.BytesIO()
        picture.save(output, format="PNG")
        self._show_image(output.getvalue())

    def show_result(self, mode, result, raw=None):
        """Validate remnant ownership, obtain a logger ID when needed and populate the
        corresponding review draft.
        """
        logger.validate_remnant_context(result)
        # A delayed remnant worker may finish while the user is correcting an
        # independent propagation recipe. Update the held remnant without
        # discarding the newer selectors, their values or the displayed scan.
        independent = (self._held_remnant_review is not None and
                       self.review_kind.property("scanKind") == "propagation")
        propagation_summary = self.review_summary.text() if independent else None
        propagation_image = self.preview.pixmap() if independent and self.preview.isVisible() else None
        if not result.get("remnant_id") and (mode == "opened" or result.get("remnants") or result.get("sockets")):
            result.update(logger.assign_ocr_id(mode, result.get("_target_map_id"),
                                              result.get("_scan_generation"), result.get("_capture_expedition")))
        self._wheel_field = None
        self._clear_commit_badge()
        self.review_group.show()
        self.results[mode] = result
        self.images[mode] = raw or b""
        if mode == "seed":
            self.saved_scan = self._saved_scan_capture = None
        if mode == "opened":
            if self.mode != "both":
                self._clear_seed_queue()
            self.resolved = None
            for widget in (self.first_recipe, self.next_recipe):
                with QSignalBlocker(widget):
                    widget.clear()
            with QSignalBlocker(self.recipe_family):
                select(self.recipe_family, "")
            self.recipe_table.hide()
        if mode != self.mode and self.mode != "both":
            select(self.mode_select, mode)
            self.mode = mode
            service.HOTKEY.set_mode(mode)
        self._show_image(self.images[mode])
        self.opened_lines.clear()
        if mode == "opened":
            for item in result.get("opened_recipes", []):
                self.opened_lines.addItem(f"{item.get('raw', '')}  →  {item.get('recipe') or 'needs review'}")
        else:
            previous = self._seed_readings
            current_context = {key: result.get(key) for key in
                    ("_scan_generation", "_capture_map_id", "_capture_expedition", "_capture_map_pending")}
            self._seed_readings = [dict(item) for item in result.get("remnants", [result])
                                   if item.get("sockets")]
            if self.mode == "both" and self._both_seed_context == current_context:
                for reading in self._seed_readings:
                    same = next((old for old in previous if all(old.get(key) == reading.get(key)
                        for key in ("sockets", "seed_slot", "seed_rune", "bar_bounds"))), None)
                    if same:
                        for key in ("saved", "rejected", "approved"):
                            if key in same:
                                reading[key] = same[key]
            elif self.mode == "both":
                self._both_last_opened = None
            for item in self._seed_readings:
                if not item.get("family") and len(item.get("candidates") or []) == 1:
                    item["family"] = f"Family {item['candidates'][0]}"
            self._populate_seed_table()
            if self.mode == "both":
                self._both_seed_context = {key: result.get(key) for key in
                    ("_scan_generation", "_capture_map_id", "_capture_expedition", "_capture_map_pending")}
                self._both_link = None
                self._both_approved = False
        status = result.get("status", "No reading.")
        success = result.get("family") and result.get("sockets") and mode == "opened" and result.get("can_use")
        set_message(self.scan_status, status, "success" if success else "error")
        self.scan_status.show()
        if result.get("remnant_id"):
            self.stat_values[0].setText(result["map_id"])
            self._sync_header_ids(logger.get_state())
        if mode == "opened":
            found = result.get("opened_recipes") or []
            rows = [(f"Reward {i + 1}", item.get("recipe") or item.get("raw", ""),
                     "Matched" if item.get("recipe") else "Review")
                    for i, item in enumerate(found)]
            summary = f"{len(found)} opened rewards · {status}"
            self._review_pending("remnant", summary, bool(result.get("first_recipe")), rows,
                                 preserve_chain_draft=independent)
            if result.get("first_recipe"):
                self.use_opened(preserve_review=True)
                if self.resolved and self.resolved["status"] == "ready":
                    set_message(self.review_summary, f"{summary} Family {self.resolved['family']} · "
                                f"{len(self.resolved['rows'])} total recipes from the database.")
        else:
            self._review_pending("seed", f"{len(self._seed_readings)} visible remnants · "
                "Select a row, then Approve to save it or Reject to skip it.", False,
                [("Sockets", result.get("sockets") or "?", ""),
                 ("Visible slot", result.get("seed_slot") or "?", ""),
                 ("Rune", result.get("seed_rune") or "?", ""),
                 ("Family", result.get("family") or "Unresolved", "Review")],
                preserve_chain_draft=independent)
            self._seed_commit_enabled()
        if self.mode == "both" and mode == "opened":
            self._link_opened_to_seed(result)
        if independent:
            self._show_independent_propagation_review(propagation_summary)
            if propagation_image is not None:
                self.preview.setPixmap(propagation_image)
                self.preview.show()
            self._set_chain_review_active(True)
        self.refresh_hotkey()

    def _populate_seed_table(self, selected_row=None):
        """Rebuild visible-seed rows with locked saved/rejected entries and select a waiting
        reading.
        """
        self._seed_loading = True
        with QSignalBlocker(self.seed_table):
            self.seed_table.setRowCount(len(self._seed_readings))
            for row, reading in enumerate(self._seed_readings):
                use = QTableWidgetItem()
                use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable |
                             Qt.ItemFlag.ItemIsSelectable)
                use.setCheckState(Qt.CheckState.Checked if reading.get("can_commit") and
                                  not reading.get("saved") else Qt.CheckState.Unchecked)
                if reading.get("saved") or reading.get("rejected"):
                    use.setFlags(Qt.ItemFlag.NoItemFlags)
                self.seed_table.setItem(row, 0, use)
                self._update_seed_row(row)
            if self._seed_readings:
                row = selected_row if selected_row is not None else next(
                    (i for i, r in enumerate(self._seed_readings) if not r.get("saved") and not r.get("rejected")), 0)
                self.seed_table.setCurrentCell(row, 2)
        self._seed_loading = False
        self._select_seed(self.seed_table.currentRow())

    def _update_seed_row(self, row):
        """Display a seed reading's identity, family and saved/rejected/approval status without
        change callbacks.
        """
        reading = self._seed_readings[row]
        saved = reading.get("saved")
        texts = (saved["remnant_id"] if saved else str(reading.get("scan_index", row+1)),
                 f"{reading.get('sockets', '?')} sockets · {reading.get('seed_slot', '?')} "
                 f"{reading.get('seed_rune', '?')}", reading.get("family") or "Unresolved",
                 "Saved" if saved else "Rejected" if reading.get("rejected") else
                 "Approved" if reading.get("approved") else "Matched" if reading.get("can_commit") else "Review")
        with QSignalBlocker(self.seed_table):
            for column, text in enumerate(texts, 1):
                self.seed_table.setItem(row, column, QTableWidgetItem(text))

    def _select_seed(self, row):
        """Load the selected seed draft into correction fields and relink both-view review when
        appropriate.
        """
        if self._seed_loading or not 0 <= row < len(self._seed_readings):
            return
        self._seed_loading = True
        reading = self._seed_readings[row]
        if self._saved_scan_capture and self._saved_scan_capture["reading"] is not reading:
            self.saved_scan = self._saved_scan_capture = None
        for widget, key in ((self.seed_sockets, "sockets"), (self.seed_slot, "seed_slot"),
                            (self.seed_rune, "seed_rune")):
            widget.setText(str(reading.get(key) or ""))
        self.update_seed_candidates(reading.get("family"))
        self.seed_fields_widget.setEnabled(not reading.get("saved"))
        self._seed_loading = False
        if self.mode == "both" and self.pending_review_kind == "remnant":
            self._link_opened_to_seed(self.results["opened"], chosen=row)
        self._seed_commit_enabled()

    def _seed_fields_changed(self, family_only=False):
        """Refresh corrected seed candidates and invalidate incompatible both-view approval.
        """
        row = self.seed_table.currentRow()
        if self._seed_loading or not 0 <= row < len(self._seed_readings):
            return
        reading = self._seed_readings[row]
        if reading.get("saved"):
            return
        self.saved_scan = self._saved_scan_capture = None
        if not family_only:
            try:
                reading["sockets"] = int(value(self.seed_sockets))
            except ValueError:
                reading["sockets"] = None
            reading["seed_slot"] = value(self.seed_slot)
            reading["seed_rune"] = value(self.seed_rune)
            self._seed_loading = True
            candidates = self.update_seed_candidates()
            reading["candidates"] = [candidate["family"] for candidate in candidates]
            self._seed_loading = False
        family = value(self.seed_family)
        reading["family"] = f"Family {family}" if family else None
        reading["can_commit"] = False
        self._update_seed_row(row)
        if family_only and family:
            self.seed_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self._seed_commit_enabled()
        if self.mode == "both" and self.pending_review_kind == "remnant":
            self._link_opened_to_seed(self.results["opened"], chosen=row)

    def _seed_commit_enabled(self):
        """Enable approval for a waiting selected seed with a family and summarize queue
        progress.
        """
        if self._seed_loading or self.pending_review_kind != "seed":
            return
        row = self.seed_table.currentRow()
        reading = self._seed_readings[row] if 0 <= row < len(self._seed_readings) else {}
        self.approve_scan_button.setEnabled(bool(reading.get("family")) and
                                            not reading.get("saved") and not reading.get("rejected"))
        pending = sum(not r.get("saved") and not r.get("rejected") for r in self._seed_readings)
        set_message(self.review_summary, f"{len(self._seed_readings)} visible remnants · "
                    f"{sum(bool(r.get('saved')) for r in self._seed_readings)} saved · {pending} waiting. " +
                    ("Open a remnant and scan again to confirm it once." if self.mode == "both" else
                     "Select a row, then Approve to save it or Reject to skip it."))

    def maybe_auto_commit_seeds(self):
        """Auto-save a wholly clear visible-seed queue only outside the both-view confirmation
        mode.
        """
        if self._manual_propagation_context and self.review_kind.property("scanKind") == "propagation":
            # A delayed seed completion must leave a newer correction visible
            # until its owner accepts or abandons it.
            return
        pending = [r for r in self._seed_readings if not r.get("saved") and not r.get("rejected")]
        if (self.pending_review_kind == "seed" and self.mode != "both" and pending and
                self.state["settings"].get("auto_commit") and
                all(r.get("can_commit") for r in pending)):
            self.commit_seed_review(automatic=True)

    def commit_seed_review(self, automatic=False, indices=None):
        """Commit checked seed selections as a batch, retain unsaved rows and learn manually
        approved references.
        """
        result = self.results["seed"]
        if not result:
            raise ValueError("Scan visible remnants first.")
        if self.mode == "both":
            raise ValueError("Open and scan the remnant to confirm its seed before committing.")
        selections = []
        for index, reading in enumerate(self._seed_readings):
            if indices is not None and index not in indices:
                continue
            if reading.get("saved") or reading.get("rejected") or self.seed_table.item(index, 0).checkState() != Qt.CheckState.Checked:
                continue
            family = str(reading.get("family") or "").replace("Family ", "")
            selections.append({"index": index, "family": family,
                               **{key: reading.get(key) for key in ("sockets", "seed_slot", "seed_rune")}})
        payload = {**result, "remnants": self._seed_readings}
        saved = logger.commit_seed_batch(payload, selections, automatic=automatic)
        training_errors = []
        for entry in saved["saved"]:
            reading = self._seed_readings[entry["index"]]
            reading["saved"] = entry
            if not automatic:
                try:
                    self._learn_approved_seed(reading, entry["remnant_id"])
                except (ValueError, OSError) as error:
                    training_errors.append(str(error))
        remaining = any(not reading.get("saved") and not reading.get("rejected") for reading in self._seed_readings)
        result["remnants"] = self._seed_readings
        if remaining:
            result.update(saved["pending"])
            result.update(saved["context"])
        else:
            self._review_saved("seed", saved["scan_commit_number"])
            self.results["seed"] = None
        self._populate_seed_table(selected_row=next(iter(indices)) if indices is not None else None)
        self._seed_commit_enabled()
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        ids = ", ".join(entry["remnant_id"] for entry in saved["saved"])
        self.note(f"{ids} saved to {saved['saved'][0]['map_id']}." +
                  (" Seed reference could not be saved: " + training_errors[0] if training_errors else ""),
                  not training_errors)

    def _learn_approved_seed(self, reading, remnant_id):
        """Save a crop of an approved seed as a local reference and link it to the committed
        Remnant ID.
        """
        raw, bounds = self.images.get("seed"), reading.get("bar_bounds")
        if not raw or not bounds:
            return
        with Image.open(io.BytesIO(raw)) as source:
            crop = source.crop((max(0, bounds["x"] - 35), max(0, bounds["y"] - 10),
                                min(source.width, bounds["x"] + bounds["width"] + 35),
                                min(source.height, bounds["y"] + bounds["height"] + 10))).convert("RGB")
        image = io.BytesIO()
        crop.save(image, format="PNG")
        reference = store.save_scan(image.getvalue(), "reviewed-seed.png", reading["sockets"],
                                    reading["seed_slot"], reading["seed_rune"],
                                    str(reading["family"]).replace("Family ", ""))
        with logger._connect() as db:
            db.execute("INSERT OR REPLACE INTO scan_links VALUES(?,?)", (remnant_id, reference["id"]))

    @staticmethod
    def _opened_identity(opened):
        """Fingerprint an opened reading by family, sockets and ordered observed rewards for
        duplicate checks.
        """
        return (opened.get("family"), opened.get("sockets"),
                tuple(line.get("recipe") or line.get("raw") for line in opened.get("opened_recipes", [])))

    def _link_opened_to_seed(self, opened, chosen=None):
        """Match an opened reading to a waiting seed in the same captured context and gate
        both-view approval.
        """
        self._both_link = None
        self._both_approved = False
        if not opened or not self.resolved or self.resolved.get("status") != "ready":
            self.approve_scan_button.setEnabled(False)
            return
        opened_context = {key: opened.get(key) for key in
                          ("_scan_generation", "_capture_map_id", "_capture_expedition", "_capture_map_pending")}
        try:
            logger.validate_remnant_context(self._both_seed_context)
            matching_context = self._both_seed_context == opened_context
        except ValueError:
            matching_context = False
        if not matching_context:
            self.approve_scan_button.setEnabled(False)
            set_message(self.review_summary, "Scan the visible seeds for this map and expedition first.", "error")
            return
        family = self.resolved["family"]
        sockets = opened.get("sockets") or self.resolved["rows"][0]["sockets"]
        matches = []
        for index, reading in enumerate(self._seed_readings):
            if (reading.get("saved") or reading.get("rejected") or
                    reading.get("sockets") != sockets or family not in (reading.get("candidates") or [])):
                continue
            try:
                store.validate(reading.get("sockets"), reading.get("seed_slot"),
                               reading.get("seed_rune"), family)
            except ValueError:
                continue
            matches.append(index)
        if chosen in matches:
            matches = [chosen]
        if len(matches) != 1:
            self.approve_scan_button.setEnabled(bool(matches))
            set_message(self.review_summary, "Select the matching visible remnant, then Approve." if matches else
                        "The opened remnant does not match a waiting visible seed.", "error")
            return
        self._both_link = matches[0]
        seed = self._seed_readings[self._both_link]
        duplicate = (self._opened_identity(opened) == self._both_last_opened or
                     logger.seed_previously_logged(opened.get("map_id"), opened.get("expedition_id"),
                                                   family, sockets))
        self._both_approved = bool(seed.get("can_commit") and not duplicate)
        self.approve_scan_button.setEnabled(True)
        set_message(self.review_summary, f"Visible remnant {seed.get('scan_index', self._both_link+1)} "
                    f"matches Family {family}. " + ("Repeated opened reading: Approve only after moving to "
                    "this different remnant." if duplicate else "Both views will create one Remnant ID."))
        self.seed_table.setVisible(True)

    def approve_remnant_scan(self):
        """Approve the selected seed or resolved opened reading; both-view mode saves one
        linked remnant.
        """
        if self.pending_review_kind == "seed":
            row = self.seed_table.currentRow()
            if not 0 <= row < len(self._seed_readings):
                raise ValueError("Select a visible remnant first.")
            reading = self._seed_readings[row]
            if reading.get("saved") or reading.get("rejected"):
                raise ValueError("Select a waiting remnant.")
            store.validate(reading.get("sockets"), reading.get("seed_slot"),
                           reading.get("seed_rune"), str(reading.get("family") or "").replace("Family ", ""))
            reading["approved"] = True
            self.seed_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            self._update_seed_row(row)
            self._seed_commit_enabled()
            if self.mode != "both":
                return self.commit_seed_review(indices={row})
            set_message(self.review_summary, "Seed approved. Scan its opened remnant to save both readings once.", "success")
            return
        if self.pending_review_kind != "remnant":
            raise ValueError("Select a remnant reading to approve.")
        if not self.resolved or self.resolved.get("status") != "ready":
            if value(self.first_recipe):
                self.resolve_recipe()
            else:
                self.use_opened(preserve_review=True)
        if self.mode == "both":
            self._link_opened_to_seed(self.results["opened"], chosen=self.seed_table.currentRow())
            if self._both_link is None:
                raise ValueError("Choose a waiting seed that matches this opened remnant.")
            self._both_approved = True
        return self.commit_remnant()

    def reject_remnant_scan(self):
        """Reject a waiting seed or opened reading and release its pending OCR ID when review
        is exhausted.
        """
        self._remnant_reading = None
        service.HOTKEY.cancel_capture(modes=("seed", "opened", "both"))
        if self.pending_review_kind == "seed":
            if not self._seed_readings:
                logger.discard_ocr_id()
                self.results["seed"] = None
                self.pending_review_kind = None
                self.approve_scan_button.setEnabled(False)
                self._review_controls(None)
                set_message(self.review_summary, "Scan rejected; no remnant was saved.")
                if self._overlay_auto_review:
                    self.hide_overlay()
                return
            row = self.seed_table.currentRow()
            if not 0 <= row < len(self._seed_readings):
                raise ValueError("Select a visible remnant first.")
            reading = self._seed_readings[row]
            if reading.get("saved"):
                raise ValueError("A saved remnant cannot be rejected here.")
            reading["rejected"] = True
            if self._saved_scan_capture and self._saved_scan_capture["reading"] is reading:
                self.saved_scan = self._saved_scan_capture = None
            use = self.seed_table.item(row, 0)
            use.setCheckState(Qt.CheckState.Unchecked)
            use.setFlags(Qt.ItemFlag.NoItemFlags)
            self._update_seed_row(row)
            self._seed_commit_enabled()
            if not any(not item.get("saved") and not item.get("rejected") for item in self._seed_readings):
                logger.discard_ocr_id()
                self.pending_review_kind = None
                self._review_controls(None)
                self.approve_scan_button.setEnabled(False)
                set_message(self.review_summary, "Remaining visible readings rejected; no new entries were saved.")
                if self._overlay_auto_review:
                    self.hide_overlay()
            return
        if self.pending_review_kind == "remnant":
            logger.discard_ocr_id()
            self._both_link = None
            self._both_approved = False
            self.results["opened"] = None
            self.resolved = None
            self.pending_review_kind = None
            self.approve_scan_button.setEnabled(False)
            self._review_controls(None)
            self.recipe_table.hide()
            set_message(self.review_summary, "Opened remnant rejected; no log entry was created.")
            if self._overlay_auto_review:
                self.hide_overlay()
            return
        raise ValueError("There is no remnant reading to reject.")

    def _clear_seed_queue(self):
        """Clear visible-seed rows and all transient both-view match and duplicate-tracking
        state.
        """
        self._seed_readings = []
        self._both_seed_context = None
        self._both_link = None
        self._both_approved = False
        self._both_last_opened = None
        self.seed_table.setRowCount(0)
        self.seed_table.hide()

    def update_seed_candidates(self, preferred=None):
        """Return exact seed candidates, populate family choices and show selected rewards.
        """
        try:
            candidates = store.candidates(int(value(self.seed_sockets)), value(self.seed_slot),
                                          value(self.seed_rune))
        except (ValueError, TypeError):
            candidates = []
        with QSignalBlocker(self.seed_family):
            self.seed_family.clear()
            self.seed_family.addItem("Unresolved", "")
            for item in candidates:
                self.seed_family.addItem(f"Family {item['family']}", item["family"])
        matched = str(preferred or "").replace("Family ", "")
        if matched.isdigit():
            select(self.seed_family, int(matched))
        elif len(candidates) == 1:
            select(self.seed_family, candidates[0]["family"])
        family = value(self.seed_family)
        selected = next((c for c in candidates if c["family"] == family), None)
        self.seed_rewards.setText(" · ".join(selected["rewards"]) if selected else "")
        return candidates

    def maybe_auto_commit(self, opened):
        """Attempt configured remnant auto-save, requiring a linked approved seed in both-view
        mode.
        """
        if self._manual_propagation_context and self.review_kind.property("scanKind") == "propagation":
            # Do not replace an independently edited propagation review with
            # automatic remnant-save feedback from an older worker.
            return
        if self.mode == "both":
            from PoE2_Data_Logger.core.auto_commit import candidate
            if (self.state["settings"].get("auto_commit") and self._both_link is not None and
                    self._both_approved and candidate(opened)["ready"]):
                self.commit_remnant()
            return
        if not self.state["settings"].get("auto_commit"):
            return
        saved = service.dispatch("/api/remnants/auto",
                                 {"opened": opened, "seed": self.results["seed"] if
                                  len((self.results["seed"] or {}).get("remnants", [])) <= 1 else None,
                                  "scan_id": self.saved_scan["id"] if self.saved_scan else None})
        if saved.get("committed"):
            self._saved_remnant(saved)
        else:
            set_message(self.scan_status, saved.get("reason", "Review before logging."), "error")

    def use_opened(self, preserve_review=False):
        """Copy observed opened rewards and family into recipe inputs, then resolve the
        database sequence.
        """
        result = self.results["opened"]
        if not result or not result.get("first_recipe"):
            raise ValueError("Enter or correct First Recipe before committing.")
        self.first_recipe.setText(result["first_recipe"])
        self.next_recipe.setText(result.get("next_recipe") or "")
        family = str(result.get("family") or "").replace("Family ", "")
        if not family and len(result.get("candidates") or []) == 1:
            family = str(result["candidates"][0])
        select(self.recipe_family, int(family) if family.isdigit() else "")
        self.resolve_recipe(preserve_review=preserve_review)

    def save_seed_scan(self):
        """Submit a selected seed screenshot/reference save while retaining its image,
        selection and capture context.
        """
        raw = self.images["seed"]
        if not raw:
            raise ValueError("Scan a visible seed first.")
        row = self.seed_table.currentRow()
        source_context = self.results.get("seed") or {}
        context = ({key: source_context.get(key) for key in
                    ("_scan_generation", "_capture_map_id", "_capture_expedition", "_capture_map_pending")}
                   if "_scan_generation" in source_context else logger.scan_context())
        capture = {"image": raw, "context": context,
                   "reading": self._seed_readings[row] if 0 <= row < len(self._seed_readings) else None,
                   "selection": tuple(value(widget) for widget in
                                      (self.seed_sockets, self.seed_slot, self.seed_rune, self.seed_family))}
        if 0 <= row < len(self._seed_readings):
            bounds = self._seed_readings[row].get("bar_bounds")
            if bounds:
                with Image.open(io.BytesIO(raw)) as source:
                    box = (max(0, bounds["x"]-35), max(0, bounds["y"]-60),
                           min(source.width, bounds["x"]+bounds["width"]+20),
                           min(source.height, bounds["y"]+bounds["height"]+60))
                    crop = source.crop(box).convert("RGB")
                memory = io.BytesIO()
                crop.save(memory, format="PNG")
                raw = memory.getvalue()
        data = {"image": base64.b64encode(raw).decode("ascii"),
                "filename": self.current_file["seed"] or "live-seed.jpg",
                "sockets": value(self.seed_sockets), "slot": value(self.seed_slot),
                "rune": value(self.seed_rune), "family": value(self.seed_family)}
        self._submit("Saving reviewed reference…",
                     lambda: service.dispatch("/api/save-scan", data),
                     lambda result: self._seed_saved(result, capture))

    def _seed_saved(self, result, capture=None):
        """Associate a saved reference with review only if the original image, reading, inputs
        and context still match.
        """
        associated = capture is None
        if capture is not None and self.images["seed"] is capture["image"]:
            row = self.seed_table.currentRow()
            reading = self._seed_readings[row] if 0 <= row < len(self._seed_readings) else None
            try:
                logger.validate_remnant_context(capture["context"])
                valid_context = True
            except ValueError:
                valid_context = False
            associated = (valid_context and
                          (not self._seed_readings or reading is capture["reading"]) and
                          capture["selection"] == tuple(value(widget) for widget in
                              (self.seed_sockets, self.seed_slot, self.seed_rune, self.seed_family)) and
                          not (capture["reading"] or {}).get("rejected"))
        if associated:
            self.saved_scan = result
            self._saved_scan_capture = capture
        self.refresh()
        self.note(f"Reviewed scan #{result['id']} saved locally.", True)

    def discard_scan(self):
        """Cancel remnant capture, release its pending logger ID and clear both remnant-view
        drafts.
        """
        service.HOTKEY.cancel_capture(modes=("seed", "opened", "both"))
        self._remnant_reading = None
        logger.discard_ocr_id()
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = self._saved_scan_capture = None
        self._show_image(None)
        self.review_group.hide()
        if self.pending_review_kind in ("remnant", "seed"):
            self.pending_review_kind = None
            self._failed_review = False
            self._review_controls(None)
            self.review_kind.setText("Nothing waiting for review")
            set_message(self.review_summary, "Pending remnant scan discarded.")
            self.approve_scan_button.setEnabled(False)
        self.refresh()
        self.note("Pending OCR scan discarded.")

    def _invalidate_resolution(self):
        """Invalidate recipe resolution when its inputs change, preserving a saved display
        until a new edit begins.
        """
        self.resolved = None
        if self.recipe_table.property("savedRemnant"):
            if (self.pending_review_kind != "remnant" and
                    not value(self.first_recipe) and not value(self.next_recipe)):
                return
            self.recipe_table.setProperty("savedRemnant", None)
            self.recipe_table.setRowCount(0)
        if self.pending_review_kind == "remnant":
            self.approve_scan_button.setEnabled(bool(value(self.first_recipe) or
                (self.results["opened"] and self.results["opened"].get("first_recipe"))))
        self.recipe_table.hide()

    def _render_recipe_rows(self, rows, saved=None):
        """Render read-only database recipe rows and optionally mark them as belonging to a
        saved remnant.
        """
        self.recipe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_table.setProperty("savedRemnant", saved)
        self.recipe_table.setRowCount(len(rows))
        self.recipe_table.setVisible(bool(rows))
        for row, item in enumerate(rows):
            for col, key in enumerate(("recipe", "sockets", "combo")):
                self.recipe_table.setItem(row, col, QTableWidgetItem(str(item[key])))

    def start_manual_remnant_review(self):
        """Restore a held remnant while abandoning unaccepted propagation choices."""
        held = self._held_remnant_review
        if held and self.pending_review_kind == held[0]:
            kind, summary, can_commit, failed = held
            # Leaving an unaccepted propagation reading abandons its choices,
            # not the accepted chain parts. Hidden choices must not prevent
            # completing the chain from Expedition.
            self._clear_manual_propagation()
            self._review_pending(kind, summary, can_commit, preserve_chain_draft=True)
            self._failed_review = failed
            self._show_image(self.images["opened" if kind == "remnant" else "seed"])
            return
        logger.assign_ocr_id("opened")
        self._review_pending("remnant", "Enter and resolve the observed recipe below.", False)
        self._sync_header_ids(logger.get_state())

    def resolve_recipe(self, preserve_review=False):
        """Display matching database recipe families without claiming unverified visible recipes."""
        result = logger.resolve(value(self.first_recipe), value(self.next_recipe),
                                value(self.recipe_family))
        self.resolved = result
        self._render_recipe_rows(result["rows"])
        if result["status"] == "ready":
            # Manual recipe entry has the same retained expedition binding as
            # an OCR reading, including while a propagation chain advances.
            if not logger.get_state()["ocr_pending"]:
                logger.assign_ocr_id("opened")
            rows = [(f"Recipe {i + 1}", item["recipe"], f"{item['sockets']} sockets · {item['combo']}")
                    for i, item in enumerate(result["rows"])]
            if preserve_review and self.pending_review_kind == "remnant":
                self.approve_scan_button.setEnabled(True)
            else:
                self._review_pending("remnant", f"Family {result['family']} resolved · {len(rows)} database recipe rows.",
                                     True, rows)
        elif self.pending_review_kind == "remnant":
            self.approve_scan_button.setEnabled(False)
        if result["status"] == "ready":
            set_message(self.recipe_status,
                        f"Family {result['family']} · {len(result['rows'])} matching database recipe rows.", "success")
        elif result["status"] == "ambiguous":
            set_message(self.recipe_status,
                        f"Multiple families: {', '.join(map(str, result['candidates']))}. "
                        "Choose one or enter Next Recipe.", "error")
        else:
            set_message(self.recipe_status, "No matching family sequence.", "error")

    def commit_remnant(self):
        """Commit a resolved remnant against captured context, validating a single linked seed
        in both-view mode.
        """
        if self._failed_review and self.pending_review_kind in ("remnant", "seed"):
            raise ValueError("This scan failed. Scan again or reject this reading before saving.")
        if self._remnant_reading is not None:
            raise ValueError("Wait for the remnant scan to finish before saving.")
        if not self.resolved or self.resolved["status"] != "ready":
            raise ValueError("Resolve the recipe first.")
        both_reading = None
        if self.mode == "both":
            approved, linked = self._both_approved, self._both_link
            self._link_opened_to_seed(self.results["opened"], chosen=linked)
            if linked != self._both_link:
                approved = False
            self._both_approved = approved
            if self._both_link is None or not self._both_approved:
                raise ValueError("Approve the matching seed and opened remnant before committing.")
            both_reading = self._seed_readings[self._both_link]
            if both_reading.get("saved") or both_reading.get("rejected"):
                raise ValueError("This remnant was already saved or rejected.")
            logger.validate_remnant_context(self.results["opened"])
        saved = logger.commit_remnant(value(self.first_recipe), value(self.next_recipe),
                                      value(self.recipe_family) or self.resolved["family"],
                                      self.saved_scan["id"] if self.saved_scan else None,
                                      visible_seed={"sockets": both_reading["sockets"],
                                          "slot": both_reading["seed_slot"], "rune": both_reading["seed_rune"],
                                          "mode": "both"} if both_reading is not None else None,
                                      expected_context=self.results.get("opened"),
                                      seed_context=self._both_seed_context if both_reading is not None else None)
        if both_reading is not None:
            both_reading["saved"] = saved
            self._both_last_opened = self._opened_identity(self.results["opened"])
        self._saved_remnant(saved)
        if both_reading is not None:
            self._both_seed_context = {**logger.scan_context(), "_capture_expedition":
                                       int(saved["expedition_id"].rsplit("-E", 1)[1])}
            self._populate_seed_table()

    def _saved_remnant(self, saved):
        """Replace the pending draft with recipe rows from the saved commit audit and refresh
        logger-owned IDs.
        """
        with logger._connect() as db:
            commit = db.execute("SELECT details_json FROM commits WHERE number=? AND reference=?",
                                (saved["scan_commit_number"], saved["remnant_id"])).fetchone()
        rows = logger._load(commit[0])["recipes"] if commit else []
        self._review_saved("remnant", saved["scan_commit_number"])
        self._discard_chain_review_draft()
        self.first_recipe.clear()
        self.next_recipe.clear()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = self._saved_scan_capture = None
        self.resolved = None
        self._show_image(None)
        self.review_group.hide()
        self.opened_lines.clear()
        self.approve_scan_button.setEnabled(False)
        self.refresh()
        self._render_recipe_rows(rows, saved["remnant_id"])
        self.remnant_log_group.show()
        set_message(self.recipe_status, f"{saved['map_id']} · {saved['expedition_id']} · "
                    f"{saved['remnant_id']} · Family {saved['family']} · Saved recipes", "success")
        self._set_commit_badge(saved["scan_commit_number"])
        set_message(self.scan_status, f"{saved['remnant_id']} · Family {saved['family']} · "
                    f"{saved['recipes']} recipe rows saved to Export.", "success")
        self.scan_status.show()
        self.note(f"{saved['remnant_id']} logged to {saved['map_id']}.", True)

    def poll(self):
        """Consume new hotkey events on the GUI thread, route activity captures and maintain
        HUD restoration availability.
        """
        self._set_ocr_indicator()
        sequence = service.HOTKEY.status().get("overlay_sequence", 0)
        if sequence != self._latest_overlay:
            presses = sequence - self._latest_overlay
            self._latest_overlay = sequence
            if presses % 2:
                self.toggle_overlay()
        if self._overlay_auto_review and not self.pending_review_kind:
            self.hide_overlay()
        hotkey = service.HOTKEY.status()
        if (self._overlay_enabled and not self._closed and not self.isVisible() and
                not (hotkey["supported"] and hotkey["registered"] and
                     hotkey["combos"].get("overlay"))):
            # A listener can stop after the HUD has already hidden. Keep a
            # taskbar entry available even when its restoration key is lost.
            self.showMinimized()
            self.statusBar().showMessage(
                "HUD shortcut unavailable. Restore the logger from the taskbar to set it again.", 8000)
        event = hotkey["latest"]
        if event and event["id"] != self._latest_hotkey:
            self._latest_hotkey = event["id"]
            if not event["error"]:
                self._overlay_capture_restore = None
            if event["error"]:
                self._restore_overlay_capture_failure()
                self.error(event["error"])
            elif event["mode"] == "item":
                self.run(lambda: self._hover_item_read(event["result"], service.HOTKEY.image(event["id"])))
            elif event["mode"] == "propagation":
                self.run(lambda: self._propagation_read(event["result"], service.HOTKEY.image(event["id"])))
            elif event["mode"] in ("currency", "ritual"):
                raw = service.HOTKEY.image(event["id"])
                if raw:
                    try:
                        logger.validate_scan_context(event["result"])
                    except ValueError as error:
                        self.error(str(error))
                        return
                    with Image.open(io.BytesIO(raw)) as source:
                        picture = source.convert("RGB")
                    self.tabs.setCurrentIndex(0)
                    if event["mode"] == "currency":
                        self.run(lambda: self._inventory_captured(picture, expected_map_id=event["result"].get("map_id"),
                                                                 expected_phase=event["result"].get("phase"),
                                                                 expected_strictness=(event["result"].get("_ocr_strictness_values") or {}).get("currency")))
                    else:
                        self.run(lambda: self._ritual_captured(picture, expected_map_id=event["result"].get("map_id"),
                                                              expected_strictness=(event["result"].get("_ocr_strictness_values") or {}).get("ritual")))
            else:
                self._remnant_reading = None
                self.run(lambda: self.show_result(event["mode"], event["result"], service.HOTKEY.image(event["id"])))
                if event["mode"] == "opened":
                    self.run(lambda: self.maybe_auto_commit(event["result"]))
                elif event["mode"] == "seed":
                    self.run(self.maybe_auto_commit_seeds)

    def _hover_item_read(self, result, raw=None):
        """Route item readings to review or auto-save with honest map-snapshot feedback."""
        logger.validate_scan_context(result)
        self._require_remnant_review_finished()
        self.tabs.setCurrentIndex(2)
        if raw:
            self._show_image(raw)
        else:
            self._show_image(None)
        if result["kind"] == "tablet":
            number = int(value(self.tablet_scan_number))
            saved = self._tablet_read(number, result)
            self.note(f"Tablet {saved} saved." if saved else
                      f"Tablet {number}: review the modifiers, then Approve.", True)
            return
        fields = result["fields"]
        learned = new_affix_names(result.get("mods", []), self.state["affixes"],
                                  result.get("ocr_rows", []) if result.get("source") == "screen OCR" else None)
        if learned:
            added = logger.add_affixes(learned)
            self.state["affixes"].extend(added)
            for widget in self.tablet_affixes:
                for name in added:
                    widget.addItem(name, name)
        with QSignalBlocker(self.tier):
            unknown_tier = self.tier.findData("")
            if fields["tier"] in (15, 16):
                if unknown_tier >= 0:
                    self.tier.removeItem(unknown_tier)
                select(self.tier, fields["tier"])
            else:
                label = "Confirm tier…" if fields["tier"] is None else f"Review T{fields['tier']}…"
                if unknown_tier < 0:
                    self.tier.insertItem(0, label, "")
                    unknown_tier = 0
                else:
                    self.tier.setItemText(unknown_tier, label)
                self.tier.setCurrentIndex(unknown_tier)
        for key, widget in (("waystone", self.waystone), ("map_mods", self.map_mods),
                            ("item_rarity", self.item_rarity),
                            ("monster_rarity", self.monster_rarity),
                            ("pack_size", self.pack_size),
                            ("effectiveness", self.effectiveness)):
            widget.setText("" if fields[key] is None else str(fields[key]))
        self.waystone_name.setText(result["name"])
        for field, mod in zip(self.waystone_mod_fields, list(result["mods"][:10]) + [""] * 10):
            field.setText(mod)
        self._extra_waystone_mods = list(result["mods"][10:])
        self.waystone_name.setVisible(bool(result["name"]))
        self.update_area()
        missing = [label for key, label in (("tier", "Tier"), ("waystone", "Waystone %"),
                                           ("map_mods", "Map Mods")) if fields[key] is None or
                   key == "tier" and fields[key] not in (15, 16)]
        set_message(self.map_scan_status,
                    f"Read {len(result['mods'])} waystone affixes. "
                    + (f"{len(self._extra_waystone_mods)} beyond the ten visible slots remain in the full Map Log text. "
                       if self._extra_waystone_mods else "")
                    + ("Check " + ", ".join(missing) + ". " if missing else "")
                    + "Review the fields, then Approve.",
                    "message" if missing else "success")
        rows = [("Tier", f"T{fields.get('tier') or '?'}", ""),
                ("Waystone %", fields.get("waystone") if fields.get("waystone") is not None else "?", ""),
                ("Map Mods", fields.get("map_mods") if fields.get("map_mods") is not None else "?", "")]
        rows += [(f"Affix {i + 1}", mod, "") for i, mod in enumerate(result.get("mods", []))]
        self._review_pending("waystone",
                             f"{result.get('name') or 'Waystone'} · T{fields.get('tier') or '?'} · "
                             f"{fields.get('waystone') if fields.get('waystone') is not None else '?'}% · "
                             f"{len(result.get('mods', []))} modifiers", fields["tier"] in (15, 16), rows)
        if self.state["settings"].get("ocr_auto_commit"):
            if clear_waystone_read(result):
                number = self.save_map_settings()
                notice = self._map_setup_save_notice()
                set_message(self.map_scan_status, notice or
                            f"✓ Waystone scan saved as Scan Commit #{number}.",
                            "message" if notice else "success")
            else:
                set_message(self.map_scan_status,
                            "Auto-commit held: review the waystone fields, then Approve.")

    def choose_folder(self):
        """Choose a local export directory and place the accepted path in the folder field."""
        folder = QFileDialog.getExistingDirectory(self, "Choose local export folder",
                                                  value(self.export_folder) or str(Path.home()))
        if folder:
            self.export_folder.setText(folder)

    def _scan_region(self, key):
        """Read a saved scan region from database metadata, returning None when it is unset."""
        with logger._connect() as db:
            return logger._meta(db, key, None)

    def _regions_saved(self):
        """Refresh the auxiliary region labels after calibration settings are saved."""
        self.refresh_aux_regions()
        self.note("Scan regions saved.", True)

    def select_region_in_game(self, key):
        """Calibrate one scan region against the current native game capture."""
        if sys.platform != "win32":
            self.error("In-game region selection is available on Windows.")
            return
        if self._region_selection_pending:
            return
        self._region_selection_pending = True
        self._region_selection_token += 1
        selection_token = self._region_selection_token
        self._overlay_auto_review = False
        self._overlay_review_token += 1
        was_maximized = self.isMaximized()
        self.note("Switch to the game. Region selection opens in 2 seconds.")
        self.showMinimized()
        def select_now():
            """Validate the current resolution draft before capturing and opening the native region picker."""
            if self._closed or selection_token != self._region_selection_token:
                return
            try:
                from PIL import ImageGrab
                from PoE2_Data_Logger.platform.hover_copy import _tooltip_bounds
                from PoE2_Data_Logger.ui.region_select import region_for, REGIONS
                from PoE2_Data_Logger.platform.live_watch import game_foreground
                if not game_foreground():
                    raise ValueError("Switch to Path of Exile 2, then select the region again.")
                bounds = _tooltip_bounds()
                left, top, right, bottom = bounds
                selected = region_for(key, bounds, for_selection=True,
                                      resolution=self.scan_regions.game_resolution.currentData())
                image = ImageGrab.grab(bbox=bounds, all_screens=True).convert("RGB")
                editor = RegionEditor(selected, parent=self, screenshot=image,
                                      screen_bounds=(left, top, right - left, bottom - top))
                self._region_editor = editor
                if editor.exec() == QDialog.DialogCode.Accepted:
                    region = editor.region()
                    self.scan_regions.calibrate_region(key, [(region["x"] - left) / (right - left),
                                                             (region["y"] - top) / (bottom - top),
                                                             region["w"] / (right - left), region["h"] / (bottom - top)])
                    group = REGIONS[key][1]
                    self.scan_regions.custom.mkdir(parents=True, exist_ok=True)
                    image.save(self.scan_regions.custom / (group + ".jpg"), quality=92)
                    self.scan_regions.save_regions()
                    self.scan_regions._select()
            except Exception as error:
                self.error(str(error))
            finally:
                if self._region_editor is not None:
                    self._region_editor.deleteLater()
                self._region_editor = None
                self._region_selection_pending = False
                if not self._closed:
                    self.showMaximized() if was_maximized else self.showNormal()
                    self.raise_()
        QTimer.singleShot(2000, select_now)

    def refresh_aux_regions(self):
        """Show the saved tablet, inventory, and Ritual capture bounds or their unset messages."""
        tablet = self._scan_region("tablet_region")
        self.tablet_region_label.setText(
            f"Tablet tooltip: {tablet['x']}, {tablet['y']} · {tablet['w']}×{tablet['h']}"
            if tablet else "No tablet tooltip region selected.")
        region = self._scan_region("inventory_region")
        self.inventory_region_label.setText(
            f"Inventory grid: {region['x']}, {region['y']} · {region['w']}×{region['h']}"
            if region else "No inventory grid selected.")
        region = self._scan_region("ritual_region")
        self.ritual_region_label.setText(
            f"Ritual page: {region['x']}, {region['y']} · {region['w']}×{region['h']}"
            if region else "No Ritual page region selected.")

    def _fill_tablet_slot(self, number, entries, raw_mods):
        """Fill four affix controls for one tablet slot and retain its complete raw modifier
        text.
        """
        start = (number - 1) * 4
        for offset in range(4):
            item = entries[offset] if offset < len(entries) else None
            name = item["affix"] if item else ""
            field = self.tablet_affixes[start + offset]
            for index in reversed(range(field.count())):
                if field.itemData(index) and field.itemData(index) not in self.state["affixes"]:
                    field.removeItem(index)
            if name and field.findData(name) < 0:
                field.addItem(name, name)
            select(field, name)
            amount = self.tablet_values[start + offset]
            amount.setText(str(item["value"]) if item and item["value"] is not None else "")
            amount.setProperty("affix", name)
            amount.setProperty("unit", item["unit"] if item else "%")
        self.tablet_raw_mods[number - 1] = list(raw_mods)

    def _tablet_read(self, number, result):
        """Validate scan context, register candidate affixes, and stage the next tablet slot
        for review, including empty readings that can be corrected manually. Automatically
        commit only ready reads whose matched affixes meet the confidence threshold.
        """
        logger.validate_scan_context(result)
        self._require_remnant_review_finished()
        auto = self.auto_tablet_checkbox.isChecked()
        strictness = ocr_sensitivity.validate(result.get("_ocr_strictness", ocr_sensitivity.DEFAULT))
        auto = auto and (result.get("source") != "screen OCR" or strictness != 100)
        floor = ocr_sensitivity.clear_threshold(.94, strictness, .14)
        next_slot = logger.tablet_next_slot()
        if next_slot > 4:
            self._pending_tablet_slot = None
            text = "Four tablets are already saved. Clear tablet config before scanning a new set."
            set_message(self.tablet_scan_status, text)
            self._review_pending("tablet", text, False)
            return None
        number = next_slot
        ocr_rows = result.get("ocr_rows")
        if ocr_rows is not None:
            ocr_rows = item_ocr.merge_tablet_lines(ocr_rows)
            original_mods = [row["text"] for row in ocr_rows if row.get("text")]
            result = {**result, **item_ocr.parse_tablet(ocr_rows, self.state["affixes"]),
                      "ocr_rows": ocr_rows}
        else:
            original_mods = list(result.get("mods") or
                                 [item["raw"] for item in result["matches"]] + result["uncertain"])
            result = {**result, **item_ocr.parse_tablet(original_mods, self.state["affixes"])}
        unknown = [raw for raw in result.get("uncertain", [])
                   if item_ocr._affix_match(raw, self.state["affixes"])[0] is None]
        proposed = new_affix_names(unknown, self.state["affixes"],
                                   ocr_rows if ocr_rows is not None
                                   else [] if result.get("source") == "screen OCR" else None)
        added = logger.add_affixes(proposed)
        if added:
            self.state["affixes"].extend(added)
            for widget in self.tablet_affixes:
                current = widget.currentData()
                with QSignalBlocker(widget):
                    for name in added:
                        widget.addItem(name, name)
                select(widget, current or "")
        result = {**result, **item_ocr.parse_tablet(ocr_rows if ocr_rows is not None else original_mods,
                                                   self.state["affixes"])}
        preview_matches = list(result["matches"])
        review_names = new_affix_names(result["uncertain"], self.state["affixes"])
        for raw in result["uncertain"]:
            amount = modifier_value(raw)
            if amount and amount["name"] in review_names:
                if not any(item["affix"] == amount["name"] for item in preview_matches):
                    preview_matches.append({"affix": amount["name"], "value": amount["value"],
                                            "unit": amount["unit"], "score": 0, "raw": raw})
        preview_matches.sort(key=lambda item: original_mods.index(item["raw"])
                             if item["raw"] in original_mods else len(original_mods))
        preview_matches = preview_matches[:4]
        select(self.tablets_used, max(int(value(self.tablets_used)), number))
        select(self.tablet_scan_number, number)
        self._fill_tablet_slot(number, preview_matches, original_mods)
        self.tablet_active()
        self.show_tablet_raw()
        rows = [(f"Affix {i + 1}" if item["score"] >= floor else "Uncertain", item["affix"],
                 f"{item['value']}%" if item["unit"] == "%" else f"{item['value']} {item['unit']}")
                for i, item in enumerate(preview_matches)]
        rows += [("Uncertain", item, "Review") for item in result["uncertain"]
                 if item not in {match["raw"] for match in preview_matches}]
        if not rows and ocr_rows is not None:
            rows = [("OCR text", item["text"], "No affix recognized")
                    for item in ocr_rows[:12] if item.get("text")]
        summary = (f"Tablet {number} · {len(preview_matches)} affixes" if preview_matches else
                   "No tablet affixes read. Use Edit activity settings to enter modifiers, "
                   "or check the captured tooltip and scan again.")
        self._pending_tablet_slot = number
        self._review_pending("tablet", summary, bool(preview_matches), rows)
        if auto and result["status"] == "ready" and all(
                item["score"] >= floor for item in result["matches"]):
            saved_number = logger.save_scanned_tablet(result["matches"], original_mods)
            self._clear_tablet_form_dirty(saved_number)
            self.state = logger.get_state()
            self._set_commit_badge(self.state["scan_commit_count"])
            self._review_saved("tablet", self.state["scan_commit_count"])
            select(self.tablets_used, max(int(value(self.tablets_used)), self.state["settings"]["tablets_used"]))
            next_slot = logger.tablet_next_slot()
            self.auto_tablet_next.setText(
                f"Next tablet: {next_slot} of 4" if next_slot <= 4 else
                "Four tablets saved. Clear tablet config to scan a new set.")
            select(self.tablet_scan_number, min(next_slot, 4))
            set_message(self.tablet_scan_status,
                        f"Tablet {saved_number} saved with {len(result['matches'])} affixes."
                        + (f" Added {len(added)} new affix name(s)." if added else ""), "success")
            return saved_number
        details = "; ".join(result["uncertain"][:3])
        set_message(self.tablet_scan_status,
                    f"Tablet {number}: {len(preview_matches)} mods filled for review. "
                    + (f"Added {len(added)} new affix name(s) to Affix DB. " if added else "")
                    + (f"Uncertain: {details}. " if details else "")
                    + "Check them, then Approve.",
                    "success" if result["status"] == "ready" else "message")
        return None

    def show_tablet_raw(self):
        """Show the selected tablet's raw modifiers only when developer mode is enabled."""
        if hasattr(self, "tablet_raw_preview"):
            number = int(value(self.tablet_scan_number)) - 1
            self.tablet_raw_preview.setPlainText("\n".join(self.tablet_raw_mods[number]))
            self.tablet_raw_preview.setVisible(self.developer_mode.isChecked() and
                                               bool(self.tablet_raw_mods[number]))

    def _inventory_phase_changed(self):
        """Keep a held inventory reading on its captured map when changing start/end phase,
        then persist the selection and refresh totals.
        """
        phase = value(self.inventory_phase)
        if self.pending_review_kind == "currency" and self._pending_currency_phase:
            previous = self._pending_currency_phase
            if self._inventory_reading is not None:
                with QSignalBlocker(self.inventory_phase):
                    select(self.inventory_phase, previous)
                return
            try:
                if self._inventory_capture_context is not None:
                    logger.validate_scan_context(self._inventory_capture_context["context"])
                if logger.currency_target_map(phase) != self._pending_currency_map:
                    raise ValueError("Changing the snapshot would assign it to another map. "
                                     "Reject this reading and scan that map again.")
            except ValueError as error:
                with QSignalBlocker(self.inventory_phase):
                    select(self.inventory_phase, previous)
                self.error(str(error))
                return
            self._pending_currency_phase = phase
            if self._inventory_capture_context is not None:
                self._inventory_capture_context["phase"] = phase
            summary = self.review_summary.text().partition(" inventory · ")[2]
            if summary:
                set_message(self.review_summary, f"{phase.title()} inventory · {summary}")
            self.approve_scan_button.setToolTip(f"Save {phase} inventory to {self._pending_currency_map}.")
        with logger._connect() as db:
            logger._set_meta(db, "inventory_scan_phase", phase)
        self.refresh_currency_summary()

    def _inventory_captured(self, image, live=True, expected_map_id=None, expected_phase=None, expected_strictness=None):
        """Bind an inventory capture to its map, phase, and session context before queueing
        icon matching and opening the held review. Freeze currency strictness for this work.
        """
        self._require_remnant_review_finished()
        service.HOTKEY.cancel_capture()
        phase = expected_phase or value(self.inventory_phase)
        map_id = logger.currency_target_map(phase)
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The currency scan belongs to {expected_map_id}. Scan {map_id} again.")
        image = item_ocr.inventory_grid(image)
        capture = {"map_id": map_id, "phase": phase, "image": image, "context": logger.scan_context(),
                   "strictness": ocr_sensitivity.validate(expected_strictness) if expected_strictness is not None
                   else ocr_sensitivity.saved_values()["currency"]}
        self._inventory_capture = image
        self._inventory_capture_context = self._inventory_reading = capture
        self._pending_currency_map = map_id
        self._pending_currency_phase = phase
        with QSignalBlocker(self.inventory_phase):
            select(self.inventory_phase, phase)
        self._show_review_capture(image)
        self.inventory_table.setRowCount(0)
        self.inventory_table.hide()
        self._show_inventory_preview(image)
        self._review_pending("currency", f"Reading {phase} inventory…", False)
        references = logger.inventory_icons()
        self._submit_scan("Matching inventory icons…",
                     lambda: item_ocr.scan_inventory_grid(image, references, strictness=capture["strictness"])
                     if capture is self._inventory_reading else None,
                     lambda result: self._inventory_read(result, live, map_id, capture), "currency", capture)

    def _show_inventory_preview(self, image):
        """Render the inventory capture with its 12-by-5 grid and numbered slots for row
        review.
        """
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        picture = QPixmap()
        picture.loadFromData(buffer.getvalue())
        picture = picture.scaled(840, 390, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(picture)
        painter.setPen(QPen(QColor("#70BD98"), 1))
        for column in range(1, 12):
            x = round(column * picture.width() / 12)
            painter.drawLine(x, 0, x, picture.height())
        for row in range(1, 5):
            y = round(row * picture.height() / 5)
            painter.drawLine(0, y, picture.width(), y)
        painter.setPen(QColor("#FFFFFF"))
        for slot in range(1, 61):
            column, row = (slot - 1) % 12, (slot - 1) // 12
            painter.drawText(round(column * picture.width() / 12) + 3,
                             round(row * picture.height() / 5) + 14, str(slot))
        painter.end()
        self.inventory_preview.setPixmap(picture)
        self.inventory_preview.setFixedSize(picture.size())

    def _inventory_read(self, result, live=True, expected_map_id=None, capture=None):
        """Show certainty groups in slot order with captured evidence; auto-save only clear live reads."""
        if capture is not None and capture is not self._inventory_reading:
            return
        if capture is not None:
            logger.validate_scan_context(capture["context"])
        self._require_remnant_review_finished()
        phase = capture["phase"] if capture is not None else value(self.inventory_phase)
        map_id = logger.currency_target_map(phase)
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The currency scan belongs to {expected_map_id}. Scan {map_id} again.")
        self._inventory_reading = None
        self._pending_currency_map = map_id
        self._pending_currency_phase = phase
        with QSignalBlocker(self.inventory_phase):
            select(self.inventory_phase, phase)
        if capture is not None:
            self._inventory_capture = capture["image"]
            self._show_review_capture(capture["image"])
            self._show_inventory_preview(capture["image"])
        self.inventory_table.setRowCount(0)
        unknown = result["unknown"]
        uncertain = len(unknown) + sum(bool(item.get("count_needs_review") or item.get("name_needs_review"))
                                       for item in result["items"])
        rows = [*result["items"], *({**item, "name": "", "quantity": "", "count_needs_review": True}
                                   for item in unknown)]
        # Sort before creating widgets so row actions and icon-learning evidence
        # stay attached to their original inventory slots, rather than row numbers.
        rows.sort(key=lambda item: (currency_review_rank(item),
                                   item["slot"] if type(item.get("slot")) is int else 61))
        for item in rows:
            self.add_inventory_row(item, image=capture["image"] if capture is not None else None)
        if unknown:
            self.icon_slot.setValue(unknown[0]["slot"])
        set_message(self.inventory_status,
                    f"{len(result['items'])} inventory stacks matched. "
                    f"{uncertain} uncertain slots or shared-icon tiers. Confident matches appear first, "
                    "then likely matches, then unknowns. Approve uncertain rows individually to include them. "
                    "Final Approve rejects unapproved rows and saves the reviewed snapshot.",
                    "success" if result["items"] and not uncertain else "message")
        self._review_pending("currency",
                             f"{phase.title()} inventory · {len(result['items'])} stacks · {uncertain} uncertain slots. "
                             "Review the editable list below.")
        self.approve_scan_button.setToolTip(f"Save {phase} inventory to {map_id}.")
        if live and self.state["settings"].get("ocr_auto_commit"):
            if clear_currency_read(result):
                self.save_inventory()
                set_message(self.inventory_status,
                            f"✓ Inventory snapshot saved as Scan Commit #{self.state['scan_commit_count']}.",
                            "success")
            else:
                set_message(self.inventory_status,
                            "Auto-commit held: review the uncertain rows, then use the bottom Approve to save the snapshot.")

    def add_inventory_row(self, item=None, *, image=None):
        """Add an editable inventory stack with captured-slot evidence and approval controls,
        holding uncertain names or counts for review.
        """
        item = item if isinstance(item, dict) else {}
        row = self.inventory_table.rowCount()
        self.inventory_table.insertRow(row)
        self.inventory_table.show()
        for column, text in enumerate((item.get("slot", ""), item.get("name", ""),
                                       item.get("quantity", ""))):
            cell = QTableWidgetItem(str(text))
            if column == 0:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                slot = item.get("slot")
                cell.setData(Qt.ItemDataRole.UserRole, slot)
                if slot is not None:
                    cell.setToolTip(f"Inventory slot {slot}")
                if isinstance(image, Image.Image) and type(slot) is int and 1 <= slot <= 60:
                    crop = item_ocr.inventory_cell(image, slot)
                    crop.thumbnail((48, 48))
                    raw = io.BytesIO()
                    crop.save(raw, format="PNG")
                    icon = QPixmap()
                    icon.loadFromData(raw.getvalue())
                    cell.setText("")
                    cell.setIcon(QIcon(icon))
            else:
                cell.setToolTip("Double-click to edit the currency or item name." if column == 1 else
                                "Double-click to edit the whole-number count, then approve the row.")
            if column == 1 and item.get("candidate"):
                cell.setToolTip("Possible match: " + item["candidate"] + ". Choose a suggestion or type the correct name, then approve the row.")
            if column == 1:
                cell.setData(Qt.ItemDataRole.UserRole, {"original": dict(item),
                             "has_capture": isinstance(image, Image.Image) and type(item.get("slot")) is int
                             and 1 <= item["slot"] <= 60})
            self.inventory_table.setItem(row, column, cell)
        candidate = str(item.get("candidate") or "").strip()
        if currency_review_rank({"candidate": candidate}) == 1:
            self._add_currency_name_selector(row, candidate)
        controls = QWidget()
        # A stable focus target keeps disabling the clicked approval button
        # from transferring focus to an earlier row or the top of Review.
        controls.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        status = QLabel()
        status.setObjectName("currencyReviewStatus")
        status.setWordWrap(True)
        layout.addWidget(status)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        approve = button("Approve", lambda checked=False, c=controls:
                         self.run(lambda: self.review_currency_row(c, True)))
        approve.setObjectName("approveCurrency")
        reject = button("Reject", lambda checked=False, c=controls:
                        self.run(lambda: self.review_currency_row(c, False)))
        reject.setObjectName("rejectCurrency")
        for action in (approve, reject):
            action.setStyleSheet("padding:3px 8px;")
            actions.addWidget(action)
        layout.addLayout(actions)
        controls.setProperty("requiresApproval", bool(item.get("count_needs_review") or item.get("name_needs_review")
                                                       or not item.get("name")))
        controls.setProperty("confidenceGroup", currency_review_rank(item))
        self.inventory_table.setCellWidget(row, 3, controls)
        self.inventory_table.setRowHeight(row, 62)
        self._set_currency_review(controls, "pending" if controls.property("requiresApproval") else "approved")

    def _add_currency_name_selector(self, row, candidate):
        """Expose this slot's suggested labels while keeping its table item authoritative.

        Only the captured suggestions populate the dropdown; manual names remain
        editable without loading the full inventory catalog for each review row.
        """
        names, seen = [], set()
        for name in candidate.split(" / "):
            name = name.strip()
            if name.casefold() in seen or currency_review_rank({"candidate": name}) != 1:
                continue
            seen.add(name.casefold())
            names.append(name)
        if not names:
            return
        cell = self.inventory_table.item(row, 1)
        selector = QComboBox()
        selector.setObjectName("currencyNameSuggestion")
        selector.setAccessibleName(f"Currency or item name for inventory slot {self.inventory_table.item(row, 0).data(Qt.ItemDataRole.UserRole)}")
        selector.setEditable(True)
        selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        selector.setCompleter(None)
        selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        selector.setMinimumContentsLength(12)
        selector.setToolTip(cell.toolTip())
        selector.lineEdit().setPlaceholderText("Possible match: " + " / ".join(names))
        for name in names:
            selector.addItem(name, name)
        # Candidate guesses never prefill an unnamed row or grant approval.
        selector.setCurrentIndex(-1)
        selector.setEditText(cell.text())
        selector.setEnabled(self.pending_review_kind == "currency" and self.approve_scan_button.isEnabled())
        self.inventory_table.setCellWidget(row, 1, selector)
        selector.currentTextChanged.connect(lambda text, editor=selector:
                                            self._currency_suggestion_changed(editor, text))

    def _currency_suggestion_changed(self, selector, text):
        """Route an editable suggestion into its current row without retaining stale row numbers."""
        row = next((row for row in range(self.inventory_table.rowCount())
                    if self.inventory_table.cellWidget(row, 1) is selector), None)
        if row is not None:
            cell = self.inventory_table.item(row, 1)
            if cell.text() != text:
                cell.setText(text)

    def _set_currency_review(self, controls, state, *, unnamed=False):
        """Display candidate certainty and review state without changing a row's captured evidence."""
        controls.setProperty("reviewStatus", state)
        controls.setProperty("unnamedRejected", bool(unnamed and state == "rejected"))
        status = controls.findChild(QLabel, "currencyReviewStatus")
        pending = {0: "Confirm name / count", 1: "Likely match · Confirm name / count",
                   2: "Unknown item · Confirm name / count"}.get(controls.property("confidenceGroup"),
                                                                "Confirm name / count")
        status.setText({"pending": pending, "approved": "Approved", "rejected": "Rejected"}[state])
        status.setStyleSheet("color:#F5C364;" if state != "rejected" else "color:#BCB7AE;")
        controls.findChild(QPushButton, "approveCurrency").setEnabled(state != "approved")
        controls.findChild(QPushButton, "rejectCurrency").setEnabled(state != "rejected")

    def _inventory_row_changed(self, item):
        """Synchronize name selectors and require renewed approval after name or count edits."""
        if item.column() not in (1, 2):
            return
        if item.column() == 1:
            selector = self.inventory_table.cellWidget(item.row(), 1)
            if selector is not None and selector.currentText() != item.text():
                with QSignalBlocker(selector):
                    selector.setEditText(item.text())
        controls = self.inventory_table.cellWidget(item.row(), 3)
        if controls:
            controls.setProperty("requiresApproval", True)
            self._set_currency_review(controls, "pending")

    def review_currency_row(self, controls, approve):
        """Validate a requested inventory-row approval, rejecting unnamed rows and recording
        the resulting review status while retaining the user's scroll and row selection.
        """
        row = next((r for r in range(self.inventory_table.rowCount())
                    if self.inventory_table.cellWidget(r, 3) is controls), None)
        if row is None:
            return
        unnamed = False
        if approve:
            name = self.inventory_table.item(row, 1).text().strip()
            quantity = self.inventory_table.item(row, 2).text().strip()
            if not name:
                approve = False
                unnamed = True
            else:
                omen_names = {entry.casefold() for entry in logger.ritual_names()}
                review_learning.validate_name(name, "Omen" if name.casefold() in omen_names else "Currency")
                logger._integer(quantity, name + " stack count", 0, 1000000)
        positions = []
        ancestor = self.inventory_table
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                for bar in (ancestor.verticalScrollBar(), ancestor.horizontalScrollBar()):
                    positions.append((bar, bar.value()))
            ancestor = ancestor.parentWidget()
        action = controls.findChild(QPushButton, "approveCurrency" if approve else "rejectCurrency")
        if QApplication.focusWidget() is action:
            controls.setFocus(Qt.FocusReason.OtherFocusReason)
        self._set_currency_review(controls, "approved" if approve else "rejected", unnamed=unnamed)
        token = getattr(self, "_currency_row_scroll_token", 0) + 1
        self._currency_row_scroll_token = token
        owner = self.pending_review_kind, self._overlay_review_token

        def restore_position():
            """Restore after immediate and queued focus/layout work only for this live row."""
            if (self._closed or self._currency_row_scroll_token != token
                    or (self.pending_review_kind, self._overlay_review_token) != owner
                    or self.inventory_table.cellWidget(row, 3) is not controls):
                return
            for bar, position in positions:
                bar.setValue(position)

        restore_position()
        QTimer.singleShot(0, self, restore_position)

    def save_icon_example(self):
        """Save an icon example from the selected captured slot, then rescan the grid without
        auto-commit while preserving its map and phase.
        """
        if self._inventory_capture is None:
            raise ValueError("Capture or open an inventory grid first.")
        name = value(self.icon_name)
        slot = self.icon_slot.value()
        cell = item_ocr.inventory_cell(self._inventory_capture, slot)
        kind = "item" if name in logger.item_names() else "currency"
        icon_id = logger.save_item_icon(name, cell) if kind == "item" else logger.save_currency_icon(name, cell)
        self.refresh_currency_references()
        capture = self._inventory_capture_context
        self._inventory_captured(self._inventory_capture, live=False,
                                 expected_map_id=capture["map_id"] if capture else None,
                                 expected_phase=capture["phase"] if capture else None)
        self.note(f"Saved {name} icon example #{icon_id} from inventory slot {slot}.", True)

    def add_currency_name(self):
        """Register a currency name and refresh the icon-reference controls with that name
        selected.
        """
        name = logger.add_currency_item(value(self.currency_new_name))
        self.currency_new_name.clear()
        self.refresh_currency_references()
        select(self.icon_name, name)
        self.note(f"Added {name} to Currency DB.", True)

    def remove_icon_example(self):
        """Delete the selected learned, item, or currency icon example and refresh the
        reference list.
        """
        selected = self.icon_list.currentItem()
        if not selected:
            raise ValueError("Select an icon example to remove.")
        kind, number = selected.data(Qt.ItemDataRole.UserRole)
        if kind == "review":
            logger.delete_review_icon(number)
        elif kind == "item":
            logger.delete_item_icon(number)
        else:
            logger.delete_currency_icon(number)
        self.refresh_currency_references()
        self.note("Icon example removed.")

    def refresh_currency_references(self):
        """Reload inventory names and typed icon-reference IDs, then refresh session totals
        with rebuilt icons.
        """
        selected = value(self.icon_name)
        with QSignalBlocker(self.icon_name):
            self.icon_name.clear()
            for name in logger.inventory_names():
                self.icon_name.addItem(name, name)
        select(self.icon_name, selected)
        self.icon_list.clear()
        from PySide6.QtWidgets import QListWidgetItem
        for reference in logger.inventory_icons():
            entry = QListWidgetItem(f"#{reference['id']} · {reference['name']}")
            entry.setData(Qt.ItemDataRole.UserRole, ("review" if reference.get("reviewed") else reference["kind"], reference["id"]))
            self.icon_list.addItem(entry)
        self.refresh_session_currency(force_icons=True)

    def save_inventory(self):
        """Commit approved stacks only; reject pending rows without registering or learning their guesses."""
        reviewing = self.pending_review_kind == "currency"
        if reviewing and self._failed_review:
            raise ValueError("This scan failed. Scan again or reject this reading before saving.")
        if reviewing and self._inventory_reading is not None:
            raise ValueError("Wait for the inventory scan to finish before saving.")
        if reviewing and self._inventory_capture_context is not None:
            logger.validate_scan_context(self._inventory_capture_context["context"])
        phase = self._pending_currency_phase if reviewing else value(self.inventory_phase)
        phase = phase or value(self.inventory_phase)
        if reviewing and phase != value(self.inventory_phase):
            raise ValueError("The snapshot selection no longer matches this reading. "
                             "Select its start/end phase again before saving.")
        rows, examples = [], []
        item_names = {name.casefold() for name in logger.item_names()}
        omen_names = {name.casefold() for name in logger.ritual_names()}
        for row in range(self.inventory_table.rowCount()):
            controls = self.inventory_table.cellWidget(row, 3)
            name = self.inventory_table.item(row, 1)
            text = name.text().strip() if name else ""
            if not controls or controls.property("reviewStatus") != "approved":
                if controls:
                    self._set_currency_review(controls, "rejected", unnamed=not text)
                continue
            if not text:
                if controls:
                    self._set_currency_review(controls, "rejected", unnamed=True)
                continue
            amount = self.inventory_table.item(row, 2)
            review_learning.validate_name(text, "Omen" if text.casefold() in omen_names else "Currency")
            logger._integer(amount.text().strip() if amount else "", text + " stack count", 0, 1000000)
            rows.append({"name": text,
                         "quantity": amount.text().strip() if amount else ""})
            metadata = name.data(Qt.ItemDataRole.UserRole) or {}
            capture = self._inventory_capture_context if reviewing else None
            if metadata.get("has_capture") and capture and logger._integer(
                    rows[-1]["quantity"], text + " stack count", 0, 1000000) > 0:
                example = review_learning.make_example(capture["image"], metadata.get("original", {}),
                                                       text, "Item" if text.casefold() in item_names else "Currency",
                                                       "currency")
                if example:
                    examples.append(example)
        saved = logger.save_currency_snapshot(phase, rows,
                                             self._pending_currency_map if reviewing else None,
                                             register_names=True, icon_examples=examples)
        self.state = logger.get_state()
        self._set_commit_badge(self.state["scan_commit_count"])
        self._review_saved("currency", self.state["scan_commit_count"])
        self.refresh_currency_summary()
        self.refresh_currency_references()
        self.refresh_reference_examples()
        self.note(f"{phase.title()} inventory saved to {saved['map_id']} · {len(rows)} stacks.", True)
        return saved

    def refresh_session_currency(self, *, force_icons=False):
        """Refresh session inventory gains and catalog entries only when their cache key
        changes or icons are forced; clear the search after a session reset.
        """
        data = logger.session_currency_totals()
        with logger._connect() as db:
            db.execute("BEGIN")
            names = {}
            for table, kind in (("currency_items", "Currency"), ("item_names", "Item"), ("ritual_names", "Omen")):
                for name in logger._active_catalog_names(db, table):
                    names[name.casefold()] = {"name": name, "kind": kind}
        catalog = sorted(names.values(), key=lambda item: item["name"].casefold())
        key = (logger.session_generation(), tuple((item["name"], item["kind"], item["quantity"])
                                                  for item in data["items"]),
               data["maps_counted"], data["maps_pending_baseline"], data["maps_pending_end"],
               data.get("maps_assumed_empty", 0), tuple((item["name"], item["kind"]) for item in catalog))
        if key == self._session_currency_key and not force_icons:
            return
        if self._session_currency_key is not None and key[0] != self._session_currency_key[0]:
            self.session_currency.search.clear()
        visible_names = {item["name"] for item in catalog} | {item["name"] for item in data["items"]}
        cached = self._session_currency_icons
        for name in set(cached) - visible_names:
            cached.pop(name)
        needed = visible_names if force_icons else visible_names - set(cached)
        references = logger.ritual_icons() if needed else ()
        updates = {name: icon_png(name, references) for name in needed}
        cached.update(updates)
        self.session_currency.set_totals(data, updates, catalog=catalog)
        self._session_currency_key = key

    def refresh_currency_summary(self):
        """Display the selected phase's map snapshots and net changes, or explain when no
        end-inventory target exists.
        """
        self.refresh_session_currency()
        try:
            map_id = logger.currency_target_map(value(self.inventory_phase))
        except ValueError:
            self.currency_saved.setText("Start a map to assign a Map ID for end inventory.")
            return
        data = logger.currency_for_map(map_id)
        parts = [f"{name}: {change:+d}" for name, change in sorted(data["net"].items()) if change]
        self.currency_saved.setText(
            f"{map_id} · start {len(data['start'])} item types · end {len(data['end'])} item types"
            + (" · net: " + ", ".join(parts[:8]) if parts else ""))

    def _ritual_captured(self, image, live=True, expected_map_id=None, expected_strictness=None):
        """Bind a Ritual capture and image hash to the current map and session context before
        queueing reward recognition with frozen Ritual strictness and opening the held review.
        """
        self._require_remnant_review_finished()
        service.HOTKEY.cancel_capture()
        map_id = logger.get_state()["current_map_id"]
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The Ritual scan belongs to {expected_map_id}. Scan {map_id} again.")
        if image.width * image.height > 12_000_000:
            raise ValueError("Ritual capture exceeds 12 megapixels.")
        capture = {"map_id": map_id, "image": image,
                   "context": logger.scan_context(),
                   "scan_hash": hashlib.sha256(image.tobytes()).hexdigest(),
                   "strictness": ocr_sensitivity.validate(expected_strictness) if expected_strictness is not None
                   else ocr_sensitivity.saved_values()["ritual"]}
        self._ritual_reading = capture
        self._ritual_capture_context = capture
        self._pending_ritual_map = map_id
        self._ritual_hash = None
        self._show_review_capture(image)
        self.ritual_table.setRowCount(0)
        self.ritual_table.hide()
        self.ritual_raw.clear()
        self._review_pending("ritual", "Reading Ritual rewards…", False)
        names = logger.ritual_names()
        references = logger.ritual_icons()
        self._submit_scan("Reading Ritual rewards…",
                     lambda: item_ocr.scan_ritual_page(image, names, references, strictness=capture["strictness"])
                     if capture is self._ritual_reading else None,
                     lambda result: self._ritual_read(result, live, map_id, capture), "ritual", capture)

    def _ritual_read(self, result, live=True, expected_map_id=None, capture=None):
        """Ignore superseded captures and validate map ownership before filling Ritual reward
        drafts. Report incomplete coverage and automatically save only clear live readings.
        """
        if capture is not None and capture is not self._ritual_reading:
            return
        if capture is not None:
            logger.validate_scan_context(capture["context"])
        self._require_remnant_review_finished()
        map_id = logger.get_state()["current_map_id"]
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The Ritual scan belongs to {expected_map_id}. Scan {map_id} again.")
        self._ritual_reading = None
        self._pending_ritual_map = map_id
        if capture is not None:
            self._ritual_hash = capture["scan_hash"]
            self._show_review_capture(capture["image"])
        self.ritual_tribute.setText("" if result.get("tribute_available") is None else str(result["tribute_available"]))
        self.ritual_rerolls.setText("" if result.get("rerolls_remaining") is None else str(result["rerolls_remaining"]))
        self.ritual_table.setRowCount(0)
        for item in result["items"]:
            self.add_ritual_row(item)
        self.ritual_raw.setPlainText(result["raw_text"])
        review_count = sum(self.ritual_table.item(row, 6).text().split(": ")[-1] != "Ready"
                           for row in range(self.ritual_table.rowCount()))
        summary = (f"{len(result['items'])} Ritual rewards · {review_count} need review. "
                   "Enter names, types and counts to include rewards. Unnamed rows are rejected when you Approve.")
        if result.get("coverage_uncertain"):
            summary = ("The complete reward grid could not be verified. " + summary +
                       " Check for missing rewards or capture the full grid again.")
        set_message(self.ritual_status,
                    summary)
        self._review_pending("ritual", summary)
        if live and self.state["settings"].get("ocr_auto_commit"):
            if clear_ritual_read(result, logger.ritual_names()):
                self.save_ritual()
                set_message(self.ritual_status,
                            f"✓ Ritual page saved as Scan Commit #{self.state['scan_commit_count']}.",
                            "success")
            else:
                set_message(self.ritual_status,
                            "Auto-commit held: check the reward names, quantities and Tribute, then Approve.")

    def add_ritual_row(self, item=None):
        """Add a Ritual reward draft with capture evidence, unresolved fields, and a tri-state
        Deferred control when its status needs confirmation.
        """
        item = item if isinstance(item, dict) else {}
        row = self.ritual_table.rowCount()
        unresolved = bool(item.get("unresolved") or unresolved_ritual_name(item.get("name")))
        legacy_placeholder = bool(item.get("name") and unresolved_ritual_name(item["name"]))
        name = "" if unresolved else item.get("name", "")
        category = "" if item and unresolved and not item.get("category_verified") else item.get("category", "Item")
        quantity = None if item.get("count_needs_review") or legacy_placeholder else item.get("quantity", 1)
        slots = item.get("grid_slots") or []
        evidence = ("Grid slots: " + ", ".join(str(slot) for slot in slots) + ". " if slots else "")
        if item.get("box"):
            evidence += "Screenshot bounds: " + ", ".join(str(point) for point in item["box"]) + ". "
        if item.get("source"):
            evidence += str(item["source"])
        with QSignalBlocker(self.ritual_table):
            self.ritual_table.insertRow(row)
            fields = (category, name, quantity, item.get("tribute", ""), item.get("source", "manual"))
            for column, field in enumerate(fields):
                cell = QTableWidgetItem("" if field is None else str(field))
                cell.setToolTip(evidence + (" Confirm the reward's name before saving." if column == 1 and unresolved else ""))
                self.ritual_table.setItem(row, column, cell)
            self.ritual_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dict(item))
            capture = self._ritual_capture_context
            image = capture.get("image") if capture else None
            box = item.get("box")
            if isinstance(image, Image.Image) and box and len(box) == 4:
                try:
                    left, top, right, bottom = (int(point) for point in box)
                    left, top = max(0, left), max(0, top)
                    right, bottom = min(image.width, right), min(image.height, bottom)
                    if right > left and bottom > top:
                        crop = image.crop((left, top, right, bottom)).convert("RGB")
                        crop.thumbnail((48, 48))
                        raw = io.BytesIO()
                        crop.save(raw, format="PNG")
                        icon = QPixmap()
                        icon.loadFromData(raw.getvalue())
                        self.ritual_table.item(row, 1).setIcon(QIcon(icon))
                        self.ritual_table.setRowHeight(row, 56)
                except (TypeError, ValueError):
                    pass
            deferred = QTableWidgetItem()
            deferred.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            unknown_deferred = (item.get("deferred_needs_review") or item.get("deferred_uncertain") or
                                type(item.get("deferred", False)) is not bool)
            if unknown_deferred:
                deferred.setFlags(deferred.flags() | Qt.ItemFlag.ItemIsUserTristate)
                deferred.setCheckState(Qt.CheckState.PartiallyChecked)
                deferred.setToolTip("Deferred status could not be read. Check or uncheck it to confirm.")
            else:
                deferred.setCheckState(Qt.CheckState.Checked if item.get("deferred") else Qt.CheckState.Unchecked)
                deferred.setToolTip(evidence)
            self.ritual_table.setItem(row, 5, deferred)
        self.ritual_table.show()
        self._refresh_ritual_row_review(row)

    def _ritual_row_changed(self, item):
        """Infer a missing reward type after a name edit, clear its rejected flag, and
        recompute the row's review status.
        """
        if item.column() == 1 and item.text().strip():
            category = self.ritual_table.item(item.row(), 0)
            if category:
                with QSignalBlocker(self.ritual_table):
                    category.setData(Qt.ItemDataRole.UserRole + 2, False)
            if category and not category.text().strip():
                name = item.text().strip().casefold()
                kind = "Omen" if name in {entry.casefold() for entry in logger.ritual_names()} or name.startswith("omen of ") else "Item"
                with QSignalBlocker(self.ritual_table):
                    category.setText(kind)
        if item.column() < 6:
            self._refresh_ritual_row_review(item.row())

    def _refresh_ritual_row_review(self, row):
        """Mark a Ritual row with missing or uncertain fields, preserving its saved/rejected
        flags and grid-slot evidence.
        """
        def field(column):
            """Read a stripped reward field, treating a missing table cell as blank."""
            cell = self.ritual_table.item(row, column)
            return cell.text().strip() if cell else ""
        cell = self.ritual_table.item(row, 0)
        metadata = cell.data(Qt.ItemDataRole.UserRole) if cell else {}
        metadata = metadata or {}
        saved = bool(cell and cell.data(Qt.ItemDataRole.UserRole + 1))
        rejected = bool(cell and cell.data(Qt.ItemDataRole.UserRole + 2))
        missing = []
        if field(0).title() not in ("Omen", "Item"):
            missing.append("type")
        if unresolved_ritual_name(field(1)):
            missing.append("name")
        try:
            logger._integer(field(2), "Ritual quantity", 1, 1000000)
        except ValueError:
            missing.append("quantity")
        try:
            logger._integer(field(3), "Tribute", 0, 1000000000, blank=True)
        except ValueError:
            missing.append("Tribute")
        deferred = self.ritual_table.item(row, 5)
        if deferred and deferred.checkState() == Qt.CheckState.PartiallyChecked:
            missing.append("Deferred")
        status = "Rejected: no name" if rejected else ("Saved" if saved else ("Confirm " + " / ".join(missing) if missing else (
            "Check reading" if metadata.get("needs_review") or (
                "tribute" in metadata and metadata["tribute"] is None and not field(3)) else "Ready")))
        slots = metadata.get("grid_slots") or []
        if slots:
            status = "Slot " + ", ".join(str(slot) for slot in slots) + ": " + status
        with QSignalBlocker(self.ritual_table):
            review = QTableWidgetItem(status)
            review.setFlags(review.flags() & ~Qt.ItemFlag.ItemIsEditable)
            review.setForeground(QColor("#F5C364") if status.split(": ")[-1] not in ("Ready", "Saved") else QColor("#BCB7AE"))
            self.ritual_table.setItem(row, 6, review)

    def remove_ritual_row(self):
        """Remove the selected Ritual reward draft and hide the table when no rows remain."""
        row = self.ritual_table.currentRow()
        if row < 0:
            raise ValueError("Select a reward row to remove.")
        self.ritual_table.removeRow(row)
        if self.ritual_table.rowCount() == 0:
            self.ritual_table.hide()

    def save_ritual(self):
        """Validate held context and named rewards, then commit their map-owned page using the
        capture hash with registered names and learned icons. Reject unnamed rows and an
        empty held reading; mark saved rows after a successful commit.
        """
        if self.pending_review_kind == "ritual" and self._failed_review:
            raise ValueError("This scan failed. Scan again or reject this reading before saving.")
        if self.pending_review_kind == "ritual" and self._ritual_reading is not None:
            raise ValueError("Wait for the Ritual scan to finish before saving.")
        if self.pending_review_kind == "ritual" and self._ritual_capture_context is not None:
            logger.validate_scan_context(self._ritual_capture_context["context"])
        rows, examples, accepted_rows = [], [], []
        for row in range(self.ritual_table.rowCount()):
            def field(col, row=row):
                """Read a stripped field from this reward row, treating a missing table cell as
                blank.
                """
                cell = self.ritual_table.item(row, col)
                return cell.text().strip() if cell else ""
            if not field(1):
                with QSignalBlocker(self.ritual_table):
                    self.ritual_table.item(row, 0).setData(Qt.ItemDataRole.UserRole + 2, True)
                self._refresh_ritual_row_review(row)
                continue
            if field(0).title() not in ("Omen", "Item"):
                raise ValueError(f"Ritual row {row + 1}: confirm whether the reward is an Item or Omen.")
            if unresolved_ritual_name(field(1)):
                raise ValueError(f"Ritual row {row + 1}: confirm the unidentified reward's name before saving.")
            logger._integer(field(2), f"Ritual row {row + 1} quantity", 1, 1000000)
            logger._integer(field(3), f"Ritual row {row + 1} Tribute", 0, 1000000000, blank=True)
            if self.ritual_table.item(row, 5).checkState() == Qt.CheckState.PartiallyChecked:
                raise ValueError(f"Ritual row {row + 1}: check or uncheck Deferred to confirm its status.")
            rows.append({"category": field(0), "name": field(1), "quantity": field(2),
                         "tribute": field(3), "source": field(4),
                         "deferred": self.ritual_table.item(row, 5).checkState() == Qt.CheckState.Checked})
            accepted_rows.append(row)
            capture = self._ritual_capture_context if self.pending_review_kind == "ritual" else None
            if capture:
                original = self.ritual_table.item(row, 0).data(Qt.ItemDataRole.UserRole) or {}
                example = review_learning.make_example(capture["image"], original, field(1), field(0).title(), "ritual")
                if example:
                    examples.append(example)
        if not rows and self.pending_review_kind == "ritual":
            self.reject_review()
            return None
        saved = logger.save_ritual_page(rows, self.ritual_raw.toPlainText(), self._ritual_hash,
                                       self._pending_ritual_map if self.pending_review_kind == "ritual" else None,
                                       value(self.ritual_tribute), value(self.ritual_rerolls),
                                       register_names=True, icon_examples=examples)
        self.state = logger.get_state()
        self._set_commit_badge(saved["scan_commit_number"])
        for row in accepted_rows:
            with QSignalBlocker(self.ritual_table):
                self.ritual_table.item(row, 0).setData(Qt.ItemDataRole.UserRole + 2, False)
                self.ritual_table.item(row, 0).setData(Qt.ItemDataRole.UserRole + 1, True)
            self._refresh_ritual_row_review(row)
        self._review_saved("ritual", saved["scan_commit_number"])
        self.refresh_ritual_summary()
        self.refresh_currency_references()
        self.refresh_reference_examples()
        self.note(f"{saved['map_id']} Ritual page {saved['page_number']} "
                  f"{'updated' if saved['updated'] else 'saved'} · {saved['count']} rewards.", True)
        return saved

    def refresh_ritual_summary(self):
        """Display the current map's saved Ritual page count and total reward entries."""
        map_id = self.state["current_map_id"]
        pages = logger.ritual_pages_for_map(map_id) if map_id else []
        rewards = sum(len(page["items"]) for page in pages)
        self.ritual_saved.setText(f"{map_id or 'No active map'} · {len(pages)} saved Ritual pages · "
                                  f"{rewards} reward entries")

    def write_export(self, kind):
        """Queue spreadsheet or full SQLite export; require saved Atlas drafts only for spreadsheets."""
        page = getattr(self, "atlas_settings_page", None)
        if kind in ("csv", "xlsx") and page is not None and page.dirty:
            self.tabs.setCurrentWidget(page)
            raise ValueError("Save Atlas / Character Settings before exporting CSV or XLSX.")
        logger.save_export_folder(value(self.export_folder))
        self._submit(f"Writing {kind.upper()}…",
                     lambda: logger.save_export_file(kind),
                     lambda result: self._export_saved(result))

    def _export_saved(self, result):
        """Refresh the UI after an export worker completes and report the primary and companion
        paths.
        """
        self.refresh()
        paths = [result["path"]]
        if result.get("atlas_path"):
            paths.append(result["atlas_path"])
        if result.get("history_path"):
            paths.append(result["history_path"])
        self.note("Export saved: " + " and ".join(paths), True)

    def save_as(self, kind):
        """Choose an export destination, confirm existing CSV companions, and queue writing
        after checking unsaved Atlas settings.
        """
        page = getattr(self, "atlas_settings_page", None)
        if kind in ("csv", "xlsx") and page is not None and page.dirty:
            self.tabs.setCurrentWidget(page)
            raise ValueError("Save Atlas / Character Settings before exporting CSV or XLSX.")
        choices = {
            "csv": (logger.export_filename("csv"), "CSV (*.csv)", logger.export_primary_csv),
            "xlsx": (logger.export_filename("xlsx"), "Excel workbook (*.xlsx)", export_xlsx),
            "backup": ("PoE2_Data_Backup.sqlite3", "SQLite database (*.sqlite3)", logger.backup_bytes),
        }
        name, extension, produce = choices[kind]
        title = "Save CSV with Atlas and Scan History companions" if kind == "csv" else "Save file"
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path.home() / name), extension)
        if path:
            destination = Path(path)
            logger.validate_export_destination(destination)
            atlas_path = destination.with_name(destination.stem + "_Atlas.csv")
            history_path = destination.with_name(destination.stem + "_Scan_History.csv")
            existing = [companion for companion in (atlas_path, history_path)
                        if companion.exists() or companion.is_symlink()] if kind == "csv" else []
            if existing:
                answer = QMessageBox.question(
                    self, "Replace CSV companion files?",
                    "These companion files already exist:\n\n" +
                    "\n".join(str(companion) for companion in existing) + "\n\nReplace them?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            def write():
                """Generate and write the chosen export in the worker, reading all three CSV
                files from one database snapshot and using recoverable grouped writes.
                """
                atlas_data = None
                history_data = None
                if kind == "csv":
                    with logger._connect() as db:
                        db.execute("BEGIN")
                        data = logger.export_primary_csv(_db=db)
                        atlas_data = logger.export_atlas_csv(_db=db)
                        history_data = logger.export_all_csv(_db=db)
                else:
                    data = produce()
                if atlas_data is not None:
                    for target in (destination, atlas_path, history_path):
                        logger.validate_export_destination(target)
                    write_export_files({destination: data, atlas_path: atlas_data,
                                        history_path: history_data})
                else:
                    logger._write_export_bytes(destination, data)
                result = {"path": str(destination), "bytes": len(data)}
                if atlas_data is not None:
                    result["atlas_path"] = str(atlas_path)
                    result["history_path"] = str(history_path)
                return result
            self._submit(f"Writing {kind.upper()}…", write, self._export_saved)

    def reset_logger(self):
        """After confirmation, clear session records and reset IDs, start the first map, and
        discard pending captures, queued reviews, and task callbacks.
        """
        answer = QMessageBox.question(
            self, "Start fresh session / reset IDs",
            "Clear all imported and new Export rows, maps, chains, counts, currency and Ritual pages, "
            "then restart Map and Remnant IDs?\n\n"
            "Family/Recipe databases, settings and saved OCR references stay in place.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        logger.clear_export_and_reset_ids()
        logger.start_map()
        self._saved_badge = None
        self._accepted_propagation_requests.clear()
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.pending_review_kind = None
        self._set_chain_review_active(False)
        self._failed_review = False
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
        self._pending_currency_phase = None
        self._inventory_reading = self._ritual_reading = self._inventory_capture_context = None
        self._ritual_capture_context = self._remnant_reading = None
        service.HOTKEY.cancel_capture()
        self.saved_scan = self._saved_scan_capture = self.resolved = None
        self._inventory_capture = self._ritual_hash = None
        self._pending_tasks.clear()
        self.inventory_table.setRowCount(0)
        self.ritual_table.setRowCount(0)
        self.found_table.setRowCount(0)
        self.recipe_table.setRowCount(0)
        self.opened_lines.clear()
        self.ritual_raw.clear()
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.review_edit_button.hide()
        for widget in (self.review_group, self.found_table, self.found_label,
                       self.currency_review_group, self.ritual_review_group, self.remnant_log_group):
            widget.hide()
        for widget in (self.first_recipe, self.next_recipe):
            with QSignalBlocker(widget):
                widget.clear()
        select(self.recipe_family, "")
        self._latest_hotkey = (service.HOTKEY.status().get("latest") or {}).get("id", self._latest_hotkey)
        self._show_image(None)
        self.review_kind.setText("New session")
        set_message(self.review_summary, "Session cleared. M0001 is ready to scan.")
        self.refresh()
        self.note("Session cleared. M0001 is ready; next remnant is R0001.", True)

    def search_catalog(self):
        """Query the selected local catalog and display up to 200 entries with columns
        appropriate to its record type.
        """
        kind = value(self.catalog_kind)
        rows = logger.search_catalog(value(self.catalog_query), kind, 200)
        columns = {
            "families": ["family", "top_socket", "recipes"],
            "recipes": ["name", "sockets", "combo", "category"],
            "seed_states": ["family", "sockets", "seed_slot", "seed_rune", "rewards"],
            "aliases": ["alias", "target"],
            "affixes": ["name"],
        }[kind]
        self.catalog_table.setColumnCount(len(columns))
        self.catalog_table.setHorizontalHeaderLabels([name.replace("_", " ").title() for name in columns])
        self.catalog_table.setRowCount(len(rows))
        self._catalog_rows = rows
        for row, data in enumerate(rows):
            for col, name in enumerate(columns):
                item = data.get(name, "")
                text = " · ".join(map(str, item)) if isinstance(item, list) else str(item)
                self.catalog_table.setItem(row, col, QTableWidgetItem(text))
        self.catalog_table.resizeColumnsToContents()
        self.note(f"{len(rows)} {kind} entries shown.")

    def load_catalog_row(self, row):
        """Load a selected family, recipe, or seed-state search result into its editor."""
        if row < 0 or row >= len(getattr(self, "_catalog_rows", [])):
            return
        item = self._catalog_rows[row]
        kind = value(self.catalog_kind)
        if kind == "families":
            select(self.family_pick, item["family"])
            self.load_family()
        elif kind == "recipes":
            self.edit_recipe_name.setText(item["name"])
            self.load_recipe()
        elif kind == "seed_states":
            select(self.edit_seed_family, item["family"])
            self.edit_seed_sockets.setText(str(item["sockets"]))
            self.load_seed()

    def load_family(self):
        """Fill the family editor from the selection, or prepare defaults and the next
        available family ID.
        """
        family_id = value(self.family_pick)
        found = next((item for item in self.state["families"] if item["id"] == family_id), None)
        next_id = max((item["id"] for item in self.state["families"]), default=0) + 1
        self.family_id.setText(str(found["id"] if found else next_id))
        self.family_top.setText(str(found["top_socket"] if found else 6))
        self.family_active.setChecked(found["valid"] if found else True)
        self.family_recipes.setPlainText("\n".join(found["recipes"]) if found else "")

    def save_family(self):
        """Save the family form to the database, refresh catalog state, and reload the saved
        family.
        """
        saved = logger.save_family({"family": value(self.family_id), "top_socket": value(self.family_top),
                                    "valid": self.family_active.isChecked(),
                                    "recipes": self.family_recipes.toPlainText()})
        self.refresh()
        select(self.family_pick, saved["family"])
        self.load_family()
        self.note(f"Family {saved['family']} saved.", True)

    def load_recipe(self):
        """Find an exact recipe name without case sensitivity and populate its editor, raising
        when no record exists.
        """
        name = value(self.edit_recipe_name)
        found = next((row for row in logger.search_catalog(name, "recipes", 500)
                      if row["name"].lower() == name.lower()), None)
        if not found:
            raise ValueError("Recipe not found. Enter its details to add it.")
        for widget, key in ((self.edit_recipe_name, "name"), (self.edit_recipe_sockets, "sockets"),
                            (self.edit_recipe_combo, "combo"), (self.edit_recipe_category, "category"),
                            (self.edit_recipe_level, "level_band"), (self.edit_recipe_source, "source")):
            widget.setText(str(found.get(key) or ""))

    def save_recipe(self):
        """Save the recipe form to the database and refresh the displayed catalog state."""
        saved = logger.save_recipe({"name": value(self.edit_recipe_name),
                                    "sockets": value(self.edit_recipe_sockets),
                                    "combo": value(self.edit_recipe_combo),
                                    "category": value(self.edit_recipe_category),
                                    "level_band": value(self.edit_recipe_level),
                                    "source": value(self.edit_recipe_source)})
        self.refresh()
        self.note(f"{saved['name']} saved to Recipe DB.", True)

    def load_seed(self):
        """Populate the visible seed mapping for the selected family/socket count, leaving
        invalid socket input untouched.
        """
        try:
            sockets = int(value(self.edit_seed_sockets))
        except (ValueError, TypeError):
            return
        found = next((entry for entry in self.state["seed_states"]
                      if entry["family"] == value(self.edit_seed_family) and entry["sockets"] == sockets), None)
        self.edit_seed_slot.setText(found["seed_slot"] if found else "")
        self.edit_seed_rune.setText(found["seed_rune"] if found else "")
        self.edit_seed_rewards.setPlainText("\n".join(found["rewards"]) if found else "")

    def save_seed_mapping(self):
        """Save the visible seed mapping and rewards for a family/socket count, then refresh
        catalog state.
        """
        saved = logger.save_seed_state({"family": value(self.edit_seed_family),
                                        "sockets": value(self.edit_seed_sockets),
                                        "seed_slot": value(self.edit_seed_slot),
                                        "seed_rune": value(self.edit_seed_rune),
                                        "rewards": self.edit_seed_rewards.toPlainText()})
        self.refresh()
        self.note(f"Family {saved['family']} · {saved['sockets']}-socket visible seed saved.", True)

    def load_saved_scan(self, item):
        """Load a stored screenshot into seed mode after any active Remnant review is finished."""
        self._require_remnant_review_finished()
        scan_id = item.data(Qt.ItemDataRole.UserRole)
        path = store.image_for(scan_id)
        if not path or not path.exists():
            raise ValueError("Saved screenshot was not found.")
        self.mode = "seed"
        select(self.mode_select, "seed")
        self.images["seed"] = path.read_bytes()
        service.HOTKEY.set_mode("seed")
        self.current_file["seed"] = path.name
        self._show_image(self.images["seed"])
        self.tabs.setCurrentIndex(0)
        self.note("Saved screenshot loaded for review.")

    def closeEvent(self, event):
        """Stop timers and hotkey listening, detach global callbacks, and cancel queued workers
        as the window closes. Invalidate captures before releasing their HUD preparation
        callback so cancelled workers cannot resume capture or clipboard work after shutdown.
        """
        self._closed = True
        service.HOTKEY.cancel_capture()
        self._commit_badge_timer.stop()
        self._region_selection_token += 1
        if self._region_editor is not None:
            self._region_editor.reject()
        if service.HOTKEY.on_event is self._scan_ready_callback:
            service.HOTKEY.on_event = None
        if self._overlay_opacity_timer.isActive():
            self._overlay_opacity_timer.stop()
            self.run(self._save_overlay_opacity)
        self._overlay_enabled = False
        self._overlay_review_token += 1
        if service.HOTKEY.before_capture == self._prepare_overlay_capture:
            service.HOTKEY.before_capture = None
        self._poll.stop()
        service.HOTKEY._unregister()
        self._pending_tasks.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)


def instance_lock():
    """Acquire the data-directory instance lock, or focus an existing Windows logger and return
    None when another instance owns it, including previous beta and stable clients.
    """
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(store.DATA_DIR / "logger.lock"))
    lock.setStaleLockTime(0)
    if lock.tryLock(0):
        return lock
    if lock.error() != QLockFile.LockError.LockFailedError:
        raise RuntimeError("Could not access the logger's instance lock.")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        window = (user32.FindWindowW(None, WINDOW_TITLE)
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.2.6 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.2.5 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.2.4 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.2")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.1.3 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.1.2 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.1.1 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3.1 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.3 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.2.2 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.2.1 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.2 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 1.1 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 33.34.1 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger 33.34 Beta")
                  or user32.FindWindowW(None, "PoE2 Data Logger"))
        if window:
            user32.ShowWindow(window, 9)
            user32.SetForegroundWindow(window)
    return None


def main(smoke_test=False):
    """Initialize and run the single-instance desktop application, optionally checking bundled
    OCR in a short smoke run. Wait for workers before releasing the instance lock on exit.
    """
    application = QApplication(sys.argv)
    application.setWindowIcon(QIcon(str(HERE / "power_rune.ico")))
    try:
        lock = instance_lock()
        if lock is None:
            return 0
        logger.initialize()
        ocr_runtime.active_threads()
        if smoke_test:
            import numpy as np
            from PIL import ImageDraw, ImageFont
            from PoE2_Data_Logger.ocr import runehelper_ocr, scan
            scan._assets()
            runehelper_ocr._read_row(np.full((32, 160), 180, dtype=np.uint8))
            item_ocr.currency_ocr.get_reader()
            image = Image.new("RGB", (600, 150), "white")
            ImageDraw.Draw(image).text((20, 40), "Chaos Orb", fill="black",
                                       font=ImageFont.truetype(str(HERE / "fonts" / "DejaVuSans.ttf"), 36))
            if not any("Chaos Orb" in row["text"] for row in item_ocr.ocr_lines(image)):
                raise RuntimeError("Bundled text recognition failed.")
        service.HOTKEY.start()
        window = LoggerWindow()
    except Exception as error:
        if "lock" in locals() and lock:
            lock.unlock()
        if smoke_test:
            raise
        QMessageBox.critical(None, "PoE2 Data Logger could not start",
                             f"{error}\n\nData folder: {store.DATA_DIR}")
        return 1
    try:
        window.show()
        if smoke_test:
            QTimer.singleShot(300, window.close)
        return application.exec()
    finally:
        window.pool.shutdown(wait=True, cancel_futures=True)
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
