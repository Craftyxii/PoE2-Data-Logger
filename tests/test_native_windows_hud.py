"""Windows desktop HUD acceptance through real OS input, with honest scan fixtures.

Run separately with QT_QPA_PLATFORM=windows. Linux/offscreen discovery skips this
check and cannot produce a passing native report. This checks native GUI use on a
simulated external game view; it is neither human testing nor PoE2 integration.
"""

import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageGrab, ImageStat
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from PoE2_Data_Logger.core import logger_store as logger, ocr_sensitivity, reference_pack, service, store
from PoE2_Data_Logger.ocr import propagation_scan, runehelper_ocr
from PoE2_Data_Logger.ocr.item_text import parse_item_text
from PoE2_Data_Logger.platform.hotkey import HotkeyManager
from PoE2_Data_Logger.platform.live_watch import game_foreground
from PoE2_Data_Logger.ui.native_desktop import LoggerWindow
from PoE2_Data_Logger.ui import native_desktop
from tools.native_hud_input import NativeWindowsInput


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM", "").lower() == "windows",
                     "Requires a separate real Windows desktop run with QT_QPA_PLATFORM=windows")
class NativeWindowsHUDTests(unittest.TestCase):
    """Exercise actual HWND input and state changes instead of an offscreen widget simulation."""

    @classmethod
    def setUpClass(cls):
        """Create a Windows-plugin QApplication without falling back to offscreen rendering."""
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Isolate storage and start real native input/reporting before showing the logger."""
        self.artifacts = Path(os.environ.get("POE2_NATIVE_HUD_ARTIFACTS", "artifacts/native-hud"))
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.report = {"status":"running", "qt_platform":self.app.platformName(),
                       "input_backend":"Win32 SendInput", "native_input_events":0, "phases":[],
                       "screenshots":[], "game_integration":False, "human_end_user_pass":False,
                       "scan_evidence":"Async injected item/remnant results; real propagation PNG OCR; real currency hotkey/capture with injected recognition; external simulated game view"}
        self.addCleanup(self.finish_report)
        self.save_report()
        self.native = NativeWindowsInput(self.app, self.artifacts)
        self.tmp = tempfile.TemporaryDirectory(prefix="poe2-native-hud-")
        self.addCleanup(self.tmp.cleanup)
        self.previous_data, self.previous_hotkey = store.DATA_DIR, service.HOTKEY
        self.addCleanup(self.restore_globals)
        store.DATA_DIR = Path(self.tmp.name)
        logger._READY = False
        logger.initialize()
        with logger._connect() as db:
            self.default_references = reference_pack._default_reference_rows(db)
        logger.save_settings({"tablets_used":1, "ocr_auto_commit":True})
        service.HOTKEY = HotkeyManager(supported=True)
        self.window = None
        self.host = None
        self.addCleanup(self.close_windows)
        self.open_logger()
        self.raw = (Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg").read_bytes()

    def save_report(self):
        """Write reviewable evidence incrementally so crashes cannot masquerade as a pass."""
        (self.artifacts / "native-hud-report.json").write_text(json.dumps(self.report, indent=2), encoding="utf-8")

    def finish_report(self):
        """Mark incomplete checks failed and include actual input/screenshot evidence."""
        if self.report["status"] != "passed":
            self.report["status"] = "failed"
            self.report["failure"] = "Native acceptance did not finish; see unittest traceback and last completed phase."
        if hasattr(self, "native"):
            self.report["native_input_events"] = sum(event.get("count", 0) for event in self.native.events)
            self.report["events"] = self.native.events
            self.report["screenshots"] = self.native.screenshots
            self.report["desktop"] = self.native.desktop_name
        self.save_report()

    def restore_globals(self):
        """Restore the caller's database and shortcut manager after native checks."""
        store.DATA_DIR, service.HOTKEY = self.previous_data, self.previous_hotkey
        logger._READY = False

    def close_windows(self):
        """Close only this test's native windows, workers and external fixture process."""
        if self.report["status"] != "passed":
            try:
                self.native.screenshot("failure-desktop")
            except Exception as error:
                self.report["failure_screenshot_error"] = str(error)
        if self.window:
            self.window.close()
            self.window.pool.shutdown(wait=True, cancel_futures=True)
        service.HOTKEY._unregister()
        if self.host:
            self.host.terminate()
            self.host.wait(timeout=10)
        self.app.processEvents()

    def open_logger(self):
        """Show the real logger maximized and establish initial OS foreground ownership."""
        self.window = LoggerWindow()
        self.window.showMaximized()
        self.native.wait(lambda: self.window.isVisible(), "visible logger")
        self.native.focus_setup(int(self.window.winId()))
        self.assertEqual(self.native.foreground(),int(self.window.winId()),
                         "A shown logger must own the Windows foreground before native input")

    def phase(self, name):
        """Record completed assertions together with a real desktop screenshot."""
        self.native.screenshot(name, int(self.window.winId()))
        self.report["phases"].append(name)
        self.save_report()

    def tab(self, index):
        """Navigate sidebar pages through OS mouse input and check GUI responsiveness."""
        nav = next(button for position, button in enumerate(self.window.nav_buttons)
                   if (position if button.property("page_index") is None else button.property("page_index")) == index)
        self.native.click(nav)
        self.native.wait(lambda: self.window.tabs.currentIndex() == index, f"sidebar page {index}")
        tick = []
        QTimer.singleShot(0, lambda: tick.append(True))
        self.native.wait(lambda: bool(tick), "responsive Qt event loop")

    def table_edit(self, table, row, column, text):
        """Settle the review layout before locating a cell for native mouse and keyboard correction."""
        self.native.expose(table)
        table.scrollToItem(table.item(row, column))
        self.native.pump()
        self.native.expose(table.viewport())
        self.native.click(table.viewport(), table.visualItemRect(table.item(row, column)).center(), double=True)
        self.native.key(ord("A"), (0x11,))
        self.native.type_text(text)
        self.native.key(0x0D)
        self.native.wait(lambda: table.item(row, column).text() == text, "native table correction")

    def deliver(self, label, reading, callback, predicate):
        """Deliver explicit fixture results through the real worker and queued GUI callback."""
        self.window._submit(f"Native fixture: {label}", lambda: reading, callback)
        self.native.wait(predicate, f"asynchronous {label} review", timeout=30)

    def test_native_hud_user_flow(self):
        """Verify navigation, corrections, saves, counters and overlay interaction with Win32 input."""
        self.check_navigation_and_preferences()
        self.check_propagation_and_counters()
        self.check_tablet_and_waystone()
        self.check_currency_and_ritual()
        self.check_overlay_capture_and_confirmation()
        self.check_reference_reset_and_raw_export()
        self.report["status"] = "passed"

    def check_navigation_and_preferences(self):
        """Visit every page and persist/reset all seven sliders using native input."""
        for index in (0,1,2,3,4,5,6,7,8,13,12):
            self.tab(index)
        menu = self.window.help_menu
        for index, action in zip((9,10,11), menu.actions()):
            bar = self.window.menuBar()
            self.native.expose(bar)
            self.native.click(bar, bar.actionGeometry(menu.menuAction()).center())
            self.native.wait(menu.isVisible, "native Help menu")
            self.native.expose(menu)
            self.native.click(menu, menu.actionGeometry(action).center())
            self.native.wait(lambda: self.window.tabs.currentIndex() == index, f"Help page {index}")
        self.tab(13)
        expected = {}
        for index, (kind, _) in enumerate(ocr_sensitivity.SCAN_TYPES):
            slider = self.window.ocr_sensitivity_sliders[kind]
            self.assertEqual(slider.value(), 50)
            self.native.click(slider)
            self.native.key(0x24 if index % 2 == 0 else 0x23)
            expected[kind] = 0 if index % 2 == 0 else 100
            self.native.wait(lambda: slider.value() == expected[kind], f"{kind} native slider")
            self.assertEqual(self.window.ocr_sensitivity_labels[kind].text(), str(expected[kind]))
        self.assertEqual(ocr_sensitivity.saved_values(), expected)
        self.phase("01-seven-sliders-and-navigation")
        self.window.close()
        self.window.pool.shutdown(wait=True, cancel_futures=True)
        self.open_logger()
        self.tab(13)
        self.assertEqual({key:slider.value() for key,slider in self.window.ocr_sensitivity_sliders.items()}, expected)
        self.native.click(self.window.ocr_sensitivity_reset)
        self.assertEqual(ocr_sensitivity.saved_values(), {key:50 for key in expected})
        self.phase("02-persisted-preferences-reset")

    def check_propagation_and_counters(self):
        """Approve real PNG OCR, inspect saved Expedition runes and complete through the live header."""
        self.tab(0)
        with Image.open(io.BytesIO(self.raw)) as opened:
            source = runehelper_ocr.default_frame(opened.convert("RGB"))
        image = Image.new("RGB", source.size, (176,161,130))
        image.paste(source.resize((round(source.width*.75),round(source.height*.75)), Image.Resampling.LANCZOS))
        image = ImageEnhance.Brightness(image).enhance(1.2)
        y = round(167*.75)
        ImageDraw.Draw(image).polygon([(1,y),(15,y-10),(35,y),(15,y+10)], fill=(242,209,124))
        png = io.BytesIO()
        image.save(png, format="PNG")
        context = logger.scan_context()
        self.window._submit("Native real .75 propagation fixture",
                            lambda: {**propagation_scan.scan_propagation(image), **context},
                            lambda result:self.window._propagation_read(result, png.getvalue()))
        self.native.wait(lambda:self.window.pending_review_kind == "propagation" and
                         self.window.propagation_recipe_table.rowCount() > 0, "held real propagation OCR", timeout=90)
        row = self.window.propagation_recipe_table.currentRow()
        self.assertEqual(self.window.propagation_recipe_table.item(row,0).text(), "Regal Orb x3")
        first, _ = self.window._propagation_row_inputs[row]
        slot = first.findData(2)
        self.assertEqual(first.itemText(slot), "Tidal")
        self.assertFalse(first.itemIcon(slot).isNull())
        self.native.choose(first, 2)
        action = self.window.propagation_recipe_table.cellWidget(row,2).findChild(QPushButton,"approvePropagationRecipe")
        before_expedition = logger.get_state()["current_expedition_id"]
        self.native.click(action)
        self.assertEqual(logger.get_state()["detonated"], 1)
        self.assertEqual(logger.get_state()["current_expedition_id"], before_expedition)
        self.assertEqual([(part["rune1"], part["rune2"]) for part in logger.get_state()["chain"]], [("Tidal", "")])
        self.assertEqual(self.window.propagation_recipe_table.rowCount(),0)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertTrue(self.window.approve_scan_button.isHidden())
        self.tab(1)
        saved_table = self.window.expedition_chain_table
        self.native.wait(lambda:saved_table.rowCount() == 1 and saved_table.isVisible(),
                         "approved propagation visible on Expedition")
        saved_first, saved_second = (saved_table.cellWidget(0,column) for column in (1,2))
        self.assertEqual((saved_first.currentText(),saved_second.currentText()),("Tidal",""))
        self.native.assert_target(saved_first)
        self.native.assert_target(saved_second)
        self.assertTrue(self.window.chain_note.isVisible())
        self.assertIn("1 saved parts",self.window.chain_note.text())
        self.phase("03-propagation-direct-approve")
        before_complete = logger.get_state()["scan_commit_count"]
        self.native.click(self.window.header_complete_chain_button)
        self.native.wait(lambda:logger.get_state()["current_expedition_id"] == "M0001-E02",
                         "global header completed chain and advanced expedition once")
        self.assertEqual(logger.get_state()["current_expedition_id"], "M0001-E02")
        self.assertEqual(logger.get_state()["scan_commit_count"],before_complete+1)
        self.assertEqual(self.window.header_expedition.currentData(),2)
        self.assertEqual(logger.get_state()["chain"], [])
        self.assertEqual(saved_table.rowCount(),0)
        self.assertFalse(self.window.header_complete_chain_button.isEnabled())
        result = {"mode":"opened", "status":"Injected opened rewards", "can_use":True,
                  "family":"Family 3", "candidates":[3], "sockets":10, "recipe_sockets":10,
                  "socket_source":"opened icons", "first_line_gap":60, "list_complete":False,
                  "first_recipe":"Perfect Chaos Orb x3", "next_recipe":"Perfect Exalted Orb x3",
                  "opened_recipes":[{"recipe":name,"ocr_score":.99,"match_score":1}
                                    for name in ("Perfect Chaos Orb x3","Perfect Exalted Orb x3")],
                  "_ocr_strictness":100, **logger.scan_context()}
        self.window.mode = "opened"
        self.deliver("opened remnant", result, lambda r:self.window._scan_done("opened",r,self.raw),
                     lambda:self.window.pending_review_kind == "remnant")
        self.assertEqual(self.window.header_remnant_id.text(), "#1")
        self.native.click(self.window.approve_scan_button)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(self.window.header_map_id.text(), "#1")
        self.assertEqual(self.window.header_remnant_id.text(), "#1")
        compact = self.window.width() < 1600
        counter_size = 41 if compact else 54
        for counter in (self.window.header_map_id,self.window.header_remnant_id):
            self.assertIn(f"font-size:{counter_size}px;",counter.styleSheet())
            self.assertEqual(counter.font().pixelSize(),counter_size)
            self.assertGreaterEqual(counter.height(),counter.fontMetrics().height())
        for index, caption in enumerate(self.window._header_identifier_captions):
            caption_size = (14 if compact else 17) if index < 2 else (12 if compact else 14)
            self.assertEqual(caption.font().pixelSize(),caption_size)
        self.phase("04-large-map-remnant-counters")
        # Even a confident, loose-threshold result must wait for actual Windows approval.
        reading = {"mode": "propagation", "runes": ["Tidal"], "positions": [3],
                   "selected_recipe": "Regal Orb x3", "can_use": True,
                   "_ocr_strictness": 0, **logger.scan_context()}
        before_review = logger.get_state()
        self.deliver("confident propagation requires approval", reading,
                     lambda r: self.window._propagation_read(r, self.raw),
                     lambda: self.window.pending_review_kind == "propagation")
        held = logger.get_state()
        self.assertEqual((held["scan_commit_count"], held["chain"], held["detonated"]),
                         (before_review["scan_commit_count"], before_review["chain"], before_review["detonated"]))
        approve = self.window.propagation_recipe_table.cellWidget(0, 2).findChild(
            QPushButton, "approvePropagationRecipe")
        self.native.click(approve)
        self.assertEqual(logger.get_state()["chain"][0]["rune1"], "Tidal")
        before_remnant = self.window.header_remnant_id.text()
        self.native.click(self.window.header_complete_chain_button)
        self.native.wait(lambda: logger.get_state()["current_expedition_id"] == "M0001-E03",
                         "completion retains the current map's remnant number")
        self.assertEqual(self.window.header_remnant_id.text(), before_remnant)
        self.assertEqual(logger.get_state()["map_remnant_id"], "R0001")
        self.phase("04b-completion-retains-remnant-number")
        new_map = next(button for button in self.window.findChildren(QPushButton) if button.text() == "+ New map")
        undo = next(button for button in self.window.findChildren(QPushButton) if button.text() == "Undo new map")
        self.native.click(new_map)
        self.assertEqual(self.window.header_map_id.text(), "#2")
        self.assertEqual(self.window.header_remnant_id.text(), "—")
        self.native.click(undo)
        self.assertEqual(self.window.header_map_id.text(), "#1")
        self.assertEqual(self.window.header_remnant_id.text(), "#1")
        self.phase("05-map-advance-and-undo")

    def check_tablet_and_waystone(self):
        """Recover incomplete tooltip OCR using native form corrections and explicit approval."""
        tablet = {"kind":"tablet", "source":"screen OCR", "mods":["The tooltip could not be read"],
                  "matches":[], "uncertain":[], "_ocr_strictness":100, **logger.scan_context()}
        self.deliver("tablet", tablet, lambda r:self.window._hover_item_read(r,self.raw),
                     lambda:self.window.pending_review_kind == "tablet")
        self.assertFalse(self.window.approve_scan_button.isEnabled())
        self.native.click(self.window.review_edit_button)
        self.native.choose(self.window.tablet_affixes[0], "Pack Size")
        self.native.edit(self.window.tablet_values[0], "31")
        self.tab(0)
        before = logger.get_state()["scan_commit_count"]
        self.native.click(self.window.approve_scan_button)
        self.assertEqual(logger.get_state()["settings"]["tablet_affixes"][0]["value"],31)
        self.assertEqual(logger.get_state()["scan_commit_count"],before+1)
        self.assertEqual(logger.tablet_next_slot(),2)
        self.phase("06-tablet-manual-review")
        reading = parse_item_text("Item Class: Waystones\nRarity: Rare\nReview Waystone\nWaystone\n"
                                  "Item Level:82\nMonsters have 20% increased maximum Life", self.window.state["affixes"])
        reading.update({"source":"screen OCR", "_ocr_strictness":100, **logger.scan_context()})
        self.deliver("waystone", reading, lambda r:self.window._hover_item_read(r,self.raw),
                     lambda:self.window.pending_review_kind == "waystone")
        self.native.click(self.window.review_edit_button)
        self.native.choose(self.window.tier,16)
        self.native.edit(self.window.waystone,"93.5")
        self.native.edit(self.window.map_mods,"1")
        self.native.edit(self.window.waystone_mod_fields[0],"Monsters have 25% increased maximum Life")
        self.tab(0)
        before = logger.get_state()["scan_commit_count"]
        self.native.click(self.window.approve_scan_button)
        self.assertIsNone(self.window.pending_review_kind)
        self.assertEqual(logger.get_state()["scan_commit_count"],before+1)
        self.assertEqual(logger.get_state()["settings"]["tier"],16)
        self.assertEqual(logger.get_state()["settings"]["waystone"],93.5)
        self.phase("07-waystone-manual-review")

    def check_currency_and_ritual(self):
        """Approve corrected rows and ensure only the final end-inventory save updates totals."""
        self.tab(4)
        reading = {"items":[{"slot":1,"name":"Chaos Orb","quantity":4}],
                   "unknown":[{"slot":2,"candidate":"Divine Orb"}], "_ocr_strictness":100}
        self.deliver("currency",reading,lambda r:self.window._inventory_read(r,live=True,expected_map_id="M0001"),
                     lambda:self.window.pending_review_kind == "currency" and self.window.inventory_table.rowCount()==2)
        # The phase selector is on the held Review page; select End before saving.
        self.native.choose(self.window.inventory_phase,"end")
        self.assertEqual(self.window._pending_currency_phase,"end")
        table = self.window.inventory_table
        self.assertFalse(table.cellWidget(0,3).findChild(QPushButton,"approveCurrency").isEnabled())
        self.assertTrue(table.cellWidget(1,3).findChild(QPushButton,"approveCurrency").isEnabled())
        self.table_edit(table,1,1,"Divine Orb")
        self.table_edit(table,1,2,"2")
        self.native.click(table.cellWidget(1,3).findChild(QPushButton,"approveCurrency"))
        self.assertEqual(logger.currency_for_map("M0001")["end"],{})
        before = logger.get_state()["scan_commit_count"]
        self.native.click(self.window.approve_scan_button)
        self.assertEqual(logger.currency_for_map("M0001")["end"],{"Chaos Orb":4,"Divine Orb":2})
        self.assertEqual(logger.get_state()["scan_commit_count"],before+1)
        self.assertEqual(self.window.session_currency.cards["Chaos Orb"].quantity,4)
        self.assertEqual(self.window.session_currency.cards["Divine Orb"].quantity,2)
        self.phase("08-currency-row-and-final-approval")
        ritual = {"items":[{"category":"Item", "category_verified":True, "name":"", "quantity":1,
                            "tribute":None, "source":"Injected reward evidence", "unresolved":True,
                            "name_needs_review":True, "needs_review":True, "deferred":False,"grid_slots":[14]}],
                  "raw_text":"Injected Ritual raw", "unmatched":[], "tribute_available":5430,
                  "rerolls_remaining":2,"_ocr_strictness":100}
        self.deliver("Ritual",ritual,lambda r:self.window._ritual_read(r,live=True,expected_map_id="M0001"),
                     lambda:self.window.pending_review_kind == "ritual" and self.window.ritual_table.rowCount()==1)
        self.table_edit(self.window.ritual_table,0,1,"A manually identified rare belt")
        before = logger.get_state()["scan_commit_count"]
        self.native.click(self.window.approve_scan_button)
        saved = logger.ritual_pages_for_map("M0001")[-1]["items"][0]
        self.assertEqual((saved["category"],saved["name"],saved["quantity"]),("Item","A manually identified rare belt",1))
        self.assertEqual(logger.get_state()["scan_commit_count"],before+1)
        self.assertIsNone(self.window.pending_review_kind)
        self.phase("09-ritual-correction-and-approval")

    def start_external_fixture(self):
        """Launch an external PID with a real capture image and the game's exact focus title."""
        ready = Path(self.tmp.name) / "fixture-ready.json"
        helper = Path(__file__).resolve().parents[1] / "tools/native_hud_input.py"
        image = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/opened.jpg"
        self.host = subprocess.Popen([sys.executable,str(helper),"--fixture-window",str(image),"--ready-file",str(ready)])
        self.native.wait(lambda:ready.exists() or self.host.poll() is not None,"external fixture startup",timeout=30)
        self.assertIsNone(self.host.poll(),"External fixture could not start on the Windows desktop")
        host = json.loads(ready.read_text(encoding="utf-8"))
        self.assertNotEqual(host["pid"],os.getpid())
        self.native.focus_setup(host["hwnd"])
        self.assertTrue(game_foreground(),"An external simulated game view must satisfy the actual title/PID focus guard")
        self.report["external_fixture"] = {**host,"description":"Simulated game view; not Path of Exile 2 gameplay"}
        self.fixture_ready = ready
        return host["hwnd"]

    def check_overlay_capture_and_confirmation(self):
        """Verify native hide/capture/reveal and uncertain confirmation retain usable HWND input."""
        self.tab(6)
        self.native.click(self.window.overlay_checkbox)
        self.native.wait(lambda:service.HOTKEY.status()["registered"],"real registered HUD shortcut")
        self.assertTrue(self.window._overlay_enabled)
        self.assertEqual(service.HOTKEY.status()["combos"]["overlay"],"Ctrl+Shift+H")
        self.tab(0)
        self.assertTrue(self.window.isMaximized())
        bounds = self.native.rect(int(self.window.winId()))
        self.native.key(0x1B)
        self.native.wait(lambda:not self.window.isVisible() or self.window.isMinimized(),"native Escape hides HUD")
        host_hwnd = self.start_external_fixture()
        left,top,right,bottom = self.native.rect(host_hwnd)
        sample = (left+25,top+100,left+125,min(bottom-50,top+200))
        self.native.pump(.25)
        baseline = ImageGrab.grab(bbox=sample,all_screens=True).convert("RGB")
        self.assertLess(max(abs(channel-target) for channel,target in
                            zip(ImageStat.Stat(baseline).mean,(37,72,95))),5,
                        "The baseline must contain the external fixture's visible background pixels")
        self.native.key(ord("H"),(0x11,0x10))
        self.native.wait(lambda:self.window.isVisible() and not self.window.isMinimized(),"global OS shortcut reveals HUD")
        self.native.wait(lambda:self.native.foreground()==int(self.window.winId()),"HUD activation after real global shortcut")
        self.assertEqual(self.native.rect(int(self.window.winId())),bounds)
        self.check_taskbar_restore_and_native_paint(host_hwnd)
        self.tab(13)
        self.tab(0)
        captures = []

        def immediate_capture():
            """Capture immediately after the actual GUI hide handoff, without compositor mocks."""
            self.window._prepare_overlay_capture()
            captured = ImageGrab.grab(bbox=sample,all_screens=True).convert("RGB")
            captured.save(self.artifacts / "10-immediate-hide-handoff.png")
            return captured

        self.window._submit("Native capture-hide handoff",immediate_capture,captures.append)
        self.native.wait(lambda:bool(captures),"actual capture-hide worker handoff")
        difference = ImageStat.Stat(ImageChops.difference(baseline,captures[0])).mean
        self.assertLess(max(difference),3,"HUD pixels remained over the external fixture at capture handoff")
        self.report["capture_handoff_mean_pixel_difference"] = difference
        self.native.screenshots.append("10-immediate-hide-handoff.png")
        self.native.focus_setup(host_hwnd)
        reading = {"items":[{"slot":1,"name":"Chaos Orb","quantity":4,"count_needs_review":True}],
                   "unknown":[],"_ocr_strictness":100}
        # Exercise the real capture/listener/GUI route after fresh input reaches
        # the external process. Only the recognition boundary is injected.
        ocr_sensitivity.save_values({**ocr_sensitivity.saved_values(),"currency":100})
        with logger._connect() as db:
            regions = logger._meta(db,"scan_region_boxes",{})
            logger._set_meta(db,"scan_region_boxes",{**regions,"inventory_region":[0,0,1,1]})
            logger._set_meta(db,"scan_region_resolution","auto")
        # Ctrl+C chords are reserved by the existing shortcut validator.
        service.HOTKEY.configure_for("currency","Ctrl+Shift+I")
        self.native.wait(lambda:service.HOTKEY.status()["registered"],"real registered currency shortcut")
        self.native.click_hwnd(host_hwnd)
        self.native.wait(lambda:json.loads(self.fixture_ready.read_text(encoding="utf-8")).get("mouse_presses",0)>0,
                         "external process received actual mouse input")
        self.assertTrue(game_foreground())
        before_event = self.window._latest_hotkey
        before_commits = logger.get_state()["scan_commit_count"]

        def injected_currency_reader(image,references=(),read=None,strictness=50):
            """Retain the actual captured pixels while injecting only the uncertain recognition result."""
            image.save(self.artifacts / "10-real-currency-hotkey-capture.png")
            self.assertEqual(strictness,100)
            return reading

        def currency_finished_or_failed():
            """Observe the real hotkey event or completed held review without bypassing GUI callbacks."""
            latest = service.HOTKEY.status().get("latest") or {}
            return ((latest.get("id",0)>before_event and bool(latest.get("error"))) or
                    (self.window.pending_review_kind=="currency" and self.window.isVisible() and
                     self.window._inventory_reading is None and self.window.inventory_table.rowCount()==1))

        with patch.object(native_desktop.item_ocr,"scan_inventory_grid",side_effect=injected_currency_reader) as reader:
            self.native.key(ord("I"),(0x11,0x10))
            try:
                self.native.wait(currency_finished_or_failed,"real currency shortcut scan and uncertain confirmation",timeout=30)
            except AssertionError as error:
                self.fail(f"{error}; hotkey status={service.HOTKEY.status()}; HUD status={self.window.scan_status.text()}")
            latest = service.HOTKEY.status().get("latest") or {}
            self.assertFalse(latest.get("error"),f"Real currency hotkey failed: {latest.get('error')}")
            self.assertEqual(reader.call_count,1)
        # _inventory_captured retires the consumed manager event; the GUI keeps
        # its handled ID and frozen capture context for ownership verification.
        self.assertGreater(self.window._latest_hotkey,before_event)
        self.assertEqual(self.window._inventory_capture_context["strictness"],100)
        self.assertEqual(self.window._inventory_capture_context["phase"],"end")
        self.assertEqual(self.window._inventory_capture_context["map_id"],"M0001")
        self.assertEqual(logger.get_state()["scan_commit_count"],before_commits)
        self.native.screenshots.append("10-real-currency-hotkey-capture.png")
        self.report["currency_confirmation_route"] = {"external_mouse_received":True,"shortcut":"Ctrl+Shift+I",
            "capture":"real ImageGrab","recognition":"injected fixture at scan_inventory_grid","strictness":100,
            "event_id":self.window._latest_hotkey}
        # No SetForegroundWindow here: the logger must reveal and activate itself.
        self.native.wait(lambda:self.native.foreground()==int(self.window.winId()),"automatic confirmation OS foreground")
        self.assertEqual(self.native.rect(int(self.window.winId())),bounds)
        self.assertEqual(self.window.windowOpacity(),1.0)
        self.table_edit(self.window.inventory_table,0,2,"7")
        self.native.click(self.window.inventory_table.cellWidget(0,3).findChild(QPushButton,"approveCurrency"))
        self.phase("11-interactive-uncertain-overlay-confirmation")
        self.native.click(self.window.approve_scan_button)
        self.assertEqual(logger.currency_for_map("M0001")["end"]["Chaos Orb"],7)
        self.native.wait(lambda:not self.window.isVisible() or self.window.isMinimized(),"approved HUD hides")
        self.native.focus_setup(host_hwnd)
        self.native.key(ord("H"),(0x11,0x10))
        self.native.wait(lambda:self.window.isVisible() and self.native.foreground()==int(self.window.winId()),"HUD can be restored again")
        self.assertEqual(self.native.rect(int(self.window.winId())),bounds)
        held = {"mode":"propagation","can_use":False,"runes":[],"status":"Injected held popup review",
                "choices":[{"selected_recipe":"Regal Orb x3","runes":[],"can_use":False}],**logger.scan_context()}
        self.deliver("popup review",held,lambda r:self.window._propagation_read(r,self.raw),
                     lambda:self.window.pending_review_kind=="propagation")
        combo = self.window._propagation_row_inputs[0][0]
        self.native.click(combo)
        self.native.wait(combo.view().isVisible,"real rune PNG popup")
        self.native.key(0x1B)
        self.native.wait(lambda:not combo.view().isVisible(),"first Escape closes popup")
        self.assertTrue(self.window.isVisible())
        self.native.key(0x1B)
        self.native.wait(lambda:not self.window.isVisible() or self.window.isMinimized(),"second Escape hides HUD")
        self.native.focus_setup(host_hwnd)
        self.native.key(ord("H"),(0x11,0x10))
        self.native.wait(lambda:self.window.isVisible() and self.native.foreground()==int(self.window.winId()),"HUD reveals after popup Escape")
        self.native.choose(combo,2)
        self.phase("12-popup-escape-and-restored-native-input")
        self.native.click(combo)
        popup = combo.view().window()
        self.native.wait(combo.view().isVisible,"popup before replacement scan")
        self.deliver("replacement propagation review",held,lambda r:self.window._propagation_read(r,self.raw),
                     lambda:self.window._propagation_row_inputs[0][0] is not combo)
        try:
            self.assertFalse(popup.isVisible(),"A replacement scan must retire its obsolete dropdown popup")
        except RuntimeError:
            pass  # Deleting the obsolete Qt popup is also a valid retirement.
        self.native.choose(self.window._propagation_row_inputs[0][0],2)
        self.phase("13-replacement-scan-retires-popup")

    def check_taskbar_restore_and_native_paint(self, host_hwnd):
        """Exercise native activation/restore with overlay enabled and compare real client pixels."""
        hwnd = int(self.window.winId())
        incoming_maximized = self.window.isMaximized()
        incoming_bounds = self.native.rect(hwnd)
        self.tab(6)
        self.native.click(self.window.overlay_opacity_slider)
        self.native.key(0x24)
        for _ in range(9):
            self.native.key(0x21)
        self.assertEqual(self.window.overlay_opacity_slider.value(), 65)
        self.tab(0)
        reading = {"mode":"propagation", "can_use":False, "runes":[],
                   "status":"Held native taskbar restoration review",
                   "choices":[{"selected_recipe":"Regal Orb x3", "runes":[], "can_use":False}],
                   **logger.scan_context()}
        self.deliver("taskbar restoration review", reading,
                     lambda result:self.window._propagation_read(result,self.raw),
                     lambda:self.window.pending_review_kind == "propagation")
        rune = self.window._propagation_row_inputs[0][0]
        self.native.choose(rune,2)
        context = dict(self.window._manual_propagation_context)
        original_kills = self.window.normal.text()
        evidence = []

        def reveal_hud():
            """Use the real registered shortcut and retain its configured transparency."""
            self.native.key(0x1B)
            self.native.wait(lambda:not self.window.isVisible() or self.window.isMinimized(),
                             "Escape hides HUD before native restoration")
            self.native.focus_setup(host_hwnd)
            self.native.key(ord("H"),(0x11,0x10))
            self.native.wait(lambda:self.window.isVisible() and not self.window.isMinimized() and
                             self.native.foreground()==hwnd, "real HUD shortcut activation")
            self.assertAlmostEqual(self.window.windowOpacity(),.65,places=2)
            self.native.click(rune)
            self.native.wait(rune.view().isVisible,"manual HUD's native rune popup")
            self.native.key(0x1B)
            self.native.wait(lambda:not rune.view().isVisible(),"Escape closes only the native rune popup")
            self.assertTrue(self.window.isVisible())
            self.assertAlmostEqual(self.window.windowOpacity(),.65,places=2,
                                   msg="Returning from an owned popup must preserve manual HUD opacity.")

        for maximized in (False, True):
            mode = "maximized" if maximized else "normal"
            self.native.user.ShowWindow(hwnd,3 if maximized else 9)
            self.native.user.SetForegroundWindow(hwnd)
            self.native.wait(lambda:self.window.isMaximized()==maximized and
                             self.native.foreground()==hwnd, f"native {mode} baseline")
            self.native.pump(.25)
            bounds = self.native.rect(hwnd)
            baseline = self.capture_native_client()
            baseline_name = f"10-taskbar-{mode}-opaque-client-baseline.png"
            baseline.save(self.artifacts / baseline_name)
            self.native.screenshots.append(baseline_name)
            self.assertGreater(max(ImageStat.Stat(baseline).var),1,
                               "The native baseline must contain painted application controls.")
            for minimized in (False, True):
                reveal_hud()
                if minimized:
                    # SW_MINIMIZE/SW_RESTORE follow the native taskbar restore
                    # path. Subsequent clicks and typing use actual SendInput.
                    self.native.user.ShowWindow(hwnd,6)
                    self.native.wait(self.window.isMinimized, "native main-window minimization")
                    self.native.focus_setup(host_hwnd)
                    self.native.user.ShowWindow(hwnd,9)
                    self.native.user.SetForegroundWindow(hwnd)
                    route = "ShowWindow(SW_MINIMIZE/SW_RESTORE), SetForegroundWindow, SendInput controls"
                else:
                    # Actual Alt+Tab activates the background main HWND;
                    # neither Qt activateWindow nor HUD restoration is invoked.
                    self.native.focus_setup(host_hwnd)
                    self.native.key(0x09,(0x12,))
                    route = "SendInput Alt+Tab from external fixture to background main HWND"
                self.native.wait(lambda:self.window.isVisible() and not self.window.isMinimized() and
                                 self.native.foreground()==hwnd, f"ordinary {mode} application restore")
                self.assertAlmostEqual(self.window.windowOpacity(),1.0,places=2)
                self.assertFalse(self.window._overlay_revealed)
                self.assertTrue(self.window._overlay_enabled)
                self.assertEqual(self.window.isMaximized(),maximized)
                self.assertEqual(self.native.rect(hwnd),bounds)
                self.assertEqual(self.window.tabs.currentIndex(),0)
                self.assertEqual(self.window.pending_review_kind,"propagation")
                self.assertEqual(self.window._manual_propagation_context,context)
                self.assertEqual(rune.currentData(),2)
                self.native.pump(.25)
                painted = self.capture_native_client()
                phase = f"10-taskbar-{mode}-{'minimized-restore' if minimized else 'activation'}"
                painted_name = phase + "-client.png"
                painted.save(self.artifacts / painted_name)
                self.native.screenshots.append(painted_name)
                self.assertEqual(painted.size,baseline.size)
                difference = ImageStat.Stat(ImageChops.difference(baseline,painted)).mean
                self.assertLess(max(difference),6,
                                "Restored native client pixels differ from the painted opaque application.")
                self.native.edit(self.window.normal,"123")
                self.assertEqual(self.window.normal.text(),"123")
                self.native.choose(rune,2)
                self.native.edit(self.window.normal,original_kills)
                self.phase(phase)
                evidence.append({"mode":mode, "minimized":minimized, "route":route,
                                 "client_mean_pixel_difference":difference,
                                 "baseline_client":baseline_name, "restored_client":painted_name,
                                 "opacity":self.window.windowOpacity(), "foreground_hwnd":self.native.foreground(),
                                 "held_review_preserved":True, "native_controls_usable":True})
        self.report["taskbar_equivalent_restore"] = evidence
        self.save_report()
        self.native.click(self.window.reject_scan_button)
        self.native.wait(lambda:self.window.pending_review_kind is None,"reject native restoration fixture")
        self.native.user.ShowWindow(hwnd,3 if incoming_maximized else 9)
        self.native.user.SetForegroundWindow(hwnd)
        self.native.wait(lambda:self.window.isMaximized()==incoming_maximized and
                         not self.window.isMinimized() and self.native.foreground()==hwnd,
                         "restore native geometry for the following acceptance phases")
        self.assertEqual(self.native.rect(hwnd),incoming_bounds)

    def capture_native_client(self):
        """Read actual desktop client pixels without using QWidget.grab or offscreen rendering."""
        hwnd = int(self.window.winId())
        origin = wintypes.POINT(0,0)
        self.assertTrue(self.native.user.ClientToScreen(hwnd,ctypes.byref(origin)))
        left,top,right,bottom = self.native.rect(hwnd,client=True)
        return ImageGrab.grab(bbox=(origin.x,origin.y,origin.x+right-left,origin.y+bottom-top),
                              all_screens=True).convert("RGB")

    def database_rows(self, db):
        """Read every table, including settings and ID sequences, for exact preservation checks."""
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: [tuple(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in tables}

    def answer_reference_reset(self, answer):
        """Click the real reset button and its nested native confirmation without mocking dialogs."""
        failures, observed = [], []
        timer = QTimer(self.window)
        deadline = time.monotonic() + 10

        def answer_visible_dialog():
            """Handle the synchronous modal from its own event loop through verified Win32 input."""
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QMessageBox):
                if time.monotonic() < deadline:
                    return
                timer.stop()
                failures.append(AssertionError("Reset confirmation did not appear"))
                if dialog is not None:
                    dialog.reject()
                return
            timer.stop()
            try:
                self.assertEqual(dialog.windowTitle(), "Reset OCR references to defaults")
                self.assertEqual(dialog.standardButton(dialog.defaultButton()), QMessageBox.StandardButton.Cancel)
                self.assertIn("Logged maps, items, currency, activities, IDs and personal settings are kept", dialog.text())
                self.native.screenshot(f"14-reference-reset-{answer.name.lower()}-confirmation")
                self.native.click(dialog.button(answer))
                observed.append(answer.name)
            except Exception as error:
                failures.append(error)
                dialog.reject()  # Cleanup only: any native-input or assertion failure still fails the test.

        timer.timeout.connect(answer_visible_dialog)
        timer.start(10)
        try:
            self.native.click(self.window.reference_reset_button)
        finally:
            timer.stop()
            timer.deleteLater()
        if failures:
            raise failures[0]
        self.assertEqual(observed, [answer.name])
        self.assertIsNone(QApplication.activeModalWidget())

    def check_reference_reset_and_raw_export(self):
        """Use visible reset/export controls, preserving logged history, settings and older backups."""
        if self.window._overlay_enabled:
            self.tab(6)
            self.native.click(self.window.overlay_checkbox)
            self.assertFalse(self.window._overlay_enabled)
        if self.window.pending_review_kind is not None:
            self.tab(0)
            self.native.click(self.window.reject_scan_button)
            self.assertIsNone(self.window.pending_review_kind)
        self.native.wait(lambda:not self.window._pending_tasks, "idle workers before reference reset")

        # Seed explicitly disclosed local references; only the user actions below use OS input.
        logger.save_recipe({"name":"Native reset reward", "sockets":3, "combo":"Rage + Rage + Rage"})
        logger.add_affix("Native reset affix")
        logger.add_currency_item("Native reset currency")
        logger.add_item_name("Native reset armour")
        logger.add_ritual_name("Omen of Native Reset")
        art = Image.new("RGB", (96,96), (25,40,60))
        ImageDraw.Draw(art).ellipse((12,12,80,80), fill=(220,120,30))
        logger.save_currency_icon("Native reset currency", art)
        logger.save_item_icon("Native reset armour", art)
        logger.save_omen_icon("Omen of Native Reset", art)
        with logger._connect() as db:
            logger._save_review_examples(db, [{"name":"Native reset armour", "category":"Item",
                                               "image":art, "columns":1, "rows":1}],
                                         {"native reset armour":("item",1)})
        seed = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger/region_examples/seed.jpg"
        scan = store.save_scan(seed.read_bytes(), "native-reset-seed.jpg", 3, "P1", "Native reset rune")
        self.assertTrue(scan["reference_added"], "The real seed fixture must supply learned glyph evidence")
        self.window.refresh()
        self.tab(8)
        with logger._connect() as db:
            before = self.database_rows(db)
        for table in ("maps", "expeditions", "chain_completions", "currency_snapshots",
                      "ritual_pages", "commits", "scans", "meta"):
            self.assertTrue(before[table], f"Reset preservation must cover saved {table}")
        for table in reference_pack._EXAMPLE_TABLES:
            self.assertTrue(before[table], f"Reset fixture must cover {table}")
        images = {path.name:path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}
        self.answer_reference_reset(QMessageBox.StandardButton.Cancel)
        with logger._connect() as db:
            self.assertEqual(self.database_rows(db), before, "Cancel must preserve every database row")
        self.phase("14-reference-reset-cancel-preserves-data")
        self.answer_reference_reset(QMessageBox.StandardButton.Yes)
        with logger._connect() as db:
            after = self.database_rows(db)
        self.assertEqual({table:after[table] for table in reference_pack._DEFAULT_TABLES},
                         self.default_references)
        self.assertTrue(all(not after[table] for table in reference_pack._EXAMPLE_TABLES))
        preserved = set(before) - set(reference_pack._DEFAULT_TABLES) - set(reference_pack._EXAMPLE_TABLES)
        before["meta"] = [row for row in before["meta"] if row[0] != "active_retired_currency_names"]
        self.assertEqual({table:before[table] for table in preserved},
                         {table:after[table] for table in preserved}, "Reset must preserve all logged data and settings")
        self.assertEqual({path.name:path.read_bytes() for path in (store.DATA_DIR / "images").iterdir()}, images)
        self.assertIn("Default OCR references restored", self.window.reference_status.text())
        self.report["reference_reset"] = {"fixture":"Seeded custom names/icons and reviewed icon; real seed PNG/JPEG glyph learning",
            "cancel":"all database rows unchanged", "confirm":"shipped defaults restored; all five example tables empty",
            "preserved_tables":sorted(preserved), "preserved_screenshots":len(images)}
        self.phase("15-reference-reset-confirm-preserves-logs")

        self.tab(5)
        directory = self.artifacts.resolve() / "raw-sql-exports"
        directory.mkdir(parents=True, exist_ok=True)
        older = directory / "older-user-backup.sqlite3"
        older.write_bytes(logger.backup_bytes())
        originals = {path:path.read_bytes() for path in directory.iterdir() if path.is_file()}
        self.native.edit(self.window.export_folder, str(directory))
        created = []
        for number in (1,2):
            known = set(directory.iterdir())
            self.native.click(self.window.raw_database_export_button)
            self.native.wait(lambda:not self.window._pending_tasks and
                             bool(set(directory.glob("PoE2_Export_*.sqlite3")) - known),
                             f"raw SQL folder export {number}", timeout=30)
            new = set(directory.iterdir()) - known
            self.assertEqual(len(new),1, "A raw SQL export must create exactly one new file")
            destination = new.pop()
            created.append(destination)
            with logger._connect() as source:
                expected = self.database_rows(source)
            with sqlite3.connect(destination) as backup:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0],"ok")
                self.assertEqual(self.database_rows(backup), expected, "Raw SQL export must preserve every source table")
            for path, contents in originals.items():
                self.assertEqual(path.read_bytes(), contents, f"Export replaced older file {path.name}")
            originals[destination] = destination.read_bytes()
        self.assertNotEqual(created[0], created[1])
        self.report["raw_sql_export"] = {"files":[path.name for path in created],
            "integrity":"ok", "source_tables":{table:len(rows) for table,rows in expected.items()},
            "older_files_unchanged":len(originals)-1}
        self.phase("16-raw-sql-folder-export-keeps-older-files")


if __name__ == "__main__":
    unittest.main()
