"""The standalone, local Atlas allocation editor.

The page owns only a draft.  Its ``saved`` signal is handled by the logger
window, which persists the settings and decides which map they apply to.
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QValidator,
)
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGraphicsObject,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QTextBrowser, QToolTip, QVBoxLayout, QWidget,
)

from PoE2_Data_Logger.core.atlas_catalog import catalog


ASSET_DIR = Path(__file__).resolve().parent.parent / "atlas"
GOLD = QColor("#e5b660")
BRONZE = QColor("#6b5639")


def _asset_pixmap(relative_path):
    if not relative_path:
        return QPixmap()
    # Bundled catalog resources only; never turn a node's filename into an
    # arbitrary local-file reader when catalog versions are imported later.
    path = (ASSET_DIR / str(relative_path)).resolve()
    if not path.is_relative_to(ASSET_DIR.resolve()):
        return QPixmap()
    return QPixmap(str(path))


def _selected_choice(choices, choice_id):
    """Keep the game's option number tied to catalog order, not variant IDs."""
    for number, option in enumerate(choices, 1):
        if str(option["id"]) == choice_id:
            return number, option
    return None, None


class AtlasNodeItem(QGraphicsObject):
    clicked = Signal(str)

    def __init__(self, node, pixmap=None, frames=None, parent=None):
        super().__init__(parent)
        self.node = node
        self.node_id = str(node["id"])
        self.allocated = False
        self.choice_number = None
        self.selected = False
        self.hovered = False
        self.radius = {"small": 30, "notable": 43, "keystone": 52,
                       "root": 60, "decorative": 30}.get(node.get("kind"), 30)
        self.pixmap = pixmap if pixmap is not None else QPixmap()
        self.frames = frames or {}
        self.setPos(float(node.get("x", 0)), float(node.get("y", 0)))
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def boundingRect(self):
        radius = self.radius + 15
        return QRectF(-radius, -radius, radius * 2, radius * 2)

    def shape(self):
        path = QPainterPath()
        radius = self.radius + 8
        path.addEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        return path

    def paint(self, painter, option, widget=None):
        radius = self.radius
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.allocated or self.hovered:
            glow = QRadialGradient(QPointF(), radius + 14)
            glow.setColorAt(0, QColor(229, 182, 96, 60 if self.allocated else 20))
            glow.setColorAt(.72, QColor(229, 182, 96, 80 if self.allocated else 25))
            glow.setColorAt(1, QColor(229, 182, 96, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(self.boundingRect())
        painter.setPen(QPen(QColor("#25211b"), 7))
        painter.setBrush(QColor("#151710"))
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        if not self.pixmap.isNull():
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(QRectF(-radius + 4, -radius + 4,
                                  radius * 2 - 8, radius * 2 - 8))
            painter.setClipPath(clip)
            painter.setOpacity(1 if self.allocated else .60)
            painter.drawPixmap(QRectF(-radius + 4, -radius + 4,
                                     radius * 2 - 8, radius * 2 - 8),
                               self.pixmap, QRectF(self.pixmap.rect()))
            painter.restore()
        else:
            painter.setPen(QPen(GOLD if self.allocated else BRONZE, 2))
            painter.drawEllipse(QRectF(-radius * .45, -radius * .45,
                                      radius * .9, radius * .9))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(GOLD if self.allocated else BRONZE,
                            5 if self.allocated else 3))
        painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
        painter.setPen(QPen(QColor("#fce8af") if self.allocated else QColor("#3e3326"), 1))
        painter.drawEllipse(QRectF(-radius + 5, -radius + 5,
                                  radius * 2 - 10, radius * 2 - 10))
        frame = self.frames.get("allocated" if self.allocated else "unallocated")
        if frame is not None and not frame.isNull():
            painter.drawPixmap(QRectF(-radius - 5, -radius - 5,
                                     radius * 2 + 10, radius * 2 + 10),
                               frame, QRectF(frame.rect()))
        if self.node.get("choices"):
            # A small chevron marks selectable passives without replacing their
            # game icon or implying that an unset choice has a default effect.
            painter.setPen(QPen(GOLD if self.allocated else BRONZE, 4))
            y = radius + 7
            painter.drawLine(QPointF(-6, y - 3), QPointF(0, y + 3))
            painter.drawLine(QPointF(0, y + 3), QPointF(6, y - 3))
            if self.allocated and self.choice_number is not None:
                # Paint on the node itself so the badge retains the existing
                # hover/click target. Its number is an option, never a rank.
                painter.save()
                badge_radius = min(16, radius * .32)
                badge = QRectF(-badge_radius, radius * .4 - badge_radius,
                               badge_radius * 2, badge_radius * 2)
                painter.setPen(QPen(GOLD, 1.5))
                painter.setBrush(QColor("#16130e"))
                painter.drawEllipse(badge)
                font = painter.font()
                font.setBold(True)
                font.setPixelSize(round(badge_radius * 1.45))
                painter.setFont(font)
                painter.setPen(QColor("#fff5d2"))
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter,
                                 str(self.choice_number))
                painter.restore()
        if self.selected:
            painter.setPen(QPen(QColor("#ded4bd"), 1))
            painter.drawEllipse(QRectF(-radius - 8, -radius - 8,
                                      radius * 2 + 16, radius * 2 + 16))

    def hoverEnterEvent(self, event):
        self.hovered = True
        self.update()
        super().hoverEnterEvent(event)
        if self.toolTip():
            QToolTip.showText(event.screenPos(), self.toolTip(), event.widget())

    def hoverLeaveEvent(self, event):
        self.hovered = False
        self.update()
        QToolTip.hideText()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        QToolTip.hideText()
        self._pressed_at = event.screenPos()
        event.accept()

    def mouseReleaseEvent(self, event):
        start = getattr(self, "_pressed_at", event.screenPos())
        if (event.screenPos() - start).manhattanLength() <= 6 and self.shape().contains(event.pos()):
            self.clicked.emit(self.node_id)
        event.accept()


