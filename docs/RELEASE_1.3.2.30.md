# PoE2 Data Logger 1.3.2.30 Beta

This beta fills Map Kills from kill-count readings and reads four-digit counts,
keeps deferred Ritual rewards out of review, never logs the same Ritual page
twice, stops same-shape look-alikes from being named by shape alone, and
suggests the right count for stacks on pale artwork, on top of
[1.3.2.29 Beta](RELEASE_1.3.2.29.md).
Download the
[Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.30-beta/PoE2-Data-Logger-Setup-v1.3.2.30-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.30-beta).

## Kill counts

- **Readings fill Map Kills.** A complete kill-count reading is now also the
  map's kill totals: the four Map Kills fields show it straight away, and the
  Maps and Export kill columns use it. A newer reading replaces an older one or
  a draft; a manual save afterwards replaces the reading. One **Undo** removes
  the reading and restores the previous totals. Every reading is still kept on
  the Kill Count Readings sheet. Before, readings went only to that sheet, so a
  working scan looked like a failed one.
- **Four-digit counts.** A Normal count such as 1498 is wider than one skull's
  spacing; its leading digit was cut off and the whole row refused. The first
  number now extends over its neighbouring digits up to the gap before the
  character name. A number cut off by the capture edge is still refused. All
  four of the user's counter captures read exactly.

## Ritual

- **Deferred rewards never wait for review.** A deferred offer adds no new find;
  it was offered on an earlier page. One read with certainty is saved as
  deferred, and one that is not is left out of the page, with a note, instead of
  holding the page for approval. A reward whose deferred state itself is unknown
  could be new and is still reviewed. On a gray capture with two deferred Omens
  that cannot be named, only the one uncertain non-deferred reward is reviewed.
- **The same page is never logged twice.** A reroll leaves one roll fewer, so a
  scan whose rerolls-remaining count matches the previous saved page on the
  same map is that page again. It is dropped on its own: no review, no message,
  nothing to click, nothing saved. The first page of each map, a page whose
  count could not be read, and a page scanned again after **Undo** are kept.
- The two new Ritual_8 pages read every reward correctly and save themselves:
  Seeing Stars, Amor Mandragora, Kaltenhalt, Nocturne, Window to Paradise,
  Saffell's Frame, Gifts from Above, Silverthorne, Glowswarm, Hoghunt, both
  Splinters of Lorrata and six Omens, each checked against the catalog art.

## Recognition

- **Look-alikes need their colour too.** Essences, Liquid emotions and some Runes
  and Soul Cores draw one shape in different colours, and a sub-pixel shift
  could let the wrong colour win on shape. When such a look-alike scores close
  behind, the strongly coloured pixels must also match the named item; otherwise
  the reward is held with each look-alike as a choice, keeping a verified count.
- Every catalog currency, Omen and unique was drawn on synthetic Ritual pages
  (1080p, 1440p, JPEG and gray) and inventories and read back:
  Potent Liquid Ferocity and Contempt were named as each other on Ritual pages,
  and no other item was named wrongly. With this release no item is.
  `tools/sweep_item_names.py` repeats the sweep and fails on any wrong name; run
  it after each league's catalog update.
- On real captures all 1,223 Ritual rewards read as before, and one of 511
  inventory stacks (a Potent Liquid Melancholy) now waits for its name.
- **Pale stacks suggest 10, not 1.** Crystal art seen through the 0 of a held
  10-stack made the count crops read 1; the aligned digits now supply the
  suggestion. Such stacks stay held for confirmation.

Running 1.3.2.29 Beta and earlier clients are protected during installation and
removal.

All required Windows gates passed on release source
`dcd7486af1dfea7aacd82ab65d30c29cd2028c75` in the
[Windows verification run](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/40116586140):
platform, capture-region, review-navigation and export fixtures; native Windows
input; the 100-map workflow; the complete regression suite; the frozen build; and
installation, update and saved-file retention. The
[publication run](https://github.com/Craftyxii/PoE2-Data-Logger/actions/runs/40122717451)
reused that verified installer; its SHA-256 is
`1121417da3e46f7c7b58fa2d2028f0348685702bacac8c42f5b72b36de8f86a9`.
