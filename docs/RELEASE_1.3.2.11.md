# PoE2 Data Logger 1.3.2.11 Beta

This beta publishes the [OCR, review and Undo fixes prepared for 1.3.2.10](RELEASE_1.3.2.10.md).

The 1.3.2.10 installer was blocked before publication by the native Windows reference-reset test. That test still expected Undo history to remain unchanged after a reset. Reset is now intentionally undoable. The assertion now requires exactly one reset inverse, unchanged earlier inverses and unchanged unrelated ID sequences, while continuing to compare every logged table and personal setting. Application behavior is unchanged from 1.3.2.10.

App, executable, installer and prior-client guard metadata are aligned to 1.3.2.11 Beta. Changed functions retain purpose notes. All native HUD, full regression, 100-map logging/export and installer/update gates remain required before publication.

See [the detailed audit](DEEP_BUG_AUDIT.md) and the release workflow artifacts for validation and platform limits. Propagation remains manually reviewed, and remnant logging and scan strictness defaults are unchanged.
