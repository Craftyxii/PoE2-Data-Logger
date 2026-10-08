# 1.3.1.3 Beta

This release combines the pending review, capture and normal-use fixes with
function-purpose documentation. SQLite remains the source of saved activity;
exports read that recorded data rather than feeding the live currency counter.

## Changes

- Keep propagation actions separate from remnant and other activity reviews.
  Returning to a held remnant preserves its original capture identity and accepted
  propagation parts/counts while retiring unaccepted propagation choices.
- Make manual rune entry reachable for a selected recipe with unreadable marks.
  Switching or denying a recipe clears stale inputs; a clear rescan replaces the
  held choice and restores chain-completion eligibility.
- Verify the complete rune-tile span and spacing before mapping three marks to
  recipe-order names. Exposure-expanded frames cannot enlarge the tile pitch;
  unverified first-tile positions hold for manual review.
- Normalize opened-panel socket evidence and distinguish a complete single-reward
  Farrul family from an incomplete list that needs review.
- Keep screenshot preparation off the Windows hotkey listener so HUD keys remain
  available during capture. Automatic confirmations use an opaque review window;
  manual HUD visibility retains the configured opacity and window geometry.
- Read complete inventory count clusters at larger cell sizes. Conflicting count
  evidence holds the row instead of automatically saving a clipped quantity.
- Scale Ritual cell-frame insets and calibrate supported dim-grid footprint
  geometry without changing the original pixels used for item identification.
- Add a saved Game resolution choice beside scan regions. Auto uses actual native
  game bounds; explicit presets validate those bounds. No preset invents a crop or
  forces resampling. Legacy pixel regions without calibration need reselection
  when capture bounds change. The preview can shrink on a 720-pixel-tall desktop
  so the resolution selector and Save action remain reachable.
- Map native monitor coordinates to Qt screen geometry using monitor identity
  and origin. Hold unsupported cross-screen or inconsistent capture geometry.
- Retain per-expedition chain correction drafts and per-master perk drafts when
  browsing. New maps clear those drafts. Chain correction validation includes
  known runes offered by the recipe-based dropdowns.
- Preserve a changed export folder through unrelated UI refreshes. Explain when
  saved settings affect future activity while a recorded map snapshot stays fixed.
- Preserve intermediate typed gear-rarity decimals and reject incomplete input
  rather than saving a different number.
- Add purpose notes to all 729 application functions and 21 classes, including
  nested helpers, plus build/installer tools, test module scopes and retained
  third-party adapters. Notes describe actual data ownership, review safeguards
  and persistence behavior. They do not assert recognition is infallible.
- Align app, installer and executable version metadata with 1.3.1.3 Beta, and keep
  install/uninstall guards for a running 1.3.1.2 Beta client.

## Validation scope

Before changing version metadata, the documentation pass compiled and compared
128 Python/spec files against the current pre-notes source, removing only
docstrings from the comparison. Eight other code/config files retained identical
non-comment lines. All executable code was unchanged by that pass. Subsequent
Python changes after that pass are limited to the display title, installer
guard/probe checks and a smaller scan-preview minimum height. Test updates supply
the real grid pitch to learned-artwork fixtures and enforce the new requirement
that propagation entry stays out of remnant review.

Capture regressions and offscreen Qt review/navigation checks exercise the
recognition and save/export paths separately. The Windows release pipeline runs
the opt-in 100-map UI simulation, regression suite, frozen-runtime inspection and
installer/update/uninstall smoke checks. The simulation injects scan results; it
does not verify native game-overlay interaction or OCR accuracy.

The reported Skyfall Celestial-to-Tempest misread has not been reproduced using
the exact original failed scan. Focused composed-pixel tests verify a second-slot
Celestial save and export, but do not prove that reported case is resolved.
Native Windows game-overlay painting and input during an actual game session
also remain outside the local offscreen verification.

Detailed function labels and capture evidence are recorded in
[TARGETED_REVIEW_FIX_VALIDATION.md](TARGETED_REVIEW_FIX_VALIDATION.md).
