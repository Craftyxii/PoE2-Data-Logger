# PoE2 Data Logger 1.3.2.22 Beta

This beta adds tablet sets and optional Ritual settings, fixes review delivery, and reduces repeated Currency/Ritual recognition work.

- Keep the single deferred scan exception limited to Remnant followed by Propagation. Currency and Ritual replacement scans use the newest reading without accumulating a review backlog. Standalone Propagation and its mandatory manual approval remain available.
- Group captured multi-slot Ritual equipment into one review item, including grayscale artwork, while keeping adjacent Omens and their editable suggestions separate.
- Store independent tablet sets with their own tablet values and scan progress. Only the selected saved set is attached when completing the map; sets are never added together. Preserve finalized maps and Undo behavior.
- Add optional Ritual Rite affixes and carry proliferating affixes from a completed earlier map of the same explicit Rite. These settings never become prerequisites for scanning. Save the first and final approved Tribute and remaining-reroll readings per Map ID, including readable counters on an empty reward page; never sum cumulative readings.
- Reuse bounded prepared references and exact raw image evidence, and accelerate the pinned digit reader's arithmetic without changing its count decisions. Extend CSV, workbook and SQL context for tablet sets, Rite settings and Ritual counters.
- Add **Use GPU for Currency / Ritual** beside the CPU thread selector. It is off by default and requires a restart. A separate DirectML helper supports compatible AMD, NVIDIA and Intel hardware on Windows and falls back to the existing CPU label reader. The shared CPU runtime and other OCR modes remain unchanged.

Preserve existing screenshot evidence, historical logs, dropdowns, manual end-map entries and mandatory propagation approval. Runefather/Runebinder alloy corrections remain unapplied. No Linux port is included.

## Validation scope

Before release metadata synchronization, **578 affected tests passed** across 51 modules with zero failures, errors or skips. A further final pass of the 57 overlapping GPU preference, IPC, helper and packaging contracts also passed. Twelve CPU baseline/current scan pairs produced identical complete results. On the three-CPU Linux host, the rendered 60-stack inventory's first scan changed from 12.45 to 11.41 seconds; these measurements exclude capture, IPC, HUD delivery and saving and do not prove a sub-second target.

Earlier validation of the tablet/Rite/counter patch completed the full Linux regression set and separate 100-map and 20-map workflows. Those reports predate the final acceleration edits and do not replace exact-source release verification.

Physical GPU execution, recognition parity and end-to-end GPU latency remain unverified. Linux contract tests do not prove native Windows behavior. Publication uses a successful run of this exact committed source through the established Windows display/export fixtures, native HUD input, 100-map simulation, complete regression suite, bundled runtime/model checks and installer/update/saved-file-retention checks. Published notes identify that source SHA and verified run.
