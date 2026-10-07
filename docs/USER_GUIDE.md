# Using PoE2 Data Logger

## Map setup and scanning

Set Biome and City Type and toggle Ocean, Irradiated, Deli or Wisp from the top row. Choose your Atlas Master and perks where applicable. Scan a waystone or tablet tooltip, or enter its modifiers manually. Accepted tablet readings fill slots 1–4 in order. **Clear tablet config** clears those slots and restarts the sequence.

Choose hotkeys under **Scan settings** and adjust capture areas under **Scan regions**. Every scan starts with a key press while **Path of Exile 2** is the active window. Default regions can be moved or resized on the reference image, selected in game, or edited using your own screenshot. Saved regions persist between launches.

Remnant modes are **Visible seed only**, **Opened remnant only**, and **Both**. Visible-seed mode reads each detected bar independently. Opened mode reads the reward panel. Both waits for the matching opened reading and records the two stages under one Remnant ID.

Auto-commit saves clear readings. Uncertain text, ambiguous matches and unknown items remain in **Review**. Correct a reading and choose **Approve** to save it or **Reject** to skip it. Waystone and tablet approvals update the configuration; other activities save their records to the log.

The optional **HUD overlay** shows held reviews over a borderless or windowed game. Its opacity is adjustable. **Show / hide HUD** has its own shortcut, initially **Ctrl+Shift+H**; that shortcut remains available outside the game.

**OCR CPU threads** under **Scan settings** offers 1, 2, 4 or 6 threads. The default is 2. Restart the app after changing it. More threads can help some readings but can slow others; the OCR models stay the same.

## Records and export

For inventory tracking, select **Start of map** or **End of map** before scanning. Exported records include starting counts, ending counts and net changes. Ritual records include readable tribute and reroll values. Deferred rewards keep their appearance record and contribute zero new finds.

The **Kills / Currency** page shows session currency totals with each item's icon and name. Totals use positive changes between approved starting and ending inventories for each map. Approving an end scan updates the counter; rescanning or correcting that map replaces its contribution. A map without an approved starting inventory waits for its baseline. Totals survive app restarts and clear with **Start fresh session / reset IDs**. Ritual offers do not add to inventory totals.

Double-click a Currency or Ritual review name to label a captured item. Approving the scan saves that icon as a local recognition example and exports the item in its own count column. Rows left unnamed are rejected. Local inventory icon tools are under **Data export → Developer Mode**.

Use the dedicated **Propagation scan** key to read the selected recipe and append its marked runes from left to right. The general scan key does not activate propagation. Each accepted scan adds **one remnant detonation**, including scans with two runes. Unclear or rejected readings add none. The Review page shows the chain in scan order. **Commit chain** saves it and advances the Expedition ID for the same map; it does not count those scans again. You can also enter chain runes manually and select an expedition from the header.

Capture the selected recipe's gold arrow and the three small peaks above each propagated rune. Remnant and propagation reads share their reference database and keep independent pending reads, so an unresolved remnant does not block propagation.

Enter Normal, Magic, Rare and Unique monster kills under **Kills / Currency**. Each has its own export column and belongs to its Map ID. The propagation detonation total belongs to its Expedition ID.

Finish pending reviews and chains before using **+ New map**. New maps clear waystone settings and carry forward tablets, Atlas Master and general map options. Clear tablets when your tablet setup changes. Existing entries keep the map information and settings saved with them.

The separate **Atlas / Character Settings** tab shows the PoE2 atlas. Click nodes to turn them on or off and hover for their effects. Multiple-choice nodes show the selected option's number; their dropdown uses matching numbers. **Autofill** maximizes allocations while retaining existing choices. Unselected choice effects remain unset. Save gear item rarity alongside the atlas setup.

Choose an export folder under **Data export**. Excel contains **Export** and **Atlas Character Settings** sheets. Map ID and Atlas Setup ID link the log to its saved atlas and gear settings; click a setup ID in the main sheet to open its atlas rows. CSV saves the same combined records plus a companion atlas CSV. Both can be imported into Google Sheets. Numeric atlas stats have separate columns, including their applied values.

**Start fresh session / reset IDs** clears recorded activities after confirmation and restarts at **M0001**, while keeping settings and reference databases. Export first if you want to keep the session's records.

## References and licenses

**Database Import/Export** shares OCR reference packs and supports additional item-icon examples. Duplicate examples are ignored; training an icon does not record loot.

The screen readers incorporate Exiled Exchange 2 / currency-overlay material and RuneHelper. Their source, models and license notices are kept under `third_party/`; font notices are under `fonts/`. **Help → Licenses** displays the applicable notices, including `CRAFTYXII_ASSETS_LICENSE.txt` and `AFFIX_CATALOG_NOTICE.txt`.

PoE2 Data Logger is an independent third-party tool and is not affiliated with or endorsed by Grinding Gear Games. The CraftyXII branding notice covers owned branding and does not claim ownership of game-derived databases or in-game facts. Existing software and third-party license terms remain included.

Clicking the CraftyXII logo opens the optional [Discord link](https://discord.gg/bE758BqSQj) in your browser. The app does not connect to Discord in the background.
