# Grayscale equipment catalog sweep — 2026-10-22

Tested executable source: `fb798a8efa8934037f84e2f9e92e92bddddb98a9`.
Catalog revision: `75885a099f2e9be1619aa3c793fe6ab0754a511f`.
Catalog manifest SHA-256: `4e71e5f8b74218dfcceab69592ffd7cceb3081fc4665b5d1bc7c7219f6de8a7e`.

The user requested exhaustive grayscale simulations after the
[frontend sweep](FRONTEND_SWEEP_2026-10-22.md). All **449 bundled equipment
catalog entries, covering 441 names and eight footprints**, were exercised by
the matcher and both full scan paths. This covers the bundled catalog, not every
possible item or artwork variant in the game. No application fixes were made.

## Results

| Layer | Scope | Observed result |
| --- | --- | --- |
| Direct artwork matcher | 449 entries × 7 variants = 3,143 queries | Correct identity retained in 3,142 queries: 2,722 clear names and 420 held candidates; one no-match, zero wrong confident names |
| Artwork-only gray on colored backing | All 449 direct queries | Correct identity retained for every entry: 387 clear names and 62 held candidates |
| Currency full scan | All 449 entries on 66 grayscale boards | 401 retained the correct identity/candidate, complete footprint and Item quantity 1; 48 observations, including six split objects; 15 clear rows, all correct |
| Ritual full scan | All 449 entries on 117 grayscale pages | 434 retained the correct identity/candidate; 448 retained a whole Item with quantity 1; 195 clear names, all correct; one split object |
| Real capture stress comparison | 27 original/grayscale pairs, 54 full scans | Existing Ritual armor split reproduced; whole-image conversion changes some geometry, as described below |

These are different layers of evidence, not independent accuracy percentages or
thousands of separate regression test cases. Correct candidate retention means
the source name is available for review; it does not mean automatic acceptance.

### Direct matching variants

| Input | Correct clear name | Correct held candidate | No match |
| --- | ---: | ---: | ---: |
| Original color, 54 pixels/cell | 395 | 54 | 0 |
| Whole-footprint gray, 40 pixels/cell | 396 | 53 | 0 |
| Whole-footprint gray, 54 pixels/cell | 388 | 61 | 0 |
| Whole-footprint gray, 80 pixels/cell | 382 | 66 | 1 |
| Whole-footprint gray at 55% brightness | 388 | 61 | 0 |
| Whole-footprint gray, JPEG quality 85 | 386 | 63 | 0 |
| Gray artwork on the original colored backing | 387 | 62 | 0 |

Shared source-art alternatives remained available in all 63 such controls.
The sole no-match was **Quill Rain** at 80 pixels/cell. One targeted repeat
returned no match again; its expected artwork ranked first at approximately
0.89259, below the unchanged 0.90 candidate floor. It received no wrong name.
This remains a coverage observation, not a newly confirmed bug.

## New user-confirmed bugs

### Idol of Uldurn is wrongly accepted as currency in a color control

- Expected: `Idol of Uldurn`, one Item, quantity 1; manifest entry `141`.
- Actual: `Vaal Armourer's Infuser`, quantity 1, score 0.694.
- Reproduced on a mixed inventory board and an isolated one-item inventory at
  slot 57, using the production scanner and normal confidence functions.
- `clear_currency_stack` returned true; the isolated result also satisfied
  `clear_currency_read`. The real offscreen Qt review hid the wrong row; the
  isolated review table was hidden with zero uncertain slots.
- The gray rendering retained the correct held candidate. This is a **color
  control finding**, not a wrong grayscale name.
- The user explicitly confirmed this as a recognition bug after reviewing the
  simulation finding. No real-game reproduction is claimed. No scan was approved
  or saved by the reproduction, and no fix was applied.

This demonstrates why an isolated matcher result cannot certify the complete
Currency classification and review path. See
[`item_ocr.py`](../PoE2_Data_Logger/ocr/item_ocr.py) and
[`native_desktop.py`](../PoE2_Data_Logger/ui/native_desktop.py) when investigating
the interaction between currency classification, unique-item lookup and clear-row policy.

### Gray Tetzlapokal's Desire splits into six Ritual entries

