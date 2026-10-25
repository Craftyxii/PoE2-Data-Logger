# PoE2 Data Logger 1.3.2.32 Beta

This beta never lets a Ritual scan replace a page that is still being read or
reviewed, saves a page taken before **+ New map** to its own map, says when
Undo drops unsaved pages, shows the map's Ritual pages in the header and names
a gray Ritual unique, on top of [1.3.2.31 Beta](RELEASE_1.3.2.31.md).
Download the
[Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.32-beta/PoE2-Data-Logger-Setup-v1.3.2.32-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.32-beta).

## Ritual pages wait in line

- **What went wrong.** A Ritual scan taken while the previous page was still
  being read, or was waiting for Approve, replaced that page. The earlier page
  was never saved.
- **Fixed.** A page being read or reviewed is never replaced. Up to two more
  pages wait behind it and are read in the order you took them, each checked
  against the page saved before it. A third press says "Two Ritual pages are
  already waiting. Approve or reject the current page, then scan again." and
  saves nothing. The status bar shows how many pages wait.

## A page taken before + New map goes to its own map

- A page still being read when you press **+ New map** is saved to the map it
  was taken on: "✓ Ritual page saved to M0172 as Scan Commit #N."
- A page waiting for review stays open after **+ New map**; Approve saves it to
  its own map. Its repeat check uses that map's pages.
- The status line after **+ New map** says where the page goes. Previously such
  a page was dropped.
- **+ New map** is otherwise unchanged: it locks the map with its settings, and
  nothing else can be added to a finished map.

## Undo and Reset map say what they drop

- Undo and Reset map still drop pages that were not saved yet, because the logs
  they were taken against have changed. They now say so: "1 unsaved Ritual page
  was not saved because the saved logs changed; scan again." Before, the pages
  disappeared without a word.
- Undo itself is unchanged: one press reverses the last saved action, and
  undoing **+ New map** reopens the previous map for editing.

## Ritual pages in the header

- On a Ritual map, the place where **Complete chain** sits shows the map's saved
  pages, for example "M0172 · 3 Ritual pages", with the Rite stage and any
  waiting pages on a second line. It updates as pages are saved, on
  **+ New map** and on Undo.
- Expedition and Grand Expedition maps, and maps with no activity set, keep the
  **Complete chain** button exactly as before, including the 60-second
  "Is this the same chain?" question. Abyss and Breach maps leave the space
  empty. The Complete chain buttons on the Review and Expedition pages are
  unchanged.

## Recognition

- A unique drawn gray because its price is above your Tribute (such as the
  Shyaba sceptre) is now named. Its thin outline let the blue cell backing show
  through, so it was judged colored and held. On the user's 49 Ritual pages only
  that reward changed.
- Every catalog item was drawn on synthetic Ritual pages (1080p, 1440p, JPEG and
  gray) and inventories and read back: none was named wrongly.

## How this was checked

- Every Ritual scan outcome (saved, already saved, nothing new, held, failed,
  save error) was tested through the real capture path.
- 200 random Ritual sessions, 8,000 steps, mixed new and repeated pages, fast
  presses, held pages, Approve, Reject, failed readings, **+ New map** in the
  middle of a reading and Undo. After every step the saved pages matched a
  separate count of what should be saved.
- The user's 49 real Ritual pages were replayed with real recognition, once at
  normal pace and once pressing the next page while the previous was read: both
  saved the same pages, and every press ended with a visible result.
- Exports were compared cell by cell with 1.3.2.27 on a scripted session
  covering every activity. They match apart from the changes listed in these
  notes and earlier ones.

Running 1.3.2.31 Beta and earlier clients are protected during installation and
removal.
