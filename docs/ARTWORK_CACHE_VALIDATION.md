# Artwork transform cache validation

Unreleased local work on `artwork-match-speed`, based on beta source `80e84e80f5c95baa2e3d58c52ef0797114e030e8`. The tagged beta remains unchanged.

## Source and scope

The calibrated RGB inventory atlas and fixed-mask grayscale Ritual atlas previously rebuilt the same reference Fourier transforms for each different icon query. `ocr/artwork_cache.py` retains those transforms and the grayscale mask normalizer. Its arithmetic follows OpenCV 4.10.0 `crossCorr` and `matchTemplateMask`, including float64 transforms, original full-atlas block geometry, per-channel float32 conversion before addition, and the final grayscale float32 normalization scale. Upstream source and the complete original notice are bundled under `third_party/opencv_matching`.

Exactly two catalog matching calls use this cache. Candidate lists, translations, scale refinements, scores, thresholds, suggestions, manual decisions, capture/hotkey routing, HUD, storage and export code retain their existing behavior. Learned-reference preparation and exact-query caches remain in place.

Reference identity is weakly held and checked against the live immutable atlas; grayscale mask content and CPU backend settings also identify the bank. Retained arrays are limited to two banks and 160 MiB. The 320 MiB preparation reservation estimates cache-owned construction payload; it is not an absolute process-memory ceiling. Unsupported inputs/versions, numerical parity mismatches, allocation failures and OpenCV failures use original matching. The first call returns the original full response and enables reuse only after the cached response agrees, including nonfinite positions.

## Verification

Final affected run: **118 passed, zero failures, errors or skips**, across 15 modules. Coverage includes full response parity with random/fractional masks and partial FFT blocks, degenerate gray queries, changed masks/catalogs, ownership/lifetime, concurrent readers, cache limits, injected failures, real inventory captures/counts/noise, grayscale Ritual equipment/Omens/workflow, 4K Ritual recognition and packaging. Independent read-only probes also confirmed the arithmetic across additional seeded queries and concurrent gray reads.

An initial 66-test run had one test failure: its spy expected every changed query to call the original OpenCV matcher. The new cache intentionally avoids that call. The test now counts fresh query comparisons at the cache entrypoint while retaining its original masked/unmasked-change assertions; independent full-response parity checks cover the arithmetic. The final 118-test run passed.

Final direct-CPU comparison: **8 before/after pairs (16 scan reads)**, with identical complete result objects in every pair. OCR models and immutable image assets were warm; every reading used a fresh reader plus fresh query/digit/label caches. The second cached reading retained only the artwork transforms. First cached readings include transform construction and parity validation.

| Case | Original first / second (s) | Cached first / second (s) |
| --- | --- | --- |
| currency_captured_sparse | 0.866 / 0.725 | 0.897 / 0.819 |
| currency_rendered_60 | 10.917 / 10.633 | 10.466 / 9.913 |
| ritual_captured_mixed | 2.597 / 2.651 | 2.462 / 2.227 |
| ritual_captured_gray | 3.230 / 3.166 | 1.861 / 1.515 |

The host has three available CPUs; OCR/OpenCV were configured for two threads. Currency dense-60 is rendered bundled artwork with captured digits; the other cases use captured fixtures. These times exclude screen capture, IPC, HUD delivery and saving. Sparse Currency shows startup cost and no demonstrated gain; dense Currency has a modest gain, while this grayscale Ritual page benefits substantially. These samples do not establish a sub-second target or native Windows/GPU performance. The new source has not had a full-suite or native Windows run; the completed full-suite report for the tagged beta predates this cache.

The final paired-run source hashes were checked against the working tree:

```
b4ca47ab3da015314f2594642e176c4a5d1c277e9dce5148c68c245dbfb82e86  PoE2_Data_Logger/ocr/artwork_cache.py
3277b9bf1e37e6ab6e53ae62e858c583f6546e9229ca481b83366a646e6a5ade  PoE2_Data_Logger/ocr/currency_ocr.py
cc20d4f90d2289f6bd4023b93bbfca658655321251839617658bada84af16642  PoE2_Data_Logger/ocr/item_ocr.py
b0e2e87550ba03b65fcf89628fc4528266ecde48fd8978bd2b07fbd5609cc3ed  PoE2_Data_Logger/ocr/digit_kernels.py
```
