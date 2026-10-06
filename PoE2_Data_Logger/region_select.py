from __future__ import annotations

import math
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabBar, QVBoxLayout, QWidget

import logger_store as logger
import store


REGIONS = {
    "live_region": ("Opened remnant", "opened", (0, .12, .32, .72), "#E5AA32"),
    "seed_region": ("Visible seeds", "seed", (0, 0, 1, 1), "#E5AA32"),
    "waystone_region": ("Waystone tooltip", "inventory", (0, 0, 1, 1), "#E5AA32"),
    "tablet_region": ("Tablet tooltip", "inventory", (0, 0, 1, 1), "#EFCBA1"),
    "inventory_region": ("Currency / items", "inventory", (.601, .609, .339, .259), "#C18DC9"),
    "ritual_region": ("Ritual rewards", "ritual", (.098, .106, .379, .78), "#E5AA32"),
}


def checked_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("Select a valid scan region.")
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in box):
        raise ValueError("Select a valid scan region.")
    x, y, w, h = box
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise ValueError("Keep the scan region inside the screenshot.")
    return [float(x), float(y), float(w), float(h)]


def region_for(key, bounds):
    if key not in REGIONS:
        raise ValueError("Unknown scan region.")
    with logger._connect() as db:
        boxes = logger._meta(db, "scan_region_boxes", {})
        legacy = logger._meta(db, key, None)
    if key not in boxes and legacy:
        from live_watch import validate_region
        return validate_region(legacy)
    x, y, w, h = checked_box(boxes.get(key, REGIONS[key][2]))
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if not 0 < width * height <= store.MAX_IMAGE_PIXELS:
        raise ValueError("The game display must be under 12 megapixels.")
    x1, y1 = round(x * width), round(y * height)
    x2, y2 = round((x + w) * width), round((y + h) * height)
    return {"x": left + x1, "y": top + y1, "w": x2 - x1, "h": y2 - y1}


