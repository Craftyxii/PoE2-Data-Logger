# OCR and scan recovery causal evidence — beta 1.3.2.13

Only propagation receives new screenshot training. Opened-remnant and visible-seed recognition algorithms and logging rules remain unchanged; their production child/direct outputs matched exactly on bundled real screenshots.

## Corrected assignment and call paths

1. Malformed observed Waystone properties: `_PERCENT.search` accepted a suffix from `+21O5%` and `_number` assigned `5` to `fields["waystone"]`; `clear_waystone_read` then regarded the proposal as complete. The numeric parser now requires a single complete token with no remaining digit/sign/percent evidence. `parse_item_text` records an observed but unreadable property in `uncertain`, and `clear_waystone_read` prevents automatic save when that list is nonempty. Absent optional properties remain allowed. Regression covers screen/copy paths, eight malformed token forms, three property types and valid signed/comma-decimal/augmented values.

2. Ritual equipment footprint under JPEG compression: fixture 13's upper ornate corner shape correlation fell from .761 to .698 at JPEG quality 85, below .72, despite .875 matching gold-colour coverage. `framed_footprints` consequently split occupied `[25,26]` into independent slots. Only an upper patch with at least .75 matched gold coverage may use the .68 shape threshold; the independent lower-corner/rectangle checks remain. All 15 real JPEG85 fixture footprints now match, with existing geometry and deferred tests retained.

3. Preflight Reading state: inventory/Ritual reference loads and file-map database preparation formerly occurred after `*_reading`/`pending_review_kind` assignments but before `_submit_scan` created a Future/watchdog. A raised preparation exception left Reading with no owned task to finish it. Reference/context preparation now completes before cancelling/replacing the existing review. Post-Reading preview/admission errors pass through capture-bound `_scan_failed`; approval is disabled and rejection/retry remain possible. Regression checks preserved drafts and six preview failure paths, and asserts neither the normal executor nor isolated OCR transport admitted work.

4. Native stalls after timeout: cancelling an ordinary running ThreadPoolExecutor Future did not terminate the native reader. Two stale readers exhausted the shared pool, blocking exports/new scans; final `shutdown(wait=True)` could keep the process/instance lock alive indefinitely. OCR Request descriptors now enter a shared bounded broker (two live readers, two queued descriptors) supervised off the Qt thread. Absolute deadlines include queue/startup/recognition. Timeout/cancel terminates and reaps the exact child before reusing/replacing its slot; ordinary exports/saves retain their own executor. Queued deadlines, overload, broken children, pickle/startup errors and temporary screenshot cleanup are tested with actual spawned processes.

5. Desktop rejection/supersession ownership: `_submit` previously retained only `(callback,generation)` while rejection/reset/new captures cleared `*_reading`. The native Future could remain active until its deadline with callbacks/watchdogs retained. Scan callbacks now carry their exact reading field/capture identity and the window tracks their Future. Obsolete callbacks are removed before cancellation can emit a completion; only obsolete scan Futures are cancelled. New-map/reset/close/reject/replacement retire their old readers. Real-child integration checks all five desktop modes, rapid rejection/retry, same-/cross-mode replacement, reset and preservation of a pending export Future. Independent reviewer reproduced active=1/task=1/timer=1 before, all zero with cancelled Future afterward.

6. Hotkey capture cancellation: each recognition stage freezes the original capture deadline and owns its exact Future/revision. Timeout/cancel reaches the child Future, old completions cannot replace the next result or release its capture lock, and rejecting another mode cannot kill an unrelated reader. Three integration tests include genuine blocked child readers and deadline inheritance across repeated fallback stages.

7. Frozen child launch and smoke coverage: `multiprocessing.freeze_support()` runs before desktop/Qt imports. The installer smoke now sends the same real bundled-model/resource/pixel checks through an isolated worker and requires a distinct PID. Its admission deadline is 45 seconds so the installer verifier's outer 60-second guard leaves time for bounded worker cleanup. Startup failure closes both Pipe endpoints. The frozen-module validator requires `core.ocr_workers`. Native Windows frozen/install validation is still required; Linux source smoke is not a substitute.

## Propagation crown corrections

The supplied opened recipe panels are independently labeled in `tests/fixtures/propagation_crowns/manifest.json`: 61 panels, 251 visible socket rows and 263 complete crowns. The 66 pre-opening images are excluded. No naturally partial crowns were present, so synthetic damage results must be labeled separately.

The upstream path is captured RGB pixels → scoped gold masks → connected peak bands → socket anchors → family-group suggestions. A horizontal recipe border merged the three crown peaks into a wide component and discarded valid anchors. Border isolation now exposes the tips below that line, with two distinct exposure samples required for a recovered candidate. The original band remains independent evidence, preventing an unrelated border beside a crown from erasing it. Confidence is assigned within the socket footprint. Narrow intermediate exposures recover pale compressed tips between the original samples.