class AtlasTreeView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#0b0d0e"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QToolTip { background:#1c1c1e; color:#eeeae3; "
                          "border:1px solid #6b5639; padding:8px; }")
        self.setMinimumHeight(460)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def zoom(self, factor):
        current = self.transform().m11()
        target = min(4.0, max(.025, current * factor))
        if current > 0:
            self.scale(target / current, target / current)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(math.pow(1.15, delta / 120))
            event.accept()
        else:
            super().wheelEvent(event)

    def hideEvent(self, event):
        QToolTip.hideText()
        super().hideEvent(event)


class GearRaritySpinBox(QDoubleSpinBox):
    """Reserve the negative sentinel for unknown, never a displayed gear stat."""

    def validate(self, text, position):
        if text.lstrip().startswith("-"):
            return QValidator.State.Invalid, text, position
        return super().validate(text, position)


class AtlasSettingsPage(QWidget):
    saved = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._catalog = catalog()
        self._main_activity = "Main Atlas" if "Main Atlas" in self._catalog.get("activities", []) else "General"
        self._nodes = {str(key): {**node, "id": str(node.get("id", key))}
                       for key, node in self._catalog["nodes"].items()}
        self._allocated = set()
        self._choices = {}
        self._selected_node = None
        self._loading = False
        self._dirty = False
        self._baseline = None
        self._first_show = True
        self._tree_built = False
        self._background_items = []
        self._activity_backgrounds = {}
        self.node_items = {}
        self.edge_items = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Activity"))
        self.activity_filter = QComboBox()
        self.activity_filter.addItem("Entire Atlas", "all")
        for activity in self._catalog.get("activities", ["General"]):
            self.activity_filter.addItem("Main Atlas" if activity == "General" else activity,
                                         activity)
        self.activity_filter.setMinimumWidth(160)
        toolbar.addWidget(self.activity_filter)
        toolbar.addStretch()
        self.autofill_button = QPushButton("Autofill all points")
        self.autofill_button.setToolTip("Allocate every Atlas node; keep your selected choices.")
        self.clear_button = QPushButton("Clear all points")
        self.clear_button.setToolTip("Remove all allocations; keep your selected choices for next time.")
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setAccessibleName("Zoom out")
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setAccessibleName("Zoom in")
        self.fit_button = QPushButton("Fit tree")
        for widget in (self.autofill_button, self.clear_button,
                       self.zoom_out_button, self.zoom_in_button, self.fit_button):
            toolbar.addWidget(widget)
        layout.addLayout(toolbar)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "note")
        layout.addWidget(self.summary)
        body = QHBoxLayout()
        self.scene = QGraphicsScene(self)
        self.view = AtlasTreeView(self.scene)
        self.view.setAccessibleName("PoE2 Atlas passive tree")
        body.addWidget(self.view, 1)
        side = QWidget()
        side.setMinimumWidth(260)
        side.setMaximumWidth(350)
        details = QVBoxLayout(side)
        details.setContentsMargins(12, 0, 0, 0)
        self.node_title = QLabel("Select an Atlas node")
        self.node_title.setWordWrap(True)
        self.node_title.setStyleSheet("font-size:17px; font-weight:700; color:#e6c78a;")
        details.addWidget(self.node_title)
        self.node_state = QLabel("Click to allocate; click again to remove.")
        self.node_state.setWordWrap(True)
        details.addWidget(self.node_state)
        self.choice_label = QLabel("Choose effect")
        self.choice_combo = QComboBox()
        self.choice_combo.setAccessibleName("Atlas node effect choice")
        self.choice_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.choice_combo.setMinimumContentsLength(18)
        self.choice_combo.setToolTip("Choose one effect for this node.")
        details.addWidget(self.choice_label)
        details.addWidget(self.choice_combo)
        self.choice_label.hide()
        self.choice_combo.hide()
        self.node_effects = QTextBrowser()
        self.node_effects.setOpenExternalLinks(False)
        self.node_effects.setStyleSheet("QTextBrowser { background:#121412; border:1px solid #443a29; padding:6px; }")
        details.addWidget(self.node_effects, 1)
        self._node_hint = QLabel("Hover a node to read its effects. Drag the background to move the tree. Scroll to zoom.")
        self._node_hint.setWordWrap(True)
        self._node_hint.setProperty("role", "note")
        details.addWidget(self._node_hint)
        body.addWidget(side)
        layout.addLayout(body, 1)

        footer = QHBoxLayout()
        form = QFormLayout()
        self.gear_rarity = GearRaritySpinBox()
        self.gear_rarity.setRange(-1, 9999)
        self.gear_rarity.setDecimals(2)
        self.gear_rarity.setSingleStep(1)
        self.gear_rarity.setSuffix(" %")
        self.gear_rarity.setSpecialValueText("Not set")
        self.gear_rarity.setValue(-1)
        self.gear_rarity.setMinimumWidth(155)
        self.gear_rarity.setToolTip("Item rarity from character gear only. Zero means no gear rarity; Not set means unknown.")
        self.gear_rarity.setStyleSheet("QDoubleSpinBox { background:#111214; color:#f2eee8; border:1px solid #655747; border-radius:5px; padding:7px; }")
        form.addRow("Gear Item Rarity", self.gear_rarity)
        footer.addLayout(form)
        footer.addStretch()
        self.save_button = QPushButton("Save Atlas / Character Settings")
        self.save_button.setProperty("role", "primary")
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "message")
        layout.addWidget(self.status)

        self.activity_filter.currentIndexChanged.connect(self._filter_changed)
        self.autofill_button.clicked.connect(self.autofill)
        self.clear_button.clicked.connect(self.clear_allocations)
        self.zoom_out_button.clicked.connect(lambda: self.view.zoom(1 / 1.25))
        self.zoom_in_button.clicked.connect(lambda: self.view.zoom(1.25))
        self.fit_button.clicked.connect(self.fit_tree)
        self.choice_combo.currentIndexChanged.connect(self._choice_changed)
        self.gear_rarity.valueChanged.connect(self._mark_dirty)
        self.save_button.clicked.connect(lambda: self.saved.emit(self.settings()))
        general = self.activity_filter.findData(self._main_activity)
        if general >= 0:
            self.activity_filter.setCurrentIndex(general)
        self.set_settings({}, force=True)

    @property
    def dirty(self):
        return self._dirty

    def ensure_tree(self):
        """Load tree artwork on first opening, not on every logger startup."""
        if self._tree_built:
            return
        self._tree_built = True
        self._build_scene()
        self._update_visuals()

    def _build_scene(self):
        art = self._catalog.get("art") or {}
        background = _asset_pixmap(art.get("background"))
        if not background.isNull():
            item = self.scene.addPixmap(background)
            # Center the game artwork beneath the general tree.  Explicit
            # bounds supplied by the catalog take precedence over this fallback.
            bounds = art.get("background_bounds") or art.get("bounds")
            background_layout = art.get("background_layout")
            if isinstance(background_layout, dict):
                width = float(background_layout.get("width", background.width()))
                height = float(background_layout.get("height", background.height()))
                rect = QRectF(float(background_layout.get("x", 0)) - width / 2,
                              float(background_layout.get("y", 0)) - height / 2, width, height)
            elif isinstance(bounds, dict):
                rect = QRectF(float(bounds.get("x", 0)), float(bounds.get("y", 0)),
                              float(bounds.get("width", background.width())),
                              float(bounds.get("height", background.height())))
            else:
                general = [node for node in self._nodes.values()
                           if node.get("activity", self._main_activity) == self._main_activity]
                min_x = min((node.get("x", 0) for node in general), default=-3000)
                max_x = max((node.get("x", 0) for node in general), default=3000)
                min_y = min((node.get("y", 0) for node in general), default=-3000)
                max_y = max((node.get("y", 0) for node in general), default=3000)
                width = max(6000, max_x - min_x + 600)
                height = width * background.height() / background.width()
                rect = QRectF((min_x + max_x - width) / 2,
                              (min_y + max_y - height) / 2, width, height)
            self._place_art(item, rect, background)
            item.setOpacity(float((background_layout or {}).get("opacity", .80)))
            item.setZValue(-100)
            self._background_items.append(item)
        for activity, data in (art.get("activity_backgrounds") or {}).items():
            if isinstance(data, str):
                data = {"path": data}
            pixmap = _asset_pixmap(data.get("path"))
            if pixmap.isNull():
                continue
            item = self.scene.addPixmap(pixmap)
            width, height = float(data.get("width", 2700)), float(data.get("height", 2700))
            rect = QRectF(float(data.get("x", 0)) - width / 2,
                          float(data.get("y", 0)) - height / 2, width, height)
            self._place_art(item, rect, pixmap)
            item.setZValue(-90)
            self._activity_backgrounds[activity] = item
        pixmaps = {}
        frames = {kind: {state: _asset_pixmap(path) for state, path in pair.items()}
                  for kind, pair in (art.get("frames") or {}).items()}
        for node_id, node in self._nodes.items():
            icon = node.get("icon", "")
            if icon not in pixmaps:
                pixmaps[icon] = _asset_pixmap(icon)
            item = AtlasNodeItem(node, pixmaps[icon], frames.get(node.get("kind")))
            self.scene.addItem(item)
            item.clicked.connect(self.toggle_node)
            self.node_items[node_id] = item
        for edge in self._catalog.get("edges", []):
            if isinstance(edge, dict):
                left, right = str(edge.get("from")), str(edge.get("to"))
            else:
                left, right = map(str, edge[:2])
            if left not in self.node_items or right not in self.node_items:
                continue
            start, end = self.node_items[left].pos(), self.node_items[right].pos()
            curve = (self._catalog.get("edge_curves") or {}).get("|".join(sorted((left, right))))
            path = QPainterPath(start)
            if curve:
                center = QPointF(float(curve["x"]), float(curve["y"]))
                radius = math.hypot(start.x() - center.x(), start.y() - center.y())
                angle = math.degrees(math.atan2(center.y() - start.y(), start.x() - center.x()))
                end_angle = math.degrees(math.atan2(center.y() - end.y(), end.x() - center.x()))
                sweep = (end_angle - angle + 180) % 360 - 180
                path.arcTo(QRectF(center.x() - radius, center.y() - radius,
                                 radius * 2, radius * 2), angle, sweep)
            path.lineTo(end)
            item = self.scene.addPath(path, QPen(QColor("#4a4030"), 5))
            item.setZValue(-1)
            self.edge_items.append((left, right, item))
        self._filter_changed()

    @staticmethod
    def _place_art(item, rect, pixmap):
        from PySide6.QtGui import QTransform
        item.setTransform(QTransform.fromScale(rect.width() / pixmap.width(),
                                               rect.height() / pixmap.height()))
        item.setPos(rect.topLeft())

    def _allocatable(self, node):
        return bool(node.get("allocatable", node.get("kind") not in ("root", "decorative")))

    def settings(self):
        rarity = self.gear_rarity.value()
        return {"catalog_version": str(self._catalog["version"]),
                "allocated": sorted(self._allocated),
                "choices": dict(sorted(self._choices.items())),
                "gear_item_rarity": round(rarity, 2) if rarity >= 0 else None}

    def set_settings(self, payload, status="", force=False):
        if status:
            self.status.setText(status)
        if self._dirty and not force:
            return False
        payload = payload or {}
        self._loading = True
        try:
            self._allocated = {str(node_id) for node_id in payload.get("allocated", [])
                               if str(node_id) in self._nodes and self._allocatable(self._nodes[str(node_id)])}
            self._choices = {}
            for node_id, choice_id in (payload.get("choices") or {}).items():
                node = self._nodes.get(str(node_id))
                if node and any(str(option["id"]) == str(choice_id) for option in node.get("choices", [])):
                    self._choices[str(node_id)] = str(choice_id)
            rarity = payload.get("gear_item_rarity")
            with QSignalBlocker(self.gear_rarity):
                self.gear_rarity.setValue(-1 if rarity is None else float(rarity))
            self._baseline = self.settings()
            self._dirty = False
            self._update_visuals()
            self._show_node_details()
        finally:
            self._loading = False
        return True

    def set_state(self, status=""):
        self.status.setText(status)

    def toggle_node(self, node_id):
        node_id = str(node_id)
        node = self._nodes.get(node_id)
        if node is None:
            return
        self._selected_node = node_id
        if self._allocatable(node):
            if node_id in self._allocated:
                self._allocated.remove(node_id)
                self.choice_combo.hidePopup()
            else:
                self._allocated.add(node_id)
        self._mark_dirty()
        self._update_visuals()
        self._show_node_details()
        if node.get("choices") and node_id in self._allocated:
            # The node click exposes the actual dropdown, including when a
            # previously configured node is enabled again.
            QTimer.singleShot(0, self, lambda: self._open_choice_popup(node_id))

    def _open_choice_popup(self, node_id):
        if (self._selected_node == node_id and node_id in self._allocated
                and self.choice_combo.isVisible()):
            self.choice_combo.showPopup()

    def autofill(self):
        self._allocated = {node_id for node_id, node in self._nodes.items() if self._allocatable(node)}
        self._mark_dirty()
        self._update_visuals()
        self._show_node_details()

    def clear_allocations(self):
        self.choice_combo.hidePopup()
        self._allocated.clear()
        self._mark_dirty()
        self._update_visuals()
        self._show_node_details()

    def _mark_dirty(self, *args):
        if self._loading:
            return
        self._dirty = self.settings() != self._baseline
        self._update_summary()

    def _choice_changed(self, index):
        node_id = self._selected_node
        if self._loading or not node_id:
            return
        choice_id = self.choice_combo.itemData(index)
        if choice_id is None:
            self._choices.pop(node_id, None)
        else:
            self._choices[node_id] = str(choice_id)
        self._mark_dirty()
        self._update_visuals()
        self._show_node_details()

    def _show_node_details(self):
        node = self._nodes.get(self._selected_node)
        if node is None:
            return
        self.node_title.setText(node.get("name") or "Atlas node")
        if not self._allocatable(node):
            state = "Activity starting point" if node.get("kind") == "root" else "Atlas decoration"
        else:
            state = "Allocated" if self._selected_node in self._allocated else "Not allocated"
        self.node_state.setText(f"{node.get('activity', 'General')} · {state}")
        choices = node.get("choices") or []
        with QSignalBlocker(self.choice_combo):
            self.choice_combo.clear()
            self.choice_combo.addItem("Choose effect…", None)
            for number, option in enumerate(choices, 1):
                self.choice_combo.addItem(f"{number}. {option.get('name') or 'Effect'}", str(option["id"]))
                effects = "\n".join(option.get("effects") or [])
                self.choice_combo.setItemData(self.choice_combo.count() - 1, effects, Qt.ItemDataRole.ToolTipRole)
            choice = self._choices.get(self._selected_node)
            index = self.choice_combo.findData(choice) if choice is not None else 0
            self.choice_combo.setCurrentIndex(max(0, index))
        self.choice_label.setVisible(bool(choices))
        self.choice_combo.setVisible(bool(choices))
        effects = list(node.get("effects") or [])
        number, chosen = _selected_choice(choices, self._choices.get(self._selected_node))
        text = "".join(f"<p>{html.escape(str(effect)).replace(chr(10), '<br>')}</p>" for effect in effects)
        if choices:
            if chosen:
                text += f"<p><b>{number}. {html.escape(chosen.get('name') or 'Selected effect')}</b></p>"
                text += "".join(f"<p>{html.escape(str(effect)).replace(chr(10), '<br>')}</p>" for effect in chosen.get("effects", []))
            else:
                text += "<p style='color:#e5b660'>Choose one effect from the dropdown.</p>"
        self.node_effects.setHtml(text or "<p>No effect description is provided for this node.</p>")

    def _node_tooltip(self, node_id):
        node = self._nodes[node_id]

        def escaped(value):
            return html.escape(str(value)).replace("\n", "<br>")

        def effects_text(effects):
            return "".join(f"<p>{escaped(effect)}</p>" for effect in effects)

        if self._allocatable(node):
            state = "Allocated" if node_id in self._allocated else "Not allocated"
            name = node.get("name") or "Atlas node"
        else:
            state = "Activity starting point" if node.get("kind") == "root" else "Atlas decoration"
            name = node.get("name") or state
        text = (f"<p><b>{escaped(name)}</b><br>"
                f"{escaped(node.get('activity', 'Main Atlas'))} · {state}</p>")
        choices = node.get("choices") or []
        number, chosen = _selected_choice(choices, self._choices.get(node_id))
        if chosen:
            label = "Selected effect" if node_id in self._allocated else "Saved choice (inactive)"
            text += f"<p><b>{label}: {number}. {escaped(chosen.get('name') or 'Effect')}</b></p>"
        text += effects_text(node.get("effects") or [])
        if chosen:
            text += effects_text(chosen.get("effects") or [])
        elif choices:
            text += "<p><b>Choose one effect:</b></p>"
            for number, option in enumerate(choices, 1):
                text += f"<p><b>{number}. {escaped(option.get('name') or 'Effect')}</b><br>"
                text += "<br>".join(escaped(effect) for effect in option.get("effects") or []) + "</p>"
        elif not node.get("effects"):
            description = ("No Atlas bonuses." if not self._allocatable(node)
                           else "No effect description is provided for this node.")
            text += f"<p>{description}</p>"
        # A table gives Qt's rich-text tooltip a stable wrapping width, so long
        # conditional effects remain readable instead of spanning the screen.
        return f'<table width="360"><tr><td>{text}</td></tr></table>'

    def _update_visuals(self):
        for node_id, item in self.node_items.items():
            item.allocated = node_id in self._allocated
            item.choice_number, _ = _selected_choice(item.node.get("choices") or [],
                                                     self._choices.get(node_id))
            item.selected = node_id == self._selected_node
            item.setToolTip(self._node_tooltip(node_id))
            item.update()
        for left, right, item in self.edge_items:
            illuminated = left in self._allocated and right in self._allocated
            if self._nodes[left].get("kind") == "root":
                illuminated = right in self._allocated
            if self._nodes[right].get("kind") == "root":
                illuminated = left in self._allocated
            item.setPen(QPen(GOLD if illuminated else QColor("#4a4030"), 6 if illuminated else 5))
        self._update_summary()

    def _update_summary(self):
        pending = sum(bool(self._nodes[node_id].get("choices")) and node_id not in self._choices
                      for node_id in self._allocated)
        current = self.activity_filter.currentData()
        visible = sum(node_id in self._allocated for node_id, node in self._nodes.items()
                      if current == "all" or node.get("activity", "General") == current)
        label = self.activity_filter.currentText()
        parts = [f"{len(self._allocated)} allocated points", f"{label}: {visible}"]
        if pending:
            parts.append(f"{pending} allocated choice node{'s' if pending != 1 else ''} awaiting a selection")
        if self._dirty:
            parts.append("Unsaved changes")
        self.summary.setText(" · ".join(parts))

    def _filter_changed(self, *args):
        activity = self.activity_filter.currentData()
        for node_id, item in self.node_items.items():
            item.setVisible(activity == "all" or self._nodes[node_id].get("activity", "General") == activity)
        for left, right, item in self.edge_items:
            item.setVisible(self.node_items[left].isVisible() and self.node_items[right].isVisible())
        for item in self._background_items:
            item.setVisible(activity in ("all", self._main_activity))
        for name, item in self._activity_backgrounds.items():
            item.setVisible(activity == "all" or activity == name)
        self.choice_combo.hidePopup()
        self.fit_tree()
        self._update_summary()

    def fit_tree(self):
        rect = QRectF()
        for item in self.node_items.values():
            if item.isVisible() and item.node.get("kind") != "decorative":
                rect = rect.united(item.sceneBoundingRect())
        if rect.isNull():
            return
        rect = rect.adjusted(-140, -140, 140, 140)
        self.scene.setSceneRect(rect)
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_tree()
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, self.fit_tree)
