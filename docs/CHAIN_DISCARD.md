# Saved-chain corrections

Expedition contains **Discard chain** and **Discard last chain step** beside the saved
chain editor. Last-step discard removes the latest saved part without requiring row selection;
earlier parts retain their IDs and order. These actions also
work on a selected completed expedition; clearing the entire chain reopens that
expedition, and removing a step updates its saved completion size. Neither action
advances the expedition number.

Discard removes only the selected logged propagation data. Saved remnants,
inventory snapshots, map kills and actual detonation totals remain intact.
**Save corrections** edits the current saved rune pairs, including those in a
completed chain. **Undo** restores discarded rows and their recipe/family origins.
Old propagation captures and save retries cannot restore discarded parts.

Current Export rows reflect the surviving chain. Scan History retains the
original approvals and adds `Chain discard` or `Chain step discard` records with
the removed step, rune pair, recipe ID and family evidence. Their `Chain Status`
is `Discarded`. Interpret the records in scan commit order: a step number can be
used again when new parts are logged after removal, so use the scan commit number
with the expedition and step when identifying a particular logged event.

The local service dispatcher also exposes `/api/chain/discard`,
`/api/chain/step/discard` (with `step`), `/api/chain/last/discard` and `/api/discard-map`. Supply
`expected_context` from `scan_context()` to reject an outdated request. Map
discard clears the current map's saved logs and counts while retaining its map ID
and settings. It preserves prior maps and learned references, records an audit
event, retires old captures, and can be reversed with Undo. It has no additional
desktop button.
