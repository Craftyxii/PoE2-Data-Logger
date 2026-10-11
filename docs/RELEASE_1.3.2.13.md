# PoE2 Data Logger 1.3.2.13 Beta

This beta improves scan recovery, propagation crown detection and the analysis guidance shipped with exports. It retains the reviewed currency, Ritual, waystone, reference and Undo fixes from [1.3.2.12](RELEASE_1.3.2.12.md).

## Changes

- Run native OCR in reusable child processes with bounded admission. A cancelled, timed-out or crashed native reader can be stopped and replaced so subsequent scans can run; its late result cannot replace a newer review or save data. Recognition keeps the existing 90-second deadline, including queued work.
- Recover cleanly from failures while admitting a scan, without leaving the HUD on Reading or cancelling an unrelated capture.
- Reject malformed or multiple waystone property percentages instead of accepting a valid-looking suffix. Observed ambiguous properties stay available for manual review.
- Retain independently verified multi-cell Ritual equipment when mild image compression blurs an ornate corner. Color support and the matching opposite corner remain required.
- Recover propagation crowns and tentative fragments below overlapping recipe borders. Preserve isolated native peaks, sample pale tips between the original exposures, and reject gold rune artwork below the socket baseline. Recovered evidence needs corroboration; plain frames and conflicting sockets remain unresolved.
- Add Analysis Context version 3: field definitions, units, saved Atlas/node/choice interpretation, modifier-count conventions, comparison guidance and limits. The same application-owned context is stored in SQLite and exported to the workbook.

Propagation always requires manual approval through the existing recipe-limited rune selectors. Screenshot previews, remnant logging, saved chain workflow, confidence thresholds and OCR strictness defaults remain in place. This work adds no game-process access or hooks. Changed functions retain purpose notes.

## Propagation accuracy

On the 61 supplied opened panels, independently labeled physical marks improved from **159/263 (60.46%)** to **252/263 (95.82%)**, with **zero false complete crowns** and 241/251 exact socket rows. This is direct mark detection using annotated row/lattice geometry on the calibration set, before text recognition and family inference.

Controlled tip erasure on 208 supported captured crowns recovered **299/624 (47.92%)** fragments with two tips remaining and **439/624 (70.35%)** with one tip remaining. None were mistaken for complete crowns or assigned to unrelated sockets; all 208 controls with every tip removed were rejected. Forty-four detected crowns lacked a verified frame for this erasure protocol, and 11 source crowns were undetected. The batch contained no naturally partial crowns, so these synthetic results do not establish live partial accuracy. Existing two-row consensus, lone-recipe exceptions and explicit approval remain in place.

See the [reproducible accuracy report](PROPAGATION_CROWN_ACCURACY_1.3.2.13.json) and [causal fixes and regression coverage](OCR_BETA_1.3.2.13_AUDIT.md).

## Validation

Installer publication requires the following source and native release gates:

- Independent captured-mark and controlled partial benchmarks completed; pixel integrity, denominators, cropped/scaled captures and false-mark controls are regression-tested.
- Actual worker cancellation, timeout, crash, queue admission, cleanup and subsequent-scan recovery.
- Full regression suite, native Windows HUD input and 100-map logging/export workflow.
- Frozen runtime, installer/update, running-client guards, saved-file retention and published checksum.

The 100-map workflow injects recognition results and checks logging independently of captured-image OCR. Native HUD checks use a simulated game view. Controlled synthetic crown-tip removal is separate from naturally captured partial marks; neither establishes accuracy on every live-game screen or native 4K configuration.
