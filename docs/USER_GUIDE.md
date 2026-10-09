# Using PoE2 Data Logger

## Map setup and scanning

Set Biome and City Type and toggle Ocean, Irradiated, Deli or Wisp from the top row. Choose your Atlas Master and perks where applicable. Scan a waystone or tablet tooltip, or enter its modifiers manually. Accepted tablet readings fill slots 1–4 in order. **Clear tablet config** clears those slots and restarts the sequence.

Choose hotkeys under **Scan settings** and adjust capture areas under **Scan regions**. Every scan starts with a key press while **Path of Exile 2** is the active window. Default regions can be moved or resized on the reference image, selected in game, or edited using your own screenshot. Saved regions persist between launches.

Remnant modes are **Visible seed only**, **Opened remnant only**, and **Both**. Visible-seed mode reads each detected bar independently. Opened mode reads the reward panel. Both waits for the matching opened reading and records the two stages under one Remnant ID.

Auto-commit saves readings that meet their selected OCR strictness and safety checks. Propagation always requires manual approval. Readings below the threshold, ambiguous matches and unknown items remain in **Review**. Correct a reading and choose **Approve** to save it or **Reject** to skip it. Waystone and tablet approvals update the configuration; other activities save their records to the log.

The optional **HUD overlay** shows held reviews over a borderless or windowed game, with large live **Map #** and **Remnant #** indicators. Its opacity is adjustable. **Show / hide HUD** has its own shortcut, initially **Ctrl+Shift+H**; that shortcut remains available outside the game.

**OCR CPU threads** under **Scan settings** offers 1, 2, 4 or 6 threads. The default is 2. Restart the app after changing it. More threads can help some readings but can slow others; the OCR models stay the same.

The **OCR Sensitivity** tab provides separate **OCR Strictness** sliders for seed bars, remnant recipes, propagation, waystones, tablets, currency inventories and Ritual rewards. **0** accepts more tentative recognized matches, **50** retains the previous behavior, and **100** requires manual confirmation before OCR results save. Higher values require stronger evidence. A scan keeps the values selected when it was captured. Quantity, explicit level, family, marked-position and capture-context checks remain enforced at every value.

## Records and export

For inventory tracking, select **Start of map** or **End of map** before scanning. Exported records include starting counts, ending counts and net changes. Ritual records include readable tribute and reroll values. Deferred rewards keep their appearance record and contribute zero new finds.

The **Currency** page shows grouped session totals with each item's icon and name, with found items first. Totals use positive changes between approved starting and ending inventories for each map. Approving an end scan updates the counter; rescanning or correcting that map replaces its contribution. A missing Start scan means an empty starting inventory. Totals survive app restarts and clear with **Start fresh session / reset IDs**. Ritual offers do not add to inventory totals.

Currency review lists confidently recognized rows first, possible matches next and unknown items last. Confident rows are approved automatically; inspect and individually approve any uncertain rows you want to include. Possible matches have editable name dropdowns: choose a suggestion or type a corrected name, confirm the count, then approve that row. Approve and Reject retain your scroll position. The final **Approve** commits accepted rows and rejects uncertain rows that you have not approved. Double-click other Currency or Ritual review names to label captured items. Accepted corrections save that icon as a local recognition example and export the item in its own count column. Rows left unnamed are rejected. Local inventory icon tools are under **Data export → Developer Mode**.

Use the dedicated **Propagation scan** key to read the selected recipe and append its marked runes from left to right. The general scan key does not activate propagation. Every propagation scan stays in review at every strictness setting; confirm the recipe and marked runes, then choose **Approve** beside that recipe to save its part directly. Each accepted scan adds **one remnant detonation**, including scans with two runes; saving its part does not count it again. Its source recipe and family are retained for analysis; ambiguous family origins remain explicitly unknown. Keep scanning and saving parts under the same Expedition ID. The Expedition page shows saved parts in order and provides dropdowns and **Save corrections** while the chain is open. Unsaved drafts are discarded when another scan replaces them, while accepted counts and saved parts remain. When the full chain is ready, use **Complete chain** on Review or Expedition to lock it, clear the current chain and advance the Expedition ID. Completed chains remain viewable and read-only through the expedition selector.

