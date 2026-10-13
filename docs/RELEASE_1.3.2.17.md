# PoE2 Data Logger 1.3.2.17 Beta

This beta repairs propagation autofill and opened-remnant recognition, removes repeated scan-review work, and records map activity with combined applicable Atlas source totals. It retains the spreadsheet export and currency quantity fixes from [1.3.2.16](RELEASE_1.3.2.16.md).

## Changes

- Fill eligible propagation recipes from verified shared sockets in a uniquely resolved family when recipient text is weak or its tile bar is absent. Each recipe uses its own database rune order; short recipes receive only sockets they contain. Conflicting marks, extra visible sockets and ambiguous families remain held.
- Reuse the crown reading for an accepted unchanged panel. Cropping or reframing still performs a fresh reading; separate captures keep separate evidence. Crown geometry, partial-mark and fragment-consensus rules, dropdowns and explicit propagation approval remain intact.
- Correct independently observed opened-remnant socket counts by rejecting the selection arrow as an anchor, preserving the first frame during normalization, and recovering supported broken borders without treating the second socket as the first. Unsupported gaps remain uncertain; database socket expectations do not replace capture evidence.
- Recover the misread Level 20 and omitted Level 19 Thaumaturgic Flux first rewards from verified captured rows. Retain quantity, explicit-level, confidence, ambiguity and spatial checks. Normal recognized rows require no additional retry.
- Add Activity Type and Map Scanned to the existing Review heading. Select one activity per map: None, Breach, Expedition, Grand Expedition, Abyss or Ritual. Map Scanned is Yes for saved manual map stats or an applied waystone scan; pending/rejected scans do not establish Yes. New Map resets readiness and Undo restores it. Compact windows place the two additions beside the title, keeping the existing Scan regions controls fully visible on a 1366-by-720 window.
- Save compact Atlas totals from the map's frozen allocation and active choices, scoped to its activity, city, biome and Ocean classifications. Combine verified equivalent general/city/biome source bonuses; keep different targets and unknown runtime requirements separate. Retain source identities and verified units without adding bonuses to observed map percentages.
- Carry Activity Type, Map Scanned and Atlas Effects JSON into Excel, map/primary/history CSV and SQL context. Primary Export includes them once per Map ID; history retains each event's saved context. Missing historical metadata stays unavailable.
- Expand Context with sourced rune and keyword meanings, aliases and guidance for the new map fields and Atlas sums. Current reference meanings do not invent rune strengths, stacking rules or loot probabilities.
- Retain each currency capture's rendered review screenshot and numbered-grid preview so completion restores the same pixels rather than rendering twice. Preserve stale-capture guards, correction controls, screenshot visibility and approval behavior. Currency recognition and thresholds are unchanged.
- Include the new Atlas snapshot helper in frozen-runtime verification and protect running 1.3.2.16 clients during update/uninstall. Installer, executable and window labels identify 1.3.2.17 Beta.

## Accuracy and validation

Captured-image accuracy checks are separate from injected-result HUD/session tests. Current scan reports and the exact-source Windows release run are attached to the published release notes once complete. No OCR model or confidence threshold was retuned for this release.

The opened-remnant audit processed 61 distinct original supplied captures: 61/61 annotated first-reward identities and independently observed socket counts matched; all 23 annotated first quantities matched; no scan exceptions occurred. Three existing catalog-conflict/ambiguity holds remain intentional. This evidence is reused only after current scanner/native-reader/model hashes, dependencies, original images and reference catalogs were verified unchanged. Its scope is first rewards and socket counts, not every reward row or unseen images.

Local change validation includes 52 Atlas/export regressions and 105 propagation/preview/overlay regressions. Four complete real propagation results and five complete Qt HUD comparisons matched before/after repeated-work removal. Test groups overlap earlier checks and are not summed as independent accuracy trials.

Atlas totals are configuration coefficients, not simulated final game effects. Unknown units, missing references and conditional effects stay explicit. Currency label recognition still takes time; repeated-work removal does not claim instant scans or a Windows timing guarantee.

Publication requires the complete regression suite on the exact release source, native Windows HUD input, the 100-map logging/export workflow, frozen runtime/model verification, installer/update/saved-file-retention checks, and the installer checksum. The published notes identify the verified source SHA and Windows run; skipped native tests cannot publish.