A second geometry defect allowed the same search tolerance below the socket baseline as above it. Three gold strokes inside ordinary rune artwork could therefore be assigned as an extra crown. The search now retains its tolerance above the baseline and permits only the resampled rounding tolerance below it. Independently labeled captured artwork, plain frames, one/two-tip shapes, overlapping borders, duplicate exposure support, frameless crowns and cropped/scaled captures are regression cases. The same border isolation and corroboration apply to tentative fragments, with artwork below the baseline rejected. Family and recipe validation, distinct-row fragment votes, socket limits and explicit manual approval remain unchanged.

`tools/audit_propagation_crowns.py` reports direct physical mark recall/precision and exact rows before family inference. It reconstructs original row phase before normalization and keeps source hashes. Controlled partial tests remove actual tips to the independently located frame line, leave the frame untouched, and report unsupported damage cases rather than treating them as successful tests. Naturally partial accuracy cannot be established from this batch.

## Measured captured-mark accuracy

See [the reproducible report](PROPAGATION_CROWN_ACCURACY_1.3.2.13.json). The historical helper found 159/263 complete physical crowns (60.46% recall, four false detections). The calibrated helper found 252/263 (95.82% recall), with zero false complete crowns and 241/251 exact socket rows. This direct geometry measurement supplies annotated row/lattice coordinates; it does not claim 95.82% end-to-end recognition across every catalog, crop or live game setting.

Controlled tip erasure on 208 supported source crowns produced 624 two-tip and 624 one-tip fragment cases. Recovery was 299/624 (47.92%) with two tips remaining and 439/624 (70.35%) with one tip remaining. Zero damaged marks were misclassified as complete crowns, zero unrelated sockets were added in these cases, and all 208 all-tip removal controls were rejected. Another 44 detected source crowns lacked a sufficiently verified frame for this damage protocol; 11 source crowns were undetected. These exclusions remain explicit in the report. The batch had zero naturally partial crowns, so no live partial accuracy is claimed. Fragment evidence can suggest a socket after the existing distinct-row/family checks; manual approval remains mandatory.

All 61 original complete panels were also processed end to end without recognition exceptions. Holds due to incomplete recipes, unmatched database entries or conflicting shared slots are intentional review safeguards, rather than evidence of correct automatic recognition.

## Completed focused checks

- Existing nonprop inventory/count/noise/currency/item/tablet baseline: 63 tests passed.
- Waystone property/launch/module-verifier focused checks: 24 tests passed; latest launcher-only three tests also passed after adding the smoke timeout assertion.
- Ritual geometry: 15 tests passed, including all 15 JPEG fixture layouts.
- Real process broker lifecycle: nine tests passed, including cancellation waiter notification and both startup-failure pipe endpoints.
- UI preparation failures: three tests passed with both executor admission assertions.
- Real hotkey transport integration: three tests passed.
- Real desktop child ownership integration: four tests passed, including remnant supersession of inventory/Ritual readers and preservation of reservation, preview and draft after late success/error callbacks.
- Existing hotkey watchdog/listener/resolution/propagation routing/CPU preference: 49 tests passed.
- Source `python -m PoE2_Data_Logger --smoke-test`: passed with real child/bundled OCR.
- Production child/direct opened-remnant and visible-seed results: exact parity.

Artifacts are under `/tmp/poe2-*-final.log`, `/tmp/poe2-desktop-worker-integration-final.log`, `/tmp/poe2-hotkey-worker-integration.log`, `/tmp/poe2-child-smoke-check.log` and `/tmp/poe2-ocr-worker-model-audit.json`.

The full-suite delayed-review fixture had mocked `_submit`, so no owned Future existed. Its old `work()` assertion invoked the new Request descriptor afresh after retirement; a returned reading caused the first assertion to fail before cleanup, and the next subtest correctly refused to overwrite the leftover remnant. The queued fixture now asserts the upstream capture identity is retired and checks both late success/error callbacks without submitting new recognition. A separate integration test uses actual blocked children and proves their cancellation/reaping plus preservation of the newer remnant reservation, screenshot and recipe draft. Both modules passed all 15 tests. No production change was needed for this test mismatch.

## Real-model memory evidence

First new-child Waystone OCR including startup/model load: 6.97 s. Two concurrent same-image reads: 5.24 s with identical fields. One warmed child's RSS was about 166 MiB; two were about 205+176 MiB after reads (native inference peaks around 417–437 MiB each). Parent RSS rose only 4.5 MiB; measured RapidOCR/seed/icon model caches stayed unloaded on the normal route. Worker shutdown took .021 s and no OCR children remained. These are cloud-side workload measurements, not native Windows input validation. The report's English-model cache field was unavailable due to using the wrong cache attribute name and is explicitly not claimed as measured.

Do not report a complete game/backend bug-free guarantee from these checks. Full-suite, propagation OCR accuracy, native Windows release validation and final beta publication are separate release gates.
