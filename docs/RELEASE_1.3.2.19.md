# PoE2 Data Logger 1.3.2.19 Beta

This beta continues from 1.3.2.18 with source-traced OCR corrections and optional screenshot kill-count readings.

- Add an independent **Kill counts** scan region, disabled until enabled and saved. Existing scan hotkeys capture observed map/chain stages; **Map kill counts · final reading** captures a final reading with or without a remnant chain. Keep all four numbers/skulls and some blank space before the Normal number visible.
- Preserve existing manual end-map controls and totals. OCR observations retain their own screenshot evidence, timestamps and ownership, and export to **Kill Count Readings** and **_Kill_Counts.csv**. They are observed counter totals, not increments to sum or inferred chain-only kills.
- Honor the selected OCR thread count consistently in the native reader and all three shared RapidOCR sessions, including hosts with fewer CPUs than the selected count. Existing saved preferences, caching and restart behavior remain unchanged.
- Recover source-traced opened-reward text omitted by panel rails/separators, and accept a clipped first rune bar only when partial-frame evidence and two complete following bars independently corroborate its family/socket stage. Preserve ambiguity, quantity, level, confidence and ownership guards.
- Reduce propagation PNG encoding overhead while retaining lossless pixels, dropdown choices and mandatory manual propagation approval. The HUD layout remains intact.
- Include an optional [Family 19 Chaos Orb x10 reference pack](reference_updates/family19-chaos-x10/README.md). It uses the existing explicit import and Undo controls; it is not applied automatically and does not rewrite historical records.

## Validation and limits

The release workflow requires the complete regression suite, native Windows HUD input checks, the 100-map Qt workflow, captured-image recognition checks, packaged-runtime verification and installer/update/saved-file-retention checks before publication. Build artifacts and native/session reports are bound to the exact source revision; a skipped native test is not release proof.

Captured-image OCR checks use the supplied evidence. The known opened-reward corpus has 61 originals and 238 readable labelled rows, with 13 clipped/unnamed annotations excluded. That corpus was used during refinement and is not an unseen-image accuracy estimate. Some scaled derivatives retain existing review holds, including the Countess capture at 1440p. Exact latest Olroth/Divine propagation cases remain unconfirmed; this release does not claim every earlier scan issue is resolved.

Kill-count checks currently cover one supplied original showing 984 Normal, 719 Magic, 656 Rare and 4 Unique, plus controlled resized copies. Original zero/four-digit examples are still needed to validate those layouts. Missing or uncertain readings are held rather than saved as zero or a cropped suffix. Workflow simulations reuse recognition fixtures and do not establish independent OCR accuracy or live game-capture performance.

Runefather/Runebinder socket-reference graph corrections remain unapplied. This beta adds no game-memory access, game-file edits or new game-input actions; the existing intended Ctrl+C scan behavior is retained. Installer/update/uninstall guards preserve running 1.3.2.18 Beta clients and saved database files.
