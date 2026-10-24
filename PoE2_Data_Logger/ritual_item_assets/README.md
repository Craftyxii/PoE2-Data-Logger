# Ritual item catalog snapshots

This directory contains the complete reviewed source data and a small Ritual-only
exclusion overlay. It preserves the shared inventory artwork catalog, existing
labels and historical records.

Ritual scans read `catalog.json` through `core/ritual_catalog.py`, from 1.3.2.30
Beta on. A name on its exclusion list, or on that module's short list of
player-confirmed exclusions (Origin Spark), is never named or suggested on a
Ritual page, and a reviewed Ritual row cannot use it; the art match is held for
review instead. A Foretold Bounty name always stays possible, and every other
name keeps its existing recognition. Currency and inventory scans do not use
this catalog. Copying these files into a 1.3.2.29 installation does not
activate the exclusions.

## Contents

- `exiledb-uniques.json`: the complete 446-record upstream response, unchanged,
  including its patch, build, attribution and terms.
- `exiledb-index.json`: the complete upstream schema/index response, unchanged.
- `repoe-omens.json`: all 50 records in the pinned RePoE Omen-class export,
  unchanged. Five are Expedition Sagas and 45 have Omen names. Full source
  presence is not a drop-eligibility claim.
- `repoe-version.txt` and `repoe-exported-version.txt`: the original version files
  from the pinned export.
- `ritual-rites-beta29.json`: an unchanged copy of the released .29 Rite catalog,
  retained beside the upstream snapshots for the bounty-intersection review.
- `catalog.json`: 66 reviewed exclusions, 25 named Foretold Bounty exceptions,
  source identities and unresolved cases.
- `provenance.json`: source URLs, revisions, capture time, byte counts, hashes,
  terms, review counts and the unchanged local-input identities.

The overlay's 66 exclusions comprise 57 uniques, eight Abyss Omens and removed
Omen of Recombination. It leaves every unreviewed item unknown, retaining the
existing recognition behavior. A missing acquisition annotation is not an
affirmative eligibility decision.

## Review boundaries

Unique acquisition restrictions come from
[ExileDB](https://exiledb.com/raw/uniques.json), corroborated against
[Path of Building's source annotations](https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2/tree/bb52d6b368307457eb9c54bb13f1829993d390b1/src/Data/Uniques).
A boss acquisition annotation is the documented basis for an inferred Ritual
exclusion; it is not an exported server-side Ritual reward table. ExileDB's
`legacy` field alone is insufficient: The Road Warrior has `legacy: false` and
`source: "No longer obtainable"`.

All 60 bundled .29 Foretold Bounty rows were checked before generating the
exclusions. Their 10 named uniques and 15 named Omens are retained as possible
reward exceptions, including Original Sin. No proposed exclusion intersects
these names. The bundled Rite catalog records published choices without
promising that every choice is currently enabled. If refreshed evidence creates
an intersection, preserve the possible named Ritual reward and review the
conflict.

Omen of Corruption has conflicting Standard-exchange and bundled bounty evidence;
it remains accessible. No new exclusions were inferred for Omen of the Blessed,
Omen of Sanctification, Grip of Kulemak, Heart of the Well or Loreweave. Campaign
Ritual, currency bounties and conversion, Queen Idols and Sacred Bloom are not
removed by a blanket family filter.

The pinned RePoE export still marks removed Omen of Recombination as released.
The official 0.5.0 patch overrides that field. The two name differences in
`aliases` are source-join hints only; they do not change inventory labels or
stored records. The original `unique_item_assets/manifest.json` and every
artwork file remain untouched.

## Sources and rights

[ExileDB's documentation](https://exiledb.com/about) describes game-file names,
base types and art, Path of Building unique definitions, and trade-site legacy
status. The complete upstream terms remain in both ExileDB JSON snapshots:
free to read and quote with a link to the source page; game data and art are
copyright Grinding Gear Games; ExileDB is not affiliated with GGG.

The [RePoE export](https://github.com/repoe-fork/poe2/tree/75885a099f2e9be1619aa3c793fe6ab0754a511f)
is pinned to game version 0.5.5.4.2. Generated game data remains copyright
Grinding Gear Games. RePoE's software license does not license game artwork.
No new artwork or third-party executable code is included in this directory.

Official removal evidence:
[GGG 0.5.0 patch notes](https://www.pathofexile.com/forum/view-thread/3932540).
Abyss acquisition evidence:
[PoE2DB Abyss](https://poe2db.tw/us/Abyss), checked against the pinned Omen records
and the existing .29 grayscale Ritual restriction.

## Refresh and validation

Refresh snapshots as a reviewed change. Verify counts, field meanings and hashes;
compare changed acquisition text; review proposed exclusions against every
bundled Foretold Bounty; and retain unresolved or conflicting names. Preserve
all source records, source terms and inventory artwork. Runtime readers should
consume this offline overlay only for Ritual, leaving unknown names unchanged.
The bundled data requires no runtime network access.
