"""Compact session totals, with responsive cards and no scan controls."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
    QVBoxLayout, QWidget,
)


class CurrencyCard(QFrame):
    def __init__(self, item, png=None, parent=None):
        super().__init__(parent)
        self.name = item["name"]
        self.setObjectName("sessionCurrencyCard")
        self.setMinimumWidth(220)
        self.setFixedHeight(94)
        self.setStyleSheet(
            "QFrame#sessionCurrencyCard { background:#191A1C; border:1px solid #40372C; border-radius:7px; }"
            "QFrame#sessionCurrencyCard QLabel { background:transparent; border:0; }")
        self.setAccessibleName(self.name)
        content = QHBoxLayout(self)
        content.setContentsMargins(12, 10, 12, 10)
        content.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(52, 60)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setAccessibleName(self.name + " icon")
        self.set_icon(png)
        content.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.name_label = QLabel(self.name)
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.name_label.setStyleSheet("font-size:12px;color:#DDD8D0;")
        self.name_label.setToolTip(self.name)
        text.addWidget(self.name_label)
        self.total_label = QLabel()
        self.total_label.setObjectName("currencyAmount")
        self.total_label.setStyleSheet("font-size:24px;font-weight:700;color:#F5C364;")
        text.addWidget(self.total_label)
        content.addLayout(text, 1)
        self.set_quantity(item["quantity"])

    def set_icon(self, png):
        picture = QPixmap()
        if png and picture.loadFromData(png, "PNG"):
            self.icon.setPixmap(picture.scaled(52, 60, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.icon.setPixmap(QPixmap())
            self.icon.setText("◇")
            self.icon.setStyleSheet("font-size:28px;color:#807361;")

    def set_quantity(self, amount):
        self.quantity = amount
        self.total_label.setText(f"{amount:,}")
        self.total_label.setAccessibleName(f"{self.name}: {amount:,} found")
        self.setToolTip(f"{self.name}\n{amount:,} found this session")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.name_label.setText(self.name_label.fontMetrics().elidedText(
            self.name, Qt.TextElideMode.ElideRight, max(1, self.name_label.width())))


class SessionCurrencyCounter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionCurrencyCounter")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.cards = {}
        self._visible = ()
        self._columns = 0
        self._layout_key = None
        content = QVBoxLayout(self)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        header = QHBoxLayout()
        captions = QVBoxLayout()
        title = QLabel("SESSION CURRENCY")
        title.setProperty("role", "eyebrow")
        captions.addWidget(title)
        self.summary = QLabel("No currency found yet")
        self.summary.setProperty("role", "note")
        self.summary.setWordWrap(True)
        captions.addWidget(self.summary)
        header.addLayout(captions, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find currency…")
        self.search.setAccessibleName("Filter session currency")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._filter)
        header.addWidget(self.search)
        content.addLayout(header)
        self.empty = QLabel("Approve a start and end inventory scan to count the currency found in each map.")
        self.empty.setProperty("role", "note")
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(0, 12, 0, 12)
        content.addWidget(self.empty)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        content.addLayout(self.grid)

    def set_totals(self, data, icons=None):
        icons = icons or {}
        found = {item["name"]: item for item in data["items"] if item["quantity"] > 0}
        for name in set(self.cards) - set(found):
            card = self.cards.pop(name)
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        for name, item in found.items():
            if name in self.cards:
                self.cards[name].set_quantity(item["quantity"])
                if name in icons:
                    self.cards[name].set_icon(icons[name])
            else:
                self.cards[name] = CurrencyCard(item, icons.get(name), self)
        counted = data["maps_counted"]
        parts = [f"{len(found)} item types · {counted} {'map' if counted == 1 else 'maps'} counted"]
        missing = data["maps_pending_baseline"]
        waiting = data["maps_pending_end"]
        if missing:
            parts.append(f"{missing} {'map needs a' if missing == 1 else 'maps need'} start scan")
        if waiting:
            parts.append(f"{waiting} {'map awaits an' if waiting == 1 else 'maps await'} end scan")
        self.summary.setText(" · ".join(parts))
        self._filter()

    def _filter(self):
        query = self.search.text().strip().casefold()
        self._visible = tuple(name for name in sorted(self.cards, key=str.casefold)
                              if query in name.casefold())
        self.empty.setVisible(not self._visible)
        self.empty.setText("No currency matches your search." if self.cards else
                           "Approve a start and end inventory scan to count the currency found in each map.")
        self._relayout()

    def _relayout(self):
        if not hasattr(self, "grid"):
            return
        columns = max(1, min(6, (self.width() + 12) // 252))
        key = (columns, self._visible)
        if key == self._layout_key:
            return
        self._layout_key = key
        while self.grid.count():
            self.grid.takeAt(0)
        for card in self.cards.values():
            card.hide()
        for index, name in enumerate(self._visible):
            card = self.cards[name]
            self.grid.addWidget(card, index // columns, index % columns)
            card.show()
        for column in range(max(columns, self._columns)):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self._columns = columns

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()
