from __future__ import annotations

import io
import csv
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import logger_store as logger


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def _column(number):
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


def _col_number(address):
    letters = re.match(r"[A-Z]+", address).group()
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - 64
    return number


class Sheet:
    def __init__(self, xml):
        self.root = ET.fromstring(xml)
        self.data = self.root.find(f"{{{NS}}}sheetData")
        self.rows = {int(row.get("r")): row for row in self.data.findall(f"{{{NS}}}row")}
        self.cells = {}
        self._indexed_rows = set()

    def _row(self, number):
        if number not in self.rows:
            self.rows[number] = ET.Element(f"{{{NS}}}row", {"r": str(number)})
            self.data.append(self.rows[number])
        return self.rows[number]

    def _cell(self, row, col, style=None):
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
        self.data[:] = sorted(self.data, key=lambda row: int(row.get("r")))
        for row in self.data:
            row[:] = sorted(row, key=lambda cell: _col_number(cell.get("r")))
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)


def _numeric_column(name):
    return (name.startswith("Item: ") or name in {"Socket Count", "Tier", "Area Level", "Base Map Mods", "Map Mods", "# +2 Mod Tablets",
        "Tablet Mods", "Total Mods", "Master +Mods", "Waystone %", "Tablets Used", "Item Rarity %",
        "Monster Rarity %", "Pack Size %", "Effectiveness %", "Source Row", "Chain Step #", "Expedition #",
        "Normal Kills (Map)", "Magic Kills (Map)", "Rare Kills (Map)", "Remnants Detonated (Expedition)",
        "Scan Commit #", "Family ID", "Quantity", "Ritual Page", "Tribute", "Tablet Slot Capacity",
        "Start Count", "End Count", "Net Change", "Normal Kills", "Magic Kills", "Rare Kills", "Total Kills",
        "Page", "Currency Commit #", "Ritual Commit #", "Start Scan Commit #", "End Scan Commit #"}
        or re.fullmatch(r"Tablet \d Mod \d (?:%|Value)", name)
        or re.fullmatch(r"Tablet \d Random Modifiers", name)
        or re.fullmatch(r"Expedition \d Detonated", name)
        or (name.startswith(("Start ", "End ")) and _numeric_column(name.split(" ", 1)[1])))


def _data_sheet(data):
    rows = csv.reader(io.StringIO(data.decode("utf-8-sig")))
    headers = next(rows)
    root = ET.Element(f"{{{NS}}}worksheet")
    ET.SubElement(root, f"{{{NS}}}dimension")
    views = ET.SubElement(root, f"{{{NS}}}sheetViews")
    view = ET.SubElement(views, f"{{{NS}}}sheetView", {"workbookViewId": "0"})
    ET.SubElement(view, f"{{{NS}}}pane", {"ySplit": "1", "topLeftCell": "A2", "activePane": "bottomLeft", "state": "frozen"})
    ET.SubElement(view, f"{{{NS}}}selection", {"pane": "bottomLeft", "activeCell": "A2", "sqref": "A2"})
    ET.SubElement(root, f"{{{NS}}}sheetFormatPr", {"defaultRowHeight": "15"})
    cols = ET.SubElement(root, f"{{{NS}}}cols")
    for col, header in enumerate(headers, 1):
        width = 38 if any(word in header for word in ("Recipe", "Affix", "Modifiers", "Name", "Perk", "Combo")) else 19
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
            if _numeric_column(headers[column - 1]) and value and re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                value = float(value) if "." in value else int(value)
            sheet.set(last, column, value)
    bounds = f"A1:{_column(len(headers))}{last}"
    sheet.root.find(f"{{{NS}}}dimension").set("ref", bounds)
    ET.SubElement(sheet.root, f"{{{NS}}}autoFilter", {"ref": bounds})
    return sheet.bytes()


def export_xlsx():
    data_sheets = [("Export", logger.export_all_csv)]
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
    for number, (name, produce) in enumerate(data_sheets, 1):
        path = f"xl/worksheets/sheet{number}.xml"
        parts[path] = _data_sheet(produce())
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
    parts["xl/styles.xml"] = (f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/><color rgb="FF191919"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2AA2B"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>').encode()
    out = io.BytesIO()
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as destination:
        for name, data in parts.items():
            destination.writestr(name, data)
    return out.getvalue()
