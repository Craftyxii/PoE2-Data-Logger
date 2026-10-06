POE2-VibeTools / poe2-currency-overlay, commit
20ea30ad1205f489a382ac2a879f2a8dd3ae6d9b
https://github.com/poe2-vibetools/poe2-currency-overlay
License: GNU GPL version 3 (see LICENSE)

currency-reader.js, icon-matcher.js, digit-reader.js, currency-icons.json,
and digit-templates.json are copied from renderer/stash at this revision.
currency_ocr.py executes their original JavaScript in embedded QuickJS.
The catalog detects currency artwork without requiring user training images.
Items with shared artwork need a separately readable tier; uncertain results
are presented for manual correction. The upstream price/network features and
Electron UI are not included.

inventory-icons.json contains 275 inventory-sized currency/omen artwork
templates covering 297 names, plus 261 exclusion templates for maps,
tablets and socketable runes. Item names and GGG CDN artwork URLs come from
Exiled Exchange 2's renderer/public/data/en/items.ndjson, retrieved 2026-10-04:
https://github.com/Kvan7/Exiled-Exchange-2
The source file's SHA-256 and individual artwork URLs are in the JSON bank.
See EXILED-EXCHANGE-2-LICENSE.txt for its MIT license. Artwork copyright
remains with Grinding Gear Games.

The catalog contains in-game reference artwork for Origin Spark and
Simulacrum, plus an exclusion reference for the Rite of Passage Golden Charm.
inventory-reference-variants.json provides additional in-game variants for
existing catalog entries.

The inventory adapter uses icon-matcher.js's foreground-weighted SSD,
top-left stack-count mask and clamped +/-4px alignment search. OpenCV's
native weighted squared-difference kernel replaces only the inner pixel
loops.
The logger aligns framed captures to their repeated 12x5 inventory grid and
normalizes nearly black background pixels to the upstream navy reference.
The same normalization is used in the native and original-JS parity checks.
Empty/dim slot motifs are excluded. When the inventory catalog is present,
an uncertain match stays in review unless the independent price-dialog
reader clearly agrees with its best inventory family. Stack counts use the upstream desaturated-value
policy and the bundled RapidOCR recognizer on cropped labels. Multiple
count crops must agree; conflicting counts require manual confirmation.
Tier text is read separately from the bottom-right icon area.
Immutable template features are shared by repeated scans; accepted count
reads do not run the slower JavaScript digit reader again.
Unmarked Chaos/Exalted/Regal/Augmentation/Transmutation art resolves to the
base tier; II and III resolve to Greater and Perfect. Uncertain icons and
counts still require review. These assets are bundled for offline use.

The combined application source is distributed under GPL-3.0 because it
includes GPL-3.0 code. Copyright of upstream components remains with their
respective authors. Game artwork remains with its respective rights holders.
