# Third-party notices

This project is not affiliated with or endorsed by Grinding Gear Games in any way. Licenses are
enforced in CI by `npm run check:compliance` (`scripts/check-licenses.mjs` + `check-provenance.mjs`).

## Game data & art — Grinding Gear Games

All vendored lookup data (`src/data/*.json`) and art atlases (`src/assets/**/*.webp`) are GGG's, from
two first-party sources, claimed under no ownership:

- **GGG's official PoE2 tree export** (<https://github.com/grindinggear/poe2-skilltree-export> — listed
  as an official Data Export on GGG's developer docs, <https://www.pathofexile.com/developer/docs/data>,
  verified 2026-07-04; no OSS license — used under GGG's developer/community-tool terms): the
  passive/atlas/genesis tree graphs and the node/skill sprite atlases.
- **Our own datamine** of PoE2 game files from GGG's public **patch CDN** (via `pathofexile-dat`, below):
  the gem / unique / mod-tier / conqueror / mastery / emotion tables and every icon atlas
  (`ItemVisualIdentity` and related `.dds` art, re-encoded to webp).

Path of Exile and Path of Exile 2 are trademarks of Grinding Gear Games; all game data and art remain
© Grinding Gear Games. `src/data/provenance.json` records the captured patch and table counts.

## npm dependencies

**Bundled (the only third-party code shipped to the browser):**

- **pako** — `MIT AND Zlib`, © Vitaly Puzrin and Andrei Tuputcyn. zlib inflate for PoB2 import codes
  (`src/convert/decode.ts`).

**Build / test only (devDependencies — never distributed):**

