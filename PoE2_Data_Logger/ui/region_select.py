"""Edit normalized OCR crops and convert selection overlays to native capture pixels.

Resolution presets validate the full capture size; they do not resample OCR pixels.
Windows overlays match physical monitor bounds to Qt logical geometry using DPI.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabBar, QVBoxLayout, QWidget

from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import store


REGIONS = {
    "live_region": ("Opened remnant", "opened", (0, .12, .32, .72), "#E5AA32"),
    "propagation_region": ("Propagation · include the left cursor", "opened", (0, 0, .35, 1), "#EFCBA1"),
    "seed_region": ("Visible seeds", "seed", (0, 0, 1, 1), "#E5AA32"),
    "waystone_region": ("Waystone tooltip", "inventory", (0, 0, 1, 1), "#E5AA32"),
    "tablet_region": ("Tablet tooltip", "inventory", (0, 0, 1, 1), "#EFCBA1"),
    "inventory_region": ("Currency / items", "inventory", (.601, .609, .339, .259), "#C18DC9"),
    "ritual_region": ("Ritual rewards", "ritual", (.098, .106, .379, .78), "#E5AA32"),
}

GAME_RESOLUTIONS = {
    "auto": ("Auto · game window", None),
    "1920x1080": ("1080p · 1920 × 1080", (1920, 1080)),
    "2560x1440": ("1440p · 2560 × 1440", (2560, 1440)),
    "3840x2160": ("4K · 3840 × 2160", (3840, 2160)),
    "2560x1080": ("Ultrawide · 2560 × 1080", (2560, 1080)),
    "3440x1440": ("Ultrawide · 3440 × 1440", (3440, 1440)),
    "3840x1600": ("Ultrawide · 3840 × 1600", (3840, 1600)),
}


def validate_capture_resolution(bounds, resolution=None):
    """Check the full native game window before cropping; never resize its pixels."""
    if resolution is None:
        with logger._connect() as db:
            resolution = logger._meta(db, "scan_region_resolution", "auto")
    if not isinstance(resolution, str) or resolution not in GAME_RESOLUTIONS:
        raise ValueError("Choose Auto or a supported game resolution in Scan regions, then Save regions.")
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if width <= 0 or height <= 0 or width * height > store.MAX_IMAGE_PIXELS:
        raise ValueError("The game display must be under 12 megapixels.")
    expected = GAME_RESOLUTIONS[resolution][1]
    if expected is not None and (width, height) != expected:
        raise ValueError(f"Choose Auto or the correct game resolution in Scan regions, then Save regions. "
                         f"The game window is {width} × {height}; the selected resolution is "
                         f"{expected[0]} × {expected[1]}.")


def checked_box(box):
    """Validate finite normalized x, y, width, and height within the screenshot."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("Select a valid scan region.")
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in box):
        raise ValueError("Select a valid scan region.")
    x, y, w, h = box
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
        raise ValueError("Keep the scan region inside the screenshot.")
    return [float(x), float(y), float(w), float(h)]


def region_for(key, bounds, *, for_selection=False, resolution=None):
    """Map normalized boxes to native pixels; hold legacy crops with unknown bounds."""
    if key not in REGIONS:
        raise ValueError("Unknown scan region.")
    with logger._connect() as db:
        boxes = logger._meta(db, "scan_region_boxes", {})
        legacy = logger._meta(db, key, None)
        if resolution is None:
            resolution = logger._meta(db, "scan_region_resolution", "auto")
    # Validate full client pixels, before the legacy 1080p shortcut or any crop.
    validate_capture_resolution(bounds, resolution)
    if key not in boxes and legacy:
        from PoE2_Data_Logger.platform.live_watch import validate_region
        if for_selection:
            try:
                legacy = validate_region(legacy)
            except ValueError:
                legacy = None
        else:
            legacy = validate_region(legacy)
        left, top, right, bottom = bounds
        if legacy is None:
            pass
        elif tuple(bounds) != (0, 0, 1920, 1080):
            if not for_selection:
                raise ValueError(f"Select the {REGIONS[key][0]} region again, then Save regions. "
                                 "The older pixel region has no saved game resolution or display.")
        else:
            if (legacy["x"] < left or legacy["y"] < top or
                    legacy["x"] + legacy["w"] > right or legacy["y"] + legacy["h"] > bottom):
                if not for_selection:
                    raise ValueError("The saved scan region is outside the game. Select the region again.")
            else:
                return legacy
    x, y, w, h = checked_box(boxes.get(key, REGIONS[key][2]))
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if not 0 < width * height <= store.MAX_IMAGE_PIXELS:
        raise ValueError("The game display must be under 12 megapixels.")
    x1, y1 = round(x * width), round(y * height)
    x2, y2 = round((x + w) * width), round((y + h) * height)
    return {"x": left + x1, "y": top + y1, "w": x2 - x1, "h": y2 - y1}


