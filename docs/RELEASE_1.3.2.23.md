# PoE2 Data Logger 1.3.2.23 Beta

This beta incorporates the unpublished 1.3.2.22 changes and adds further Currency artwork and count acceleration.

- Cache exact reference artwork transforms for Currency and grayscale Ritual Omen matching, with bounded memory and the original matcher as fallback.
- Prepare independent Currency artwork rankings using the selected CPU worker count. Preserve the same candidates, scores, quantities, slot order and review decisions; this adds no scan queue.
- Reduce repeated count-window arithmetic with exact integer operations. Reuse the optional GPU helper's first batch after mandatory GPU execution proof succeeds.
- Limit deferred scan holding to Remnant followed by Propagation. Keep Currency/Ritual replacement reads latest-only, without accumulating a review backlog. Preserve standalone Propagation and mandatory manual approval.
- Group multi-slot Ritual equipment into one review item, including grayscale artwork, while preserving adjacent Omens and editable suggestions.
- Save independent tablet sets and their scan progress. Attach only the selected saved set when completing a map; never add sets together.
- Add optional Ritual Rite affixes and proliferation from a completed earlier map of the same explicit Rite. Save first/final approved Tribute and remaining-reroll readings per Map ID, including readable counters on empty reward pages. Never sum cumulative readings.
- Extend CSV, workbook and SQL context for tablet sets, Rite settings and Ritual counters. Preserve historical observations, screenshots, dropdowns and manual end-map entries.
- Offer optional GPU label acceleration beside the CPU selector, off by default and applied after restart. The separate Windows DirectML helper supports compatible AMD, NVIDIA and Intel hardware, with CPU fallback.

Runefather/Runebinder alloy reference corrections remain unapplied. No Linux port is included.

## Validation scope

Before release metadata synchronization, **152 affected tests passed** with zero failures, errors or skips. A separate, overlapping GPU/preference/IPC/packaging run passed 58 tests. Eight full baseline/current CPU scan pairs produced identical complete results.

On the three-CPU Linux host with two selected threads, the rendered 60-stack Currency case changed from 11.048/10.221 seconds to 8.359/8.647 seconds for first/second reads. Models were warm; the second read retained reference transforms. This excludes capture, IPC, HUD delivery and saving. It does not establish a sub-second target or performance on the user's PC. See [the detailed validation](CURRENCY_SCAN_SPEED_VALIDATION.md) and [artwork cache validation](ARTWORK_CACHE_VALIDATION.md).

Physical GPU execution, recognition parity and end-to-end GPU latency remain unverified. Windows-specific fixture path/connection cleanup failures from the unpublished beta were repaired in tests; application database cleanup was already explicit.

Publication requires a successful run of this exact committed source through Windows display/export fixtures, native HUD input, the 100-map workflow simulation, the complete regression suite, bundled runtime/model checks, and installer/update/saved-file-retention checks. Published notes identify the source SHA and verified run. Linux checks do not establish native Windows behavior.
