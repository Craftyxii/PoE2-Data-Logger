# Audit after 1.3.2.6 Beta

This audit fixes demonstrated failures without changing manual propagation approval,
currency confidence grouping, saved-chain completion or existing export formats.
The release target is 1.3.2.7 Beta; the 1.3.2 stable release remains unchanged.

## Changes and user impact

| Area | Failure reproduced | Correction |
| --- | --- | --- |
| New map | Invalid kill counts cancelled ownership of a pending remnant file scan, leaving Review stuck on “Reading”. | Retire the scan only after map validation and creation succeed. |
| Capture cancellation | Cancellation during HUD preparation, screenshot capture or clipboard reading still allowed later capture/OCR work. Closing also left active capture ownership valid. | Recheck cancellation between blocking adapters; invalidate capture before closing HUD callbacks. |
| Windows shortcuts | A failed Windows message read silently stopped shortcut processing. | Report the listener error and release its timer/hotkeys. |
| Reset consistency | Capture tokens and HUD state could combine fields from before and after a concurrent session reset. | Read each state/context from one SQLite snapshot. |
| Reset versus writes | Detonated-count writes could recreate orphan expedition activity; a next-map marker could reappear after reset. | Acquire the write transaction before selecting and validating the target map. |
| Enlarged opened scans | Enlarged captures lost the heading or mistook enlarged reward/frame pixels for an incomplete list. | Restrict heading OCR to the panel; normalize parchment completeness checks to reference scale. Visible unread rewards still prevent complete-list evidence. |
| Tablet values | Extra numbers such as `10 20% increased Pack Size in Map` were silently reduced to an exact affix match. | Keep ambiguous numeric rows for review; retain valid decimals and displayed roll ranges. |
| Failed paired exports | Recovery replaced an existing symbolic link with a regular file, or removed a dangling link. | Back up and restore the link itself, retaining recovery copies if rollback fails. |

Changed functions include purpose/behavior notes. Each correction has a regression
test, including real screenshot recognition at 150% and 200% scale.

## Validation

- Full Linux/offscreen suite on the final code: 1,046 tests, zero failures/errors,
  three platform/opt-in skips (1,109.9 seconds).
- Native Windows HUD gate: passed 22 phases with 902 Win32 input events, covering
  sliders, review edits, deep-row scroll retention, overlay/taskbar restoration,
  reference reset and raw SQLite export. It uses an external simulated game view.
- Targeted storage/export checks: 96 passed.
- Targeted platform/capture checks: 71 passed.
- HUD failure/cancellation checks: 2 passed.
- Tablet ambiguity checks: 3 passed; existing item regressions: 17 passed.
- Fresh 100-map Qt workflow: 2,587 commits, zero ledger mismatches; saved exports,
  full SQLite backup and restart checks passed. Scan results in this workflow are injected.
- The same 100-map workflow also passed on Windows: 2,587 commits, zero mismatches
  (983.0 seconds).
- Ten repeated 60-row currency review/navigation cycles under the normal Qt event
  loop: constant widget count, RSS increase 1.93 MiB, individual row approvals at
  most 2.8 ms. Largest observed full-review rendering pause was 635 ms.

The 100-map harness recreates windows in one Python process and manually pumps Qt;
its process-memory growth is not evidence of normal application memory behavior.
The separate timer-driven probe uses the ordinary event loop without forced deletion.

These checks cannot establish that every game screen, GPU, DPI combination or
future OCR reading is correct. Live Path of Exile interaction remains a separate
manual validation requirement. Reference imports hold a write transaction; contention
can delay synchronous inventory/Ritual saves, but no naturally prolonged import
hang was reproduced in this audit.
