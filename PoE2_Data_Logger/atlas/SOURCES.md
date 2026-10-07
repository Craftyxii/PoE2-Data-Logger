# Path of Exile 2 Atlas catalog

This bundle contains the **PoE2 Atlas**, not the PoE1 Atlas or the character
passive tree. It loads entirely offline. Game data and artwork are copyright
Grinding Gear Games. This application is not affiliated with or endorsed by GGG.

## Data sources

- Atlas graph: [RePoE PoE2 `Atlas.json`](https://github.com/repoe-fork/poe2/blob/283159358ddd925e6ac0b4ca2f54dc5737ab0c9d/data/passive_skill_trees/Atlas.json),
  commit `283159358ddd925e6ac0b4ca2f54dc5737ab0c9d`, September 6, 2026, exported
  game build `4.5.5.1.5`. This supplies the nodes, names, effects, numeric stats,
  connections, layout groups, orbits and artwork paths.
- Choice options and numeric values: [RePoE PoE2 dat-export tables](https://github.com/repoe-fork/dat-export/tree/16088913cc941c15484b3e0bab96481106ae8879/current/poe2/heuristics/csv),
  commit `16088913cc941c15484b3e0bab96481106ae8879`, September 15, 2026, exported
  game build `4.5.5.2`. The used tables are `PassiveSkills.csv`,
  `PassiveSkillVariants.csv`, `PassiveSkillVariantTypes.csv`,
  `AtlasPassiveSkillSubTrees.csv` and `Stats.csv`.
- English formatting rules: RePoE PoE2 `data/stat_translations/`
  `atlas_stat_descriptions.json`, `atlas_variant_stat_descriptions.json`,
  `stat_descriptions.json`, `endgame_map_stat_descriptions.json`,
  `map_stat_descriptions.json` and `passive_skill_stat_descriptions.json`.
  Exact downloaded input SHA-256 hashes are retained in both the input archive
  and built catalog. Conditions and numeric formatters are applied before
  flattening in-game glossary markup into readable English.

The catalog has 575 graph definitions: **530 allocatable nodes**, seven roots
and 38 decorative nodes. It contains 44 choice nodes and 142 choice options,
covering Main Atlas, Breach, Expedition, Ritual, Delirium, Abyss and Incursion.
Main Atlas includes the other mechanics represented in the original export.
Every node and choice keeps stable game IDs and the original numeric stat IDs.
Hidden or untranslated stats remain in numeric data without invented effects.

The bundle version `4.5.5.2` is an **internal exported game build**, not a claim
that it is the user's installed public patch. All graph node stats were checked
against the newer raw passive table and match exactly. Historical snapshots
must retain the catalog version and effects they were created with.

## Artwork sources

Icons and backgrounds are GGG game artwork distributed with the
[Sweet Vision PoE2 build converter](https://github.com/PraedythXIV/poe2-build-converter/tree/27f5dad0d0979aa23a604defd11fcf7ae4668444),
pinned commit `27f5dad0d0979aa23a604defd11fcf7ae4668444`:

- `src/assets/tree/atlas-icons.webp` with `src/data/atlasIcons.json`:
  all 126 distinct game icon paths referenced by this Atlas export.
- `src/assets/tree/atlas-bg.webp`, `atlas-bg-general.webp` and
  `src/data/atlasBg.json`: main and six activity backgrounds.
- `src/assets/tree/frame.webp` and `frame.json`: allocated/unallocated generic
  GGG normal, notable and keystone frames. These are **generic passive-tree
  frames**, not the exact AtlasScreen frame assets.

Atlas artwork was captured July 4, 2026 from exported game build `4.5.4.3`.
All 126 icon paths match this newer graph; no node icons are missing. Sprite
crops preserve the game artwork. Upstream paths and SHA-256 hashes are listed
in `art-provenance.json`.

The upstream project's MIT license applies only to its own source code; it
does not relicense GGG game data or artwork. The original license and notices
are included as `UPSTREAM-LICENSE.txt` and `UPSTREAM-NOTICES.md`. No upstream
planner implementation code, including AGPL planner code, is used here.

## Offline rebuild

Run `python tools/build_atlas_catalog.py` from the repository root to regenerate
`catalog.json.gz` from `sources/inputs.json.gz`. This compressed source archive
contains the entire original Atlas export and only the relevant passive-table
rows and translation entries. It requires Python's standard library and does
not access the network. Gzip timestamps and JSON ordering are deterministic.

For a source refresh, download the pinned JSON/CSV inputs above into a research
directory and name them as specified in `INPUT_NAMES` in the rebuild script.
Then pass `--research-directory DIRECTORY`. Numeric values, choice joins,
counts and graph references are validated during the build.

To regenerate cropped artwork, additionally provide `--art-directory DIRECTORY`
containing the original sprite images, crop JSON and asset manifest. This
optional asset build uses Pillow. Runtime loading has no Pillow requirement.

Allocations are binary: clicking a node switches it on or off. The exported
graph provides no multiple-rank caps or authoritative point budgets. The
catalog therefore does not invent ranks, progression requirements or budgets.
Choice alternatives remain mutually exclusive selections within each node.
