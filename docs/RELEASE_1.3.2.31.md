# PoE2 Data Logger 1.3.2.31 Beta

This beta fixes Ritual pages that 1.3.2.30 dropped without a word, confirms
every Ritual scan, never logs deferred rewards, names unique flasks on Ritual
pages, and reads Rite map names and biomes, on top of
[1.3.2.30 Beta](RELEASE_1.3.2.30.md).
Download the
[Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.31-beta/PoE2-Data-Logger-Setup-v1.3.2.31-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.31-beta).

## Ritual pages dropped in 1.3.2.30

- **What went wrong.** After a map was finished but before **+ New map** was
  pressed, a Ritual scan was compared with the finished map's last page. When
  the rolls left matched, the scan was dropped: the capture showed, the message
  bar stayed empty and nothing was saved.
- **Fixed.** A scan belongs only to the map shown when you take it. It is
  compared with that map's saved pages and saved to that map, never to a
  previous map or one that does not exist yet. A finished map takes no more
  pages: the review says to press **+ New map** and scan the page again.
  **+ New map** starts the next map's count afresh. The roll count is still the
  tracker; rewards are not compared, because they can repeat.
- **Pages dropped by 1.3.2.30 cannot be recovered.** They were never saved, so
  there is nothing to restore.

## Every Ritual scan confirms itself

- A repeat of a saved page now says which page it matched, for example
  "✓ Already saved: M0172 page 1 had 4 rolls left (Scan Commit #12).", under the
  capture and in the status bar. Before, the message bar was empty, which looked
  like a failure.
- A page whose rewards are all deferred and whose Tribute and rolls cannot be
  read says it was read and that nothing new was logged.
- No window or click was added.

## Deferred rewards are never logged

- A deferred reward is one you have already seen on an earlier page, so it is
  not a new find. It is never shown for review and never saved or counted. A
  reward counts as not deferred unless the page shows it deferred.
- A page whose rewards are all deferred saves its Tribute and rolls on its own
  instead of stopping at "Auto-commit held".
- A reward whose deferred state cannot be read could be new, so it is still
  reviewed.

## Recognition

- **Unique flasks on Ritual pages.** The game draws a flask there without its
  liquid, while the catalog art shows it full, so Blood of the Warrior and
  Lavianga's Spirits were left unnamed. Flasks are now also compared without
  their liquid; the whole flask's outline still has to match. On the user's 49
  Ritual pages only those three rewards changed, each now named.
- Every catalog item was drawn on synthetic Ritual pages (1080p, 1440p, JPEG and
  gray) and inventories and read back: none was named wrongly.
- Every unique name was checked against poe2db: all weapon, armour, jewellery,
  flask and jewel names match. Unique Tablets and Relics are not in the item
  catalog yet.
- Items drawn gray because their price is above your Tribute are held for
  review when look-alikes cannot be told apart. They are not guessed.

## Rite maps

- A scanned Rite map's name is read from its tooltip title, not from other text
  on screen such as an FPS counter, so scanning the same map again replaces its
  stage instead of queueing it twice.
- **+ New map** sets the played stage's Biome, or for a city map its City Type
  plus the Atlas keystone's additional biome. The BIOME and CITY TYPE pickers
  show it straight away, the status line names the stage, and a wrong value is
  corrected in the picker. Two maps with the same biome stay separate stages.

Running 1.3.2.30 Beta and earlier clients are protected during installation and
removal.
