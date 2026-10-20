"""Build a self-contained XLSX from logged data, Context and its reference databases.

Export, Atlas Character Settings, Scan History and Analysis Context use one SQLite read snapshot.
Saved Atlas setup IDs link the summary/history rows to their exported settings;
CSV columns determine worksheet order and recognized numeric cell types.
"""

from __future__ import annotations

import io
import csv
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from PoE2_Data_Logger.core import kill_counts
from PoE2_Data_Logger.core import logger_store as logger
from PoE2_Data_Logger.core import workbook_references as references


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def _column(number):
    """Convert a positive one-based column number to its Excel letter address."""
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def _col_number(address):
    """Convert the uppercase column prefix of a cell address to its one-based number."""
    letters = re.match(r"[A-Z]+", address).group()
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - 64
    return number


class Sheet:
    """Edit worksheet cells while retaining surrounding XML and existing cell styles."""
    def __init__(self, xml):
        """Parse worksheet XML and index rows, deferring cell indexing until a row is used."""
        self.root = ET.fromstring(xml)
        self.data = self.root.find(f"{{{NS}}}sheetData")
        self.rows = {int(row.get("r")): row for row in self.data.findall(f"{{{NS}}}row")}
        self.cells = {}
        self._indexed_rows = set()

    def _row(self, number):
        """Return an existing numbered row or append an empty row to sheetData."""
        if number not in self.rows:
            self.rows[number] = ET.Element(f"{{{NS}}}row", {"r": str(number)})
            self.data.append(self.rows[number])
        return self.rows[number]

    def _cell(self, row, col, style=None):
        """Index the requested row and create a missing cell with its optional initial style."""
        key = row, col
        group = self._row(row)
        if row not in self._indexed_rows:
            for existing in group.findall(f"{{{NS}}}c"):
                self.cells[row, _col_number(existing.get("r"))] = existing
            self._indexed_rows.add(row)
        if key not in self.cells:
            attributes = {"r": f"{_column(col)}{row}"}
            if style is not None:
                attributes["s"] = style
            self.cells[key] = ET.SubElement(group, f"{{{NS}}}c", attributes)
        return self.cells[key]

    def set(self, row, col, value, style=None):
        """Replace a cell value/formula with a typed scalar or sanitized inline string.

        Empty values clear content; text stays text, including strings that resemble
        spreadsheet formulas, and existing cell styling is retained.
        """
        cell = self._cell(row, col, style)
        for child in list(cell):
            if child.tag in {f"{{{NS}}}f", f"{{{NS}}}v", f"{{{NS}}}is"}:
                cell.remove(child)
        cell.attrib.pop("t", None)
        if value is None or value == "":
            return
        if isinstance(value, bool):
            cell.set("t", "b")
            ET.SubElement(cell, f"{{{NS}}}v").text = "1" if value else "0"
        elif isinstance(value, (int, float)):
            ET.SubElement(cell, f"{{{NS}}}v").text = str(value)
        else:
            cell.set("t", "inlineStr")
            inline = ET.SubElement(cell, f"{{{NS}}}is")
            text = ET.SubElement(inline, f"{{{NS}}}t")
            text.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(value))[:32767]
            if str(value) != str(value).strip():
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

    def bytes(self):
        """Sort rows and cells into address order and serialize the worksheet XML."""
        self.data[:] = sorted(self.data, key=lambda row: int(row.get("r")))
        for row in self.data:
            row[:] = sorted(row, key=lambda cell: _col_number(cell.get("r")))
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)