def _native_monitor_bounds(capture):
    """Locate the physical game monitor and its Windows display device name."""
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        """Match the Windows monitor-info layout, including physical bounds and device name."""
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MonitorFromRect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
        user32.MonitorFromRect.restype = wintypes.HANDLE
        rect = wintypes.RECT(capture.x(), capture.y(), capture.x() + capture.width(),
                            capture.y() + capture.height())
        monitor = user32.MonitorFromRect(ctypes.byref(rect), 0)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ValueError("Could not locate the game display. Select the region again.")
        native = info.rcMonitor
        return info.szDevice, QRect(native.left, native.top,
                                    native.right - native.left, native.bottom - native.top)
    except (AttributeError, OSError) as error:
        raise ValueError("Could not locate the game display. Select the region again.") from error


def _logical_capture_bounds(capture):
    """Anchor native game pixels to the matching Qt screen before applying DPI."""
    if sys.platform == "win32":
        name, native = _native_monitor_bounds(capture)
        normalized_name = name.removeprefix("\\\\.\\").casefold()
        screen = next((screen for screen in QGuiApplication.screens()
                       if screen.name().removeprefix("\\\\.\\").casefold() == normalized_name), None)
        if screen is None:
            raise ValueError("The game display changed. Select the region again after reconnecting the display.")
        if not native.contains(capture):
            raise ValueError("Keep the game capture inside one display, then select the region again.")
    else:
        screen = QGuiApplication.screenAt(capture.center()) or QGuiApplication.primaryScreen()
        native = screen.geometry() if screen else QRect()
    if screen is None:
        raise ValueError("Could not locate the game display. Select the region again.")
    ratio = screen.devicePixelRatio()
    logical = screen.geometry()
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("Could not determine the game display scale. Select the region again.")
    if sys.platform == "win32" and (abs(logical.width() * ratio - native.width()) > 2 * ratio or
                                    abs(logical.height() * ratio - native.height()) > 2 * ratio):
        raise ValueError("The game display scale changed. Select the region again after adjusting the display.")
    # Qt scales monitor sizes on Windows, while retaining monitor origins.
    # Only the offset inside the matched native monitor is divided by DPI.
    return QRect(round(logical.x() + (capture.x() - native.x()) / ratio),
                 round(logical.y() + (capture.y() - native.y()) / ratio),
                 round(capture.width() / ratio), round(capture.height() / ratio))


class RegionCanvas(QWidget):
    """Edit normalized scan boxes over a scaled screenshot preview."""
    changed = Signal()

    def __init__(self, parent=None):
        """Initialize default normalized boxes and the active drag state."""
        super().__init__(parent)
        # Let the preview shrink on a 720-pixel-tall desktop so resolution,
        # calibration and Save controls remain reachable above/below it.
        self.setMinimumSize(500, 160)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.picture = QPixmap()
        self.boxes = {key: list(item[2]) for key, item in REGIONS.items()}
        self.active = "live_region"
        self.operation = ""

    def image_rect(self):
        """Fit the screenshot proportionally in the canvas with centred letterboxing."""
        if self.picture.isNull():
            return QRectF()
        scale = min(self.width() / self.picture.width(), self.height() / self.picture.height())
        w, h = self.picture.width() * scale, self.picture.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def box_rect(self, key):
        """Map a normalized region box into the displayed screenshot rectangle."""
        area = self.image_rect()
        x, y, w, h = self.boxes[key]
        return QRectF(area.x() + x * area.width(), area.y() + y * area.height(),
                      w * area.width(), h * area.height())

    def paintEvent(self, event):
        """Draw the screenshot and active region with its label and resize handle."""
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
        """Convert a canvas pointer to clamped normalized screenshot coordinates."""
        area = self.image_rect()
        return QPointF(max(0, min(1, (point.x() - area.x()) / area.width())),
                       max(0, min(1, (point.y() - area.y()) / area.height())))

    def mousePressEvent(self, event):
        """Choose move, resize, or draw mode from the active box and handle hit areas."""
        if event.button() != Qt.MouseButton.LeftButton or not self.image_rect().contains(event.position()):
            return
        self.setFocus()
        self.anchor = self._position(event.position())
        self.initial = list(self.boxes[self.active])
        box = self.box_rect(self.active)
        handle = QRectF(box.bottomRight() - QPointF(20, 20), box.bottomRight())
        self.operation = "resize" if handle.contains(event.position()) else "move" if box.contains(event.position()) else "draw"

    def mouseMoveEvent(self, event):
        """Adjust the normalized draft box within image bounds and emit a calibration change."""
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
        """End the current normalized-box drag operation."""
        self.operation = ""