- Expected: one 2×3 Item, quantity 1; manifest entry `24`.
- Actual in artwork-only gray: six single-cell review entries covering slots
  `73, 74, 85, 86, 97, 98`.
- The same color control remains one whole Item and retains the correct candidate.
- Reproduced on its mixed Ritual page and an isolated one-item page. The grid was
  detected as complete in both modes. No fragment was automatically accepted.
- The user explicitly confirmed this as a grayscale grouping bug, with the
  generated-input limitation explained. No fix was applied.

The grouping boundary precedes whole-art matching. Relevant entry points are
[`ritual_grid.py`](../PoE2_Data_Logger/ocr/ritual_grid.py) and
`item_ocr._ritual_grid_items`. This is an additional example alongside the
already-confirmed gray armor split in the original 264-Tribute capture.

## Other observations awaiting confirmation

Currency has 48 source-control observations: 41 also occur in color, and seven
are confined to the primary gray input. The seven are **Keelhaul, Temporalis,
Xoph's Blood, Choir of the Storm, The Pandemonius, The Adorned, Bursting Decay**.
All lack the correct identity/candidate in that particular delivery control;
some also lack a verified category/count. All six split objects also split in
color. There were no wrong assigned grayscale names, wrong clear grayscale
identities/counts, or rows outside the expected occupied slots.

Ritual has 15 missing-identity controls. Four regain their correct candidate in
color: **Bursting Decay, Sanguine Diviner, Tetzlapokal's Desire, Lycosidae**.
Only the Tetzlapokal grouping issue was explicitly confirmed as a bug in this
pass. The remaining controls stayed reviewable and are observations; shared
color/gray findings can include rendering or pipeline limitations.

## Real captures and rendering limits

The real-image comparison includes all 15 Ritual reward fixtures, the original
264-Tribute fixture, and all 11 Currency training fixtures. It calls the actual
scanners on each original and a whole-image grayscale conversion without
injecting geometry, OCR labels, names or count results.

- The 15 ordinary Ritual reward fixtures retain their expected footprints in
  both modes.
- The original 264-Tribute capture reproduces its confirmed four-cell armor split.
  Converting the entire image to gray instead groups that armor correctly.
- Whole-image gray loses whole-equipment grouping in the two shield captures and
  the two mixed-equipment inventory captures; the full scanner then presents
  held fragments instead of the original whole equipment.
- All 11 wholly gray inventory captures stayed in review: zero rows passed the
  clear-stack gate. Therefore this comparison provides no accepted-grayscale
  quantity accuracy sample. Independent visible-count annotations exist for
  six original inventory captures; their accepted counts showed no mismatch.
- There were no clear original-to-gray identity changes in these fixture pairs.
  That finding is limited by the small number of clear gray Ritual rows and the
  absence of clear gray inventory rows.

**Graying an entire screenshot is different from graying only item artwork.**
It changes blue backplates and grid/frame color evidence as well. The primary
catalog scan simulations therefore gray source artwork before compositing,
preserve alpha and aspect ratio, and retain colored backing and frames. Separate
pilots cover whole-board gray and gray art plus gray frames on blue backing.

The full scans use default production strictness, actual grid detection, artwork
matching and count/name readers. Color controls cover unexpected grayscale
pages: 26 Currency boards/191 entries and 11 Ritual pages/49 entries. Synthetic
boards use sparse placement, 54 pixels/cell and no quantity markers. Ritual
frames use actual bundled ornate samples. A dense synthetic layout rejected in
both color and gray was treated as an unsupported test rendering, not an item bug.

All catalog queries use the same sourced artwork known to the matcher. They
establish invariance and pipeline coverage, not accuracy on unseen game captures
or specificity against arbitrary objects. Direct queries supply the correct
footprint; full scan simulations establish geometry separately. No native Windows
hotkeys, live game capture or physical input were tested.

## Evidence and maintenance

The deliverable contains a 449-entry comparison CSV, this report, raw result
JSON/CSV, annotated external audit/reproduction scripts and selected input/UI
images for the confirmed bugs. Disposable profiles and Python caches are excluded.
The simulation scripts were kept outside production code and the default
regression suite. Repeats were limited to observed cases and renderer validation.
No runtime files, HUD elements, confidence thresholds, installer or release tags
were changed during this sweep.
