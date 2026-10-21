# PoE2 Data Logger

## OCR Settings

Set hotkeys in **Scan settings**. Capture regions have defaults. On **Scan regions**, choose an activity to see its labelled box. Use **Select region in game** to adjust it, or **Use own screenshot…** to replace its reference image.

Auto-commit saves an opened remnant when its recognized first recipe resolves to one matched family; socket and OCR-confidence warnings do not hold that match. Consecutive scans of the same family still require confirmation. Other activities retain their strictness and safety checks. Propagation always requires manual approval. If Overlay is turned on, items held for review open it automatically. Press **Esc** to close the overlay. Large live **Map #** and **Remnant #** indicators keep the active records visible.

Each scan needs a hotkey press. Scanning pauses when PoE2 is not the active window.

**CPU OCR threads** in Scan settings offers 1, 2, 4 or 6 threads. Restart the app to apply a changed value. Two is the default; higher counts can help large scans but use more CPU.

**Use GPU for Currency / Ritual** beside that selector is off by default and also requires a restart. It accelerates count/tier label recognition where DirectML is available, with CPU fallback.

**OCR Sensitivity** provides a separate **OCR Strictness** slider for each scan type. **0** accepts more tentative recognized matches, **50** keeps the previous behavior, and **100** requires manual confirmation before OCR results save. Higher values require stronger evidence. Each scan keeps its captured settings; quantity, level, family, marked-position and capture-context checks still apply.

## Map / Tablet Setup

Choose Biome and City Type, or toggle Ocean, Irradiated, Deli and Wisp, from the top row. These changes save immediately.

Tablets save to slots **1–4** in order. **Clear tablet config** clears them and restarts at slot 1.

Named tablet sets retain separate configurations and scan progress. Select the set to use for the map; only that selected saved set is attached when completing the map.

New maps clear waystone settings. Tablets, Atlas Master settings and map tags—including Irradiated and Ocean Map—carry over until changed.

## Logging and Review

On **Review**, correct anything held for review, then **Approve** to save it. **Reject** skips it. Waystones and tablets update their settings; other scans save to the log.

**Chains:** the dedicated **Propagation scan** key reads the selected recipe and its marked runes from left to right. The general key cannot activate it. Each accepted scan adds one remnant detonation, even with two runes. Every propagation scan requires manual review, regardless of confidence, strictness or auto-commit settings. Check the recipe and its rune dropdowns; **Approve** beside its row saves the part directly. Normal propagation review keeps its recipe rows, Approve and Complete controls together. Use **Enter propagation manually** to open the recipe-only rune dropdowns when needed; they show rune icons and names in socket order. **Complete chain**, on Review or Expedition, finishes the saved chain and advances this map's expedition number. Saved parts and selected completed chains can be corrected on Expedition. **Discard last chain step** and **Discard chain** remove saved parts while retaining remnant logs and actual detonation counts.

The three peaks above each propagated rune identify its mark. Include the gold arrow beside the selected recipe in the capture. Remnant review and propagation remain separate: a pending remnant can be reviewed later and still saves to the expedition it was captured for.

**Counts:** enter Normal, Magic, Rare and Unique kills per map. Approving a propagation scan adds one remnant detonation to its expedition, even with two runes. **+ New map** saves kill totals for the map you’re leaving.

**Kill-count OCR:** use its dedicated key. Capture is enabled by default; in **Scan regions**, select **Kill counts**, include all four skulls and numbers with some blank space to the left of the Normal number, then **Save regions**. White is Normal, purple/blue is Magic, yellow is Rare, and orange is Unique. Currency, General and other scan keys do not trigger kill-count OCR. Multiple map readings are cumulative snapshots: 10 then 20 means 20, not 30. History retains both; **Current Map Kill Reading** marks the latest snapshot, independently of expedition. Manual end-map entries stay unchanged.

Set **Map kill counts · final reading** in **Scan settings** to capture the final reading without scanning another activity. Start a map first; no remnant chain is required. Capture the final counters before advancing to the next map. Missing or uncertain counters are not saved as zero: rescan or use the existing manual fields. OCR does not fill or overwrite those manual end-map entries. Stage and final OCR readings appear in the **Kill Count Readings** worksheet and **_Kill_Counts.csv** companion; the existing manual totals remain in their original export fields.

