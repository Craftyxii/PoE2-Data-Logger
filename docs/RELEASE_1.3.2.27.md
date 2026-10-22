# PoE2 Data Logger 1.3.2.27 Beta candidate

This candidate applies the four user-confirmed scan/review corrections on top of
[1.3.2.26 Beta](RELEASE_1.3.2.26.md). It is not yet a published installer.

- Keep the original gray Ritual armor as one complete 2×2 Item instead of four
  review entries.
- Keep grayscale Tetzlapokal's Desire as one complete 2×3 Item instead of six
  review entries.
- Preserve independently verified quantity one for an unnamed inventory object
  without a quantity marker. Request its unresolved name while retaining count
  confirmation for clipped, uncertain or conflicting evidence.
- Prevent the confirmed Idol of Uldurn control from being silently accepted as
  Vaal Armourer's Infuser. Unresolved identity must remain reviewable.
- Retain install/uninstall protection for the preceding beta and older clients.

The HUD controls, layout and styling are retained. The prior audit reports remain
evidence for their original tested source, not proof that these corrections pass.
See the [frontend sweep](FRONTEND_SWEEP_2026-10-22.md) and
[grayscale catalog sweep](GRAYSCALE_CATALOG_SWEEP_2026-10-22.md) for the confirmed
reproductions and simulation limits.

Publication requires the focused reproductions, grayscale catalog checks,
logging/snapshot checks, native Windows HUD and 100-map workflows, full regression,
frozen runtime, installer/update/file-retention checks and installer checksum on
the exact release source. Verified results and the installer link are added only
after those gates complete.
