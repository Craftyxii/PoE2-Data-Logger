# Propagation recipe IDs

Each catalog recipe has a stable `RCP-` ID in `recipe_ids`. Approval saves that ID
beside the existing family ID and propagated rune slots in
`chain_step_provenance`, the commit history and `chain_rune_provenance`.
No full recipe rune list is copied into propagation logs.

For analysis, join a logged `recipe_id` to `recipe_ids.recipe_id`, then join
`recipe_ids.recipe_name` to `recipes.name`. The recipe's `combo` supplies its full
rune order, and the logged `family_id` identifies its originating family.
This lookup uses the current reference database, as requested.

IDs derive from the canonical recipe name and remain consistent across profiles,
reference packs, restarts, combo edits and reference resets. A different recipe
name represents a different recipe identity. Reference packs include IDs and
still accept older packs without them. Logged custom IDs survive reference reset;
removed custom recipes stay outside the active catalog.

CSV and XLSX exports append `Propagation Recipe ID` after their existing columns.
Older explicitly attributed origins receive an ID when their catalog recipe is
available. Unidentified legacy/manual origins remain unidentified.

This change does not alter remnant creation, IDs, pending review, propagation
approval, chain completion or detonation counts. Changed functions contain notes
describing ID registration, migration, import validation and export behavior.
