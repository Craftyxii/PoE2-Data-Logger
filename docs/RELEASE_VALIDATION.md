# 1.3.1.1 Beta validation

The release gate exercises the application workflow and separately verifies recognition against real captures. All generated session data is simulated; it is not a player's session.

## 100-map workflow

Run `POE2_RUN_HUNDRED_MAP_SESSION=1 python -m unittest tests.test_hundred_map_session` with `QT_QPA_PLATFORM=offscreen`. Set `POE2_SESSION_ARTIFACTS` to a new directory to retain the independent expected ledger, reopened exports, database backup and verification metrics. The Windows installer workflow also runs this gate and retains its artifacts.

The local gate completed 100 maps and checked 1,672 commits against an independent ledger without mismatches. User actions go through the Qt controls and scan completion callbacks, including:

- Start/End inventory review, end-only maps, corrected End scans and rejected rows/scans.
- Ten custom item labels and captured icon references, retained across application restarts.
- Ritual quantities, prices, deferred flags and header counters.
- Remnant recipes, seed approvals, paired propagation runes, manual fallback, denied choices and chain commits.
- Twenty Atlas/gear setups, map and tablet modifiers, master/perk settings, and automatic kill saves on New Map.
- CSV and XLSX export at maps 25, 50, 75 and 100, reopened independently with numeric item totals checked once per map.
- Four application restarts, a SQLite backup integrity check and repeated folder exports preserving older files.

The final workbook contains three visible sheets: **Export**, **Atlas Character Settings**, and **Scan History**. It has 20 compact Atlas setup rows, rather than a row-by-node matrix for every saved setup. A new, unscanned map opened by the final New Map action remains explicitly marked as unscanned.

## Real capture checks

Permanent regressions cover supplied Ritual reward geometry and deferred markers at 75%, 100% and 125%, clipped grids, seeded remnant bars at five scales, actual inventory stacks and empty inventories, opened recipe quantities, propagation crown marks, missing cursors and partial marks. Manual seed approval is checked against the selected captured glyph and subsequent recognition using the learned reference.

Recognition tests assert the expected records and positions, not merely that OCR returns a result. Unknown names or ambiguous glyphs remain held for review and are not treated as confident automatic commits.

Propagation specifically verifies three crown peaks without requiring a gold tile frame or counting unmarked sockets. The selected recipe supplies its rune order from the database. Missing cursors use explicit recipe approval; incomplete paired marks stay in review. A clear selected recipe is not blocked by unrelated unreadable rows.

## Scope of the verification

The 100-map gate uses synthetic recognition results at the application scan ingress, so real-capture recognition is a separate gate. Offscreen Qt interaction does not reproduce a live game's physical capture or keyboard focus. The Windows workflow checks the packaged runtime, installer/update/uninstall behavior and saved-file retention; it cannot establish perfect OCR accuracy for every possible capture.
