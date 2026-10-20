# Further Currency scan acceleration validation

Local, unpushed work based on `22f3cd6`, with Windows test-fixture repairs in `c4db21f`.

## Implementation

Currency scans prepare independent catalog rankings using the selected two/four/six CPU workers; one CPU keeps serial dispatch. Each worker owns only numerical scale state and shares immutable artwork. JavaScript, learned references, quantity verification, identity decisions and review remain serial. Pools end with the current scan; this introduces no scan admission or review queue. Results are published in slot order only after successful preparation. Existing-hit snapshots avoid premature LRU eviction; overridden/custom readers and failed optional preparation retain serial dispatch.

Digit matching replaces repeated binary-window reductions with exact integer integral sums and correlation. Validated input bounds keep each intersection at most1024 and the full integral at most8192; no float approximation, threshold changes or vote changes are introduced. The vendor JavaScript retains threshold sweeps, gap filling and confidence decisions.

The optional GPU helper reuses its first validated batch after mandatory provider-profile proof succeeds. It no longer reruns that batch solely because proof was needed. Batch membership, finite/shape checks, reductions, CPU fallback and request cleanup remain unchanged. This needs a fresh helper build; physical GPU execution has not been verified here.

## Results

**152 affected tests passed**, zero failures/errors/skips. These cover exact complete rank equality, one/two/four/six-worker bounds and lifetime, mixed old/new LRU entries, failure fallback, real captures and scaled captures, count clipping/reconciliation/noise, grayscale Ritual equipment/Omens, source packaging, QuickJS comparisons and500seeded integer-window cases. A separate overlapping GPU/preference/IPC/packaging run passed **58 tests**, zero failures/errors/skips. Do not add those overlapping totals.

**Eight complete baseline/current scan pairs matched exactly** (16 reads). Models and immutable assets were warm, with new per-reading readers and fresh label/digit/query caches. The second reading retained only reference transforms. Direct CPU Linux, two selected threads on three available CPUs; dense60 is rendered bundled art/captured glyphs. Other rows use captured fixtures. Capture, IPC, HUD delivery and saving are excluded.

| Case | Serial22f first / second (s) | Current first / second (s) |
| --- | --- | --- |
| currency_rendered60 | 11.048 / 10.221 | 8.359 / 8.647 |
| currency_captured_sparse | 0.992 / 0.816 | 0.985 / 0.794 |
| ritual_captured_mixed | 2.808 / 2.449 | 2.454 / 2.094 |
| ritual_captured_gray | 2.635 / 1.410 | 2.597 / 1.452 |

Currency remains above the sub-second goal. In the earlier dense profile, model inference consumed5.4s, artwork3.55s and independent count confirmation1.18s. Same-model CPU scheduling, width specialization and parallel Rec experiments did not establish a reliable additional gain. More threshold-independent caching had zero additional reuse. A narrower input changed51text readings; the existing RuneHelper English model and upstream English V4/V5 models also changed evidence. None of those model/crop changes were adopted.

The failed Windows beta22 regression run had3path-alias fixture assertions and6unclosed standalone backup readers in tests. Only test fixtures were repaired;45affected checks passed locally. Application database cleanup was already explicit. The Windows gate still needs rerunning. The full Linux2084-test result belongs to the earlier beta source and does not establish a full-suite/native-Windows pass for these changes.

Final CPU paired-run hashes (unchanged through verification):

```
54983838f11b0ecc5c3c34c57e654d77384c848c99e1c89cac8b7ee623cb6618  PoE2_Data_Logger/ocr/currency_ocr.py
9e0654c8ea505138d6a7c15a9e6eec06ec0d02edce9e303e90d578cc7fe66ab4  PoE2_Data_Logger/ocr/item_ocr.py
b4ca47ab3da015314f2594642e176c4a5d1c277e9dce5148c68c245dbfb82e86  PoE2_Data_Logger/ocr/artwork_cache.py
41c4e6ec2264ebd281a3dc675fd7ab780ec33be450503f48c0e164beb9a21684  PoE2_Data_Logger/ocr/digit_kernels.py
```

GPU worker hash checked by the separate contract run:

```
3ac70d7a44d814b791cf3f2afba10a3f9692b21e4f3e25476085a3aea0970006  gpu_worker/worker.py
```
