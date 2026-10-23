# PoE2 Data Logger 1.3.2.29 Beta

This beta logs unique Tablets alongside normal ones, fixes general-key hovers
that opened a Ritual review, and names more Ritual and Currency items without
review, on top of [1.3.2.28 Beta](RELEASE_1.3.2.28.md).
Download the
[Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.29-beta/PoE2-Data-Logger-Setup-v1.3.2.29-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.29-beta).

## Unique Tablets

- Unique Tablets scan with the Tablet key, the general key or copied text. The
  orange unique title marks the tooltip; modifiers are read between the
  uses-remaining line and the orange flavour text, and a short wrapped ending
  such as "times" rejoins its line.
- Number-free modifiers such as "Map also counts as a Desert Map" are logged as
  a flag of 1. Unknown wording is added to Affix DB, as for Rites and
  waystones, so a set can mix normal and unique Tablets and every slot exports
  its value and unit.
- Wording that implies a value is never read as a flag, so a roll OCR dropped
  stays in review. A unique title without its flavour boundary is held for
  review.
- A learned flag picked by hand in the Tablet form still saves as a flag, and
  learned names get their keyword capitals back.
- On the four captured uniques (Freedom of Faith, Visions of Paradise and both
  Mastered Domains) every modifier is read, learned and auto-saved.

## Fixes

- **General-key hovers.** A Rite map whose modifier mentions Favours and
  Tribute, or a Ritual Tablet, no longer opens a Ritual reward review; the Rite
  stage or Tablet is saved. The Ritual window is recognized by its own Tribute
  counter, title and controls, and now finds all 34 recorded windows (31
  before).
- **Rite stage in exports.** Saving the new map's waystone kept the map's Rite
  name, stage and modifiers in its export row; they had disappeared until
  **+ New map**.
- **Tier XV waystones** are no longer suggested as Tier 16; a bundled reference
  picture was mislabelled.
- Grey footer and orange flavour text skip the slow percentage retry, which
  added about 4.5 seconds to Tablets whose footer OCR garbled.

## Less review

- Framed and highlighted deferred Ritual Omens are named when their art leads
  every rival clearly. On 33 real pages this names 19 Omens, each checked
  against catalog art and glow colour; deferred Omens still add no new finds.
- Partly covered or highlighted multi-cell Ritual rewards stay one reward,
  removing 12 phantom rows on the real pages. An unnamed reward whose
  suggestions are all Omens defaults to the Omen type for confirmation.
- Inventory stacks whose art is far ahead of every rival are named even when
  their count or II/III badge lowers the score. On the training captures no
  accepted count or name was wrong.

## Faster

Profiles open without re-reading the 10 MB icon catalog, and the Session
Currency cards are built when the page first shows, so the app starts about
half a second sooner.

Settings saved on a later map still leave finished maps and their export rows
unchanged until Undo reopens them. Running 1.3.2.28 Beta and earlier clients are
protected during installation and removal.