class ScanRegionsPage(QWidget):
    """Draft normalized regions and a resolution constraint, persisting them only on Save."""
    saved = Signal()
    select_in_game = Signal(str)

    def __init__(self, parent=None):
        """Preview normalized crops without guessing the origin of legacy pixel crops."""
        super().__init__(parent)
        self.root = Path(__file__).resolve().parent.parent / "region_examples"
        self.custom = store.DATA_DIR / "region_examples"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 22)
        self.region_tabs = QTabBar()
        self.region_tabs.setExpanding(True)
        self.region_tabs.setStyleSheet("QTabBar::tab { background:#1C1C1E; color:#BCB7AE; padding:10px; border-bottom:2px solid #3D352C; }"
                                     "QTabBar::tab:selected { color:#FBE3B2; border-bottom:2px solid #E5AA32; }")
        self.keys = list(REGIONS)
        for key, (title, _, _, _) in REGIONS.items():
            index = self.region_tabs.addTab(title)
            self.region_tabs.setTabData(index, key)
        layout.addWidget(self.region_tabs)
        resolution_row = QHBoxLayout()
        resolution_label = QLabel("Game resolution")
        self.game_resolution = QComboBox()
        self.game_resolution.setAccessibleName("Game resolution")
        for mode, (title, _size) in GAME_RESOLUTIONS.items():
            self.game_resolution.addItem(title, mode)
        with logger._connect() as db:
            mode = logger._meta(db, "scan_region_resolution", "auto")
        self.game_resolution.setCurrentIndex(max(0, self.game_resolution.findData(mode)))
        resolution_label.setBuddy(self.game_resolution)
        resolution_row.addWidget(resolution_label)
        resolution_row.addWidget(self.game_resolution)
        resolution_row.addStretch()
        layout.addLayout(resolution_row)
        self.resolution_note = QLabel("Auto uses the current game window. Presets must match the game's resolution. "
                                      "Windows display scaling is handled automatically.")
        self.resolution_note.setWordWrap(True)
        layout.addWidget(self.resolution_note)
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
        self._legacy_regions = set()
        with logger._connect() as db:
            boxes = logger._meta(db, "scan_region_boxes", {})
            for key in REGIONS:
                if key in boxes:
                    self.canvas.boxes[key] = checked_box(boxes[key])
                else:
                    legacy = logger._meta(db, key, None)
                    if legacy:
                        self._legacy_regions.add(key)
        layout.addWidget(self.canvas, 1)
        self.status = QLabel("Drag inside a box to move it; drag its bottom-right corner to resize it.")
        if self._legacy_regions:
            self.status.setText(self._legacy_instruction())
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
        self.canvas.changed.connect(self._region_changed)
        self.game_resolution.currentIndexChanged.connect(self._resolution_changed)
        self._select()

    def _resolution_changed(self):
        """Keep the resolution selection as a draft until Save regions is pressed."""
        message = "Resolution changed. Save regions to apply; your region boxes are unchanged."
        if self._legacy_regions:
            message += " " + self._legacy_instruction()
        self.status.setText(message)

    def _legacy_instruction(self):
        """Name the legacy regions that still need explicit calibration."""
        titles = ", ".join(REGIONS[key][0] for key in self.keys if key in self._legacy_regions)
        return f"Select these older regions again to save them: {titles}. " \
               "Use a current full game screenshot or Select region in game; Reset selected region accepts its default box."

    def calibrate_region(self, key, box):
        """Accept an explicitly selected normalized box for one capture mode."""
        if key not in REGIONS:
            raise ValueError("Unknown scan region.")
        self.canvas.boxes[key] = checked_box(box)
        self._legacy_regions.discard(key)
        self.status.setText(self._legacy_instruction() if self._legacy_regions else
                            "Unsaved changes. Save regions when ready.")
        self.canvas.update()

    def _region_changed(self):
        """Treat an explicit drag or default reset as calibration of the active box."""
        self.calibrate_region(self.canvas.active, self.canvas.boxes[self.canvas.active])

    def select_region(self, key):
        """Activate the tab for a recognized region key."""
        if key in self.keys:
            self.region_tabs.setCurrentIndex(self.keys.index(key))

    def _select(self):
        """Show the active region over its custom group screenshot or bundled example."""
        key = self.region_tabs.tabData(self.region_tabs.currentIndex())
        if key not in REGIONS:
            return
        self.canvas.active = key
        group = REGIONS[key][1]
        path = self.custom / (group + ".jpg")
        if not path.is_file():
            path = self.root / (group + ".jpg")
        self.canvas.picture = QPixmap(str(path))
        self.canvas.update()

    def load_screenshot(self):
        """Check file and image size limits and atomically replace the group preview as JPEG."""
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
        """Accept the active default box as an explicit calibration in the draft."""
        key = self.canvas.active
        self.canvas.boxes[key] = list(REGIONS[key][2])
        self.canvas.update()
        self.canvas.changed.emit()

    def save_regions(self):
        """Save trusted normalized boxes while retaining unresolved legacy crops."""
        try:
            boxes = {key: checked_box(box) for key, box in self.canvas.boxes.items()
                     if key not in self._legacy_regions}
            if not boxes:
                raise ValueError(self._legacy_instruction())
            if any(box[2] < 200 / 1920 or box[3] < 100 / 1080 for box in boxes.values()):
                raise ValueError("Make each region at least 200×100 pixels at 1080p.")
            with logger._connect() as db:
                logger._set_meta(db, "scan_region_boxes", boxes)
                logger._set_meta(db, "scan_region_resolution", self.game_resolution.currentData())
            status = "✓ Scan regions saved."
            if self._legacy_regions:
                status += " " + self._legacy_instruction()
            self.status.setText(status)
            self.saved.emit()
        except ValueError as error:
            QMessageBox.warning(self, "Region could not be saved", str(error))


