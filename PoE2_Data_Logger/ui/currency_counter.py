"""Grouped session totals, retaining the tracked catalog at zero until found."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
    QVBoxLayout, QWidget,
)


_GROUP_ORDER = (
    "Chaos orbs", "Exalted orbs", "Divine orbs", "Other orbs", "Essences",
    "Omens", "Runes and soul cores", "Catalysts", "Alloys", "Delirium",
    "Expedition", "Other currency", "Items",
)
_TIERS = {"lesser": 0, "": 1, "greater": 2, "perfect": 3, "refined": 2, "ancient": 2}


def _group_for(item):
    """Prefer an explicit catalog group, then classify by item kind and currency name."""
    explicit = str(item.get("group") or "").strip()
    if explicit:
        return explicit
    name = item["name"].casefold().replace("’", "'")
    kind = str(item.get("kind") or "Currency").casefold()
    if kind == "omen" or name.startswith("omen of "):
        return "Omens"
    if kind == "item":
        return "Items"
    if "essence of " in name:
        return "Essences"
    if kind in ("rune", "soul core") or re.search(r"\brune\b", name) or "soul core" in name or "talisman" in name:
        return "Runes and soul cores"
    if "chaos orb" in name:
        return "Chaos orbs"
    if "exalted orb" in name:
        return "Exalted orbs"
    if "divine orb" in name:
        return "Divine orbs"
    if "orb" in name:
        return "Other orbs"
    if "catalyst" in name:
        return "Catalysts"
    if "alloy" in name:
        return "Alloys"
    if "liquid" in name or "simulacrum" in name:
        return "Delirium"
    if "artifact" in name or "coinage" in name:
        return "Expedition"
    return "Other currency"


def _variant_key(name):
    """Sort by natural base name, then tier, preserving numeric level order."""
    normalized = name.casefold().replace("’", "'")
    match = re.match(r"^(lesser|greater|perfect|refined|ancient)\s+(.+)$", normalized)
    tier, base = (match.group(1), match.group(2)) if match else ("", normalized)
    # Natural ordering also keeps level 9 before level 10 for fluxes and gems.
    natural = tuple((0, int(part)) if part.isdigit() else (1, part)
                    for part in re.split(r"(\d+)", base))
    return natural, _TIERS[tier], normalized


def _group_key(group):
    """Order known currency families first, then custom groups alphabetically."""
    try:
        return 0, _GROUP_ORDER.index(group), ""
    except ValueError:
        return 1, 0, group.casefold()


class CurrencyCard(QFrame):
    """Display one tracked item with its icon and current session quantity."""
    def __init__(self, item, png=None, parent=None):
        """Build an accessible item card and initialize its icon and supplied quantity."""
        super().__init__(parent)
        self.name = item["name"]
        self._icon_bytes = None
        self._icon_initialized = False
        self.quantity = None
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
        """Display supplied PNG bytes at card size or use a diamond placeholder."""
        if self._icon_initialized and png == self._icon_bytes:
            return
        self._icon_initialized = True
        self._icon_bytes = png
        picture = QPixmap()
        if png and picture.loadFromData(png, "PNG"):
            self.icon.setStyleSheet("")
            self.icon.setPixmap(picture.scaled(52, 60, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.icon.setPixmap(QPixmap())
            self.icon.setText("◇")
            self.icon.setStyleSheet("font-size:28px;color:#807361;")

    def set_quantity(self, amount):
        """Update the current total, zero-state colour, tooltip, and accessible amount."""
        # Most reference/import refreshes leave quantities unchanged. Avoid
        # restyling every card and invalidating the whole dashboard layout.
        if self.quantity == amount:
            return
        self.quantity = amount
        self.total_label.setText(f"{amount:,}")
        colour = "#F5C364" if amount else "#807B73"
        self.total_label.setStyleSheet(f"font-size:24px;font-weight:700;color:{colour};")
        self.total_label.setAccessibleName(f"{self.name}: {amount:,} found")
        self.setToolTip(f"{self.name}\n{amount:,} found this session")

    def resizeEvent(self, event):
        """Elide the visible name to fit while retaining its full tooltip."""
        super().resizeEvent(event)
        self.name_label.setText(self.name_label.fontMetrics().elidedText(
            self.name, Qt.TextElideMode.ElideRight, max(1, self.name_label.width())))


class SessionCurrencyCounter(QWidget):
    """Filter and group supplied session totals alongside zero-count catalog entries."""
    def __init__(self, parent=None):
        """Build the search, counting-status summary, and responsive grouped card grid."""
        super().__init__(parent)
        self.setObjectName("sessionCurrencyCounter")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.cards = {}
        self.groups = {}
        self.group_labels = {}
        self._catalog = {}
        self._items = {}
        self._visible = ()
        self._visible_groups = ()
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
        self.empty = QLabel("Approve an end inventory scan to count currency found. A start scan subtracts what you brought in.")
        self.empty.setProperty("role", "note")
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(0, 12, 0, 12)
        content.addWidget(self.empty)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        content.addLayout(self.grid)

    def set_totals(self, data, icons=None, catalog=None):
        """Replace displayed totals, merge names case-insensitively, and retain catalog zeros.

        Negative supplied quantities contribute zero; found items and groups sort first.
        The caller supplies the map counts and pending-scan status shown in the summary.
        """
        icons = icons or {}
        if catalog is not None:
            self._catalog = {}
            for entry in catalog:
                item = {"name": entry, "kind": "Currency"} if isinstance(entry, str) else dict(entry)
                name = str(item.get("name") or "").strip()
                if name:
                    self._catalog.setdefault(name.casefold(), {**item, "name": name})
        items = {key: {**item, "quantity": 0} for key, item in self._catalog.items()}
        for item in data["items"]:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key not in items:
                items[key] = {**item, "name": name, "quantity": 0}
            items[key]["quantity"] += max(0, item.get("quantity", 0))
            if item.get("kind"):
                items[key]["kind"] = item["kind"]
        tracked = {item["name"]: item for item in items.values()}
        found = sum(item["quantity"] > 0 for item in tracked.values())
        for name in set(self.cards) - set(tracked):
            card = self.cards.pop(name)
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._items = tracked
        for name, item in tracked.items():
            if name in self.cards:
                self.cards[name].set_quantity(item["quantity"])
                if name in icons:
                    self.cards[name].set_icon(icons[name])
            else:
                self.cards[name] = CurrencyCard(item, icons.get(name), self)
        groups = {}
        for name, item in tracked.items():
            groups.setdefault(_group_for(item), []).append(name)
        self.groups = {
            group: tuple(sorted(groups[group], key=lambda name: (
                tracked[name]["quantity"] == 0, _variant_key(name))))
            for group in sorted(groups, key=lambda group: (
                not any(tracked[name]["quantity"] > 0 for name in groups[group]),
                _group_key(group)))
        }
        for group in set(self.group_labels) - set(self.groups):
            label = self.group_labels.pop(group)
            self.grid.removeWidget(label)
            label.hide()
            label.deleteLater()
        for group in self.groups:
            if group not in self.group_labels:
                label = QLabel(group)
                label.setAccessibleName(group + " currency group")
                label.setProperty("role", "eyebrow")
                label.setStyleSheet("color:#D2B275;font-size:12px;font-weight:700;")
                label.setContentsMargins(0, 8, 0, 2)
                self.group_labels[group] = label
        counted = data["maps_counted"]
        parts = [f"{found} of {len(tracked)} item types found · {counted} {'map' if counted == 1 else 'maps'} counted"]
        assumed = data.get("maps_assumed_empty", 0)
        waiting = data["maps_pending_end"]
        if assumed:
            parts.append(f"{assumed} {'map assumed' if assumed == 1 else 'maps assumed'} to start empty")
        if waiting:
            parts.append(f"{waiting} {'map awaits an' if waiting == 1 else 'maps await'} end scan")
        self.summary.setText(" · ".join(parts))
        self._filter()

    def _filter(self):
        """Match item or group names and prioritize groups with visible nonzero totals."""
        query = self.search.text().strip().casefold()
        self._visible_groups = tuple(
            (group, tuple(name for name in names if query in name.casefold() or query in group.casefold()))
            for group, names in self.groups.items())
        self._visible_groups = tuple((group, names) for group, names in self._visible_groups if names)
        self._visible_groups = tuple(sorted(self._visible_groups, key=lambda entry: (
            not any(self._items[name]["quantity"] > 0 for name in entry[1]),
            _group_key(entry[0]))))
        self._visible = tuple(name for _, names in self._visible_groups for name in names)
        self.empty.setVisible(not self._visible)
        self.empty.setText("No currency matches your search." if self.cards else
                           "Approve an end inventory scan to count currency found. A start scan subtracts what you brought in.")
        self._relayout()

    def _relayout(self):
        """Reuse cards in one to six columns, rebuilding only when width or visible groups change."""
        if not hasattr(self, "grid"):
            return
        columns = max(1, min(6, (self.width() + 12) // 252))
        key = (columns, self._visible_groups)
        if key == self._layout_key:
            return
        self._layout_key = key
        while self.grid.count():
            self.grid.takeAt(0)
        visible = set(self._visible)
        visible_groups = {group for group, _names in self._visible_groups}
        for name, card in self.cards.items():
            if name not in visible and not card.isHidden():
                card.hide()
        for group, label in self.group_labels.items():
            if group not in visible_groups and not label.isHidden():
                label.hide()
        row = 0
        for group, names in self._visible_groups:
            label = self.group_labels[group]
            self.grid.addWidget(label, row, 0, 1, columns)
            if label.isHidden():
                label.show()
            row += 1
            for index, name in enumerate(names):
                card = self.cards[name]
                self.grid.addWidget(card, row + index // columns, index % columns)
                if card.isHidden():
                    card.show()
            row += (len(names) + columns - 1) // columns
        for column in range(max(columns, self._columns)):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self._columns = columns

    def resizeEvent(self, event):
        """Reflow the grouped cards after the widget width changes."""
        super().resizeEvent(event)
        self._relayout()
