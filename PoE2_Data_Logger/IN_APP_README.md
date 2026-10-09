# PoE2 Data Logger

## OCR Settings

Set hotkeys in **Scan settings**. Capture regions have defaults. On **Scan regions**, choose an activity to see its labelled box. Use **Select region in game** to adjust it, or **Use own screenshot…** to replace its reference image.

Auto-commit saves eligible non-propagation scans that meet their selected strictness and safety checks. Propagation always requires manual approval. If Overlay is turned on, items held for review open it automatically. Press **Esc** to close the overlay. Large live **Map #** and **Remnant #** indicators keep the active records visible.

Each scan needs a hotkey press. Scanning pauses when PoE2 is not the active window.

**CPU OCR threads** in Scan settings offers 1, 2, 4 or 6 threads. Restart the app to apply a changed value. Two is the default; higher counts can help large scans but use more CPU.

**OCR Sensitivity** provides a separate **OCR Strictness** slider for each scan type. **0** accepts more tentative recognized matches, **50** keeps the previous behavior, and **100** requires manual confirmation before OCR results save. Higher values require stronger evidence. Each scan keeps its captured settings; quantity, level, family, marked-position and capture-context checks still apply.

## Map / Tablet Setup

Choose Biome and City Type, or toggle Ocean, Irradiated, Deli and Wisp, from the top row. These changes save immediately.

Tablets save to slots **1–4** in order. **Clear tablet config** clears them and restarts at slot 1.

New maps clear waystone settings. Tablets, Atlas Master settings and map tags—including Irradiated and Ocean Map—carry over until changed.

## Logging and Review

On **Review**, correct anything held for review, then **Approve** to save it. **Reject** skips it. Waystones and tablets update their settings; other scans save to the log.

**Chains:** the dedicated **Propagation scan** key reads the selected recipe and its marked runes from left to right. The general key cannot activate it. Each accepted scan adds one remnant detonation, even with two runes. Every propagation scan requires manual review, regardless of confidence, strictness or auto-commit settings. Check the recipe and its rune dropdowns; **Approve** beside its row saves the part directly. Normal propagation review keeps its recipe rows, Approve and Complete controls together. Use **Enter propagation manually** to open the recipe-only rune dropdowns when needed; they show rune icons and names in socket order. **Complete chain**, on Review or Expedition, finishes the saved chain and advances this map's expedition number. Saved parts remain editable on Expedition until completion.

The three peaks above each propagated rune identify its mark. Include the gold arrow beside the selected recipe in the capture. Remnant review and propagation remain separate: a pending remnant can be reviewed later and still saves to the expedition it was captured for.

**Counts:** enter Normal, Magic, Rare and Unique kills per map. Approving a propagation scan adds one remnant detonation to its expedition, even with two runes. **+ New map** saves kill totals for the map you’re leaving.

**Currency/items:** select **Start of map** or **End of map** before scanning the inventory. These snapshots measure the change in item quantities.

**Currency** shows tracked item types in groups as icon, name and count cards, including zero totals. Found items move ahead of zero-count items while keeping their groups. Every approved end scan updates the positive end-minus-start gains across maps. If no start scan was taken, the map starts with an assumed empty inventory. Rescans replace that map's contribution. Use the search to find a type. Resetting IDs clears the totals. Manual inventory icon-reference tools are under **Data export → Developer Mode**.

Currency review lists confident items first, possible matches next, then unknown items. Confident rows are approved automatically. Double-click a name or count to correct it; editing requires a fresh row **Approve**. The bottom **Approve** saves approved rows and rejects all remaining pending rows. Possible matches stay suggestions until you enter and confirm their name and count.

Double-click an item name in Currency or Ritual review to correct it. Committing an accepted name correction saves its captured artwork as a local recognition example for later scans and adds its item-count column to exports. Rows left unnamed are rejected. Confirm other critical fields before approval. Local recognition examples can be exported in an OCR reference pack.

**Ritual:** deferred rewards are marked **Deferred** and contribute zero new finds. Available tribute and remaining rerolls are saved with the scan.

Finish pending reviews and chains before clicking **+ New map**.

## Atlas / Character Settings

The separate **Atlas / Character Settings** page shows the PoE2 atlas. Click a node to light it up; click it again to turn it off. Hover to see its effects immediately. Choice nodes open a numbered dropdown and show their selected option's number on the tree. Choose an activity to focus its tree, scroll to zoom, or drag the background to pan.

**Autofill** turns on every allocatable node and keeps your selected choices. Unselected choice effects remain unset. **Clear all** turns nodes off. Enter only the character's **Gear Item Rarity %**, then use **Save settings** to save the setup.

The page shows which Map ID will receive your saved settings. Once a map has recorded activity, atlas and gear rarity changes apply to the next map. Earlier maps keep their saved setup.

## Export and Reset

Choose an export folder in **Data export**. Saved entries keep their original settings.

Excel exports contain **Export**, **Atlas Character Settings** and **Scan History** sheets. The main sheet includes map totals and activity rows tied to Map ID; each tracked item has a numeric count column. The shared **Atlas Setup ID** links each Map ID to its saved setup and gear item rarity. The compact Atlas sheet uses separate columns for unchecked nodes and numbered choices. Click a setup ID in the main sheet to open its Atlas row. **Scan History** retains the audit records for scans, corrections and commits. CSV export saves a companion Atlas CSV alongside the main CSV. Folder exports receive unique filenames so earlier exports remain available.

**Start fresh session / reset IDs** clears logs and restarts IDs. Settings and databases are kept. Export first to keep your recorded data.

## Help

Discord: https://discord.gg/bE758BqSQj
