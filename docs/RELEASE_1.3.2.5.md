# PoE2 Data Logger 1.3.2.5 Beta

- Center the live **Map #** and **Remnant #** indicators and enlarge them to 64 px in wide layouts and 48 px in compact layouts.
- Fix the header's **Complete chain** action so it finishes the saved chain and advances to the next Expedition ID. Completion waits while an unrelated scan is pending, preserving that scan's captured context.
- Save each accepted OCR chain part atomically even when a separate manual draft is open, while preserving that draft.
- Refresh the editable saved-rune dropdowns on **Expedition** immediately after **Approve** on Review saves a chain part.
- Hide empty manual-draft controls on **Expedition** while keeping explicit manual entry available. Keep chain status visible when optional guidance is turned off.
- Size the workspace from its active page so **Save regions** stays visible on a 720-pixel-tall window. Use the compact header at intermediate widths to keep page headings readable.

This beta updates the runtime while retaining the user's **Databases** folder. Close any running logger before updating. The **1.3.2 stable release** remains available.
