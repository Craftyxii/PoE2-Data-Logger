# PoE2 Data Logger 1.3.2.10 Beta

This beta fixes incorrect currency quantities, lost review edits and reference changes that could block Universal Undo.

- Preserve evidence of visible stack digits when nearby icon fragments interfere with count recognition. A captured stack of 14 no longer becomes an automatically approved 1; uncertain quantities still require confirmation.
- Keep recognized Currency names filled in. When only the quantity needs review, show **Confirm count** instead of asking the user to confirm the name again. Likely and unknown matches retain manual approval.
- Join supported wrapped waystone modifiers without counting the continuation as an extra modifier. Ambiguous boundaries remain pending.
- Keep a validated multi-cell Ritual item together when its dark interior has weaker color, rather than splitting one reward into several rows.
- Preserve corrected and rejected seed rows when switching between Seed and Opened review. Preserve Family DB drafts and selected seed families during unrelated refreshes.
- Open catalog results in the editor matching the displayed result type, even after changing the next search type.
- Journal reference reset, removal and import changes so they can be undone before earlier scan approvals. Group screenshot/name creation and seed approval/learning into their original commands. Keep raw capture files.
- Attach automatic affix discovery to the latest saved action without changing its Undo label. Rediscovering a reset affix no longer blocks Undo; Undo reverses that discovery together with the saved action.
- Include explicitly restored retired names in reference packs even when they have no saved icons. Older packs retain their existing behavior.
- Preserve distinct historical recipe IDs across Unicode case variants and reference-pack roundtrips. Repair demonstrably incorrect propagation origins and their retry/Undo records consistently.
- Align app, installer and executable beta versions and recognize 1.3.2.8/.9 clients during installation and single-instance checks.

Propagation always requires manual review; its selectors and row approval workflow are unchanged. Remnant logging, scan strictness defaults and game interaction are unchanged. Changed code functions include purpose notes.

Publication requires the complete regression suite, native Windows HUD input checks, a 100-map logging/export simulation and installer/update verification. The simulation injects scanner results; OCR regressions separately use captured panels and rendered fixtures. These checks do not establish perfect live-game recognition or prove the absence of every possible bug.

See [the audit findings and validation](DEEP_BUG_AUDIT.md). Workflow logs and downloadable validation artifacts provide the Windows release results.
