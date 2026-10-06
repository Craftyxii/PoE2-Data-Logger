# Using PoE2 Data Logger

## Map setup and scanning

Set Biome and City Type and toggle Ocean or Irradiated from the top row. Choose your Atlas Master and perks where applicable. Scan a waystone or tablet tooltip, or enter its modifiers manually. Accepted tablet readings fill slots 1–4 in order. **Clear tablet config** clears those slots and restarts the sequence.

Choose hotkeys under **Scan settings** and adjust capture areas under **Scan regions**. Every scan starts with a key press while **Path of Exile 2** is the active window. Default regions can be moved or resized on the reference image, selected in game, or edited using your own screenshot. Saved regions persist between launches.

Remnant modes are **Visible seed only**, **Opened remnant only**, and **Both**. Visible-seed mode reads each detected bar independently. Opened mode reads the reward panel. Both waits for the matching opened reading and records the two stages under one Remnant ID.

Auto-commit saves clear readings. Uncertain text, ambiguous matches and unknown items remain in **Review**. Correct a reading and choose **Approve** to save it or **Reject** to skip it. Waystone and tablet approvals update the configuration; other activities save their records to the log.

The optional **HUD overlay** shows held reviews over a borderless or windowed game. Its opacity is adjustable. **Show / hide HUD** has its own shortcut, initially **Ctrl+Shift+H**; that shortcut remains available outside the game. Exclusive fullscreen behavior has not been verified.

## Records and export

For inventory tracking, select **Start of map** or **End of map** before scanning. Exported records include starting counts, ending counts and net changes. Ritual records include readable tribute and reroll values. Deferred rewards keep their appearance record and contribute zero new finds.

Enter an Expedition chain's runes in order, then use **Commit chain**. Set **Expedition #** to **2** for the second City expedition. Kills are per map; **Remnants Detonated** is per expedition.

Finish pending reviews and chains before using **+ New map**. New maps clear waystone settings and carry forward tablets, Atlas Master and general map options. Clear tablets when your tablet setup changes. Existing entries keep the map information and settings saved with them.

Choose an export folder under **Data export**. Excel contains one **Export** sheet; CSV contains the same combined records and columns. Both can be imported into Google Sheets.

**Start fresh session / reset IDs** clears recorded activities after confirmation and restarts at **M0001**, while keeping settings and reference databases. Export first if you want to keep the session's records.

## References and licenses

**Database Import/Export** shares OCR reference packs and supports additional item-icon examples. Duplicate examples are ignored; training an icon does not record loot.

The screen readers incorporate Exiled Exchange 2 / currency-overlay material and RuneHelper. Their source, models and license notices are kept under `third_party/`; font notices are under `fonts/`. **Help → Licenses** displays the applicable notices, including `CRAFTYXII_ASSETS_LICENSE.txt` and `AFFIX_CATALOG_NOTICE.txt`.

PoE2 Data Logger is an independent third-party tool and is not affiliated with or endorsed by Grinding Gear Games. The CraftyXII branding notice covers owned branding and does not claim ownership of game-derived databases or in-game facts. Existing software and third-party license terms remain included.

Clicking the CraftyXII logo opens the optional [Discord link](https://discord.gg/bE758BqSQj) in your browser. The app does not connect to Discord in the background.
