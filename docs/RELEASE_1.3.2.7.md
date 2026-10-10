# PoE2 Data Logger 1.3.2.7 Beta

This beta fixes failures found during the post-1.3.2.6 HUD, OCR and storage audit.

- Fixed a remnant review getting stuck on “Reading” after New map rejected invalid kill counts.
- Cancelled scans now stop before subsequent screenshot, clipboard and OCR work. Closing the HUD invalidates the pending capture before detaching its callbacks.
- Windows shortcut listener failures now report an error and clean up their registrations.
- Prevented mixed pre-reset/post-reset HUD state and scan context, orphan detonated-count records, and next-map markers surviving a reset.
- Improved opened-remnant recognition at enlarged resolutions: heading detection stays inside the panel, empty-list checks account for scaling, and short unread reward names are not mistaken for blank parchment.
- Ambiguous tablet numbers stay available for review instead of silently becoming an exact affix match. Valid decimals and displayed roll ranges continue to work.
- Failed paired exports now preserve existing symbolic links during rollback, including dangling links.
- Installer and single-instance checks recognize the previous 1.3.2.6 Beta client.

Propagation still requires manual review. Recipe approval, chain completion, currency confidence grouping and export formats retain their existing behavior. Changed functions include purpose/behavior notes.

The release workflow requires the complete regression suite, native Windows HUD input checks, a 100-map logging/export workflow and installer/update checks before publication. The audit also measured repeated currency review/navigation: no sustained hang or widget growth in that bounded run, but a full 60-row review rebuild could pause for about 0.6 seconds.

Automated HUD checks use a simulated game window; the 100-map workflow injects OCR results. Real screenshot OCR regressions are separate checks. Live-game compatibility across every GPU, resolution and Windows scaling combination is not guaranteed by these tests.

See [the audit notes](POST_1.3.2.6_AUDIT.md) for reproduced causes and validation details. This is a prerelease; the 1.3.2 stable release remains available.
