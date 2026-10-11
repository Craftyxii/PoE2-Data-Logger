# PoE2 Data Logger 1.3.2.14 Beta

This beta fixes propagation scans that recognize the recipe family but leave its existing rune dropdowns blank. It retains all changes shipped in [1.3.2.13](RELEASE_1.3.2.13.md).

## Changes

- Recheck crown peaks at verified row and socket positions. Gold artwork inside a rune and off-lattice duplicate anchors cannot invalidate correctly recognized family crowns.
- Recover interrupted frame contours using visible tile bodies and independently verified sibling socket geometry. One proven socket bar can anchor the family; a guessed standard layout cannot.
- Deduplicate evidence by socket and share one or two confirmed positions across the recognized family. Each recipe receives its own rune names at those positions; shorter recipes receive only sockets that actually exist.
- Preserve corroborating partial-mark recovery: two distinct recipe rows must support a fragment socket, with the existing exception for a single visible recipe. Exposure retries do not supply extra row votes.

The manual review HUD has no layout or control changes. Both existing dropdowns, their recipe-limited options, the screenshot preview and explicit Approve action remain. Recognition never logs a chain automatically. Other OCR modes, remnant logging, the reference catalog, saved logs and Analysis Context are unchanged. Version labels and installer metadata identify this new beta.

## Accuracy and limitations

Full production OCR was tested on the 61 original opened-panel captures, without supplying annotated text boxes or socket geometry. This tests image normalization, text recognition, mark reading, family inference and the suggestions sent to the existing dropdowns together. Detailed results accompany the propagation autofill accuracy report.

- **249/255 (97.65%)** labeled crown sockets on 238 readable recipe rows reached the selectors correctly, compared with 57/255 (22.35%) before this fix. No incorrect socket suggestions occurred in this batch.
- **234/238** readable rows and **59/61** panels had exact expected socket suggestions, including rows with no applicable marked socket.
- The remaining two panels contain Runefather/Runebinder recipes whose eight visible sockets disagree with the seven-socket catalog entries. They remain held for manual review; this beta does not alter the catalog.
- Thirteen clipped or unnamed rows, containing eight labeled crowns, were excluded from recipe-selector scoring. Counting every labeled crown, including those unassignable rows, yields 249/263 (94.68%) reaching a verified recipe selector.

See [the complete capture report](PROPAGATION_PREFILL_ACCURACY_1.3.2.14.json). Reproduce it with `python tools/audit_propagation_prefill.py --sources <extracted-three-part-evidence> --output <report.json>`. The audit verifies original image hashes before scanning.

This is the supplied calibration batch, not a held-out estimate of every live screen. Socket suggestions include family inference and are distinct from isolated physical-crown detector recall. Clipped or unnamed recipe rows cannot provide a verified dropdown identity and are reported separately. Catalog/socket contradictions stay unresolved rather than assigning the wrong rune.

The supplied captures contain no naturally partial crowns. Existing controlled partial-erasure results remain documented in [the previous accuracy report](PROPAGATION_CROWN_ACCURACY_1.3.2.13.json); synthetic fragment tests verify the two-row and lone-recipe rules without claiming natural partial-mark accuracy.

## Validation

Regression coverage includes original complete captures of Families 57, 52 and 30 and the catalog-mismatch capture, shared single and paired sockets, two-row fragment consensus, shorter recipes, interrupted contours, duplicate anchors, glyph artifacts, quantity mismatches and erased origins. Backend-to-Qt tests check every row's existing selectors and screenshot, and verify that only a manual Approve writes its own recipe/family provenance.

Publication requires the complete regression suite, native Windows HUD input, the 100-map logging/export workflow, frozen runtime and installer/update/saved-file checks. Native HUD checks use a simulated game view; the 100-map workflow injects recognition results and independently validates logging. Those checks do not establish live-game OCR accuracy.
