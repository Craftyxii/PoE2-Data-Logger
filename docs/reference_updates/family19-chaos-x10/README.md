# Family 19: confirmed Chaos Orb x10 reference update

This optional pack uses the existing importer; it is not applied automatically.
It targets the `v1.3.2.18-beta` baseline
(`f8577695e360861a55e6c63ae56ff60365c174de`).

## Exact update and evidence

The adjacent `manifest.json` is the complete readable content of
`Family19_ChaosOrb_x10_reference_proposal.zip` (607 bytes).

| Reference key | Change |
| --- | --- |
| Recipe `Chaos Orb x10` | Add 7 sockets, Currency, Lv70+, Wisdom + Vision + Rebirth + Rage + Power + Arcane + Adaptive |
| Family 19 | Keep top socket 7 and active status; use Cadigan's Epiphany → Astrid's Creativity → Chaos Orb x10 |
| Seed (Family 19, 7 sockets) | Keep P1/Wisdom; replace Aldur's Saga with Chaos Orb x10 in its reward set; mark the deliberate correction local |

The user confirms the family and supplied the current three-reward sequence.
[PoE2DB Runeshape Combinations](https://poe2db.tw/us/Runeshape_Combinations),
checked 2026-10-15, separately confirms the new Chaos quantity and seven-rune
combination. It still lists Aldur's Saga with its distinct original combination.
PoE2DB does not establish logger family IDs or a patch number/date. Shared Wisdom
alone is not the basis for this family update.

Aldur's Saga remains a separate recipe with its original combination and ID.
The new Chaos quantity receives ID `RCP-3d3b9c16959c508f96961e1e8a0e3d6f`.
The pack contains no aliases, screenshots, glyphs, inventory names or logs.
New scans use updated references; propagation still requires manual approval.

## Apply with existing controls

1. Inspect Family 19 and its 7-socket stage under **Data & export → Developer
   Mode**. This pack **replaces the supplied matching keys**, including any
   custom Family 19 list, custom (19, 7) seed fields, or existing Chaos Orb x10
   recipe. The importer has no before-image check. Unlisted references remain.
2. Open **Database Import/Export → SHARE OCR DATABASE** and check **Update
   matching family, recipe and seed records on import**.
3. Choose **Import reference pack…** and select the adjacent ZIP. Success reports
   three new/updated records: one recipe, one family, one seed.
4. Existing **Undo** reverses the import as one **Import OCR references** action.
   The new ID remains as a retired registry identity, consistent with existing
   Undo behavior. Recorded history, old attribution and screenshot bytes remain.

Import is atomic. On the target baseline, leaving the replacement checkbox
unchecked rejects the inconsistent stage and rolls back every change. Existing
Undo also invalidates stale captures/retry receipts; that behavior is preserved.

## Validation

`tests/test_family19_reference_update.py` checks the exact pack, injected reward
text before/after, Family 19 resolution without approval, three-record scope,
unchanged old identities/history/Export CSV/Scan History CSV/PNG bytes, one-action
Undo, idempotence and invalid-import rollback. Original bytes for the latest
screenshot were unavailable; these are text/backend integration checks, not a
captured-image OCR accuracy claim.

SHA256: `7699c2ceafa2cb0d930bb102fbf8d499bb64514e3a8a1168dccec3c5ff73b6ff`
