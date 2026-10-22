POE2-VibeTools / poe2-currency-overlay, commit
20ea30ad1205f489a382ac2a879f2a8dd3ae6d9b
https://github.com/poe2-vibetools/poe2-currency-overlay
License: GNU GPL version 3 (see LICENSE)

currency-reader.js, icon-matcher.js, digit-reader.js, currency-icons.json,
and digit-templates.json are copied from renderer/stash at this revision.
currency_ocr.py executes their original JavaScript in embedded QuickJS.
For the pinned digit reader, digit_kernels.py accelerates binary IoU window
matching, candidate collection and reflected grayscale opening with native arithmetic. The
original JavaScript retains threshold sweeps, digit assembly and confidence
decisions, and remains the fallback for changed source or unsupported inputs.
The catalog detects currency artwork without requiring user training images.
Items with shared artwork need a separately readable tier; uncertain results
are presented for manual correction. The upstream price/network features and
Electron UI are not included.

inventory-icons.json contains inventory-sized currency/omen artwork
templates and exclusion templates for maps,
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

Sacred Bloom adds a 40x40 RGBA template under family sacred-bloom, with the
single member Sacred Bloom. Canonical identity, 1x1 footprint and stack size
one were verified in repoe-fork/poe2 data/base_items/AtlasCurrency.json at
revision 75885a099f2e9be1619aa3c793fe6ab0754a511f (SacredBloom). Its artwork is:
https://raw.githubusercontent.com/repoe-fork/poe2/75885a099f2e9be1619aa3c793fe6ab0754a511f/data/Art/2DItems/Currency/Harvest/SacredLifeforce.png
Source PNG SHA-256:
24666edfeab920db684c2d2844fda26b0f49f642248313b642e675cd253ec6d3
The canonical PNG is contained and centered in a transparent 40x40 canvas
using Lanczos resampling. One captured variant comes from the unlabelled
Sacred Bloom in tests/fixtures/ritual_rewards/10.png, slot 37, independently
identified against that canonical artwork. It uses the existing 40x40 RGB
Lanczos resize and near-black-to-navy background normalization. Slot 49 of
the same page is held out from the bank for regression coverage; it is a
same-page control, not an independent accuracy sample. These additions
supply the missing identity without changing matcher thresholds or mapping
Mirror of Kalandra to Sacred Bloom.

The inventory adapter uses icon-matcher.js's foreground-weighted SSD,
top-left stack-count mask and clamped +/-4px alignment search. OpenCV's
native weighted squared-difference kernel replaces only the inner pixel
loops.
The logger aligns framed captures to their repeated 12x5 inventory grid and
normalizes nearly black background pixels to the upstream navy reference.
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