- **pathofexile-dat** — MIT, © SnosMe (<https://github.com/SnosMe/poe-dat-viewer>). Exports GGG game
  tables/files during `scripts/extract-tables.mjs`.
  - ⚠ pulls in **ooz-wasm** — `GPL-3.0-or-later` (Oodle decompressor). Used **only** during that
    extraction; never imported by `src/`, never bundled. An arm's-length build tool — running it does
    not make our output GPL — and `check-licenses.mjs` fails the build if it ever reaches the runtime
    dependency tree.
- **@jsquash/webp** — Apache-2.0, © Jamie Sinclair / Google (jSquash; wasm codec built from **libwebp**,
  BSD-3-Clause © Google). Encodes the icon atlases in `scripts/build-*-icons.mjs`. (Chosen over `sharp`,
  whose prebuilt binaries are `Apache-2.0 AND LGPL-3.0-or-later` — rejected by the license guard.)
- **vite**, **vitest** (MIT), **typescript** (Apache-2.0), **jsdom** (MIT), **@types/node**,
  **@types/pako** (MIT) — bundler / test runner / types.
  - **lightningcss** (+ its per-platform `lightningcss-*` binaries) — `MPL-2.0`, transitive via
    `vite@8` (its built-in CSS transformer/minifier). Build-time only, used as an unmodified
    arm's-length tool; only its CSS *output* ships (CSS output is not covered by MPL). The license
    guard carries a matching documented exception.
- **vite-node** — MIT, © Anthony Fu / the Vitest team. Runs the TypeScript fixture-coverage report
  (`scripts/report-tier-coverage.mts`, spawned by `scripts/build-mod-tiers.mjs`); shares the same
  vite 8 install. Build-time only, never imported by `src/`. (Pinned as a devDependency so `npx`
  resolves it locally — vitest 2 used to provide it transitively; vitest 3+ absorbed it.)
- **eslint**, **@eslint/js**, **eslint-config-prettier**, **globals**, **typescript-eslint**, **prettier**
  (all MIT) — linter + formatter (`npm run lint` / `format`); build-time only, never imported by `src/`.
- **jscpd** (MIT, © Andrey Kucherenko) — copy-paste-detection gate (`npm run check:dedupe`); build-time
  only, never imported by `src/`.
- **tdd-guard-vitest** (MIT, © 2025 Nizar Selander) — `scripts/tdd-guard-reporter.mjs` is a local
  zero-dependency **adaptation** of this vitest reporter (the npm package itself is NOT installed:
  its transitive deps — `@anthropic-ai/claude-agent-sdk`, sharp's LGPL binary — fail the license
  guard). Test-time only, never imported by `src/`.

## Schema & protocol references (no code copied)

- **pathofexile-dat-schema** (poe-tool-dev/dat-schema) — MIT, © poe-tool-dev
  (<https://github.com/poe-tool-dev/dat-schema>). The npm package SnosMe ships that republishes
  poe-tool-dev's `schema.min.json`; its `SCHEMA_URL` (imported in `scripts/lib-dat.mjs`) determines which
  columns the datamine can export. Pulled in transitively via `pathofexile-dat`.
- **Patch-server probe** (`scripts/patch-version.mjs`) — clean-room from publicly documented protocol
  facts (host/port, the 2-byte query, the reply offsets). No code was copied from the no-license
  `poe-tool-dev/poe-patch-update`.

## Adapted / reimplemented code (each file carries an attribution header)

- **.psg decoder** (`scripts/decode-psg.mjs`) — ports the `.psg` (Passive Skill Graph) binary-format
  knowledge from **PyPoE** (MIT, © 2015 Omega_K2, `PyPoE/poe/file/psg.py`). The PoE2 layout changes
  were re-derived empirically and fit-validated against GGG's official character-tree export.
- **Interactive passive / atlas tree** (`src/tree/`) — from **poe2-tools/poe2-build-planner** (MIT,
  © 2026 theofbonin): `viewport.ts` and `spatial.ts` are ports of its `src/render/{viewport,spatialIndex}.ts`;
  `interact.ts` (multi-source BFS allocation + cascade deallocation) and the `render.ts` LOD tiers are
  adapted from its `src/tree/allocation.ts` and `src/render/lod.ts`.
- **Render conventions + plan-share codec** — from **cvenzin/poe2-skilltree** (MIT, © 2026 cvenzin):
  the Canvas2D render conventions (frame-key naming, edge arc math, fit/zoom constants) and the share
  codec in `src/atlas/share.ts` (sorted node ids → delta → unsigned LEB128 varints → base64url)
  reimplemented from its format description. No code copied (that project is React + pixi.js).
- **Loadout recovery** (`src/export/loadouts.ts`) — reimplements PoB's `buildMode:SyncLoadouts()`
  (**PathOfBuilding-PoE2**, MIT, © Path of Building Community, `src/Modules/Build.lua`): loadout
  matching from set titles (full-name equality, `{id}` brace links, single-set fan-out). Behaviour
  reimplemented in TypeScript; no Lua copied. `src/data/configLabels.json` also vendors PoB's
  config-option label strings (`src/Modules/ConfigOptions.lua`, via `scripts/build-config-labels.mjs`)
  under the same MIT license.

## Consumed services (API clients only — no repository code used)

- **poe2scout** — MIT, © the poe2scout contributors (<https://github.com/poe2scout/poe2scout>). The
  optional **Market prices** feature reads its public API (`api.poe2scout.com`) through the BFF
  (`server/worker.mjs`), with a descriptive contact User-Agent per its README. `src/economy/exchange.ts`
  reimplements its `normalizeSnapshotPair` / `computePairPrices` pair-orientation logic (React → vanilla
  TypeScript) under MIT. Prices are GGG trade data aggregated by poe2scout — snapshots, not appraisals.
- **pobb.in** / **pasteofexile** — `AGPL-3.0` *service*, © Dav1dde
  (<https://github.com/Dav1dde/pasteofexile>). We are a client of its public `/:id/raw` endpoint only
  (through the BFF); no repository code is used or copied, so AGPL obligations do not attach.

---

Adding a dependency, vendored asset, or adapted algorithm? Append its notice here and verify with
`npm run check:compliance`.