def _numeric_column(name):
    """Identify counts, stats and known propagation family IDs for numeric cells, preserving Unknown as text."""
    return (name.startswith(("Item: ", "Currency: ", "Omen: ", "Stat: ", "Applied Stat: ", "Atlas Choice: ")) or name in {"Socket Count", "Tier", "Area Level", "Base Map Mods", "Map Mods", "# +2 Mod Tablets",
        "Tablet Mods", "Total Mods", "Master +Mods", "Waystone %", "Tablets Used", "Item Rarity %",
        "Monster Rarity %", "Pack Size %", "Effectiveness %", "Source Row", "Chain Step #", "Expedition #",
        "Normal Kills (Map)", "Magic Kills (Map)", "Rare Kills (Map)", "Unique Kills (Map)", "Remnants Detonated (Expedition)",
        "Remnants Detonated (Scan)",
        "Scan Commit #", "Family ID", "Propagation Family ID", "Quantity", "Ritual Page", "Tribute", "Tablet Slot Capacity",
        "Start Count", "End Count", "Net Change", "Normal Kills", "Magic Kills", "Rare Kills", "Unique Kills", "Total Kills",
        "Currency Scan #", "Start Currency Scan #", "End Currency Scan #",
        "Ritual Rite Map Ordinal",
        "Ritual Reading Count", "Ritual First Tribute Available", "Ritual First Rerolls Remaining",
        "Ritual Final Tribute Available", "Ritual Final Rerolls Remaining",
        "Ritual First Commit #", "Ritual Final Commit #",
        "New Find Quantity", "Session Found Quantity", "Ritual Tribute Available", "Ritual Rerolls Remaining", "Visible Seed Sockets",
        "Page", "Currency Commit #", "Ritual Commit #", "Start Scan Commit #", "End Scan Commit #",
        "Gear Item Rarity %", "Points", "Normal Kills (Observed)", "Magic Kills (Observed)",
        "Rare Kills (Observed)", "Unique Kills (Observed)", "Total Kills (Observed)"}
        or name in {"Top Socket Count", "Recipe # In Family", "Alias Position"}
        or re.fullmatch(r"Tablet \d Mod \d (?:%|Value)", name)
        or re.fullmatch(r"Tablet \d Random Modifiers", name)
        or re.fullmatch(r"Expedition \d+ Detonated", name)
        or (name.startswith(("Start ", "End ")) and _numeric_column(name.split(" ", 1)[1])))


def _atlas_setup_hyperlinks(data, atlas_data, target_sheet):
    """Map exported setup IDs to their first settings-sheet row and reject uneven CSV rows."""
    atlas_rows = csv.reader(io.StringIO(atlas_data.decode("utf-8-sig")))
    atlas_headers = next(atlas_rows)
    rows = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    headers = next(rows)
    if "Atlas Setup ID" not in atlas_headers or "Atlas Setup ID" not in headers:
        return {}
    atlas_column = atlas_headers.index("Atlas Setup ID")
    column = headers.index("Atlas Setup ID")
    locations = {}
    sheet_reference = "'" + target_sheet.replace("'", "''") + "'!"
    for number, values in enumerate(atlas_rows, 2):
        if len(values) != len(atlas_headers):
            raise ValueError(f"Export row {number} has an invalid width.")
        setup_id = values[atlas_column]
        if setup_id:
            locations.setdefault(setup_id, f"{sheet_reference}{_column(atlas_column + 1)}{number}")
    hyperlinks = {}
    for number, values in enumerate(rows, 2):
        if len(values) != len(headers):
            raise ValueError(f"Export row {number} has an invalid width.")
        location = locations.get(values[column])
        if location:
            hyperlinks[f"{_column(column + 1)}{number}"] = location
    return hyperlinks


