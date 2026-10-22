`264_tribute.png` is the byte-for-byte original `Screenshot 2026-10-18 162433.png`
from the supplied `Ritual (2).zip`. It is 684×660 pixels; SHA-256:
`ca4b50720bbe1df992fe09ce0b93b32ee1c276abc52f672c641cbcbb4c264df8`.

The capture shows 264 Tribute, gray Omen art with and without deferred badges,
colored Omen art, and gray equipment. It establishes the actual gray appearance;
it does not independently establish why the game grayed each offer.

Expected shared shape sets come from bundled `inventory-icons.json` source-art
filenames `VoodooOmens1`, `VoodooOmens2`, and `VoodooOmens3` with their color
suffixes. No annotations or transformed artwork have been added to the image.
The user confirmed that the gray armor at slots 73, 74, 85 and 86 is one 2×2
item. Tests assert that complete footprint, Item category and quantity one while
keeping adjacent single-cell rewards independent. Individually cropped pixels
from those four armor cells remain negative controls for Omen-shape matching;
they do not represent four separate rewards. Earlier sweep reports document the
old split behavior with their original tested revision.
