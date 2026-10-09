# PoE2 Data Logger 1.3.2.6 Beta

- Tighten shared page spacing and group padding. Reduce Currency and Ritual preview height by 30% to leave more room for review rows.
- Keep Map # and Remnant # together in compact windows, place Complete chain beside them, and reduce counter text by 15% while enlarging their captions by 20%. Retain the wide-window arrangement.
- Keep the map's latest remnant number visible when Complete chain advances the expedition. Preserve expedition-specific remnant records, pending reservations, and ID allocation.
- Wrap review guidance so populated Ritual and Currency pages do not push approval controls outside the page. Keep long recipe rune names and PNG icons readable in compact propagation dropdowns.
- Preserve manual propagation corrections when an earlier independent remnant scan finishes. Hold expedition changes while Currency or Ritual review still belongs to the captured expedition.
- Check independent stack-count readings even when digit-presence detection misses a count. Conflicting counts remain for manual review instead of automatically accepting a quantity of one.
- Add Reset OCR references to defaults on Database Import/Export. Clear learned/custom reference examples and restore shipped references while retaining logs, IDs, settings, and scan history.
- Add Save raw SQL database to folder on Data export. Save a complete SQLite backup of all 33 database tables, including logs, references, settings, and embedded image data. Files stored separately from SQLite are not included.
- Preserve older SQLite backups and OCR reference ZIP exports. Block exports that would overwrite the active logger database or its SQLite sidecar files.
- Reduce repeated currency-card redraw and layout work during filtering and catalog updates. Retain zero-count cards and existing counter behavior.
- Update running-client guards to recognize the previous beta during installation and application startup.

Confident items still use automatic approval, tentative readings still require review, and final Currency approval rejects unapproved uncertain rows. Recipe approval saves propagation directly; Complete chain finishes the saved chain and advances the expedition. CSV/XLSX retain the saved Atlas-settings requirement. OCR strictness defaults remain unchanged.

The release pipeline checks native Windows HUD input, a 100-map workflow with independently reopened exports, the complete regression suite, packaged runtime/OCR checks, and installer update/file retention. Native HUD testing uses a simulated game window; the workflow simulation injects OCR results, and real OCR fixtures are tested separately.

Close any running logger before updating. The installer retains the user's Databases folder. The 1.3.2 stable release remains available.
