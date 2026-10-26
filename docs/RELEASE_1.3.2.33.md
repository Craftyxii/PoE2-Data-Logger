# PoE2 Data Logger 1.3.2.33 Beta

This beta keeps every Ritual page taken with quick key presses, asks when the roll
count goes up on a map, removes cold scans, shows held reviews sooner, reads Currency
and Ritual scans faster with the same results, and explains every affix in the
Analysis Context sheet, on top of [1.3.2.32 Beta](RELEASE_1.3.2.32.md).

## Quick Ritual presses keep every page

- **What went wrong.** Two Ritual pages finished before the window collected them
  kept only the newer one, and a key press while a screenshot was being taken did
  nothing.
- **Fixed.** A Ritual or general key pressed while another capture runs takes its
  screenshot at once and waits in line in press order. The header shows how many
  pages wait and the status bar confirms each one. One page being read plus two
  waiting is the limit; a further press says "Two Ritual pages are already waiting.
  Approve or reject the current page, then scan again."
- A held screenshot that shows no Ritual window (for example, the window was
  already closed) is discarded with "Nothing to scan in that capture — discarded."
  and never opens a review.
- Reject, + New map, Undo and a Currency scan no longer cancel a Ritual page that
  is being taken or waiting; each page is still checked against the map it was
  taken on.

## Roll checks

- Rolls only go down on a map. A page with more rolls than the map's last saved
  page asks "Rolls went up (30 → 35) — did you start a new map?" in the review panel.
  - **Yes, new map** does exactly what **+ New map** does and saves the page to the
    new map. Pages taken after it on the old map follow it. Undo removes the page,
    then the new map.
  - **No, fix rolls** lets you type the correct count; Approve checks it again.
- A later page whose rolls cannot be read is held with a note: "Rolls left couldn't
  be read — this may repeat M0172 page 3 (5 rolls left). Approve only if it is a new
  page." The first page of a map saves as before.

## No cold scans

- A reading cancelled by Reject, Undo, + New map or a newer scan used to stop its
  worker, so the next scan there first reloaded everything (about 18 s). Now the
  cancelled reading finishes in the background and is discarded.
- A worker that crashes or hangs is replaced and loaded at once.
- Each worker reads one sample inventory and Ritual page at startup, so the first
  scan is as fast as later ones. A scan pressed while recognition still loads says
  "Recognition is still loading — your scan starts in a moment."

## Faster reviews and scans

- The review area is no longer re-styled on every Currency and Ritual scan; showing a
  held review took the window about half a second before, now about 0.2 s.
- **Fast recognition** (Scan settings, on by default) reaches the same results with
  quicker arithmetic: icon ranking computes only the positions it uses, the backup
  icon reader and the digit reader run without the built-in JavaScript, and the text
  read runs alongside icon and grid work. Turn it off to use the original method.
- Checked identical to the original method on every test capture and the user's 49
  Ritual pages, including 1440p and 4K scales: 2,448 icon rankings, 4,244 icon
  reads, 2,160 digit reads, 12 inventories and 59 Ritual pages.
- A Ritual page reads in about 1.3–1.6 s instead of about 2.8–3.5 s on the test
  machine. The status line shows "Read in N s" after each Currency and Ritual scan.

## Analysis Context

- Every affix in the Affix DB and every bundled Rite affix has a row explaining its
  unit, meaning and source.

Running 1.3.2.32 Beta and earlier clients are protected during installation and
removal.
