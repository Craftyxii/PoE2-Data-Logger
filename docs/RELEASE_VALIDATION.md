# 1.3.1.2 Beta validation

The release gate exercises the application workflow and separately verifies recognition against real captures. All generated session data is simulated; it is not a player's session.

## 100-map workflow

Run `POE2_RUN_HUNDRED_MAP_SESSION=1 python tools/run_tests.py --pattern test_hundred_map_session.py` with `QT_QPA_PLATFORM=offscreen`. Set `POE2_SESSION_ARTIFACTS` to a new directory to retain the independent expected ledger, reopened exports, database backup and verification metrics. The Windows installer workflow runs this gate in a separate process before the remaining regression suite and retains its artifacts.

The expanded local gate completed 100 maps and checked 2,587 commits against an independent ledger without mismatches. Auto-all is enabled through the Options checkbox. Each OCR activity has at least 100 clear live automatic saves, and ambiguous readings are held for review. User actions go through the Qt controls and scan completion callbacks, including:

- Start/End inventory review, end-only maps, corrected End scans and rejected rows/scans.
- Ten custom item labels and captured icon references, retained across application restarts.
- Ritual quantities, prices, deferred flags and header counters, with 100 reviewed and 100 automatic saves.
- 100 automatic opened-remnant saves and 100 automatic visible-seed saves, plus ambiguous held/rejected readings.
- 200 ordered propagation parts, reviewed fallback, denied choices, saved-rune dropdown corrections and 100 explicit chain completions split between Review and Expedition.
- 100 Atlas/gear save actions referencing twenty distinct setups, 100 automatic waystone saves, 250 automatic tablet saves, master/perk settings, and automatic kill saves on New Map.
- CSV and XLSX export at maps 25, 50, 75 and 100, reopened independently with numeric item totals checked once per map.
- Four application restarts, a SQLite backup integrity check and repeated folder exports preserving older files.

The final workbook contains three visible sheets: **Export**, **Atlas Character Settings**, and **Scan History**, with 701, 20 and 3,575 data rows respectively. It has 20 compact Atlas setup rows, rather than a row-by-node matrix for every saved setup. A new, unscanned map opened by the final New Map action remains explicitly marked as unscanned.

## Chain lifecycle and HUD

Focused checks verify that saving a part preserves the Expedition ID, paired runes stay ordered, accepted scans count once, duplicate callbacks and transaction failures cannot duplicate or partially save data, saved open chains survive restart, and unrelated scans discard only unsaved drafts. Saved-rune corrections retain identity and counts. Complete chain closes the saved chain and advances once; completed history is read-only. Pending remnant review remains tied to its captured expedition.

Rendered Review, Expedition, Currency and Atlas pages were checked at 1366×720 and 1920×1080. Complete buttons and rune dropdowns remain reachable through page scrolling, and Atlas gear/save controls remain inside the smaller window. Reduced Atlas and region-editor minimum sizes fix an existing window-height constraint. A permanent regression covers the smaller window after chain completion and historical selection.

## Real capture checks

Permanent regressions cover supplied Ritual reward geometry and deferred markers at 75%, 100% and 125%, clipped grids, seeded remnant bars at five scales, actual inventory stacks and empty inventories, opened recipe quantities, propagation crown marks, missing cursors and partial marks. Manual seed approval is checked against the selected captured glyph and subsequent recognition using the learned reference.

Recognition tests assert the expected records and positions, not merely that OCR returns a result. Unknown names or ambiguous glyphs remain held for review and are not treated as confident automatic commits.

Propagation specifically verifies three crown peaks without requiring a gold tile frame or counting unmarked sockets. The selected recipe supplies its rune order from the database. Missing cursors use explicit recipe approval; incomplete paired marks stay in review. A clear selected recipe is not blocked by unrelated unreadable rows.

## Scope of the verification

The 100-map gate uses synthetic recognition results at the application scan ingress, so real-capture recognition is a separate gate. Offscreen Qt interaction does not reproduce a live game's physical capture or keyboard focus. The Windows workflow checks the packaged runtime, installer/update/uninstall behavior and saved-file retention; it cannot establish perfect OCR accuracy for every possible capture.