class RegionEditor(QDialog):
    """Select in widget coordinates and return native pixels when capture bounds are supplied."""
    def __init__(self, current=None, parent=None, screenshot=None, screen_bounds=None):
        """Display a physical game capture at the matched monitor's logical geometry."""
        super().__init__(parent)
        self.capture_bounds = QRect(*screen_bounds) if screen_bounds else None
        bounds = QRect(*screen_bounds) if screen_bounds else QRect()
        if screen_bounds:
            if screenshot is not None and screenshot.size != (bounds.width(), bounds.height()):
                raise ValueError("The screenshot does not match the game capture bounds. Select the region again.")
            bounds = _logical_capture_bounds(bounds)
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
            alpha = "A" in screenshot.getbands() or "transparency" in screenshot.info
            image = screenshot.convert("RGBA" if alpha else "RGB")
            raw = image.tobytes()
            self.picture = QPixmap.fromImage(QImage(
                raw, image.width, image.height, image.width * (4 if alpha else 3),
                QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888).copy())
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.selection = QRect()
        if current:
            if self.capture_bounds is not None:
                scale_x = self.width() / self.capture_bounds.width()
                scale_y = self.height() / self.capture_bounds.height()
                self.selection = QRect(round((current["x"] - self.capture_bounds.x()) * scale_x),
                                       round((current["y"] - self.capture_bounds.y()) * scale_y),
                                       round(current["w"] * scale_x), round(current["h"] * scale_y))
            else:
                top = QPoint(int(current["x"]), int(current["y"])) - self.origin
                self.selection = QRect(top.x(), top.y(), int(current["w"]), int(current["h"]))
            self.selection = self.selection.intersected(self.rect())
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
        """Grab selection keys and align the Windows overlay to physical capture bounds."""
        super().showEvent(event)
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if self.capture_bounds is not None:
                user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
                bounds = self.capture_bounds
                user32.SetWindowPos(wintypes.HWND(int(self.winId())), wintypes.HWND(-1),
                                    bounds.x(), bounds.y(), bounds.width(), bounds.height(), 0)
            user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
            user32.SetForegroundWindow(wintypes.HWND(int(self.winId())))

    def hideEvent(self, event):
        """Release the keyboard grab when the selection overlay closes."""
        self.releaseKeyboard()
        super().hideEvent(event)

    def resizeEvent(self, event):
        """Rescale the selection with widget size when physical capture bounds are known."""
        previous = event.oldSize()
        if (self.capture_bounds is not None and hasattr(self, "selection") and
                previous.width() > 0 and previous.height() > 0):
            scale_x, scale_y = self.width() / previous.width(), self.height() / previous.height()
            self.selection = QRect(round(self.selection.x() * scale_x), round(self.selection.y() * scale_y),
                                   round(self.selection.width() * scale_x), round(self.selection.height() * scale_y))
        super().resizeEvent(event)

    def paintEvent(self, event):
        """Dim the capture or desktop and reveal the selected area with a resize handle."""
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
                source = QRectF(self.selection.x() * self.picture.width() / self.width(),
                                self.selection.y() * self.picture.height() / self.height(),
                                self.selection.width() * self.picture.width() / self.width(),
                                self.selection.height() * self.picture.height() / self.height())
                painter.drawPixmap(QRectF(self.selection), self.picture, source)
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
        """Clamp the pointer and choose move, resize, or a new rectangle selection."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        pos = QPoint(max(0, min(pos.x(), self.width() - 1)), max(0, min(pos.y(), self.height() - 1)))
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
        """Move within widget bounds, resize with minimum dimensions, or draw the selection."""
        if not self.operation:
            return
        pos = event.position().toPoint()
        pos = QPoint(max(0, min(pos.x(), self.width() - 1)), max(0, min(pos.y(), self.height() - 1)))
        if self.operation == "move":
            moved = self.initial.translated(pos - self.anchor)
            moved.moveLeft(max(0, min(moved.left(), self.width() - moved.width())))
            moved.moveTop(max(0, min(moved.top(), self.height() - moved.height())))
            self.selection = moved
        elif self.operation == "resize":
            minimum_width, minimum_height = 200, 100
            if self.capture_bounds is not None:
                # The acceptance threshold is in native capture pixels, while
                # mouse positions and the overlay selection use Qt logical pixels.
                minimum_width = math.ceil(200 * self.width() / self.capture_bounds.width())
                minimum_height = math.ceil(100 * self.height() / self.capture_bounds.height())
            end = QPoint(min(self.width() - 1, max(self.initial.left() + minimum_width - 1, pos.x())),
                         min(self.height() - 1, max(self.initial.top() + minimum_height - 1, pos.y())))
            self.selection = QRect(self.initial.topLeft(), end).normalized()
        else:
            self.selection = QRect(self.anchor, pos).normalized()
        self.update()

    def mouseReleaseEvent(self, event):
        """End the current overlay selection drag."""
        self.operation = ""

    def keyPressEvent(self, event):
        """Confirm on Enter, cancel on Escape, and delegate other keys to the dialog."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def confirm(self):
        """Accept a clipped selection whose returned region is at least 200 by 100."""
        self.selection = self.selection.intersected(self.rect())
        region = self.region()
        if region["w"] >= 200 and region["h"] >= 100:
            self.accept()
        else:
            self.hint = "Select a region at least 200×100 pixels.\nEnter to save · Esc to cancel"
            self.update()

    def region(self):
        """Map selection edges to native capture pixels, or add the Qt desktop origin."""
        if self.capture_bounds is not None:
            bounds = self.capture_bounds
            x1 = round(self.selection.x() * bounds.width() / self.width())
            y1 = round(self.selection.y() * bounds.height() / self.height())
            x2 = round((self.selection.x() + self.selection.width()) * bounds.width() / self.width())
            y2 = round((self.selection.y() + self.selection.height()) * bounds.height() / self.height())
            return {"x": bounds.x() + x1, "y": bounds.y() + y1, "w": x2 - x1, "h": y2 - y1}
        top = self.selection.topLeft() + self.origin
        return {"x": top.x(), "y": top.y(), "w": self.selection.width(), "h": self.selection.height()}
