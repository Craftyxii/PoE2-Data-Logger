# Currency and Ritual artwork startup preparation

Implementation commit `cd3fc7e`, based on
`00cf00fa8dc448e80589124a31dd8ab7410e7cad` (`v1.3.2.24-beta`), prepared for
[1.3.2.25 Beta](RELEASE_1.3.2.25.md). The measurements below precede release
metadata synchronization; published notes identify the verified release source.

## Implementation

Normal desktop startup asynchronously admits one preparation job to each of the
two existing OCR supervisors. Explicit supervisor affinity ensures that both
physical processes prepare their own banks, even when the first job finishes
quickly. These jobs use the existing bounded admissions, deadlines, cancellation
and shutdown transport, outside scan-review bookkeeping.

Preparation calls the production cache builders for the catalog candidates and
scale refinements, inventory RGB and Ritual grayscale reference transforms,
learned inventory/Ritual reference snapshots, Ritual fallback icons and all eight
unique-item footprints. Scans retrieve these same objects from the same caches;
there is no separate startup icon store or first-scan preparation pass. Pixel
queries and screenshot-result caches remain capture-dependent. The first actual
FFT query still checks the entire response against the original OpenCV matcher.

The unique-bank LRU increases from 32 to 64 MiB because the eight current banks
occupy about 39.96 MiB. Other existing bounds remain: two FFT banks / 160 MiB,
16 MiB / 24 learned-reference banks, and eight example-atlas batches. Imported
reference collections remain subject to these bounds. Content-based reference
invalidation, exact-content Undo reuse and source-integrity checks remain active.

A replacement worker prepares before Currency/Ritual recognition within that
scan's original deadline. Unrelated recognition does not trigger preparation.
A failed bulk preparation is attempted once per process and leaves the normal
on-demand recognition path available. Installer smoke startup keeps its existing
recognition and timeout checks.

## Measurements

Measured on the Linux test host with two OpenCV threads, a fresh initialized
profile and no learned references. These are artwork-preparation measurements,
not Windows capture-to-HUD scan latency or GPU benchmarks.

- Direct full preparation: 3.934 seconds; RSS increased from 70.14 to 408.89 MiB
  (338.75 MiB). The complete scale refinements account for 42.64 MiB.
- Both public workers ready: 5.019 seconds after a 0.81 ms asynchronous admission.
  Worker RSS: 411.93 and 411.82 MiB; combined PSS: 781.79 MiB.
- Each worker retained 539 families / 1,617 scale arrays, two FFT banks
  (127.20 MiB), and all eight unique banks (39.96 MiB by bank accounting).
- Repeated preparation retained the same reader, scale arrays, transforms,
  unique banks and learned-reference banks. Public startup admission reused the
  same futures. Neither startup preparation loaded OCR models nor GPU helpers.
- Before/after CSV bytes, every database table's row count and content hash,
  and all 22 metadata entries were identical. No observations or scans were added.
- Separately loading the real CPU text engine preserved the prepared transforms'
  OpenCV backend identity on this host.

## Verification

The new cache tests exercise production cold/warm result equality, forbid PNG
decoding, scale construction and FFT construction during warm matching, and
check object identity for all eight unique footprints. They also cover learned
reference changes, bundled Omen changes and corrupt-source fallback.

Twelve real-process lifecycle tests passed, covering distinct prepared PIDs,
idempotence, context changes, bounded admissions, cancellation, replacement,
original deadlines, preparation failures, unaffected unrelated work and shutdown.
The existing 21 worker, hotkey and desktop recovery tests also passed.

Full Linux regression run: **2,184 cases; 2,181 passed, three skipped, zero
failures or errors**, across 12 isolated batches in 1,764.237 seconds. The skips
were native Windows hotkey APIs, the separate native Windows desktop gate, and
the opt-in 100-map GUI gate. No batch crashed or required rerunning. These results
do not replace the separate Windows release checks.
