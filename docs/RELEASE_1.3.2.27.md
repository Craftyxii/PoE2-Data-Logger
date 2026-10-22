# PoE2 Data Logger 1.3.2.27 Beta

This beta applies the confirmed scan/review corrections on top of
[1.3.2.26 Beta](RELEASE_1.3.2.26.md). Download the
[verified Windows installer](https://github.com/Craftyxii/PoE2-Data-Logger/releases/download/v1.3.2.27-beta/PoE2-Data-Logger-Setup-v1.3.2.27-beta.exe)
or view the [release and checksum](https://github.com/Craftyxii/PoE2-Data-Logger/releases/tag/v1.3.2.27-beta).

- Keep the original gray Ritual armor as one complete 2×2 Item instead of four
  review entries.
- Keep grayscale Tetzlapokal's Desire as one complete 2×3 Item instead of six
  review entries.
- Preserve independently verified quantity one for an unnamed inventory object
  without a quantity marker. Request its unresolved name while retaining count
  confirmation for clipped, uncertain or conflicting evidence.
- Prevent the confirmed Idol of Uldurn control from being silently accepted as
  Vaal Armourer's Infuser. Unresolved identity remains reviewable; choosing the
  Item candidate preserves its Item category in logging, learning and exports,
  while the currency alternative retains its Currency category.
- Retain install/uninstall protection for the preceding beta and older clients.
- Keep independently verified Ritual identities out of review instead of applying
  an unrelated second score cutoff. Counts, deferred state and unresolved
  alternatives retain their separate confirmation requirements.
- Use Currency's existing per-item Approve/Reject behavior in Ritual's existing
  Review column. Final Approve saves verified and individually approved rewards;
  pending or rejected guesses do not register names or teach artwork.
- Make lower unique-item strictness settings useful over a wider confidence
  range while retaining the standard value-50 threshold and ambiguity guards.
- Recognize Sacred Bloom from verified canonical artwork and the supplied capture
  variant, preserving Mirror identification, separate occurrences and existing
  Item registrations when updating an older profile.

Ritual reuses Currency's row actions; the other HUD controls, table columns,
layout and styling are retained. Each non-deferred reward on a distinct Ritual
page contributes its quantity independently, including the same item on pages 1
and 5. Deferred appearances contribute zero new finds. The prior audit reports remain
evidence for their original tested source, not proof that these corrections pass.
See the [frontend sweep](FRONTEND_SWEEP_2026-10-22.md) and
[grayscale catalog sweep](GRAYSCALE_CATALOG_SWEEP_2026-10-22.md) for the confirmed
reproductions and simulation limits.

All required Windows gates passed on release source
`fec15f15a37d37c64157b3a6949866bb44615f57`: native Windows input, the 100-map
workflow, 2,249 regression tests with three expected skips, frozen build,
installation, update, recognition and saved-file retention. Publication reused
the verified installer and checked its SHA-256. The [validation record](BETA27_VALIDATION.md)
identifies the exact runs, audit sources and remaining simulation limits.
