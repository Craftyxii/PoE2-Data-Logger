from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import sys
import tempfile
import threading
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
from PoE2_Data_Logger.ocr.affix_capture import new_affix_names, affix_unit, modifier_value
from PoE2_Data_Logger.ocr import item_ocr
from PoE2_Data_Logger.core import service
from PoE2_Data_Logger.core import store
from PoE2_Data_Logger.ui.region_select import RegionEditor, ScanRegionsPage
from PoE2_Data_Logger.core.workbook_export import export_xlsx


HERE = Path(__file__).resolve().parent.parent
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
            margin-top:12px; padding:20px 15px 14px; font-weight:600; }
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
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    return widget


def combo(values, selected=None):
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
    index = widget.findData(value)
    if index < 0:
        index = widget.findText(str(value))
    with QSignalBlocker(widget):
        widget.setCurrentIndex(max(0, index))


def value(widget):
    return widget.currentData() if isinstance(widget, QComboBox) else widget.text().strip()


def button(text, callback, role=None):
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    widget.clicked.connect(callback)
    return widget


def message(text=""):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setProperty("role", "message")
    widget.setProperty("guidance", bool(text))
    return widget


def set_message(widget, content, role="message"):
    widget.setText(content)
    if widget.property("guidance"):
        widget.setProperty("guidance", False)
        widget.show()
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def clear_waystone_read(result):
    fields = result.get("fields") or {}
    mods = result.get("mods") or []
    if (fields.get("tier") not in (15, 16) or fields.get("waystone") is None or
            fields.get("map_mods") is None or fields["map_mods"] != len(mods) or
            not 1 <= len(mods) <= 10):
        return False
    if result.get("source") == "screen OCR":
        rows = result.get("ocr_rows") or []
        return bool(rows) and all(float(row.get("score", 0)) >= .9 for row in rows
                                  if row.get("text", "").strip())
    return True


def clear_currency_read(result):
    items = result.get("items") or []
    return bool(items) and not result.get("unknown") and all(
        item.get("name") and type(item.get("quantity")) is int and
        item["quantity"] >= 1 and not item.get("count_needs_review") for item in items)


def clear_ritual_read(result, omen_names):
    items = result.get("items") or []
    return bool(items) and not result.get("unmatched") and all(
        item.get("category") in ("Omen", "Item") and
        (item["category"] != "Omen" or item.get("name") in omen_names) and
        (item["category"] != "Omen" or float(item.get("name_match", 0)) >= .9) and
        float(item.get("score", 0)) >= .94 and item.get("tribute") is not None and
        not item.get("needs_review") and
        not str(item.get("source", "")).startswith("icon reference") and
        not re.search(r"\b[x×]\s*\d+\b", str(item.get("source", "")), re.I)
        for item in items)


class IconCropCanvas(QWidget):
    def __init__(self, image):
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
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.picture)
        if not self.selection.isNull():
            painter.setPen(QPen(QColor("#E8AB30"), 2))
            painter.drawRect(self.selection)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.selection = QRect(self.origin, self.origin)
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            self.selection = QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect())
            self.origin = None
            self.update()

    def crop(self, image):
        area = self.selection.normalized()
        if area.width() < 12 or area.height() < 12:
            raise ValueError("Drag a box around one icon before saving the example.")
        box = (round(area.left() * image.width / self.width()),
               round(area.top() * image.height / self.height()),
               round((area.right() + 1) * image.width / self.width()),
               round((area.bottom() + 1) * image.height / self.height()))
        return image.crop(box)


class Tasks(QObject):
    completed = Signal(str, object, object)
    hide_overlay = Signal(object)
    scan_ready = Signal()


class LoggerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.state = logger.get_state()
        if not self.state["current_map_id"]:
            self.state = logger.start_map()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runeshape-ui")
        self.tasks = Tasks(self)
        self.tasks.completed.connect(self._task_done)
        self.mode = service.HOTKEY.status()["mode"]
        self.images = {"seed": None, "opened": None}
        self.results = {"seed": None, "opened": None}
        self.current_file = {"seed": "", "opened": ""}
        self.saved_scan = None
        self._seed_readings = []
        self._seed_loading = False
        self._both_seed_context = None
        self._both_link = None
        self._both_approved = False
        self._both_last_opened = None
        self.resolved = None
        self._pending_tasks = {}
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
        self._overlay_review_token = 0
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
        self._ritual_hash = None
        self._currency_catalog_loaded = False
        self.pending_review_kind = None
        self._pending_tablet_slot = None
        self._pending_currency_map = None
        self._pending_ritual_map = None
        self.tablet_raw_mods = [[] for _ in range(4)]
        self._extra_waystone_mods = []
        for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            QFontDatabase.addApplicationFont(str(HERE / "fonts" / font))
        self.setWindowTitle("PoE2 Data Logger")
        self.setWindowIcon(QIcon(str(HERE / "power_rune.ico")))
        self.resize(1280, 860)
        self.setStyleSheet(APP_STYLE)
        self._build()
        self.overlay_escape = QShortcut(QKeySequence("Escape"), self)
        self.overlay_escape.activated.connect(self.hide_overlay)
        self.overlay_escape.setEnabled(self._overlay_enabled)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowOpacity(self._overlay_opacity / 100 if self._overlay_enabled else 1.0)
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
        if self._closed:
            return
        self._task_id += 1
        key = str(self._task_id)
        self._pending_tasks[key] = (done, logger.session_generation())
        future = self.pool.submit(function)
        def finished(item):
            if self._closed:
                return
            try:
                self.tasks.completed.emit(key, item.result(), None)
            except Exception as error:
                self.tasks.completed.emit(key, None, error)
        future.add_done_callback(finished)
        self.statusBar().showMessage(label)

    def _task_done(self, key, result, error):
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
            self.error(str(error))
        elif callback:
            try:
                callback(result)
            except Exception as problem:
                self.error(str(problem))

    def run(self, action):
        try:
            return action()
        except Exception as error:
            self.error(str(error))
            return None

    def error(self, text):
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
        self.statusBar().showMessage(text, 6000)

    def open_discord(self):
        if not QDesktopServices.openUrl(QUrl(DISCORD_INVITE)):
            self.error("Could not open Discord in your default browser.")

    def _page(self):
        outer = QScrollArea()
        outer.setFrameShape(QFrame.Shape.NoFrame)
        outer.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 18, 12, 22)
        layout.setSpacing(12)
        outer.setWidget(body)
        return outer, layout

    def _group(self, title, parent):
        group = QGroupBox(title)
        content = QVBoxLayout(group)
        content.setSpacing(10)
        parent.addWidget(group)
        return content

    def _build(self):
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
        logo = self.brand_logo = QToolButton()
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
                                   "Kills / Currency", "Data export")):
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
        root.addWidget(sidebar)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(24, 10, 16, 4)
        main_layout.setSpacing(10)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        self.page_title = QLabel("Review")
        self.page_title.setStyleSheet("font-size:24px;font-weight:700;color:#FFFFFF;")
        self.page_subtitle = QLabel("Check the latest scan, then Approve to save it.")
        self.page_subtitle.setProperty("role", "subheading")
        self.page_subtitle.setWordWrap(True)
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_subtitle)
        top.addLayout(heading)
        top.addStretch()
        actions = QHBoxLayout()
        actions.addWidget(button("Undo new map", lambda: self.run(self.undo_map)))
        actions.addWidget(button("+ New map", lambda: self.run(self.finish_map), "primary"))
        top.addLayout(actions)
        main_layout.addLayout(top)
        cards = QHBoxLayout()
        self.stat_values = [QLabel("—") for _ in range(4)]
        for number in self.stat_values:
            number.setStyleSheet("font-size:17px;font-weight:700;color:#FFFFFF;")
        self.header_biome = combo(logger.BIOMES)
        self.header_city_type = combo(logger.CITY_TYPES)
        self.header_ocean = QCheckBox("Ocean")
        self.header_irradiated = QCheckBox("Irradiated")
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
                toggles = QHBoxLayout()
                toggles.addWidget(self.header_ocean)
                toggles.addWidget(self.header_irradiated)
                toggles.addStretch()
                card_layout.addLayout(toggles)
            else:
                card_layout.addWidget((self.header_biome, None, self.stat_values[2],
                                       self.header_city_type)[index])
            cards.addWidget(card, 1)
        main_layout.addLayout(cards)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
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
        for key, widgets in self._map_tag_controls().items():
            for widget in widgets:
                changed = widget.toggled if isinstance(widget, QCheckBox) else widget.currentIndexChanged
                changed.connect(lambda _, key=key, widget=widget:
                                self.run(lambda: self._save_map_tag(key, widget)))
        self.tabs.currentChanged.connect(self._page_changed)
        self._page_changed(0)
        self._apply_developer_mode()
        root.addWidget(main, 1)
        self.setCentralWidget(central)
        menu = self.menuBar().addMenu("File")
        menu.addAction("Save Export CSV as…", lambda: self.run(lambda: self.save_as("csv")))
        menu.addAction("Save Export XLSX as…", lambda: self.run(lambda: self.save_as("xlsx")))
        menu.addAction("Save database backup as…", lambda: self.run(lambda: self.save_as("backup")))
        self.help_menu = self.menuBar().addMenu("Help")
        for label, index in (("README", 9), ("Licenses", 10), ("Disclaimer", 11)):
            self.help_menu.addAction(label, lambda checked=False, index=index: self.tabs.setCurrentIndex(index))

    def _page_changed(self, index):
        if index != 0:
            self._overlay_auto_review = False
            self._overlay_review_token += 1
        if hasattr(self, "scan_regions"):
            if index == 7 and not self._editing_regions:
                service.HOTKEY._unregister()
                self._editing_regions = True
            elif index != 7 and self._editing_regions:
                self._editing_regions = False
                service.HOTKEY.start()
        titles = ("Review", "Expedition", "Map / Tablets", "Atlas Masters", "Kills / Currency",
                  "Data export", "Scan settings", "Scan regions", "Database Import/Export", "README", "Licenses", "Disclaimer")
        subtitles = ("Check the latest scan, then Approve to save it.",
                     "Set the expedition and log its propagation chain.",
                     "Read a waystone or tablet, then review and save the map setup.",
                     "Choose the active master and its perks.",
                     "Record map kills and currency inventory snapshots.",
                     "Save the local data and manage your databases.",
                     "Set the hotkeys and scan behaviour.",
                     "Adjust the labelled capture boxes on a game screenshot.",
                     "Label examples and share your OCR reference database.",
                     "", "", "")
        self.page_title.setText(titles[index])
        self.page_subtitle.setText(subtitles[index])
        self.page_subtitle.setVisible(bool(subtitles[index]))
        for i, item in enumerate(self.nav_buttons):
            item.setProperty("active", "true" if i == index else "false")
            item.style().unpolish(item)
            item.style().polish(item)

    def _build_readme(self):
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
        text = (HERE / value(self.license_picker)).read_text(encoding="utf-8")
        self.license_view.setPlainText(text)

    def _build_disclaimer(self):
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

    def _review_pending(self, kind, summary, can_commit=True, rows=None):
        if kind != "tablet":
            self._pending_tablet_slot = None
        self.pending_review_kind = kind
        self.review_kind.setText({"remnant": "Remnant", "seed": "Visible remnants",
                                  "waystone": "Waystone", "tablet": "Tablet",
                                  "currency": "Currency inventory", "ritual": "Ritual rewards"}[kind])
        set_message(self.review_summary, summary)
        self.found_table.setRowCount(0)
        for label, detail, check in rows or []:
            index = self.found_table.rowCount()
            self.found_table.insertRow(index)
            for column, text in enumerate((label, detail, check)):
                self.found_table.setItem(index, column, QTableWidgetItem(str(text)))
        self.found_table.setVisible(kind in ("waystone", "tablet") and
                                    self.found_table.rowCount() > 0)
        self.found_label.setVisible((kind in ("waystone", "tablet") and self.found_table.rowCount() > 0) or
                                    (kind == "currency" and self.inventory_table.rowCount() > 0) or
                                    (kind == "ritual" and self.ritual_table.rowCount() > 0))
        self.approve_scan_button.setEnabled(can_commit)
        self._review_controls(kind)
        self.review_edit_button.setVisible(kind in ("waystone", "tablet"))
        self.manual_remnant_button.setVisible(kind in ("remnant", "seed"))
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
        self.approve_scan_button.setVisible(kind is not None)
        self.reject_scan_button.setVisible(kind is not None)
        self.review_clear_tablets_button.setVisible(kind == "tablet")

    def _review_saved(self, kind, number):
        if self.pending_review_kind != kind:
            return
        self.pending_review_kind = None
        if self._overlay_auto_review:
            self.hide_overlay()
        if kind == "tablet":
            self._pending_tablet_slot = None
        elif kind == "currency":
            self._pending_currency_map = None
        elif kind == "ritual":
            self._pending_ritual_map = None
        self.approve_scan_button.setEnabled(False)
        self._review_controls(None)
        self.review_edit_button.hide()
        set_message(self.review_summary, f"✓ {self.review_kind.text()} saved · Scan Commit #{number}.", "success")

    def edit_review_settings(self):
        self.tabs.setCurrentIndex(2)

    def approve_review(self):
        kind = self.pending_review_kind
        if kind in ("seed", "remnant"):
            return self.approve_remnant_scan()
        if kind == "currency":
            known = {name.casefold() for name in logger.inventory_names()}
            controls = []
            for row in range(self.inventory_table.rowCount()):
                review = self.inventory_table.cellWidget(row, 3)
                if review and review.property("reviewStatus") == "rejected":
                    continue
                name = self.inventory_table.item(row, 1).text().strip()
                quantity = self.inventory_table.item(row, 2).text().strip()
                if name.casefold() not in known:
                    raise ValueError(f"Inventory row {row + 1}: choose a Currency or Item name, or reject the row.")
                logger._integer(quantity, name + " stack count", 0, 1000000)
                if review:
                    controls.append(review)
            for review in controls:
                self._set_currency_review(review, "approved")
        return self.commit_review()

    def reject_review(self):
        kind = self.pending_review_kind
        if kind in ("seed", "remnant"):
            return self.reject_remnant_scan()
        if kind is None:
            return
        self.pending_review_kind = None
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
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
        raise ValueError("There is no scan waiting to commit.")

    def _build_review(self):
        page, content = self._page()
        self.tabs.addTab(page, "Review")
        latest = self._group("SCAN REVIEW", content)
        self.review_content = latest
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
        self.manual_remnant_button = button("Enter remnant manually", lambda: self._review_pending(
            "remnant", "Enter and resolve the observed recipe below.", False))
        controls.addWidget(self.manual_remnant_button)
        controls.addStretch()
        latest.addLayout(controls)
        content.addStretch()

    def _build_expedition(self):
        page, content = self._page()
        self.tabs.addTab(page, "Expedition")
        self._build_chain_controls(content)
        content.addStretch()

    def _build_scan_settings(self):
        page, content = self._page()
        self.tabs.addTab(page, "Scan settings")
        self.tablet_region_label = QLabel()
        self.inventory_region_label = QLabel()
        self.ritual_region_label = QLabel()
        hotkey = self._group("Scan hotkey", content)
        hotkey.addWidget(QLabel("General scan reads the hovered item or selected remnant view. Optional direct keys target one scanner."))
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
                                               ("overlay", "Show / hide HUD")), 1):
            keys.addWidget(QLabel(title), index, 0)
            label = QLabel("Off")
            self.direct_hotkey_labels[kind] = label
            keys.addWidget(label, index, 1)
            keys.addWidget(button("Set key", lambda checked=False, target=kind: self.arm_hotkey(target)), index, 2)
            keys.addWidget(button("Clear", lambda checked=False, target=kind:
                                  self.run(lambda: self.clear_hotkey(target))), index, 3)
        hotkey.addLayout(keys)
        overlay = self._group("HUD overlay", content)
        self.overlay_checkbox = QCheckBox("Enable HUD overlay")
        self.overlay_checkbox.setChecked(self._overlay_enabled)
        self.overlay_checkbox.toggled.connect(lambda checked: self.run(lambda: self.set_overlay_enabled(checked)))
        overlay.addWidget(self.overlay_checkbox)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
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
        overlay_help = QLabel("Use the Show / hide HUD shortcut to open Review over the game. Held scans open it automatically; "
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
        mode.addWidget(QLabel("Hover a waystone or tablet and press the hotkey to read its copied item text."
                              " Results open on Review."))
        mode.addWidget(QLabel("Each scan is triggered by a hotkey press."))
        self.tablet_scan_number = combo([1, 2, 3, 4])
        self.tablet_scan_number.hide()
        self.tablet_scan_number.currentIndexChanged.connect(self.show_tablet_raw)
        content.addStretch()

    def _build_reference_database(self):
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
        self.reference_replace = QCheckBox("Update matching family, recipe and seed records on import")
        sharing.addWidget(self.reference_replace)
        self.reference_status = message("")
        sharing.addWidget(self.reference_status)
        content.addStretch()
        self.refresh_reference_names()
        self.refresh_reference_examples()

    def _build_settings(self):
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
        checks.addWidget(self.irradiated)
        checks.addWidget(self.ocean)
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
        self.tablet_scan_status = message("OCR fills the selected tablet for review. Save Tablet Config after checking the values.")
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
        page, content = self._page()
        self.tabs.addTab(page, "Kills / Currency")
        self._build_kill_controls(content)
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
        self.inventory_phase.currentIndexChanged.connect(lambda: self.refresh_currency_summary())
        phase = QHBoxLayout()
        phase.addWidget(QLabel("Snapshot"))
        phase.addWidget(self.inventory_phase)
        phase.addWidget(button("Add review row", self.add_inventory_row))
        phase.addStretch()
        review.addLayout(phase)
        self.inventory_table = QTableWidget(0, 4)
        self.inventory_table.setHorizontalHeaderLabels(["Slot", "Currency / Item", "Count", "Review"])
        self.inventory_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.inventory_table.setColumnWidth(0, 55)
        self.inventory_table.setColumnWidth(2, 75)
        self.inventory_table.setColumnWidth(3, 205)
        self.inventory_table.itemChanged.connect(self._inventory_row_changed)
        self.inventory_table.setMinimumHeight(360)
        review.addWidget(self.inventory_table)
        self.inventory_table.hide()
        review.addWidget(QLabel("Repeat scans keep their history and update this map's current totals."))
        self.currency_saved = message("Start and end snapshots show each currency's net change per map.")
        review.addWidget(self.currency_saved)

        examples = self._group("LOCAL INVENTORY ICON REFERENCES", content)
        examples.addWidget(QLabel("For artwork missing from the bundled catalog, choose a captured slot and label it. "
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
        content.addStretch()

    def _build_ritual(self):
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
        totals.addWidget(QLabel("Available Tribute"))
        totals.addWidget(self.ritual_tribute)
        totals.addWidget(QLabel("Rerolls remaining"))
        totals.addWidget(self.ritual_rerolls)
        review.addLayout(totals)
        self.ritual_table = QTableWidget(0, 6)
        self.ritual_table.setHorizontalHeaderLabels(["Type", "Omen / item name", "Quantity", "Tribute", "OCR source", "Deferred"])
        self.ritual_table.setColumnWidth(0, 90)
        self.ritual_table.setColumnWidth(2, 80)
        self.ritual_table.setColumnWidth(3, 90)
        self.ritual_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ritual_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.ritual_table.verticalHeader().setDefaultSectionSize(38)
        self.ritual_table.setMinimumHeight(300)
        review.addWidget(self.ritual_table)
        self.ritual_table.hide()
        actions = QHBoxLayout()
        actions.addWidget(button("Add reward row", self.add_ritual_row))
        actions.addWidget(button("Remove selected row", self.remove_ritual_row))
        actions.addStretch()
        review.addLayout(actions)
        review.addWidget(QLabel("Type is Omen or Item. Correct quantity and Tribute if visible; "
                                "leave Tribute blank if unreadable."))
        self.ritual_raw = QTextEdit()
        self.ritual_raw.setReadOnly(True)
        self.ritual_raw.setPlaceholderText("Full OCR text appears here so missing items can be added manually.")
        self.ritual_raw.setMaximumHeight(150)
        review.addWidget(self.ritual_raw)
        self.new_ritual_name = line("New Omen name")
        self.new_ritual_name.setParent(self)
        self.new_ritual_name.hide()
        self.ritual_saved = message("")
        review.addWidget(self.ritual_saved)

    def _build_chain_controls(self, content):
        chain = self._group("PROPAGATION CHAIN", content)
        self.expedition = combo([1, 2])
        self.expedition.currentIndexChanged.connect(lambda: self.run(self.set_expedition))
        expedition_row = QHBoxLayout()
        expedition_row.addWidget(QLabel("Expedition #"))
        expedition_row.addWidget(self.expedition)
        expedition_row.addWidget(button("Expedition 2 · new chain", lambda: self.run(self.new_chain)))
        expedition_row.addStretch()
        chain.addLayout(expedition_row)
        self.chain_note = message("Enter the whole chain, then commit it once.")
        chain.addWidget(self.chain_note)
        self.rune_grid = QGridLayout()
        self.rune_inputs = []
        chain.addLayout(self.rune_grid)
        self.add_runes(18)
        buttons = QHBoxLayout()
        buttons.addWidget(button("Commit chain", lambda: self.run(self.commit_chain), "primary"))
        buttons.addWidget(button("+ More runes", lambda: self.add_runes(6)))
        buttons.addStretch()
        chain.addLayout(buttons)
        self.chain_list = QListWidget()
        self.chain_list.setMaximumHeight(170)
        chain.addWidget(self.chain_list)
        self.chain_list.hide()

    def _build_kill_controls(self, content):
        kills = self._group("MAP KILLS & EXPEDITION DETONATIONS", content)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.normal = line("Normal kills")
        self.magic = line("Magic kills")
        self.rare = line("Rare kills")
        self.detonated = line("Remnants detonated")
        for i, (label, widget) in enumerate((("Normal kills", self.normal), ("Magic kills", self.magic),
                                            ("Rare kills", self.rare),
                                            ("Remnants detonated · expedition", self.detonated))):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(widget)
            grid.addLayout(column, i // 2, i % 2)
        kills.addLayout(grid)
        save = QHBoxLayout()
        save.addWidget(button("Save counts", lambda: self.run(self.save_counts), "primary"))
        save.addStretch()
        kills.addLayout(save)

    def _build_data(self):
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
        reset = self._group("START A FRESH LOG", content)
        reset.addWidget(QLabel("Clears imported and new Export rows, maps, chains, kills, currency, Ritual pages and IDs. "
                               "Your settings, families, recipes and saved OCR references remain."))
        reset_actions = QHBoxLayout()
        reset_actions.addWidget(button("Start fresh session / reset IDs…", lambda: self.run(self.reset_logger), "danger"))
        reset_actions.addStretch()
        reset.addLayout(reset_actions)
        self.developer_mode = QCheckBox("Developer Mode · show OCR details and database editors")
        with logger._connect() as db:
            self.developer_mode.setChecked(bool(logger._meta(db, "developer_mode", False)))
        self.developer_mode.toggled.connect(self.set_developer_mode)
        content.addWidget(self.developer_mode)
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
        with logger._connect() as db:
            logger._set_meta(db, "developer_mode", bool(enabled))
        self._apply_developer_mode()

    def _apply_developer_mode(self):
        enabled = self.developer_mode.isChecked()
        self.map_ocr_overrides.setVisible(enabled)
        for label in self.findChildren(QLabel):
            if label.property("guidance"):
                label.setVisible(enabled)
        self.ritual_table.setColumnHidden(4, not enabled)
        self.ritual_raw.setVisible(enabled)
        self.show_tablet_raw()
        self.counts.setVisible(enabled)
        for group in (self.diagnostics_group, self.catalog_group, self.editors_group, self.scans_group):
            group.setVisible(enabled)

    def add_runes(self, count):
        start = len(self.rune_inputs)
        for i in range(start, min(start + count, 96)):
            field = line(f"Rune {i + 1}")
            self.rune_grid.addWidget(QLabel(f"Rune {i + 1}"), i // 3 * 2, i % 3)
            self.rune_grid.addWidget(field, i // 3 * 2 + 1, i % 3)
            self.rune_inputs.append(field)

    def refresh(self):
        tablet_draft = None
        if self.pending_review_kind == "tablet" and self._pending_tablet_slot is not None:
            number = self._pending_tablet_slot
            start = (number - 1) * 4
            entries = [{"affix": value(a), "value": value(v),
                        "unit": v.property("unit") if v.property("affix") == value(a) else affix_unit(value(a))}
                       for a, v in zip(self.tablet_affixes[start:start + 4], self.tablet_values[start:start + 4])]
            tablet_draft = (number, entries, list(self.tablet_raw_mods[number - 1]), int(value(self.tablets_used)))
        self.state = logger.get_state()
        state = self.state
        config = state["settings"]
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
        self.update_area()
        select(self.perk_master, config["atlas_master"] if config["atlas_master"] != "None" else "Jado")
        self.render_perks()
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
        if tablet_draft is not None:
            number, entries, raw, capacity = tablet_draft
            self._fill_tablet_slot(number, entries, raw)
            select(self.tablets_used, max(capacity, number))
        self.tablet_active()
        select(self.expedition, config["expedition"])
        self.chain_note.setText(f"Commit the entire chain to {state['current_expedition_id'] or 'the first map'} in order.")
        self.chain_list.clear()
        for entry in state["chain"]:
            self.chain_list.addItem(f"#{entry['step']}  {entry['rune1']}")
        self.chain_list.setVisible(bool(state["chain"]))
        for index, widget in enumerate((self.normal, self.magic, self.rare)):
            widget.setText("" if state["kills"][index] is None else str(state["kills"][index]))
        self.detonated.setText("" if state["detonated"] is None else str(state["detonated"]))
        self.export_folder.setText(state["export_folder"])
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

    def refresh_hotkey(self):
        status = service.HOTKEY.status()
        self.hotkey_label.setText(status["combo"] or "Off")
        for kind, label in self.direct_hotkey_labels.items():
            label.setText(status["combos"].get(kind) or "Off")
        self._set_ocr_indicator()

    def _set_ocr_indicator(self):
        shortcuts = service.HOTKEY.status()
        scan_key = bool(shortcuts["combo"] or any(shortcuts["combos"].get(kind)
                        for kind in ("remnant", "waystone", "tablet", "currency", "ritual")))
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
        if number is None:
            return
        self._saved_badge = int(number)
        self.state["scan_commit_count"] = int(number)
        self.stat_values[3].setText(f"✓ #{number} SAVED")
        self.stat_values[3].setStyleSheet("font-size:17px;font-weight:700;color:#F5C364;")
        self._commit_badge_timer.start()

    def _clear_commit_badge(self, expected=None):
        if self._saved_badge is not None and (expected is None or self._saved_badge == expected):
            self._saved_badge = None
            self.stat_values[3].setText(f"#{self.state['scan_commit_count']}")
            self.stat_values[3].setStyleSheet("font-size:17px;font-weight:700;color:#FFFFFF;")

    def _map_tag_controls(self):
        return {"biome": (self.header_biome, self.biome),
                "city_type": (self.header_city_type, self.city_type),
                "ocean": (self.header_ocean, self.ocean),
                "irradiated": (self.header_irradiated, self.irradiated)}

    def _sync_map_tags(self, config):
        for key, widgets in self._map_tag_controls().items():
            for widget in widgets:
                if isinstance(widget, QCheckBox):
                    with QSignalBlocker(widget):
                        widget.setChecked(bool(config.get(key, False)))
                else:
                    select(widget, config.get(key, "None"))
        self.update_area()

    def _save_map_tag(self, key, widget):
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
        if not hasattr(self, "area"):
            return
        level = (79 if value(self.tier) == 15 else 80) + int(self.irradiated.isChecked()) + int(self.ocean.isChecked())
        set_message(self.area, f"Calculated Area Level: {level}")
        self.stat_values[2].setText(str(level))

    def tablet_active(self):
        for i, group in enumerate(self.tablet_groups):
            group.setVisible(i < int(value(self.tablets_used)))

    def render_perks(self):
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

    def save_map_settings(self):
        logger.save_settings({"waystone": value(self.waystone), "tier": value(self.tier),
                              "map_mods": value(self.map_mods), "irradiated": self.irradiated.isChecked(),
                              "ocean": self.ocean.isChecked(), "aldur": value(self.aldur),
                              "biome": value(self.biome), "city_type": value(self.city_type),
                              "item_rarity": value(self.item_rarity),
                              "monster_rarity": value(self.monster_rarity),
                              "pack_size": value(self.pack_size),
                              "effectiveness": value(self.effectiveness),
                              "waystone_name": value(self.waystone_name),
                              "waystone_mods": [value(field) for field in self.waystone_mod_fields]
                                                + self._extra_waystone_mods})
        commit_number = logger.record_commit("Map settings")
        self.refresh()
        self._set_commit_badge(commit_number)
        self._review_saved("waystone", commit_number)
        self.note("Map settings saved.")
        return commit_number

    def save_active_master(self):
        logger.save_settings({"atlas_master": value(self.master)})
        commit_number = logger.record_commit("Atlas Master")
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note("Active Atlas Master saved.", True)

    def save_perks(self):
        master = value(self.perk_master)
        selections = dict(self.state["settings"]["master_selections"])
        selections[master] = [value(item) for item in self.perk_boxes]
        logger.save_settings({"master_selections": selections})
        commit_number = logger.record_commit("Master perks", master)
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note(f"{master} perks saved.")

    def save_tablets(self):
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
        if reviewed == next_slot:
            logger.advance_tablet_slot(next_slot)
        self._review_saved("tablet", commit_number)
        self.refresh()
        self._set_commit_badge(commit_number)
        self.note(f"Tablet {reviewed} saved." if reviewed is not None else "Tablet config saved.")
        return commit_number

    def clear_tablets(self):
        logger.clear_tablets()
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
        logger.add_affix(value(self.new_affix))
        self.new_affix.clear()
        self.refresh()
        self.note("Affix added to tablet dropdowns.")

    def counts_data(self):
        return {"normal": value(self.normal), "magic": value(self.magic),
                "rare": value(self.rare), "detonated": value(self.detonated)}

    def finish_map(self):
        if self.state["current_map_id"] and not self.state["pending_new_map"]:
            previous = self.state["current_map_id"]
            service.dispatch("/api/finish-map", self.counts_data())
        else:
            previous = None
        state = logger.start_map()
        self.refresh()
        self.pending_review_kind = None
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = self.resolved = None
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
        self.review_kind.setText("New map")
        set_message(self.review_summary, "Waystone settings cleared. Scan or enter the new map's waystone.")
        if previous:
            self._set_commit_badge(state["scan_commit_count"])
        self.note(f"Map {state['current_map_id']} started."
                  + (" Previous map totals saved." if previous else ""), True)

    def new_chain(self):
        state = service.dispatch("/api/next-chain")
        for field in self.rune_inputs:
            field.clear()
        self.refresh()
        self.note(f"{state['current_expedition_id']} is ready for its chain.")

    def undo_map(self):
        logger.undo_empty_map()
        self.refresh()
        self.note("Empty map undone.")

    def set_expedition(self):
        logger.save_settings({"expedition": value(self.expedition)})
        self.refresh()

    def commit_chain(self):
        runes = [value(field) for field in self.rune_inputs]
        while runes and not runes[-1]:
            runes.pop()
        if not runes or any(not rune for rune in runes):
            raise ValueError("Fill runes in order, starting at Rune 1.")
        saved = logger.commit_chain_runes(runes)
        for field in self.rune_inputs:
            field.clear()
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        self.note(f"{saved['expedition_id']} · {len(saved['steps'])} runes saved in order.", True)

    def save_counts(self):
        data = self.counts_data()
        saved = logger.save_counts(data["normal"], data["magic"], data["rare"], data["detonated"])
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        self.note("Map kills and expedition detonations updated.", True)

    def edit_region(self):
        self.choose_scan_region("live_region")

    def arm_hotkey(self, target="default"):
        if self._capturing_hotkey:
            self._capturing_hotkey = False
            self.releaseKeyboard()
            self.overlay_escape.setEnabled(self._overlay_enabled)
            service.HOTKEY.start()
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
        if target == "default":
            service.HOTKEY.configure("")
        else:
            service.HOTKEY.configure_for(target, "")
        self.refresh_hotkey()
        action = "Show / hide HUD" if target == "overlay" else f"{target.title()} scan"
        self.note(f"{action} shortcut cleared.")

    def set_overlay_opacity(self, percent):
        self._overlay_opacity = max(20, min(100, int(percent)))
        self.overlay_opacity_label.setText(f"{self._overlay_opacity}%")
        self.setWindowOpacity(self._overlay_opacity / 100 if self._overlay_enabled else 1.0)
        self._overlay_opacity_timer.start()

    def _save_overlay_opacity(self):
        with logger._connect() as db:
            logger._set_meta(db, "hud_overlay_opacity", self._overlay_opacity)

    def set_overlay_enabled(self, enabled):
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
        self._overlay_review_token += 1
        visible, minimized = self.isVisible(), self.isMinimized()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setWindowOpacity(self._overlay_opacity / 100 if self._overlay_enabled else 1.0)
        if visible:
            self.showMinimized() if minimized else self.show()
        self.overlay_escape.setEnabled(self._overlay_enabled)
        self.overlay_toggle_button.setEnabled(self._overlay_enabled)
        with QSignalBlocker(self.overlay_checkbox):
            self.overlay_checkbox.setChecked(self._overlay_enabled)
        self.refresh_hotkey()
        self.note("HUD overlay enabled." if enabled else "HUD overlay disabled.", True)

    def toggle_overlay(self):
        if not self._overlay_enabled:
            return
        if self.isVisible() and not self.isMinimized():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self, automatic=False):
        if not self._overlay_enabled:
            return
        self._overlay_revealed = True
        self._overlay_auto_review = automatic
        self.tabs.setCurrentIndex(0)
        self.setWindowOpacity(self._overlay_opacity / 100)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_overlay(self):
        if self._overlay_enabled:
            if self._capturing_hotkey:
                self._capturing_hotkey = False
                self.releaseKeyboard()
                self.overlay_escape.setEnabled(True)
                service.HOTKEY.start()
            self._overlay_review_token += 1
            self._overlay_revealed = False
            self._overlay_auto_review = False
            self.hide()

    def _hide_overlay_capture(self, ready):
        try:
            self.hide_overlay()
        finally:
            ready.set()

    def _prepare_overlay_capture(self):
        if not self._overlay_enabled or not self._overlay_visible:
            return
        if QThread.currentThread() == self.thread():
            self.hide_overlay()
            return
        ready = threading.Event()
        self.tasks.hide_overlay.emit(ready)
        if not ready.wait(.75):
            raise ValueError("The HUD is busy. Hide it and try the scan again.")

    def _reveal_review_overlay(self, token):
        if (self._overlay_enabled and not self._closed and not self._editing_regions and
                not self._region_selection_pending and self.tabs.currentIndex() == 0 and
                token == self._overlay_review_token and self.pending_review_kind):
            self.show_overlay(automatic=True)

    def refresh_reference_names(self):
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
        if not hasattr(self, "reference_list"):
            return
        from PySide6.QtWidgets import QListWidgetItem
        self.reference_list.clear()
        for kind, entries in (("Omen", logger.omen_icons()),
                              ("Currency", logger.currency_icons()), ("Item", logger.item_icons())):
            for entry in entries:
                row = QListWidgetItem(f"{kind} · {entry['name']}")
                picture = QPixmap()
                picture.loadFromData(entry["image"])
                row.setIcon(QIcon(picture.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation)))
                row.setData(Qt.ItemDataRole.UserRole, (kind.lower(), entry["id"]))
                self.reference_list.addItem(row)

    def add_reference_screenshot(self):
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
        self.tabs.setCurrentIndex(0)
        self.mode = "seed"
        select(self.mode_select, "seed")
        service.HOTKEY.set_mode("seed")
        self.scan_file()

    def remove_reference_example(self):
        item = self.reference_list.currentItem()
        if not item:
            raise ValueError("Select an example to remove.")
        kind, number = item.data(Qt.ItemDataRole.UserRole)
        if kind == "omen":
            logger.delete_omen_icon(number)
        elif kind == "item":
            logger.delete_item_icon(number)
        else:
            logger.delete_currency_icon(number)
        self.refresh_reference_examples()
        self.refresh_currency_references()
        set_message(self.reference_status, "✓ Icon example removed.", "success")

    def export_reference_pack(self):
        default = DEFAULT_REFERENCE_FOLDER
        if Path(value(self.reference_folder)) == default:
            default.mkdir(parents=True, exist_ok=True)
        folder = logger.save_reference_export_folder(value(self.reference_folder))
        destination = Path(folder) / "PoE2_OCR_References.zip"
        set_message(self.reference_status, "Exporting OCR references…")
        def work():
            data = reference_pack.export_pack()
            fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return {"path": str(destination), "bytes": len(data)}
        self._submit("Exporting OCR references…", work,
                     lambda result: self._reference_exported(result))

    def _reference_exported(self, result):
        set_message(self.reference_status,
                    f"✓ Reference database exported · {result['bytes']:,} bytes · {result['path']}",
                    "success")
        self.note("Reference database exported.", True)

    def choose_reference_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose reference export folder",
                                                  value(self.reference_folder) or str(Path.home()))
        if folder:
            self.reference_folder.setText(logger.save_reference_export_folder(folder))

    def import_reference_pack(self):
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
        self.refresh()
        self.refresh_currency_references()
        self.refresh_reference_names()
        self.refresh_reference_examples()
        total = sum(counts.values())
        set_message(self.reference_status,
                    f"✓ Reference database imported · {total} new or updated records · "
                    f"{counts['scans']} remnant screenshots · {counts['currency_icons']} currency icons · "
                    f"{counts['omen_icons']} Omen icons · {counts['item_icons']} Item icons.", "success")
        self.note("Reference database imported and ready for the next scan.", True)

    def set_auto_commit(self, enabled):
        self.state = logger.save_settings({"auto_commit": enabled})
        self.note("Clear opened scans will save automatically." if enabled else
                  "Opened scans will wait for manual review before saving.", True)

    def set_auto_all(self, enabled):
        self.state = logger.save_settings({"ocr_auto_commit": enabled,
                                           "auto_commit": enabled,
                                           "tablet_auto_commit": enabled})
        self.refresh()
        self.note("Clear OCR scans will save across all activities." if enabled else
                  "Map, currency and Ritual scans will wait for review.", True)

    def set_auto_tablets(self, enabled):
        self.state = logger.save_settings({"tablet_auto_commit": enabled})
        self.refresh()
        self.note("Clear tablet scans will save in order from Tablet 1 to 4." if enabled else
                  "Tablet scans will wait for manual review before saving.", True)

    def eventFilter(self, source, event):
        if source is self and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._overlay_visible = event.type() == QEvent.Type.Show
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
            self._capturing_hotkey = False
            self.releaseKeyboard()
            self.overlay_escape.setEnabled(self._overlay_enabled)
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
            if target == "default":
                self.run(lambda: service.HOTKEY.configure(shortcut))
            else:
                self.run(lambda: service.HOTKEY.configure_for(target, shortcut))
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
        self.current_file[mode] = Path(filename).name
        self.images[mode] = raw
        self._show_image(raw)
        set_message(self.scan_status, "Reading image…")
        self.scan_status.show()
        with logger._connect() as db:
            number = logger._meta(db, "current_map_number", 0)
            if not number or logger._meta(db, "pending_new_map", False):
                number += 1
            map_id = logger._map_id(number)
        self._submit("Reading image…",
                     lambda: service.dispatch(f"/api/scan?mode={mode}",
                                              {"image": encoded, "map_id": map_id,
                                               "scan_generation": context["_scan_generation"]}),
                     lambda result: self._scan_done(mode, result, raw))

    def _scan_done(self, mode, result, raw):
        mode = result.get("mode", mode)
        self.show_result(mode, result, raw)
        if mode == "opened":
            self.maybe_auto_commit(result)
        elif mode == "seed":
            self.maybe_auto_commit_seeds()

    def _show_image(self, raw):
        if not raw:
            self.preview.setPixmap(QPixmap())
            self.preview.hide()
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(raw):
            self.preview.setPixmap(pixmap.scaled(1100, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
            self.preview.show()
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.hide()

    def _show_review_capture(self, picture):
        output = io.BytesIO()
        picture.save(output, format="PNG")
        self._show_image(output.getvalue())

    def show_result(self, mode, result, raw=None):
        logger.validate_scan_context(result)
        self._wheel_field = None
        self._clear_commit_badge()
        self.review_group.show()
        self.results[mode] = result
        self.images[mode] = raw or b""
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
                    ("_scan_generation", "_capture_map_id", "_capture_expedition")}
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
                    ("_scan_generation", "_capture_map_id", "_capture_expedition")}
                self._both_link = None
                self._both_approved = False
        status = result.get("status", "No reading.")
        success = result.get("family") and result.get("sockets") and mode == "opened" and result.get("can_use")
        set_message(self.scan_status, status, "success" if success else "error")
        self.scan_status.show()
        if result.get("remnant_id"):
            self.stat_values[0].setText(result["map_id"])
        if mode == "opened":
            found = result.get("opened_recipes") or []
            rows = [(f"Reward {i + 1}", item.get("recipe") or item.get("raw", ""),
                     "Matched" if item.get("recipe") else "Review")
                    for i, item in enumerate(found)]
            summary = f"{len(found)} opened rewards · {status}"
            self._review_pending("remnant", summary, bool(result.get("first_recipe")), rows)
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
                 ("Family", result.get("family") or "Unresolved", "Review")])
            self._seed_commit_enabled()
        if self.mode == "both" and mode == "opened":
            self._link_opened_to_seed(result)
        self.refresh_hotkey()

    def _populate_seed_table(self, selected_row=None):
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
        if self._seed_loading or not 0 <= row < len(self._seed_readings):
            return
        self._seed_loading = True
        reading = self._seed_readings[row]
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
        row = self.seed_table.currentRow()
        if self._seed_loading or not 0 <= row < len(self._seed_readings):
            return
        reading = self._seed_readings[row]
        if reading.get("saved"):
            return
        if not family_only:
            try:
                reading["sockets"] = int(value(self.seed_sockets))
            except ValueError:
                reading["sockets"] = None
            reading["seed_slot"] = value(self.seed_slot)
            reading["seed_rune"] = value(self.seed_rune)
            self._seed_loading = True
            self.update_seed_candidates()
            self._seed_loading = False
        family = value(self.seed_family)
        reading["family"] = f"Family {family}" if family else None
        reading["can_commit"] = False
        self._update_seed_row(row)
        if family_only and family:
            self.seed_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self._seed_commit_enabled()

    def _seed_commit_enabled(self):
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
        pending = [r for r in self._seed_readings if not r.get("saved") and not r.get("rejected")]
        if (self.pending_review_kind == "seed" and self.mode != "both" and pending and
                self.state["settings"].get("auto_commit") and
                all(r.get("can_commit") for r in pending)):
            self.commit_seed_review(automatic=True)

    def commit_seed_review(self, automatic=False, indices=None):
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
        for entry in saved["saved"]:
            self._seed_readings[entry["index"]]["saved"] = entry
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
        self.note(f"{ids} saved to {saved['saved'][0]['map_id']}.", True)

    @staticmethod
    def _opened_identity(opened):
        return (opened.get("family"), opened.get("sockets"),
                tuple(line.get("recipe") or line.get("raw") for line in opened.get("opened_recipes", [])))

    def _link_opened_to_seed(self, opened, chosen=None):
        self._both_link = None
        self._both_approved = False
        if not opened or not self.resolved or self.resolved.get("status") != "ready":
            self.approve_scan_button.setEnabled(False)
            return
        if self._both_seed_context != logger.scan_context():
            self.approve_scan_button.setEnabled(False)
            set_message(self.review_summary, "Scan the visible seeds for this map and expedition first.", "error")
            return
        family = self.resolved["family"]
        sockets = opened.get("sockets") or self.resolved["rows"][0]["sockets"]
        matches = [i for i, r in enumerate(self._seed_readings) if not r.get("saved") and
                   not r.get("rejected") and r.get("sockets") == sockets and
                   family in (r.get("candidates") or [])]
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
        self._seed_readings = []
        self._both_seed_context = None
        self._both_link = None
        self._both_approved = False
        self._both_last_opened = None
        self.seed_table.setRowCount(0)
        self.seed_table.hide()

    def update_seed_candidates(self, preferred=None):
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

    def maybe_auto_commit(self, opened):
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
        raw = self.images["seed"]
        if not raw:
            raise ValueError("Scan a visible seed first.")
        row = self.seed_table.currentRow()
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
                     self._seed_saved)

    def _seed_saved(self, result):
        self.saved_scan = result
        self.refresh()
        self.note(f"Reviewed scan #{result['id']} saved locally.", True)

    def discard_scan(self):
        logger.discard_ocr_id()
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = None
        self._show_image(None)
        self.review_group.hide()
        if self.pending_review_kind in ("remnant", "seed"):
            self.pending_review_kind = None
            self.review_kind.setText("Nothing waiting for review")
            set_message(self.review_summary, "Pending remnant scan discarded.")
            self.approve_scan_button.setEnabled(False)
        self.refresh()
        self.note("Pending OCR scan discarded.")

    def _invalidate_resolution(self):
        self.resolved = None
        if self.pending_review_kind == "remnant":
            self.approve_scan_button.setEnabled(bool(value(self.first_recipe) or
                (self.results["opened"] and self.results["opened"].get("first_recipe"))))
        self.recipe_table.hide()

    def resolve_recipe(self, preserve_review=False):
        result = logger.resolve(value(self.first_recipe), value(self.next_recipe),
                                value(self.recipe_family))
        self.resolved = result
        self.recipe_table.setRowCount(len(result["rows"]))
        self.recipe_table.setVisible(bool(result["rows"]))
        for row, item in enumerate(result["rows"]):
            for col, key in enumerate(("recipe", "sockets", "combo")):
                self.recipe_table.setItem(row, col, QTableWidgetItem(str(item[key])))
        if result["status"] == "ready":
            rows = [(f"Recipe {i + 1}", item["recipe"], f"{item['sockets']} sockets · {item['combo']}")
                    for i, item in enumerate(result["rows"])]
            if preserve_review and self.pending_review_kind == "remnant":
                self.approve_scan_button.setEnabled(True)
            else:
                self._review_pending("remnant", f"Family {result['family']} resolved · {len(rows)} verified recipes.",
                                     True, rows)
        elif self.pending_review_kind == "remnant":
            self.approve_scan_button.setEnabled(False)
        if result["status"] == "ready":
            set_message(self.recipe_status,
                        f"Family {result['family']} · {len(result['rows'])} verified export rows.", "success")
        elif result["status"] == "ambiguous":
            set_message(self.recipe_status,
                        f"Multiple families: {', '.join(map(str, result['candidates']))}. "
                        "Choose one or enter Next Recipe.", "error")
        else:
            set_message(self.recipe_status, "No matching family sequence.", "error")

    def commit_remnant(self):
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
            logger.validate_scan_context(self.results["opened"])
        saved = logger.commit_remnant(value(self.first_recipe), value(self.next_recipe),
                                      value(self.recipe_family) or self.resolved["family"],
                                      self.saved_scan["id"] if self.saved_scan else None,
                                      visible_seed={"sockets": both_reading["sockets"],
                                          "slot": both_reading["seed_slot"], "rune": both_reading["seed_rune"],
                                          "mode": "both"} if both_reading is not None else None)
        if both_reading is not None:
            both_reading["saved"] = saved
            self._both_last_opened = self._opened_identity(self.results["opened"])
        self._saved_remnant(saved)
        if both_reading is not None:
            self._both_seed_context = logger.scan_context()
            self._populate_seed_table()

    def _saved_remnant(self, saved):
        self._review_saved("remnant", saved["scan_commit_number"])
        self.first_recipe.clear()
        self.next_recipe.clear()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.saved_scan = None
        self.resolved = None
        self._show_image(None)
        self.review_group.hide()
        self.opened_lines.clear()
        self.approve_scan_button.setEnabled(False)
        self.recipe_table.setRowCount(0)
        self.recipe_table.hide()
        self.refresh()
        self._set_commit_badge(saved["scan_commit_number"])
        set_message(self.scan_status, f"{saved['remnant_id']} · Family {saved['family']} · "
                    f"{saved['recipes']} recipe rows saved to Export.", "success")
        self.scan_status.show()
        self.note(f"{saved['remnant_id']} logged to {saved['map_id']}.", True)

    def poll(self):
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
        event = hotkey["latest"]
        if event and event["id"] != self._latest_hotkey:
            self._latest_hotkey = event["id"]
            if event["error"]:
                self.error(event["error"])
            elif event["mode"] == "item":
                self.run(lambda: self._hover_item_read(event["result"], service.HOTKEY.image(event["id"])))
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
                        self.run(lambda: self._inventory_captured(picture, expected_map_id=event["result"].get("map_id")))
                    else:
                        self.run(lambda: self._ritual_captured(picture, expected_map_id=event["result"].get("map_id")))
            else:
                self.run(lambda: self.show_result(event["mode"], event["result"], service.HOTKEY.image(event["id"])))
                if event["mode"] == "opened":
                    self.run(lambda: self.maybe_auto_commit(event["result"]))
                elif event["mode"] == "seed":
                    self.run(self.maybe_auto_commit_seeds)

    def _hover_item_read(self, result, raw=None):
        logger.validate_scan_context(result)
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
        if fields["tier"] in (15, 16):
            select(self.tier, fields["tier"])
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
                                           ("map_mods", "Map Mods")) if fields[key] is None]
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
                             f"{len(result.get('mods', []))} modifiers", True, rows)
        if self.state["settings"].get("ocr_auto_commit"):
            if clear_waystone_read(result):
                number = self.save_map_settings()
                set_message(self.map_scan_status,
                            f"✓ Waystone scan saved as Scan Commit #{number}.", "success")
            else:
                set_message(self.map_scan_status,
                            "Auto-commit held: review the waystone fields, then Approve.")

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose local export folder",
                                                  value(self.export_folder) or str(Path.home()))
        if folder:
            self.export_folder.setText(folder)

    def _scan_region(self, key):
        with logger._connect() as db:
            return logger._meta(db, key, None)

    def choose_scan_region(self, key):
        self.tabs.setCurrentIndex(7)
        self.scan_regions.select_region(key)

    def _regions_saved(self):
        self.refresh_aux_regions()
        self.note("Scan regions saved.", True)

    def select_region_in_game(self, key):
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
                image = ImageGrab.grab(bbox=bounds, all_screens=True).convert("RGB")
                editor = RegionEditor(region_for(key, bounds), parent=self, screenshot=image,
                                      screen_bounds=(left, top, right - left, bottom - top))
                self._region_editor = editor
                if editor.exec() == QDialog.DialogCode.Accepted:
                    region = editor.region()
                    self.scan_regions.canvas.boxes[key] = [(region["x"] - left) / (right - left),
                                                          (region["y"] - top) / (bottom - top),
                                                          region["w"] / (right - left), region["h"] / (bottom - top)]
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
        logger.validate_scan_context(result)
        auto = self.auto_tablet_checkbox.isChecked()
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
        result = item_ocr.parse_tablet(ocr_rows if ocr_rows is not None else original_mods,
                                        self.state["affixes"])
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
        if preview_matches:
            if auto and number == 1:
                for affix, amount in zip(self.tablet_affixes, self.tablet_values):
                    select(affix, "")
                    amount.clear()
                self.tablet_raw_mods = [[] for _ in range(4)]
            select(self.tablets_used, max(int(value(self.tablets_used)), number))
            select(self.tablet_scan_number, number)
        if preview_matches:
            self._fill_tablet_slot(number, preview_matches, original_mods)
        self.show_tablet_raw()
        rows = [(f"Affix {i + 1}" if item["score"] >= .94 else "Uncertain", item["affix"],
                 f"{item['value']}%" if item["unit"] == "%" else f"{item['value']} {item['unit']}")
                for i, item in enumerate(preview_matches)]
        rows += [("Uncertain", item, "Review") for item in result["uncertain"]
                 if item not in {match["raw"] for match in preview_matches}]
        if not rows and ocr_rows is not None:
            rows = [("OCR text", item["text"], "No affix recognized")
                    for item in ocr_rows[:12] if item.get("text")]
        summary = (f"Tablet {number} · {len(preview_matches)} affixes" if preview_matches else
                   "No tablet affixes read. Check that the captured preview includes the full tooltip and adjust its region.")
        self._pending_tablet_slot = number if preview_matches else None
        self._review_pending("tablet", summary, bool(preview_matches), rows)
        if auto and result["status"] == "ready" and all(
                item["score"] >= .94 for item in result["matches"]):
            saved_number = logger.save_scanned_tablet(result["matches"], original_mods)
            self.state = logger.get_state()
            self._set_commit_badge(self.state["scan_commit_count"])
            self._review_saved("tablet", self.state["scan_commit_count"])
            select(self.tablets_used, self.state["settings"]["tablets_used"])
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
        if hasattr(self, "tablet_raw_preview"):
            number = int(value(self.tablet_scan_number)) - 1
            self.tablet_raw_preview.setPlainText("\n".join(self.tablet_raw_mods[number]))
            self.tablet_raw_preview.setVisible(self.developer_mode.isChecked() and
                                               bool(self.tablet_raw_mods[number]))

    def test_inventory_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open a cropped inventory grid", str(Path.home()),
                                               "Images (*.png *.jpg *.jpeg)")
        if path:
            if Path(path).stat().st_size > service.MAX_UPLOAD:
                raise ValueError("Inventory test image exceeds 16 MB.")
            with Image.open(path) as source:
                if source.width * source.height > 12_000_000:
                    raise ValueError("Inventory test image exceeds 12 megapixels.")
                image = source.convert("RGB")
            self._inventory_captured(image, live=False)

    def _inventory_captured(self, image, live=True, expected_map_id=None):
        map_id = expected_map_id or logger.currency_target_map(value(self.inventory_phase))
        image = item_ocr.inventory_grid(image)
        self._inventory_capture = image
        self._show_review_capture(image)
        self.inventory_table.setRowCount(0)
        self.inventory_table.hide()
        self._show_inventory_preview(image)
        references = logger.inventory_icons()
        self._submit("Matching inventory icons…",
                     lambda: item_ocr.scan_inventory_grid(image, references),
                     lambda result: self._inventory_read(result, live, map_id))

    def _show_inventory_preview(self, image):
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

    def _inventory_read(self, result, live=True, expected_map_id=None):
        map_id = logger.currency_target_map(value(self.inventory_phase))
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The currency scan belongs to {expected_map_id}. Scan {map_id} again.")
        self._pending_currency_map = map_id
        self.inventory_table.setRowCount(0)
        for item in result["items"]:
            self.add_inventory_row(item)
        unknown = result["unknown"]
        for item in unknown:
            candidate = item.get("candidate", "")
            self.add_inventory_row({"slot": item["slot"],
                                    "name": "",
                                    "quantity": "", "count_needs_review": True,
                                    "candidate": candidate})
        if unknown:
            self.icon_slot.setValue(unknown[0]["slot"])
        set_message(self.inventory_status,
                    f"{len(result['items'])} inventory stacks matched. "
                    f"{len(unknown)} uncertain slots or shared-icon tiers. Edit names or counts, then approve or reject flagged rows. "
                    "Approve saves the reviewed snapshot.",
                    "success" if result["items"] and not unknown else "message")
        self._review_pending("currency",
                             f"{len(result['items'])} inventory stacks · {len(unknown)} uncertain slots. "
                             "Review the editable list below.")
        if live and self.state["settings"].get("ocr_auto_commit"):
            if clear_currency_read(result):
                self.save_inventory()
                set_message(self.inventory_status,
                            f"✓ Inventory snapshot saved as Scan Commit #{self.state['scan_commit_count']}.",
                            "success")
            else:
                set_message(self.inventory_status,
                            "Auto-commit held: approve or reject the uncertain rows to save the snapshot.")

    def add_inventory_row(self, item=None):
        item = item if isinstance(item, dict) else {}
        row = self.inventory_table.rowCount()
        self.inventory_table.insertRow(row)
        self.inventory_table.show()
        for column, text in enumerate((item.get("slot", ""), item.get("name", ""),
                                       item.get("quantity", ""))):
            cell = QTableWidgetItem(str(text))
            if column == 0:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            else:
                cell.setToolTip("Double-click to edit the currency or item name." if column == 1 else
                                "Double-click to edit the whole-number count, then approve the row.")
            if column == 1 and item.get("candidate"):
                cell.setToolTip("Possible match: " + item["candidate"] + ". Enter the correct name, then approve.")
            self.inventory_table.setItem(row, column, cell)
        controls = QWidget()
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        status = QLabel()
        status.setObjectName("currencyReviewStatus")
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
        controls.setProperty("requiresApproval", bool(item.get("count_needs_review") or not item))
        self.inventory_table.setCellWidget(row, 3, controls)
        self.inventory_table.setRowHeight(row, 62)
        self._set_currency_review(controls, "pending" if controls.property("requiresApproval") else "approved")

    def _set_currency_review(self, controls, state):
        controls.setProperty("reviewStatus", state)
        status = controls.findChild(QLabel, "currencyReviewStatus")
        status.setText({"pending": "Confirm name / count", "approved": "Approved", "rejected": "Rejected"}[state])
        status.setStyleSheet("color:#F5C364;" if state != "rejected" else "color:#BCB7AE;")
        controls.findChild(QPushButton, "approveCurrency").setEnabled(state != "approved")
        controls.findChild(QPushButton, "rejectCurrency").setEnabled(state != "rejected")

    def _inventory_row_changed(self, item):
        if item.column() not in (1, 2):
            return
        controls = self.inventory_table.cellWidget(item.row(), 3)
        if controls and controls.property("requiresApproval") and controls.property("reviewStatus") != "rejected":
            self._set_currency_review(controls, "pending")

    def review_currency_row(self, controls, approve):
        row = next((r for r in range(self.inventory_table.rowCount())
                    if self.inventory_table.cellWidget(r, 3) is controls), None)
        if row is None:
            return
        if approve:
            name = self.inventory_table.item(row, 1).text().strip()
            quantity = self.inventory_table.item(row, 2).text().strip()
            if name.lower() not in {n.lower() for n in logger.inventory_names()}:
                raise ValueError("Enter a name from the Currency or Item database before approving this row.")
            logger._integer(quantity, name + " stack count", 0, 1000000)
        self._set_currency_review(controls, "approved" if approve else "rejected")
        if self.pending_review_kind == "currency":
            states = [self.inventory_table.cellWidget(i, 3).property("reviewStatus")
                      for i in range(self.inventory_table.rowCount())]
            if states and "pending" not in states:
                if all(state == "rejected" for state in states):
                    self.reject_review()
                else:
                    self.save_inventory()

    def save_icon_example(self):
        if self._inventory_capture is None:
            raise ValueError("Capture or open an inventory grid first.")
        name = value(self.icon_name)
        slot = self.icon_slot.value()
        cell = item_ocr.inventory_cell(self._inventory_capture, slot)
        kind = "item" if name in logger.item_names() else "currency"
        icon_id = logger.save_item_icon(name, cell) if kind == "item" else logger.save_currency_icon(name, cell)
        self.refresh_currency_references()
        self._inventory_captured(self._inventory_capture, live=False)
        self.note(f"Saved {name} icon example #{icon_id} from inventory slot {slot}.", True)

    def add_currency_name(self):
        name = logger.add_currency_item(value(self.currency_new_name))
        self.currency_new_name.clear()
        self.refresh_currency_references()
        select(self.icon_name, name)
        self.note(f"Added {name} to Currency DB.", True)

    def remove_icon_example(self):
        selected = self.icon_list.currentItem()
        if not selected:
            raise ValueError("Select an icon example to remove.")
        kind, number = selected.data(Qt.ItemDataRole.UserRole)
        if kind == "item":
            logger.delete_item_icon(number)
        else:
            logger.delete_currency_icon(number)
        self.refresh_currency_references()
        self.note("Icon example removed.")

    def refresh_currency_references(self):
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
            entry.setData(Qt.ItemDataRole.UserRole, (reference["kind"], reference["id"]))
            self.icon_list.addItem(entry)

    def save_inventory(self):
        phase = value(self.inventory_phase)
        rows = []
        for row in range(self.inventory_table.rowCount()):
            controls = self.inventory_table.cellWidget(row, 3)
            if controls and controls.property("reviewStatus") == "rejected":
                continue
            if controls and controls.property("reviewStatus") == "pending":
                raise ValueError(f"Inventory row {row + 1}: edit the name/count and approve it, or reject it before saving.")
            name = self.inventory_table.item(row, 1)
            amount = self.inventory_table.item(row, 2)
            rows.append({"name": name.text().strip() if name else "",
                         "quantity": amount.text().strip() if amount else ""})
        saved = logger.save_currency_snapshot(phase, rows,
                                             self._pending_currency_map if self.pending_review_kind == "currency" else None)
        self.state = logger.get_state()
        self._set_commit_badge(self.state["scan_commit_count"])
        self._review_saved("currency", self.state["scan_commit_count"])
        self.refresh_currency_summary()
        self.note(f"{phase.title()} inventory saved to {saved['map_id']} · {len(rows)} stacks.", True)
        return saved

    def refresh_currency_summary(self):
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

    def test_ritual_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open a Ritual reward page", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg)")
        if path:
            if Path(path).stat().st_size > 16_000_000:
                raise ValueError("Ritual test image exceeds 16 MB.")
            with Image.open(path) as source:
                if source.width * source.height > 12_000_000:
                    raise ValueError("Ritual test image exceeds 12 megapixels.")
                image = source.convert("RGB")
            self._ritual_captured(image, live=False)

    def _ritual_captured(self, image, live=True, expected_map_id=None):
        map_id = expected_map_id or logger.get_state()["current_map_id"]
        if image.width * image.height > 12_000_000:
            raise ValueError("Ritual capture exceeds 12 megapixels.")
        self._ritual_hash = hashlib.sha256(image.tobytes()).hexdigest()
        self._show_review_capture(image)
        self.ritual_table.setRowCount(0)
        self.ritual_table.hide()
        self.ritual_raw.clear()
        names = logger.ritual_names()
        references = logger.omen_icons()
        self._submit("Reading Ritual rewards…",
                     lambda: item_ocr.scan_ritual_page(image, names, references),
                     lambda result: self._ritual_read(result, live, map_id))

    def _ritual_read(self, result, live=True, expected_map_id=None):
        map_id = logger.get_state()["current_map_id"]
        if expected_map_id is not None and expected_map_id != map_id:
            raise ValueError(f"The Ritual scan belongs to {expected_map_id}. Scan {map_id} again.")
        self._pending_ritual_map = map_id
        self.ritual_tribute.setText("" if result.get("tribute_available") is None else str(result["tribute_available"]))
        self.ritual_rerolls.setText("" if result.get("rerolls_remaining") is None else str(result["rerolls_remaining"]))
        self.ritual_table.setRowCount(0)
        for item in result["items"]:
            self.add_ritual_row(item)
        self.ritual_raw.setPlainText(result["raw_text"])
        set_message(self.ritual_status,
                    f"{len(result['items'])} rewards found. Check names and amounts, add anything missed, "
                    "then Approve.")
        self._review_pending("ritual", f"{len(result['items'])} Ritual rewards. "
                             "Review the editable list below.")
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
        item = item if isinstance(item, dict) else {}
        row = self.ritual_table.rowCount()
        self.ritual_table.insertRow(row)
        self.ritual_table.show()
        fields = (item.get("category", "Item"), item.get("name", ""),
                  item.get("quantity", 1), item.get("tribute", ""), item.get("source", "manual"))
        for column, field in enumerate(fields):
            self.ritual_table.setItem(row, column, QTableWidgetItem(
                "" if field is None else str(field)))
        deferred = QTableWidgetItem()
        deferred.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        deferred.setCheckState(Qt.CheckState.Checked if item.get("deferred") else Qt.CheckState.Unchecked)
        self.ritual_table.setItem(row, 5, deferred)

    def remove_ritual_row(self):
        row = self.ritual_table.currentRow()
        if row < 0:
            raise ValueError("Select a reward row to remove.")
        self.ritual_table.removeRow(row)
        if self.ritual_table.rowCount() == 0:
            self.ritual_table.hide()

    def save_ritual(self):
        rows = []
        for row in range(self.ritual_table.rowCount()):
            def field(col, row=row):
                cell = self.ritual_table.item(row, col)
                return cell.text().strip() if cell else ""
            rows.append({"category": field(0), "name": field(1), "quantity": field(2),
                         "tribute": field(3), "source": field(4),
                         "deferred": self.ritual_table.item(row, 5).checkState() == Qt.CheckState.Checked})
        saved = logger.save_ritual_page(rows, self.ritual_raw.toPlainText(), self._ritual_hash,
                                       self._pending_ritual_map if self.pending_review_kind == "ritual" else None,
                                       value(self.ritual_tribute), value(self.ritual_rerolls))
        self.state = logger.get_state()
        self._set_commit_badge(saved["scan_commit_number"])
        self._review_saved("ritual", saved["scan_commit_number"])
        self.refresh_ritual_summary()
        self.note(f"{saved['map_id']} Ritual page {saved['page_number']} "
                  f"{'updated' if saved['updated'] else 'saved'} · {saved['count']} rewards.", True)
        return saved

    def add_ritual_name(self):
        name = logger.add_ritual_name(value(self.new_ritual_name))
        self.new_ritual_name.clear()
        self.note(f"Added {name} to local Omen names.", True)

    def refresh_ritual_summary(self):
        map_id = self.state["current_map_id"]
        pages = logger.ritual_pages_for_map(map_id) if map_id else []
        rewards = sum(len(page["items"]) for page in pages)
        self.ritual_saved.setText(f"{map_id or 'No active map'} · {len(pages)} saved Ritual pages · "
                                  f"{rewards} reward entries")

    def write_export(self, kind):
        logger.save_export_folder(value(self.export_folder))
        self._submit(f"Writing {kind.upper()}…",
                     lambda: logger.save_export_file(kind),
                     lambda result: self._export_saved(result))

    def _export_saved(self, result):
        self.refresh()
        self.note(f"Export saved: {result['path']}", True)

    def save_as(self, kind):
        choices = {
            "csv": ("PoE2_Export.csv", "CSV (*.csv)", logger.export_all_csv),
            "xlsx": ("PoE2_Export.xlsx", "Excel workbook (*.xlsx)", export_xlsx),
            "backup": ("PoE2_Data_Backup.sqlite3", "SQLite database (*.sqlite3)", logger.backup_bytes),
        }
        name, extension, produce = choices[kind]
        path, _ = QFileDialog.getSaveFileName(self, "Save file", str(Path.home() / name), extension)
        if path:
            def write():
                data = produce()
                destination = Path(path)
                logger._write_export_bytes(destination, data)
                return {"path": str(destination), "bytes": len(data)}
            self._submit(f"Writing {kind.upper()}…", write, self._export_saved)

    def reset_logger(self):
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
        self._clear_seed_queue()
        self.results = {"seed": None, "opened": None}
        self.images = {"seed": None, "opened": None}
        self.pending_review_kind = None
        self._pending_currency_map = self._pending_ritual_map = self._pending_tablet_slot = None
        self.saved_scan = self.resolved = None
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
        family_id = value(self.family_pick)
        found = next((item for item in self.state["families"] if item["id"] == family_id), None)
        next_id = max((item["id"] for item in self.state["families"]), default=0) + 1
        self.family_id.setText(str(found["id"] if found else next_id))
        self.family_top.setText(str(found["top_socket"] if found else 6))
        self.family_active.setChecked(found["valid"] if found else True)
        self.family_recipes.setPlainText("\n".join(found["recipes"]) if found else "")

    def save_family(self):
        saved = logger.save_family({"family": value(self.family_id), "top_socket": value(self.family_top),
                                    "valid": self.family_active.isChecked(),
                                    "recipes": self.family_recipes.toPlainText()})
        self.refresh()
        select(self.family_pick, saved["family"])
        self.load_family()
        self.note(f"Family {saved['family']} saved.", True)

    def load_recipe(self):
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
        saved = logger.save_recipe({"name": value(self.edit_recipe_name),
                                    "sockets": value(self.edit_recipe_sockets),
                                    "combo": value(self.edit_recipe_combo),
                                    "category": value(self.edit_recipe_category),
                                    "level_band": value(self.edit_recipe_level),
                                    "source": value(self.edit_recipe_source)})
        self.refresh()
        self.note(f"{saved['name']} saved to Recipe DB.", True)

    def load_seed(self):
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
        saved = logger.save_seed_state({"family": value(self.edit_seed_family),
                                        "sockets": value(self.edit_seed_sockets),
                                        "seed_slot": value(self.edit_seed_slot),
                                        "seed_rune": value(self.edit_seed_rune),
                                        "rewards": self.edit_seed_rewards.toPlainText()})
        self.refresh()
        self.note(f"Family {saved['family']} · {saved['sockets']}-socket visible seed saved.", True)

    def load_saved_scan(self, item):
        scan_id = item.data(Qt.ItemDataRole.UserRole)
        path = store.image_for(scan_id)
        if not path or not path.exists():
            raise ValueError("Saved screenshot was not found.")
        self.mode = "seed"
        select(self.mode_select, "seed")
        self.images["seed"] = path.read_bytes()
        self.current_file["seed"] = path.name
        self._show_image(self.images["seed"])
        self.tabs.setCurrentIndex(0)
        self.note("Saved screenshot loaded for review.")

    def closeEvent(self, event):
        self._closed = True
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
        window = user32.FindWindowW(None, "PoE2 Data Logger")
        if window:
            user32.ShowWindow(window, 9)
            user32.SetForegroundWindow(window)
    return None


def main():
    application = QApplication(sys.argv)
    application.setWindowIcon(QIcon(str(HERE / "power_rune.ico")))
    try:
        lock = instance_lock()
        if lock is None:
            return 0
        logger.initialize()
        service.HOTKEY.start()
        window = LoggerWindow()
    except Exception as error:
        if "lock" in locals() and lock:
            lock.unlock()
        QMessageBox.critical(None, "PoE2 Data Logger could not start",
                             f"{error}\n\nData folder: {store.DATA_DIR}")
        return 1
    try:
        window.show()
        return application.exec()
    finally:
        window.pool.shutdown(wait=True, cancel_futures=True)
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
