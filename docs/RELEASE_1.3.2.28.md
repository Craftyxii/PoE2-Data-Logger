# PoE2 Data Logger 1.3.2.28 Beta

This beta cuts Currency and Ritual review down to items that are genuinely
uncertain, adds Rite scanning and **Reset map…**, fixes kill-count reading and
removes the GPU label helper, on top of [1.3.2.27 Beta](RELEASE_1.3.2.27.md).
Download the
[Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.28-beta/PoE2-Data-Logger-Setup-v1.3.2.28-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.28-beta).

## Less review, same accuracy

- Gray Ritual rewards whose artwork belongs to one item are named: Omens with
  their own source art, Sacred Bloom, Idols and currency. Gray art is never
  matched by color, so a gray Sacred Bloom is no longer suggested as Armourer's
  Scrap. Gray Omens that share source art (for example the Sinistral/Dextral
  families) and near ties stay in review with every look-alike suggested.
  Abyss Omens never appear in Ritual offers and are not Ritual rivals.
- Gold-framed deferred and highlighted Ritual rewards are retried without the
  frame edge under the same matcher gates. Deferred detection is unchanged and
  deferred appearances still add zero new finds.
- Unique items whose artwork has no rival anywhere in the catalog are named;
  shared art and close rivals still require a choice.
- A stack count read once at high confidence is accepted when its digits match
  the separately counted label glyphs. Conflicting and clipped counts, and
  every Propagation scan, stay in review.

On the 16 captured Ritual pages, review rows fell from 102 to 25, with every
newly named item checked visually. Captured Currency holds fell from 51 to 34
with no wrong accepted count out of 285.

## New

- **Rite scanning.** Hover a map on the Atlas and press the general scan key to
  read its Rite of the Nameless modifiers into the current map's Rite stage.
  Unknown wording is added as a custom modifier of its Foretold kind, and
  **+ New map** advances the stage. Rites apply only while Ritual is the
  selected Activity Type.
- **Reset map…** on Map / Tablets clears every scan and log saved for the
  current map after confirmation, keeping its Map #, settings, earlier maps and
  learned OCR references. One **Undo** restores everything it cleared.
- The OCR text models load while each worker prepares artwork at startup, so the
  first scan no longer pays the model setup.

## Fixes and cleanup

- Kill counters are read at every game resolution and UI scale.
- The optional GPU label setting and its separately built helper are removed.
  Labels use the same CPU engine and caches as before; artwork pre-caching is
  unchanged, and the installer is smaller.
- Dead review flags, test-only wrappers and the unreachable map-only undo
  backend are removed. The full suite runs its isolated batches two at a time,
  cutting the Windows release suite from about 58 to 39 minutes.

Settings saved on a later map, including Atlas, waystone, master, tablet,
activity, Rite and OCR settings, leave finished maps and their export rows
unchanged until Undo reopens them. A new regression test checks this end to end.
Running 1.3.2.27 Beta and earlier clients are protected during installation and
removal.
