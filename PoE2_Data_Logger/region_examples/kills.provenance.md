The file `kills.jpg` is the unchanged user-supplied `Kill_Count_1_Compressed.jpg`
from the kill-count ZIP supplied in this conversation. Its SHA-256 is
`09ccea376c93c950ea03cad500d988e420673c17020f844dad16d72e5cb8cdc5`.
Dimensions: 1919 × 1079 pixels. Actual visible counters: Normal 984, Magic 719,
Rare 656, Unique 4. The player's name is a separate field to the left.

The skulls follow their associated numbers. `ocr/kill_counts.py` extracts its
four shape/colour references directly from these half-open source pixel bounds:

| Category | Left | Top | Right | Bottom |
| --- | ---: | ---: | ---: | ---: |
| Normal, white/gray | 1705 | 301 | 1723 | 322 |
| Magic, blue/purple | 1759 | 301 | 1777 | 322 |
| Rare, yellow | 1815 | 301 | 1833 | 322 |
| Unique, orange | 1869 | 301 | 1887 | 322 |

The default normalized region is (.85, .24, .15, .12), enclosing all four
counters. A full 16:9 screenshot is normalized to the reference's dimensions
for this region; a selected region is searched at bounded template scales.
The implementation requires all four shapes, independent colour signatures,
their observed order, one horizontal row and compatible spacing. It then runs
the actual bundled recognition model separately on each number, twice with
different image bounds. It retains actual raw text, confidence and source
bounds. Missing evidence, conflicting readings and unsupported numeric formats
remain incomplete. It never fills missing counts with zero.

The first numeric cell additionally requires a captured left gutter, one cell
wide, without extra character bodies. A controlled derivative that prepended
the source's digit 7 previously exposed a shared-crop truncation: both numeric
reads agreed on 984 while the added prefix lay outside both crops. The gutter
guard now rejects the derivative before OCR, including a prefix overlapping
the crop edge. A selected region lacking the left gutter also remains
incomplete. These are controlled unsupported-layout negatives, not independent
four-digit game captures or an inferred four-digit layout.

`tests/test_kill_count_ocr.py` uses this unchanged original for actual-model
positives. Resolution derivatives at 720p, 1080p, 1440p and 2160p are explicitly
resized versions of this single capture. Erasures, swapped skulls, clipping and
duplicated rows are controlled negative derivatives. Numeric zero and model
disagreement checks explicitly inject unit-test outputs and do not claim to
validate an independently supplied zero-counter screenshot.

The additional 125% selected-region derivative reads the original digit
strings, but one raw-crop confidence is only .83423. It remains incomplete at
the default .90 numeric floor; no threshold was lowered to admit it.

Only one original kill-counter example is currently available. These checks do
not establish independent game-rendering/DPI accuracy, longer or abbreviated
number formats, or other kill-counter layouts.