Capture the selected recipe's gold arrow and the three small peaks above each propagated rune. The three peaks identify the marked positions; the recipe database supplies their rune names and order. Ordinary propagation review shows recipe rows with **Approve** and **Complete chain** controls. If a reading needs manual rune identification, choose **Enter propagation manually** to open the recipe-only rune dropdowns, with artwork in socket order. Choose its first marked rune and optional second marked rune, then **Approve** that recipe. Remnant and propagation reads share their reference database and keep independent pending reads, so an unresolved remnant does not block propagation.

Enter Normal, Magic, Rare and Unique monster kills in the compact **Map kills** box at the top of the page. **+ New map** saves those counts to the current Map ID and clears the fields. The propagation detonation total belongs to its Expedition ID.

Finish pending reviews and chains before using **+ New map**. New maps clear waystone settings and carry forward tablets, Atlas Master and general map options. Clear tablets when your tablet setup changes. Existing entries keep the map information and settings saved with them.

The separate **Atlas / Character Settings** tab shows the PoE2 atlas. Click nodes to turn them on or off and hover for their effects. Multiple-choice nodes show the selected option's number; their dropdown uses matching numbers. **Autofill** maximizes allocations while retaining existing choices. Unselected choice effects remain unset. Save gear item rarity alongside the atlas setup.

Choose an export folder under **Data export**. Each export gets a new timestamped filename, preserving previous exports. Excel contains **Export**, **Atlas Character Settings** and **Scan History** sheets. **Export** keeps recipe rows and adds a named column for each tracked item. Acquired item counts, scanned map/tablet modifiers and kills appear once per Map ID. Counts use the latest approved End inventory minus Start inventory, with a missing Start treated as empty. An unscanned End stays blank; an approved empty End counts as zero. Ritual offers and individual scans remain in **Scan History**. CSV writes these same three sheets as related files.

**Save raw SQL database to folder** creates a complete SQLite backup, including logs, references, settings and stored image data. The `chain_rune_provenance` view exposes each saved rune with its frozen source family and recipe for analysis. Files stored outside SQLite are separate from this backup.

Save Atlas / Character Settings edits before exporting. Map ID and Atlas Setup ID link the log to its frozen atlas and gear settings; click a setup ID in the main sheet to open its setup row. The Atlas sheet has one row per saved setup, including gear rarity and the linked Map IDs. Node columns are created only for nodes unchecked in at least one exported setup: **No** means unchecked and **Yes** means checked; omitted node columns mean checked in every setup where that node exists. Choice columns contain the option number in that saved dataset's dropdown order, or **0** when no option is selected. An unchecked choice node can retain a remembered option number, just like the editor. Blank cells mean the node was not available in that setup's dataset. Atlas Data Version and Atlas Catalog ID identify the saved dataset.

**Start fresh session / reset IDs** clears recorded activities after confirmation and restarts at **M0001**, while keeping settings and reference databases. Export first if you want to keep the session's records.

## References and licenses

**Database Import/Export** shares OCR reference packs and supports additional item-icon examples. Duplicate examples are ignored; training an icon does not record loot.

**Reset OCR references to defaults** restores bundled reference data and removes learned or user-added reference entries after confirmation. It retains logs, IDs, settings and scan history. Reference packs exclude those personal records; use the raw SQLite backup to save the full database.

The screen readers incorporate Exiled Exchange 2 / currency-overlay material and RuneHelper. Their source, models and license notices are kept under `third_party/`; font notices are under `fonts/`. **Help → Licenses** displays the applicable notices, including `CRAFTYXII_ASSETS_LICENSE.txt` and `AFFIX_CATALOG_NOTICE.txt`.

PoE2 Data Logger is an independent third-party tool and is not affiliated with or endorsed by Grinding Gear Games. The CraftyXII branding notice covers owned branding and does not claim ownership of game-derived databases or in-game facts. Existing software and third-party license terms remain included.

Clicking the CraftyXII logo opens the optional [Discord link](https://discord.gg/bE758BqSQj) in your browser. The app does not connect to Discord in the background.
