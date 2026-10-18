# PoE2 Data Logger 1.3.2.20 Beta

This beta continues from 1.3.2.19 with the confirmed scan, review and logging changes.

- Retain a Propagation request made immediately after a Remnant scan, reuse that scan's frozen image once, and deliver both results in order. Standalone Propagation scans remain available. Bounded caches reuse exact-pixel OCR evidence without caching approval or family decisions. Every Propagation scan still requires manual approval.
- Auto-save a unique opened-family match and fill its unseen catalog rows despite socket/confidence warnings. Consecutive same-family captures require confirmation; ambiguous families and stale capture ownership remain held.
- Ask whether steps belong to the same chain after more than 60 seconds. Answering No discards the older unapproved Propagation review, completes the logged chain through the existing Complete chain action, and reviews the new scan in the next expedition.
- Give each approved Currency scan a map-local ID and preserve its history. The latest approved End inventory minus the separately scanned Start inventory determines gains for every currency; snapshots are never added together or carried forward into a later scan that lacks that currency.
- Keep multiple cumulative kill-count readings per map without expedition dependence. A reading of 10 followed by 20 means 20, not 30. Only the kill-count key triggers this OCR; its region is enabled by default and accepts a tightly selected crop. General scan excludes Currency, and Currency uses its dedicated key.
- Preserve completed Ritual pages while an earlier page is being read or reviewed, including later pages when one review is rejected. Retain recognized Remnant, Waystone and Tablet readings during a busy database writer, with nonblocking retries before review and restoration of the existing HUD. Retry admission without rerunning OCR or replaying saves.
- Apply the source-traced Family 19 reward correction only to unchanged defaults, with atomic Undo and custom-reference protection. Runefather/Runebinder alloy corrections remain unapplied.
- Update Analysis Context: Gaining Traction grants +50% Monster Rarity per completed remnant on the same map; each 1% Monster Rarity contributes 1% increased Rare Monster chance and 1% increased Magic Monster chance. This guidance does not rewrite saved map percentages or kill totals.

The HUD layout, dropdowns, screenshot evidence, historical logs and manual end-map entries are preserved. Existing Currency replacement, Undo/New Map draft clearing and other busy-key lockouts retain their confirmed behavior. This release adds no game-memory access, game-file edits or new game-input actions; intended Ctrl+C behavior remains. Installer/update/uninstall guards protect running 1.3.2.19 Beta and older clients and retain saved database files.

## Validation and publication gates

Before release-only version synchronization, the complete Linux run finished with **1,858 tests: 1,856 passed, zero failures/errors and two native Windows skips**. Its injected-output Qt workflow completed **100 maps and 2,587 commits with zero mismatches**, reopened four CSV/XLSX exports and independently checked a SQLite backup. One Ritual integration case saved 55 unique pages and 55 history rows. Captured-image OCR checks are separate; workflow simulations do not establish independent raw-corpus accuracy or live game review latency.

Publication requires the final committed source to pass the Windows display/export fixtures, real native HUD input checks, 100-map workflow, complete regression suite, packaged runtime verification and installer/update/saved-file-retention checks. The verified publisher binds those reports and the installer checksum to the exact source SHA and successful first-attempt build run. Skipped native tests do not count as release proof.
