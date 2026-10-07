# PoE2 Data Logger

## OCR Settings

Set hotkeys in **Scan settings**. Capture regions have defaults. On **Scan regions**, choose an activity to see its labelled box. Use **Select region in game** to adjust it, or **Use own screenshot…** to replace its reference image.

Auto-commit saves scans that don’t need review. If Overlay is turned on, items held for review open it automatically. Press **Esc** to close the overlay.

Each scan needs a hotkey press. Scanning pauses when PoE2 is not the active window.

## Map / Tablet Setup

Choose Biome and City Type, or toggle Ocean, Irradiated, Deli and Wisp, from the top row. These changes save immediately.

Tablets save to slots **1–4** in order. **Clear tablet config** clears them and restarts at slot 1.

New maps clear waystone settings. Tablets, Atlas Master settings and map tags—including Irradiated and Ocean Map—carry over until changed.

## Logging and Review

On **Review**, correct anything held for review, then **Approve** to save it. **Reject** skips it. Waystones and tablets update their settings; other scans save to the log.

**Chains:** the dedicated **Propagation scan** key reads the selected recipe and adds its marked runes from left to right. The general key cannot activate it. Each accepted scan adds one remnant detonation, even with two runes; uncertain or rejected scans add none. Review shows each chain part in scan order. **Commit chain** saves the chain and advances the expedition number for this map without counting its scans again. You can also enter runes manually.

**Counts:** enter Normal, Magic, Rare and Unique kills per map. Propagation counts remnants detonated per expedition automatically. **+ New map** saves kill totals for the map you’re leaving.

**Currency/items:** select **Start of map** or **End of map** before scanning the inventory. These snapshots measure the change in item quantities.

**Ritual:** deferred rewards are marked **Deferred** and contribute zero new finds. Available tribute and remaining rerolls are saved with the scan.

Finish pending reviews and chains before clicking **+ New map**.

## Atlas / Character Settings

The separate **Atlas / Character Settings** page shows the PoE2 atlas. Click a node to light it up; click it again to turn it off. Hover to see its effects immediately. Choice nodes open a numbered dropdown and show their selected option's number on the tree. Choose an activity to focus its tree, scroll to zoom, or drag the background to pan.

**Autofill** turns on every allocatable node and keeps your selected choices. Unselected choice effects remain unset. **Clear all** turns nodes off. Enter only the character's **Gear Item Rarity %**, then use **Save settings** to save the setup.

The page shows which Map ID will receive your saved settings. Once a map has recorded activity, atlas and gear rarity changes apply to the next map. Earlier maps keep their saved setup.

## Export and Reset

Choose an export folder in **Data export**. Saved entries keep their original settings.

Excel exports contain **Export** and **Atlas Character Settings** sheets. The shared **Atlas Setup ID** links each Map ID to its saved allocations, choice effects and gear item rarity. Numeric atlas stats and their applied values have individual columns. Click that setup ID in the main sheet to open its atlas rows. CSV export saves a companion atlas CSV alongside the main CSV.

**Start fresh session / reset IDs** clears logs and restarts IDs. Settings and databases are kept. Export first to keep your recorded data.

## Help

Discord: https://discord.gg/bE758BqSQj