class RegionCanvas(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.picture = QPixmap()
        self.boxes = {key: list(item[2]) for key, item in REGIONS.items()}
        self.active = "live_region"
        self.operation = ""

    def image_rect(self):
        if self.picture.isNull():
            return QRectF()
        scale = min(self.width() / self.picture.width(), self.height() / self.picture.height())
        w, h = self.picture.width() * scale, self.picture.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def box_rect(self, key):
        area = self.image_rect()
        x, y, w, h = self.boxes[key]
        return QRectF(area.x() + x * area.width(), area.y() + y * area.height(),
                      w * area.width(), h * area.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111214"))
        area = self.image_rect()
        if area.isEmpty():
            return
        painter.drawPixmap(area, self.picture, QRectF(self.picture.rect()))
        keys = [self.active]
        for index, key in enumerate(keys):
            title, _, _, color = REGIONS[key]
            box = self.box_rect(key).adjusted(2, 2, -2, -2)
            painter.setPen(QPen(QColor(color), 3 if key == self.active else 1,
                                Qt.PenStyle.SolidLine if key == self.active else Qt.PenStyle.DashLine))
            painter.drawRect(box)
            label_y = min(box.bottom() - 22, box.top() + 3 + index * 25)
            label = QRectF(box.left() + 4, label_y, min(175, box.width() - 8), 23)
            painter.fillRect(label, QColor("#181512"))
            painter.setPen(QColor(color))
            painter.drawText(label.adjusted(5, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, title)
            if key == self.active:
                painter.fillRect(QRectF(box.bottomRight() - QPointF(10, 10), box.bottomRight()), QColor(color))

    def _position(self, point):
        area = self.image_rect()
        return QPointF(max(0, min(1, (point.x() - area.x()) / area.width())),
                       max(0, min(1, (point.y() - area.y()) / area.height())))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(event.position()):
            return
        self.setFocus()
        self.anchor = self._position(event.position())
        self.initial = list(self.boxes[self.active])
        box = self.box_rect(self.active)
        handle = QRectF(box.bottomRight() - QPointF(20, 20), box.bottomRight())
        self.operation = "resize" if handle.contains(event.position()) else "move" if box.contains(event.position()) else "draw"

    def mouseMoveEvent(self, event):
        if not self.operation:
            return
        pos = self._position(event.position())
        x, y, w, h = self.initial
        if self.operation == "move":
            box = [max(0, min(1 - w, x + pos.x() - self.anchor.x())),
                   max(0, min(1 - h, y + pos.y() - self.anchor.y())), w, h]
        elif self.operation == "resize":
            box = [x, y, min(1 - x, max(.02, pos.x() - x)), min(1 - y, max(.02, pos.y() - y))]
        else:
            box = [min(self.anchor.x(), pos.x()), min(self.anchor.y(), pos.y()),
                   max(.001, abs(pos.x() - self.anchor.x())), max(.001, abs(pos.y() - self.anchor.y()))]
            box[2], box[3] = min(box[2], 1 - box[0]), min(box[3], 1 - box[1])
        self.boxes[self.active] = box
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.operation = ""


class ScanRegionsPage(QWidget):
    saved = Signal()
    select_in_game = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = Path(__file__).resolve().parent / "region_examples"
        self.custom = store.DATA_DIR / "region_examples"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 22)
        self.region_tabs = QTabBar()
        self.region_tabs.setExpanding(True)
        self.region_tabs.setStyleSheet("QTabBar::tab { background:#1C1C1E; color:#BCB7AE; padding:10px; border-bottom:2px solid #3D352C; }"
                                     "QTabBar::tab:selected { color:#FBE3B2; border-bottom:2px solid #E5AA32; }")
        self.keys = list(REGIONS)
        for key, title in zip(self.keys, ("Opened remnant", "Visible seeds", "Waystone", "Tablet", "Currency / items", "Ritual")):
            self.region_tabs.addTab(title)
        layout.addWidget(self.region_tabs)
        toolbar = QHBoxLayout()
        live = QPushButton("Select region in game")
        live.clicked.connect(lambda: self.select_in_game.emit(self.canvas.active))
        toolbar.addWidget(live)
        load = QPushButton("Use own screenshot…")
        load.clicked.connect(self.load_screenshot)
        toolbar.addWidget(load)
        defaults = QPushButton("Reset selected region")
        defaults.clicked.connect(self.reset_region)
        toolbar.addWidget(defaults)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.canvas = RegionCanvas()
        with logger._connect() as db:
            boxes = logger._meta(db, "scan_region_boxes", {})
            for key in REGIONS:
                if key in boxes:
                    self.canvas.boxes[key] = checked_box(boxes[key])
                else:
                    legacy = logger._meta(db, key, None)
                    if legacy and 0 <= legacy["x"] < 1920 and 0 <= legacy["y"] < 1080:
                        proposed = (legacy["x"] / 1920, legacy["y"] / 1080, legacy["w"] / 1920, legacy["h"] / 1080)
                        try:
                            self.canvas.boxes[key] = checked_box(proposed)
                        except ValueError:
                            pass
        layout.addWidget(self.canvas, 1)
        self.status = QLabel("Drag inside a box to move it; drag its bottom-right corner to resize it.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        save = QPushButton("Save regions")
        save.setProperty("role", "primary")
        save.clicked.connect(self.save_regions)
        actions.addStretch()
        actions.addWidget(save)
        layout.addLayout(actions)
        self.region_tabs.currentChanged.connect(self._select)
        self.canvas.changed.connect(lambda: self.status.setText("Unsaved changes. Save regions when ready."))
        self._select()

    def select_region(self, key):
        if key in self.keys:
            self.region_tabs.setCurrentIndex(self.keys.index(key))

    def _select(self):
        key = self.keys[self.region_tabs.currentIndex()]
        self.canvas.active = key
        group = REGIONS[key][1]
        path = self.custom / (group + ".jpg")
        if not path.is_file():
            path = self.root / (group + ".jpg")
        self.canvas.picture = QPixmap(str(path))
        self.canvas.update()

    def load_screenshot(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a full game screenshot", "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 16_000_000:
                raise ValueError("Use a screenshot under 16 MB.")
            with Image.open(path) as image:
                if image.width * image.height > store.MAX_IMAGE_PIXELS or min(image.size) < 300:
                    raise ValueError("Use a full game screenshot under 12 megapixels.")
                picture = image.convert("RGB")
            self.custom.mkdir(parents=True, exist_ok=True)
            target = self.custom / (REGIONS[self.canvas.active][1] + ".jpg")
            temporary = target.with_suffix(".tmp")
            picture.save(temporary, format="JPEG", quality=92)
            temporary.replace(target)
            self._select()
            self.status.setText("Screenshot loaded. Adjust the boxes, then Save regions.")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Screenshot could not be loaded", str(error))

    def reset_region(self):
        key = self.canvas.active
        self.canvas.boxes[key] = list(REGIONS[key][2])
        self.canvas.update()
        self.canvas.changed.emit()

    def save_regions(self):
        try:
            boxes = {key: checked_box(box) for key, box in self.canvas.boxes.items()}
            if any(box[2] < 200 / 1920 or box[3] < 100 / 1080 for box in boxes.values()):
                raise ValueError("Make each region at least 200×100 pixels at 1080p.")
            with logger._connect() as db:
                logger._set_meta(db, "scan_region_boxes", boxes)
            self.status.setText("✓ Scan regions saved.")
            self.saved.emit()
        except ValueError as error:
            QMessageBox.warning(self, "Region could not be saved", str(error))


class RegionEditor(QDialog):
    def __init__(self, current=None, parent=None, screenshot=None, screen_bounds=None):
        super().__init__(parent)
        bounds = QRect(*screen_bounds) if screen_bounds else QRect()
        if not screen_bounds:
            for screen in QGuiApplication.screens():
                bounds = bounds.united(screen.geometry())
        self.origin = bounds.topLeft()
        self.setGeometry(bounds)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, screenshot is None)
        self.picture = QPixmap()
        if screenshot is not None:
            raw = io.BytesIO()
            screenshot.save(raw, format="PNG")
            self.picture.loadFromData(raw.getvalue())
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.selection = QRect()
        if current:
            top = QPoint(int(current["x"]), int(current["y"])) - self.origin
            self.selection = QRect(top.x(), top.y(), int(current["w"]), int(current["h"]))
        self.anchor = QPoint()
        self.initial = QRect()
        self.operation = ""
        self.hint = "Drag to select · drag inside to move · drag the corner to resize\nEnter to save · Esc to cancel"
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save region")
        save.clicked.connect(self.confirm)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(save)
        actions.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addLayout(actions)

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
            user32.SetForegroundWindow(wintypes.HWND(int(self.winId())))

    def hideEvent(self, event):
        self.releaseKeyboard()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.picture.isNull():
            painter.drawPixmap(self.rect(), self.picture)
        painter.fillRect(self.rect(), QColor(10, 10, 12, 165))
        if self.selection.isValid():
            if self.picture.isNull():
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(self.selection, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            else:
                painter.drawPixmap(self.selection, self.picture, self.selection)
            painter.setPen(QPen(QColor("#E5AA32"), 3))
            painter.drawRect(self.selection)
            handle = QRect(self.selection.right() - 13, self.selection.bottom() - 13, 13, 13)
            painter.fillRect(handle, QColor("#E5AA32"))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(self.font())
        painter.drawText(QRect(20, 16, max(200, self.width() - 40), 55),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         self.hint)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        self.anchor = pos
        self.initial = QRect(self.selection)
        handle = QRect(self.selection.right() - 22, self.selection.bottom() - 22, 28, 28)
        if self.selection.isValid() and handle.contains(pos):
            self.operation = "resize"
        elif self.selection.contains(pos):
            self.operation = "move"
        else:
            self.operation = "draw"
            self.selection = QRect(pos, pos)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.operation:
            return
        pos = event.position().toPoint()
        if self.operation == "move":
            moved = self.initial.translated(pos - self.anchor)
            moved.moveLeft(max(0, min(moved.left(), self.width() - moved.width())))
            moved.moveTop(max(0, min(moved.top(), self.height() - moved.height())))
            self.selection = moved
        elif self.operation == "resize":
            end = QPoint(max(self.initial.left() + 199, min(pos.x(), self.width() - 1)),
                         max(self.initial.top() + 99, min(pos.y(), self.height() - 1)))
            self.selection = QRect(self.initial.topLeft(), end).normalized()
        else:
            self.selection = QRect(self.anchor, pos).normalized()
        self.update()

    def mouseReleaseEvent(self, event):
        self.operation = ""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def confirm(self):
        if self.selection.width() >= 200 and self.selection.height() >= 100:
            self.accept()
        else:
            self.hint = "Select a region at least 200×100 pixels.\nEnter to save · Esc to cancel"
            self.update()

    def region(self):
        top = self.selection.topLeft() + self.origin
        return {"x": top.x(), "y": top.y(), "w": self.selection.width(), "h": self.selection.height()}
