`264_tribute.png` is the byte-for-byte original `Screenshot 2026-10-18 162433.png`
from the supplied `Ritual (2).zip`. It is 684×660 pixels; SHA-256:
`ca4b50720bbe1df992fe09ce0b93b32ee1c276abc52f672c641cbcbb4c264df8`.

The capture shows 264 Tribute, gray Omen art with and without deferred badges,
colored Omen art, and gray equipment. It establishes the actual gray appearance;
it does not independently establish why the game grayed each offer.

Expected shared shape sets come from bundled `inventory-icons.json` source-art
filenames `VoodooOmens1`, `VoodooOmens2`, and `VoodooOmens3` with their color
suffixes. No annotations or transformed artwork have been added to the image.
The current grid detector splits the visible gray armor into several cells;
these tests preserve that existing geometry and use those crops as negatives.