**Currency/items:** select **Start of map** or **End of map** before scanning the inventory. These snapshots measure the change in item quantities.

**Currency** shows tracked item types in groups as icon, name and count cards, including zero totals. Found items move ahead of zero-count items while keeping their groups. Every approved end scan updates the positive end-minus-start gains across maps. If no start scan was taken, the map starts with an assumed empty inventory. Rescans replace that map's contribution. Use the search to find a type. Resetting IDs clears the totals. Manual inventory icon-reference tools are under **Data export → Developer Mode**.

Currency review lists confident items first, possible matches next, then unknown items. Confident rows are approved automatically. Possible matches have editable suggestion dropdowns: select a name or type a correction and confirm the count. Editing requires a fresh row **Approve**. Row approval and rejection preserve your scroll position. The bottom **Approve** saves approved rows and rejects all remaining pending rows.

Double-click an item name in Currency or Ritual review to correct it. Committing an accepted name correction saves its captured artwork as a local recognition example for later scans and adds its item-count column to exports. Rows left unnamed are rejected. Confirm other critical fields before approval. Local recognition examples can be exported in an OCR reference pack.

Currency and Ritual also match bundled unique-item artwork. Multi-slot items use one row with quantity 1. Shared or uncertain artwork offers name choices for review; item rolls and prices are not inferred from its icon. Accepted uniques are logged as Items using the existing snapshots and exports.

**Ritual:** deferred rewards are marked **Deferred** and contribute zero new finds. Available tribute and remaining rerolls are saved with the scan.

The optional **Ritual** tab records Rite affixes without requiring them for scans. Proliferating affixes carry from completed earlier maps of the same selected Rite. The first and final approved Tribute/reroll readings are saved per map, including readable counters on an empty reward page.

Finish pending reviews and chains before clicking **+ New map**.

**Undo** reverses the last saved action, including scans, chains, settings and New map. The latest 100 actions survive app restarts. Undoing New map reopens the previous map for edits.

## Atlas / Character Settings

The separate **Atlas / Character Settings** page shows the PoE2 atlas. Click a node to light it up; click it again to turn it off. Hover to see its effects immediately. Choice nodes open a numbered dropdown and show their selected option's number on the tree. Choose an activity to focus its tree, scroll to zoom, or drag the background to pan.

**Autofill** turns on every allocatable node and keeps your selected choices. Unselected choice effects remain unset. **Clear all** turns nodes off. Enter only the character's **Gear Item Rarity %**, then use **Save settings** to save the setup.

The page shows which Map ID will receive your saved settings. Once a map has recorded activity, atlas and gear rarity changes apply to the next map. Earlier maps keep their saved setup.

An allocated, active city-biome choice can fill the existing Biome selector: Faridun → Desert/Mountain, Ezomyte → Grass/Swamp, or Vaal → Forest/Water. This is an additional Atlas classification, not a physical terrain change. Manual Biome editing remains available; changing City, applicable Atlas settings or starting a map can apply the rule again.

## Export and Reset

Choose an export folder in **Data export**. Saved entries keep their original settings.

Excel exports contain eight sheets: **Export**, **Atlas Character Settings**, **Scan History**, **Analysis Context**, **Family Database**, **Recipe Database**, **Seed Database** and **Reference Aliases**. The main sheet includes map totals and activity rows tied to Map ID; each tracked item has a numeric count column. **Atlas Setup ID** links a map to its saved setup and gear item rarity. Click a setup ID to open its Atlas row. **Scan History** retains scan, correction and commit audit records. **Analysis Context** explains fields, units, joins, counting rules, saved Atlas effects, scoped totals and analysis limits. Reference sheets explain recipe/family IDs and rune combinations; their rows are not acquired rewards. CSV export saves three related data files. Folder exports receive unique filenames so earlier exports remain available.

The raw SQLite backup includes logs, reference databases and SQL-readable Context notes/views. External image files need a separate copy. Missing data and historical reference edits can limit analysis; inventory gains do not identify which individual remnant or chain step produced loot.

**Start fresh session / reset IDs** clears logs and restarts IDs. Settings and databases are kept. Export first to keep your recorded data.

## Help

Discord: https://discord.gg/bE758BqSQj