def _rite_json_table(data):
    """Append lossless Rite JSON fragments only when their new fields exceed an Excel cell.

    Leave SQL/CSV data and the established worksheet columns intact. Item names
    can be arbitrary column labels, so choose a distinct continuation namespace
    for the entire appended block when an existing label would collide.
    """
    source = io.StringIO(data.decode("utf-8-sig"))
    rows = csv.reader(source)
    headers = next(rows)
    fields = ("Ritual Rite Affixes JSON", "Ritual Rite Inherited Sources JSON")
    columns = [(headers.index(name), name) for name in fields if name in headers]
    if not columns:
        return headers, rows
    parts = {column: 0 for column, _ in columns}
    for number, values in enumerate(rows, 2):
        if len(values) != len(headers):
            raise ValueError(f"Export row {number} has an invalid width.")
        for column, _ in columns:
            parts[column] = max(parts[column], max(0, (len(values[column]) - 1) // 32767))
    source.seek(0)
    rows = csv.reader(source)
    next(rows)
    plan = [(column, name, part) for column, name in columns
            for part in range(1, parts[column] + 1)]
    if not plan:
        return headers, rows
    prefix = ""
    labels = [f"{name} continuation {part}" for _, name, part in plan]
    while any(prefix + label in headers for label in labels):
        prefix = "Rite metadata | " + prefix

    def expanded_rows():
        for values in rows:
            fragments = [values[column][part * 32767:(part + 1) * 32767]
                         for column, _, part in plan]
            for column, _ in columns:
                if parts[column]:
                    values[column] = values[column][:32767]
            yield [*values, *fragments]

    return [*headers, *(prefix + label for label in labels)], expanded_rows()


def _data_sheet(data, *, hyperlinks=None, numeric_columns=()):
    """Turn a CSV table into a filtered worksheet with a frozen header and optional setup links.

    Recognized numeric columns become numbers only for matching decimal text;
    other values remain inline strings and each row must match the header width.
    """
    headers, rows = _rite_json_table(data)
    root = ET.Element(f"{{{NS}}}worksheet")
    ET.SubElement(root, f"{{{NS}}}dimension")
    views = ET.SubElement(root, f"{{{NS}}}sheetViews")
    view = ET.SubElement(views, f"{{{NS}}}sheetView", {"workbookViewId": "0"})
    ET.SubElement(view, f"{{{NS}}}pane", {"ySplit": "1", "topLeftCell": "A2", "activePane": "bottomLeft", "state": "frozen"})
    ET.SubElement(view, f"{{{NS}}}selection", {"pane": "bottomLeft", "activeCell": "A2", "sqref": "A2"})
    ET.SubElement(root, f"{{{NS}}}sheetFormatPr", {"defaultRowHeight": "15"})
    cols = ET.SubElement(root, f"{{{NS}}}cols")
    numeric_columns = set(numeric_columns)
    for col, header in enumerate(headers, 1):
        width = 38 if any(word in header for word in ("Recipe", "Affix", "Modifiers", "Name", "Perk", "Combo", "Effects", "Stats")) else 19
        if header in numeric_columns:
            width = min(48, max(19, len(header) + 2))
        ET.SubElement(cols, f"{{{NS}}}col", {"min": str(col), "max": str(col), "width": str(width), "customWidth": "1"})
    ET.SubElement(root, f"{{{NS}}}sheetData")
    sheet = Sheet(ET.tostring(root))
    for column, value in enumerate(headers, 1):
        sheet.set(1, column, value, style="1")
    last = 1
    for last, values in enumerate(rows, 2):
        if len(values) != len(headers):
            raise ValueError(f"Export row {last} has an invalid width.")
        for column, value in enumerate(values, 1):
            if value == "":
                continue
            if (headers[column - 1] in numeric_columns or _numeric_column(headers[column - 1])) and value and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                value = float(value) if "." in value else int(value)
            address = f"{_column(column)}{last}"
            sheet.set(last, column, value, style="2" if hyperlinks and address in hyperlinks else None)
    bounds = f"A1:{_column(len(headers))}{last}"
    sheet.root.find(f"{{{NS}}}dimension").set("ref", bounds)
    ET.SubElement(sheet.root, f"{{{NS}}}autoFilter", {"ref": bounds})
    if hyperlinks:
        links = ET.SubElement(sheet.root, f"{{{NS}}}hyperlinks")
        for address, location in hyperlinks.items():
            ET.SubElement(links, f"{{{NS}}}hyperlink", {"ref": address, "location": location,
                "tooltip": "View saved Atlas / Character Settings"})
    return sheet.bytes()


def _analysis_context_sheet(data):
    """Keep Context guidance lossless across Excel cells while retaining its SQL/CSV columns."""
    source = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    headers = next(source)
    rows = list(source)
    for number, values in enumerate(rows, 2):
        if len(values) != len(headers):
            raise ValueError(f"Export row {number} has an invalid width.")
    guidance_column = headers.index("Guidance")
    continuations = max((max(0, (len(row[guidance_column]) - 1) // 32767) for row in rows), default=0)
    if continuations > 0:
        # Only oversized Context guidance is split. Original columns and the
        # underlying SQL/CSV text keep their established order and values.
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([*headers, *(f"Guidance continuation {n}" for n in range(1, continuations + 1))])
        for values in rows:
            guidance = values[guidance_column]
            values[guidance_column] = guidance[:32767]
            writer.writerow([*values, *(guidance[n * 32767:(n + 1) * 32767]
                                        for n in range(1, continuations + 1))])
        data = output.getvalue().encode("utf-8-sig")
    sheet = Sheet(_data_sheet(data))
    widths = (22, 42, 112, 19, *([112] * continuations))
    for column, width in zip(sheet.root.findall(f"{{{NS}}}cols/{{{NS}}}col"), widths):
        column.set("width", str(width))
    for number, row in sheet.rows.items():
        if number == 1:
            continue
        guidance = "".join(sheet._cell(number, 3).itertext())
        # Approximate lines at the fixed width so wrapped notes stay visible in spreadsheet readers.
        lines = sum(max(1, (len(line) + 119) // 120) for line in guidance.split("\n"))
        row.set("ht", str(max(32, lines * 15 + 8)))
        row.set("customHeight", "1")
        for cell in row:
            cell.set("s", "3")
    return sheet.bytes()


def export_xlsx():
    """Serialize logged data, analysis notes and reference databases on one SQLite snapshot.

    Append the context sheet after the established data sheets to preserve their
    indices. Read all sources and item headers in one transaction, then add setup
    hyperlinks and worksheet styles to the ZIP package. Append reference sheets
    after the existing four so their indices, fields and event accounting stay intact.
    Append independent kill-count observations last without changing manual map totals.
    """
    with logger._connect() as db:
        db.execute("BEGIN")
        data_sheets = [("Export", logger.export_primary_csv(_db=db)),
                       ("Atlas Character Settings", logger.export_atlas_csv(_db=db)),
                       ("Scan History", logger.export_all_csv(_db=db)),
                       ("Analysis Context", logger.export_analysis_context_csv(_db=db))]
        data_sheets.extend(references.export_sheets(db))
        data_sheets.append(("Kill Count Readings", kill_counts.export_csv(_db=db)))
        item_headers = logger.map_summary_item_headers(_db=db)
    setup_links = {number: _atlas_setup_hyperlinks(data, data_sheets[1][1], data_sheets[1][0])
                   for number, (_, data) in enumerate(data_sheets, 1) if number in (1, 3)}
    content = ET.Element(f"{{{TYPES}}}Types")
    for extension, kind in (("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml")):
        ET.SubElement(content, f"{{{TYPES}}}Default", {"Extension": extension, "ContentType": kind})
    for path, kind in (("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
                       ("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")):
        ET.SubElement(content, f"{{{TYPES}}}Override", {"PartName": path, "ContentType": kind})
    workbook = ET.Element(f"{{{NS}}}workbook")
    views = ET.SubElement(workbook, f"{{{NS}}}bookViews")
    ET.SubElement(views, f"{{{NS}}}workbookView", {"activeTab": "0"})
    sheets = ET.SubElement(workbook, f"{{{NS}}}sheets")
    rels = ET.Element(f"{{{PKG_REL}}}Relationships")
    parts = {}
    for number, (name, data) in enumerate(data_sheets, 1):
        path = f"xl/worksheets/sheet{number}.xml"
        parts[path] = (_analysis_context_sheet(data) if name == "Analysis Context" else
                       _data_sheet(data, hyperlinks=setup_links.get(number),
                                   numeric_columns=item_headers if number == 1 else ()))
        ET.SubElement(content, f"{{{TYPES}}}Override", {"PartName": "/" + path,
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
        ET.SubElement(sheets, f"{{{NS}}}sheet", {"name": name, "sheetId": str(number), f"{{{DOC_REL}}}id": f"rId{number}"})
        ET.SubElement(rels, f"{{{PKG_REL}}}Relationship", {"Id": f"rId{number}", "Type": DOC_REL + "/worksheet",
                                                     "Target": f"worksheets/sheet{number}.xml"})
    ET.SubElement(rels, f"{{{PKG_REL}}}Relationship", {"Id": "rIdStyles", "Type": DOC_REL + "/styles", "Target": "styles.xml"})
    roots = ET.Element(f"{{{PKG_REL}}}Relationships")
    ET.SubElement(roots, f"{{{PKG_REL}}}Relationship", {"Id": "rId1", "Type": DOC_REL + "/officeDocument", "Target": "xl/workbook.xml"})
    parts.update({"[Content_Types].xml": ET.tostring(content, encoding="utf-8", xml_declaration=True),
                  "_rels/.rels": ET.tostring(roots, encoding="utf-8", xml_declaration=True),
                  "xl/workbook.xml": ET.tostring(workbook, encoding="utf-8", xml_declaration=True),
                  "xl/_rels/workbook.xml.rels": ET.tostring(rels, encoding="utf-8", xml_declaration=True)})
    parts["xl/styles.xml"] = (f'<styleSheet xmlns="{NS}"><fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/><color rgb="FF191919"/></font>'
        '<font><u/><sz val="11"/><name val="Calibri"/><color rgb="FF0563C1"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2AA2B"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
        '<alignment vertical="top" wrapText="1"/></xf></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>').encode()
    out = io.BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as destination:
        for name, data in parts.items():
            destination.writestr(name, data)
    return out.getvalue()
