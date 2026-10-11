# PoE2 Data Logger 1.3.2.8 Beta

This beta adds reversible logging corrections and improves propagation suggestions while preserving manual propagation approval.

- **Undo** reverses the latest saved action, including scan approvals, chain actions, counts, settings and starting a map. Its latest 100 actions survive restarting the logger. Undoing New map reopens the previous map for edits.
- Expedition provides **Discard chain** and **Discard last chain step**. The latter removes the latest saved part without selecting a row. Both retain remnant logs and actual detonation counts, support selected completed chains, and can be undone. Stale propagation results cannot restore discarded steps.
- Propagation can recover a shared marked socket across an unambiguous recipe group from a complete crown on any row. Two independent rows with plausible crown fragments at the same socket can suggest that socket; a single visible recipe can suggest its own fragment. Repeated detections on one row count once. Two sockets may be recovered from different rows. Conflicting evidence stays unresolved. The existing recipe-limited dropdowns and manual approval workflow are unchanged.
- Saved propagation origins include stable recipe IDs alongside family IDs for analysis. Remnant logging keeps its existing behavior.
- Excel exports add an **Analysis Context** sheet after the three existing sheets. The same schema and interpretation notes are stored in the raw SQL log database. Current totals and superseded historical actions are explained separately.
- Opened-remnant icon counting handles small vertical shifts in enlarged captures, avoiding a false socket-count mismatch while retaining review for actual mismatches.
- Exposure-amplified frame speckles no longer supply false crown-fragment votes. The Skyfall regression retains Celestial without adding an unmarked second rune.
- Scan worker submission failures now clear the busy state. File and shortcut scans have a 90-second timeout; overdue results cannot overwrite newer reviews or save data. Immediate worker completion clears its status message, and reset/close removes pending timeout callbacks.
- Partial seed approval returns consistent capture context after an implicit map start, including the chain revision used to reject stale propagation.
- Windows packaging includes the new context and Undo modules and recognizes the previous beta during installation and single-instance checks.
- The test runner rejects selectors that match no tests instead of reporting a false success.

Changed functions include purpose and behavior notes. The logger continues to use screenshots and ordinary desktop input; these changes add no game-process access or hooks.

Release publication requires the complete regression suite, a 100-map logging/export simulation, native Windows HUD input checks and installer/update verification. The simulation injects scanner results and tests logging workflows; screenshot OCR is tested separately. Some scan modes have no real captured training panels in this repository, so rendered fixtures and mocked text recognition do not establish live-game recognition accuracy.

See [the release validation record](RELEASE_1.3.2.8_VALIDATION.md) for the final test counts, findings and platform limits.
