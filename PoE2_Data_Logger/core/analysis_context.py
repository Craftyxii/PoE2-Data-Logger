"""Keep analysis guidance in SQLite so workbook and raw database exports explain the same data.

These application-owned notes describe stored observations and their aggregation
rules. They are metadata, independent of session resets and the undo journal.
"""

import csv
import io


CONTEXT_VERSION = "3"
HEADERS = ("Section", "Topic", "Guidance", "Context Version")
ENTRIES = (
    ("purpose", "Overview", "How to use this export",
     "This is a Path of Exile 2 logging dataset of saved, user-reviewed observations. "
     "Use these notes to interpret the workbook or SQLite database. Report missing data, "
     "scan coverage and assumptions when comparing maps; observations alone do not prove causation or game mechanics."),
    ("analysis_method", "Analysis", "Comparing any recorded stat",
     "For any requested stat, first read its Field guide definition, unit, source and level of observation. "
     "Choose a defined outcome and the matching Map ID, Expedition ID, remnant or event unit; build one "
     "observation per unit before grouping. Compare numeric values/ranges or categorical selections "
     "without treating IDs, choice numbers or text positions as measured strengths. Use saved configuration "
     "at the relevant observation, not current settings or repeated history rows. Check missing values, "
     "scan coverage, discarded/undone parts, overlapping settings and outcome availability. For loot, "
     "use recorded inventory gains and the baseline rules, not Ritual offers or recipe names. "
     "Report sample counts, means/medians, variation and uncertainty, and compare similar settings "
     "or account for relevant confounders. Keep numeric quantities separate from prices and do not "
     "assume independent, additive or multiplicative game effects without a documented rule. "
     "Report observed associations and limits; a recorded configuration does not prove causation. "
     "No measured map duration, comprehensive ground-drop stream or automatic market valuation is "
     "recorded here, so those outcomes require additional data. Unrecognized legacy/custom fields "
     "require their source definitions rather than an invented interpretation."),
    ("export_sheet", "Workbook", "Export: current state",
     "Export contains current saved remnant/recipe and chain rows, with acquired-item totals and map metrics "
     "projected once per Map ID. A map can occupy many rows. Inventory-only maps receive a Map totals row. "
     "Count distinct Map IDs, not worksheet rows; a queued Start scan can create a row before the map is started."),
    ("atlas_sheet", "Workbook", "Atlas Character Settings",
     "Atlas Character Settings contains one row per referenced saved setup, plus the current setup. "
     "Join Atlas Setup ID to the data sheets; Map IDs can list several maps and do not make separate setups. "
     "Atlas Node columns are compact: nodes allocated in every referenced setup can be omitted. "
     "Yes/No describes allocation; Atlas Choice numbers follow the saved catalog's option order, with 0 for no choice. "
     "A remembered choice can coexist with an unallocated node. Blank means unavailable in that saved catalog."),
    ("history_sheet", "Workbook", "Scan History: event expansion",
     "Scan History expands saved commits and preserves surviving earlier approvals and corrections, "
     "with their recorded settings/details. One commit can produce many rows: recipes, inventory labels, "
     "Ritual offers or chain steps. Imported rows without a commit can also appear. "
     "Use Scan Commit # to group an event; do not treat each history row as another scan or acquired item."),
    ("snapshot", "Workbook", "Snapshot and current projections",
     "All four worksheets use one SQLite read snapshot. Saved event details and settings remain frozen "
     "through later rescans or edits, while Current Inventory Snapshot, Session Found Quantity and "
     "ordinary Chain Status are projected from the current state at export time. Discard audit rows explicitly "
     "have Chain Status Discarded. History is not a purely historical-state table."),
    ("map_metrics", "Aggregation", "Map totals appear once",
     "In Export, acquired-item columns, map kill totals, scanned map/tablet modifiers and Expedition N Detonated "
     "columns appear only on the first exported row for that Map ID. Later rows leave these fields blank. "
     "The separate Remnants Detonated (Expedition) field can remain on individual expedition rows. "
     "Group by Map ID to recover map metrics; repeated recipe rows are not additional maps. "
     "Export's Scan Commit # is the latest map commit projected once, not necessarily the recipe row's event. "
     "Use Scan History or stored provenance for original event links. "
     "Do not sum cumulative kill or detonated snapshots across Scan History events."),
    ("effectiveness_analysis", "Map analysis", "Effectiveness versus loot",
     "Effectiveness % is a recorded numeric map setting, stored as effectiveness in maps.snapshot_json; "
     "it is not a calculated score for every rune effect. Use the map's saved settings, not current live "
     "meta settings or repeated history snapshots. Use one observation per Map ID and take "
     "Effectiveness % and acquired-item totals from that map's first Export row. Blank effectiveness "
     "is unrecorded, not zero; 0 is an explicit value. Choose a defined outcome, such as Chaos Orb "
     "quantity per map, and compare observed effectiveness levels or clearly stated ranges. "
     "Prefer maps with approved Start and End inventories timed to bracket that same map; "
     "flag or analyze Assumed empty baselines "
     "separately and exclude maps without an approved End. Acquired quantities are positive inventory "
     "gains, not direct counts of ground drops: spending, transfers and incomplete scans can affect them. "
     "Ritual offers are not acquired loot. Compare similar maps or account for Item Rarity %, "
     "Monster Rarity %, Pack Size %, Gear Item Rarity %, Atlas Setup ID, map/tablet modifiers, tier, "
     "area level, biome, city, Irradiated, Ocean Map, Deli, Wisp and recorded rune chains. "
     "Show map counts, means/medians, variation and uncertainty; sparse or inseparable settings "
     "cannot establish an independent effectiveness effect. Normalize by recorded kills or "
     "remnants detonated only for a relevant outcome with complete, positive denominators. "
     "Do not divide map loot among recipe or chain-step rows: inventory gains are map-level, "
     "so these records do not identify which individual recipe, remnant or step produced a drop. "
     "Do not convert mixed item quantities into currency value without a separate, dated price source. "
     "Report observed associations, missing-data exclusions and assumptions rather than claiming "
     "effectiveness caused a change in drops or inventing unrecorded game mechanics."),
    ("kill_totals", "Aggregation", "Kills and detonated counts",
     "Normal, Magic, Rare and Unique kills are saved map totals. Unknown Unique kills remain blank. "
     "Total Kills adds the known kill categories and can therefore be a partial total when categories are missing. "
     "Remnants Detonated (Expedition) is a cumulative expedition count; Remnants Detonated (Scan) is 1 for "
     "a Propagation event. Manual totals can replace previous counts; do not add those snapshots together."),
    ("history_duplicates", "Aggregation", "Related propagation and chain events",
     "Immediate propagation approval saves a Propagation event and a Chain append event for the same part. "
     "A held/manual propagation approval can save its counted Propagation event before the chain draft is saved. "
     "Chain completion repeats the expedition's existing steps, and Chain correction repeats changed steps. "
     "These are not additional detections. For current chain parts use (Expedition ID, Chain Step #); "
     "for recorded events use Scan Commit # and Type, retaining links to the original propagation/chain commits."),
    ("discarded_activity", "Aggregation", "Discarded chains and map logs",
     "Chain discard and Chain step discard remove current chain rows/provenance and add an audit event "
     "superseding those removed parts; earlier chain approvals remain in history. Do not count their removed "
     "steps as current observations. Surviving step numbers retain their identities and can contain gaps. "
     "Discarding the highest/all steps permits later step-number reuse within that expedition; join history "
     "using original event commits and discard context, not expedition/step alone. "
     "Chain discard preserves actual expedition detonated totals. Map logs discard clears the current map's "
     "activity tables and scoped commits while retaining its Map ID/settings; a new Map logs discard commit "
     "records the removal. Its details_json embeds removed commits for explanation, not extra current activity. "
     "Discard operations are undoable."),
    ("inventory_latest", "Inventory", "Latest approved inventories",
     "Only the latest approved Start and End inventory for each Map ID determine current gains. "
     "Rescans replace a phase's current snapshot rather than adding quantities. Currency scans also include "
     "registered equipment/items and Omens. Item name headers can carry a category prefix to avoid metadata collisions; "
     "labels differing only in case share current gain counts."),
    ("inventory_gains", "Inventory", "Signed net versus acquired counts",
     "Net Change is signed End Count minus Start Count. Export's acquired-item columns and Session Found Quantity "
     "use max(0, latest End minus latest Start) per map and label. Negative deltas indicate inventory loss or spending "
     "and contribute zero acquired items. Session totals sum these positive per-map gains, not raw End inventories "
     "or all historical approvals. These are inferred inventory gains; spending, transfers and inaccurate scans "
     "can affect the result."),
    ("inventory_baseline", "Inventory", "Missing Start or End",
     "An approved End with no Start assumes an empty baseline: the entire End inventory counts, and Start Baseline "
     "is Assumed empty. A saved Start takes precedence even if recorded later than End. Without an approved End, "
     "current acquisition counts stay blank. An approved empty End produces zero current gains. "
     "Use Currency Scan Status and the phase commit/timestamp fields to distinguish these cases."),
    ("inventory_history", "Inventory", "Historical quantities and current credits",
     "Quantity in a Currency/Item history row is that approval's inventory count. Historical Start Count, End Count "
     "and Net Change reflect the baseline available in commit order. Session Found Quantity credits only the "
     "current End approval, using the current Start; earlier and Start approvals receive zero. "
     "A later Start can change credited gains without rewriting the earlier End's recorded details."),
    ("ritual_offers", "Ritual", "Offered rewards versus obtained inventory",
     "Ritual rows record rewards offered on a saved reward page, including Quantity, Tribute cost, "
     "Tribute Available and Rerolls Remaining. They do not record purchases or prove an item was obtained. "
     "New Find Quantity describes a non-deferred offered reward; deferred rewards have zero New Find Quantity. "
     "Neither offered quantities nor New Find Quantity contribute to current inventory acquisitions. "
     "Obtained rewards count only when present in approved inventory gains."),
    ("ritual_history", "Ritual", "Page replacements and repeated offers",
     "ritual_pages stores the latest saved page for (Map ID, page_number), while commits retain surviving earlier "
     "Ritual approvals. The same reward can appear on multiple pages or rescans. Do not sum every history offer "
     "as a unique reward. Deferred status is a recorded offer attribute, not an acquisition flag."),
    ("missing_values", "Interpretation", "Blank, zero and Unknown",
     "Blank can mean unrecorded, inapplicable, legacy data without metadata, or a repeated map-level field "
     "suppressed on a later Export row. Zero is an explicit saved value or a computed zero gain; it is not a missing "
     "End scan. Unknown or SQL NULL provenance means no resolved attribution and must not be converted to a valid "
     "Family ID or Recipe ID. Keep these states distinct when filtering, averaging or measuring coverage."),
    ("identifiers", "Identifiers", "Session map, expedition and remnant IDs",
     "Map IDs such as M0001 identify maps within this session; Expedition IDs such as M0001-E1 identify expeditions "
     "within a map. Chain Step # identifies a current part within an expedition and can be reused after discard. "
     "Remnant IDs identify logged remnants, "
     "not recipe catalog entries. Clearing exports resets session counters, so IDs and Scan Commit # can recur "
     "after undo or in another session/export; do not join unrelated databases by these values alone."),
    ("recipe_ids", "Identifiers", "Propagation Recipe ID",
     "Propagation Recipe ID is an RCP- text identifier for the canonical recipe name. It is separate from the "
     "numeric Family ID, a recipe's position in its family list and rune strings. Propagation Recipe gives the "
     "recipe name. The registry keeps IDs across recipe combo edits and reference resets; "
     "IDs normally derive from the trimmed, case-insensitive canonical name. Distinct legacy Unicode names "
     "with colliding case-folded spellings retain separate deterministic exact-spelling identities. "
     "Approved chain provenance stores the ID. "
     "An unknown manual origin stays blank rather than receiving a later-added catalog recipe's ID. "
     "A shared recipe can belong to several families."),
    ("family_provenance", "Identifiers", "Frozen family evidence",
     "Propagation Family Status is known, ambiguous or unknown. Family ID is populated only for a resolved family. "
     "Candidates preserve possible IDs (semicolon-separated in CSV/XLSX; JSON in SQLite). Family Source describes "
     "the supplied/candidate/recipe evidence. Keep ambiguous candidates instead of assigning the first family. "
     "Legacy/manual chain rows can lack provenance. Later catalog changes or rune corrections do not rewrite "
     "the original approved recipe/family attribution."),
    ("rune_parts", "Propagation", "Observed rune pairs and approval",
     "A propagation capture is a draft until the user approves it. Immediate part approval increments the "
     "expedition's detonated count once and appends one chain step containing one or two detected runes. "
     "A held/manual approval can increment the count before its draft chain is saved. "
     "Manual chain entry is also supported. Propagation Rune 1/2 describe the recorded part, not the full recipe "
     "combo; never fill missing rune slots from Recipe ID. Corrections retain step identity and original provenance, "
     "with Previous Rune 1/2 in correction history. Backend crown-slot recovery can use verified marks across a recognized family group, or tentative matching fragments on two distinct recipe rows. A single visible recipe can receive a fragment-based suggestion. These are suggestions requiring manual approval, not certainty measurements. OCR and manual review can still contain errors."),
    ("rune_chain_analysis", "Propagation", "Prior propagated runes plus the current recipe",
     "For a chain step, analyze the previously propagated runes plus the full rune combo on that step's recipe. "
     "Prior propagated runes are Propagation Rune 1/2 from earlier surviving steps in the same Expedition ID, "
     "ordered by Chain Step #. Resolve the current recipe through Propagation Recipe ID and its recorded "
     "Propagation Family ID; in SQLite join chain_step_provenance.recipe_id to recipe_ids.recipe_id, then "
     "recipe_ids.recipe_name to recipes.name for recipes.combo. Family membership is in families.recipes_json. "
     "A workbook analysis needs the matching recipe/family reference database or reference pack. "
     "Effective rune list at step N = prior propagated runes + current recipe combo. Do not add step N's "
     "marked runes again: the intended model includes them in its recipe combo. Manual entries/corrections "
     "can differ from the attributed combo, and membership is not enforced; flag such inconsistencies "
     "instead of assuming the attribution validates those runes. Carry only the approved marked rune(s) "
     "from step N into later steps, not its entire recipe combo. For example, after propagating Fire, "
     "a recipe containing Cold + Life is analyzed as Fire + Cold + Life. Retain repeated rune occurrences "
     "rather than assuming they cancel or share an effect; these notes do not define effect stacking or caps. "
     "Start a new accumulated list for each Expedition ID. Exclude discarded or undone parts and avoid "
     "double-counting repeated approval, completion or correction history rows. Unknown recipe/family "
     "attribution remains unresolved; never infer a full recipe combo from the marked runes alone. "
     "Catalog combos are editable and are not frozen at approval, so later reference edits can change "
     "the reconstructed full combo even though the logged recipe/family IDs remain fixed."),
    ("timestamps", "Interpretation", "UTC and event order",
     "Stored recorded_at and Recorded UTC timestamps use UTC ISO 8601 with second precision and +00:00. "
     "Several events can have the same timestamp; use commits.number / Scan Commit # for their session order. "
     "Export filenames use local clock time and are not event timestamps."),
    ("undo", "Interpretation", "Undo and surviving history",
     "Undo reverses saved row changes atomically, including affected current rows and commits. Undone events can "
     "therefore disappear from Scan History. The database is a snapshot of surviving session state, not an immutable "
     "audit archive. undo_actions is a bounded operational undo stack (at most 100 actions), with compressed JSON row "
     "images in a BLOB; it is not a loot/event table and must not be aggregated as gameplay activity. "
     "Session reset clears that stack and logged map activity, retaining reference catalogs, reviewed "
     "seed screenshot metadata/files and these notes."),
    ("sql_discovery", "SQLite schema", "Discover tables and JSON fields",
     "Use sqlite_master and PRAGMA table_info(table_name) for the exact schema. Fields ending in _json contain JSON "
     "text; use json_extract/json_each or parse JSON in your analysis tool. PNG/vector BLOBs support recognition "
     "and are not item-count records. A raw database backup includes current tables, catalogs, settings, history, "
     "undo state and this analysis_context table."),
    ("sql_context", "SQLite schema", "analysis_context",
     "analysis_context has key (primary key), position (reading order), section, topic, guidance and context_version. "
     "Read SELECT section,topic,guidance,context_version FROM analysis_context ORDER BY position; "
     "these are exactly the Analysis Context worksheet's rows. Notes are application-owned metadata refreshed "
     "during initialization, independent of session counters and undo actions."),
    ("sql_maps", "SQLite schema", "maps / map_unique_kills / expeditions",
     "maps is keyed by map_id and stores snapshot_json (saved configuration), kills_json (Normal, Magic, Rare) "
     "and recorded_at. map_unique_kills stores optional Unique kills by map_id. expeditions is keyed by "
     "expedition_id and stores map_id, number and optional cumulative detonated. Join these keys for current map "
     "metrics; queued inventory scans can reference a map_id not yet present in maps."),
    ("sql_rows", "SQLite schema", "legacy_export / new_export",
     "legacy_export contains imported rows; new_export contains saved current rows. Both have position, map_id, "
     "expedition_id, remnant_id, chain_step and row_json, a positional spreadsheet row array. "
     "The base column names are in meta key export_headers. Propagation rune slots are row_json[26] and [27] "
     "(zero-based). Prefer chain_rune_provenance for per-rune analysis instead of guessing array positions."),
    ("sql_provenance", "SQLite schema", "chain_step_provenance / chain_rune_provenance",
     "chain_step_provenance is keyed by (expedition_id,chain_step) and stores recipe, recipe_id, family_id, "
     "family_status, family_source, family_candidates_json, propagation_commit_number and chain_commit_number. "
     "chain_rune_provenance is a view of nonempty current rune slots joined to this frozen origin; one chain step "
     "can yield two rune rows. Its family text is Unknown when family_id is NULL. "
     "Count steps by distinct expedition_id/chain_step rather than counting view rows."),
    ("sql_catalogs", "SQLite schema", "recipes / recipe_ids / families",
     "recipes is keyed by canonical name and contains sockets, combo and descriptive catalog fields. "
     "recipe_ids maps recipe_name to unique recipe_id, retaining retired identities. families is keyed by numeric "
     "id with top_socket, valid and recipes_json (recipe-name list). Catalogs are editable references, "
     "not counts of logged drops; join frozen provenance to the registry by recipe_id when identifying recorded recipes. "
     "Recipe ID identifies the recipe, not its ordinal position within families.recipes_json."),
    ("sql_inventory", "SQLite schema", "currency_snapshots / ritual_pages",
     "currency_snapshots is keyed by (map_id,phase), with phase start/end, items_json (name-to-quantity map), "
     "recorded_at and snapshot_json. ritual_pages has id and unique (map_id,page_number), items_json (offer list), "
     "raw_text, scan_hash, recorded_at and snapshot_json. These hold current approved inventories/pages. "
     "Earlier surviving approvals and item-category evidence are in commits.details_json."),
    ("sql_scans", "SQLite schema", "scans / scan_links / seed_states",
     "scans stores reviewed seed screenshot metadata by id, including sockets, seed_slot, seed_rune, "
     "family text, rewards_json and mapping_status. scan_links maps remnant_id to scan_id; join scan_id to scans.id. "
     "image_name/file_name refer to image files and are not image bytes included in a database-only export. "
     "seed_states is a reference table keyed by (family,sockets), containing seed_slot, seed_rune, "
     "rewards_json and status; it describes catalog stages, not logged events."),
    ("sql_item_catalogs", "SQLite schema", "Item catalogs and recognition references",
     "currency_items, item_names and ritual_names register Currency, Item and Omen names. Their rows are "
     "reference labels, not acquired quantities. currency_icons, item_icons, omen_icons and review_icon_examples "
     "store recognition examples, including PNG BLOBs. Removing an active reference does not remove saved "
     "event labels or current inventory counts."),
    ("sql_commits", "SQLite schema", "commits / chain_completions",
     "commits is keyed by number, with kind, map_id, expedition_id, reference, recorded_at, snapshot_json "
     "(event configuration) and details_json (kind-specific payload). Details include inventory items/phase, "
     "Ritual offers, remnant recipes, chain steps/changes/origins, or kill/detonated totals. "
     "chain_completions is keyed by expedition_id and stores the current completion commit, step_count and "
     "next_expedition. Ordinary Chain Status in exports uses this current completion table; discard audit "
     "rows explicitly have status Discarded."),
    ("sql_atlas", "SQLite schema", "atlas_setups / atlas_catalogs / meta",
     "atlas_setups maps setup_id to catalog_id and settings_json; atlas_catalogs stores catalog_id, version "
     "and catalog_json. Saved snapshot_json fields refer to atlas_setup_id. meta stores JSON values by key, "
     "including current settings, session counters and workflow state. Current meta settings do not replace "
     "the configuration frozen in an earlier map or commit snapshot."),
)

# Column patterns keep field guidance auditable against current and dynamic export headers.
# They describe interpretation only; they do not calculate stats or alter saved logging records.
COLUMN_GUIDANCE = (
    ("field_ids", r"Map ID|Expedition ID|Remnant ID|Occurrence ID", "Record identifiers",
     "Map ID identifies a session map, Expedition ID identifies a chain within it, and Remnant ID identifies "
     "a logged remnant. Occurrence ID groups the recipe rows of one recorded occurrence, not a loot quantity. "
     "Use IDs for joins/grouping, never as numeric strengths; do not merge independent databases by session IDs alone."),
    ("field_order", r"Expedition #|Chain Step #", "Expedition and chain order",
     "Expedition # is the expedition number within a map. Chain Step # orders current parts within that "
     "Expedition ID, with possible gaps/reuse after discard. Compare ordered chain patterns, not average "
     "step numbers as a rune strength. Use original event commits when analyzing historical reuse."),
    ("field_markers", r"New Map \(X\)|New Remnant \(X\)|Source Row", "Layout markers",
     "New Map (X) and New Remnant (X) are row markers, not extra occurrences or complete coverage indicators. "
     "Source Row is a source-layout row label, not a reward count or chronological event number. "
     "Count distinct session IDs or correctly grouped commits instead of summing these fields."),
    ("field_events", r"Type|Reference", "Event type and reference",
     "Type describes a saved activity such as Remnant, Chain, Currency/Item, Ritual or a correction/discard. "
     "Reference is its human-readable label. Select the appropriate activity before aggregating; "
     "a row describing a recipe or an offer does not mean that reward was obtained."),
    ("field_commits", r"(?:Scan Commit|Start Commit|End Commit|Start Scan Commit|End Scan Commit|Currency Commit|Ritual Commit|Last Commit) #", "Commit links",
     "Commit numbers order surviving saved actions within a session and can recur after Undo/reset. "
     "Start/End and Currency/Ritual commit fields link approvals to their saved event. Export's Scan Commit # "
     "is the latest map commit projected once, so use Scan History or provenance for a recipe's original "
     "approval. One commit can produce many rows; do not count rows as separate scans."),
    ("field_times", r"Recorded UTC|Map Recorded UTC|Start Recorded UTC|End Recorded UTC", "Recorded times",
     "These are saved UTC timestamps for the indicated event, map or inventory phase. Use commit order "
     "to break ties and verify inventory timing brackets the intended map. Time between approvals is "
     "not measured map duration, so it does not establish loot per hour or clear speed."),
    ("field_recipe", r"Recipe Input|Matched Recipe|Recipe", "Recorded recipe labels",
     "Recipe Input preserves the recipe input/label used for a logged remnant. Matched Recipe/Recipe identifies "
     "the resolved recipe row. A remnant may have several recipe rows: group by Remnant ID for remnant counts. "
     "Compare recipe/family frequencies as observed options; names do not prove acquired drops. "
     "Propagation Recipe is the distinct source-attribution field for an approved chain part."),
    ("field_family", r"Family ID", "Remnant family",
     "Family ID identifies the resolved reference recipe group, not an outcome amount or ordered strength. "
     "Compare frequencies by distinct recorded remnants, not repeated recipe rows. For chain parts use "
     "Propagation Family ID and its status/candidates, which preserve approved origin separately."),
    ("field_combo", r"Socket Count|Exact Rune Combo", "Recipe sockets and rune combo",
     "Socket Count is the recorded number of rune sockets for the recipe. Exact Rune Combo is the recorded "
     "ordered full combo on a remnant recipe row, not only propagated runes and not numeric effect totals. "
     "Compare socket stages or rune occurrences while retaining repeated runes. Chain analysis instead uses "
     "the approved marked slots plus the identified recipe catalog as explained in Propagation guidance."),
    ("field_seed", r"Visible Seed Sockets|Visible Seed Slot|Visible Seed Rune|Remnant Scan Mode", "Visible seed evidence",
     "These fields record the reviewed visible seed's socket stage, seed position/rune and scan mode. "
     "They describe source evidence for recipe resolution, not loot quantities or an additional remnant. "
     "Match positions to the saved seed_states reference; blank legacy fields do not imply a zero-socket seed."),
    ("field_origin", r"Propagation Recipe(?: ID)?|Propagation Family (?:ID|Status|Candidates|Source)", "Propagation origin",
     "Propagation Recipe names the source recipe and Propagation Recipe ID is its stable RCP- registry ID, "
     "not its ordinal in a family. Propagation Family ID is numeric only when resolved; Status is known, "
     "ambiguous or unknown, Candidates lists alternatives and Source describes the evidence. "
     "Join the recipe and family catalogs for interpretation, preserve uncertainty and original attribution, "
     "and do not treat a later rune correction as a new source recipe."),
    ("field_runes", r"Propagation Rune [12]|Previous Rune [12]", "Approved and previous marked runes",
     "Propagation Rune 1/2 are the one or two approved marked runes in a chain part, not the full recipe combo. "
     "Previous Rune 1/2 describe replaced values in correction history. Carry surviving approved runes forward "
     "within the expedition, retaining both slots and repeats; do not add previous and corrected versions "
     "together or duplicate the current marked runes over their full current recipe combo."),
    ("field_chain_status", r"Chain Status", "Current chain status",
     "Open/Completed describes current completion state at export time, even on earlier history rows. "
     "Discard audit rows explicitly say Discarded. Compare current surviving chains or event history as "
     "separate populations; completion repeats saved steps and is not another rune detection."),
    ("field_kills", r"(?:Normal|Magic|Rare|Unique) Kills(?: \(Map\))?|Total Kills", "Monster kill counts",
     "Normal, Magic, Rare and Unique kills are saved category counts per map. Total Kills adds known "
     "categories and is partial if any category is missing; blank Unique kills is unknown, not zero. "
     "Compare loot per map/category composition or use complete positive counts for appropriate per-kill "
     "rates. Sum once per map, never repeated history snapshots; kills are not recorded individual drops."),
    ("field_detonations", r"Expedition \d+ Detonated|Remnants Detonated \((?:Expedition|Scan)\)", "Detonation counts",
     "Expedition N Detonated and Remnants Detonated (Expedition) are cumulative saved expedition counts, "
     "including counts retained after chain discard. Remnants Detonated (Scan) is 1 for a Propagation event. "
     "Use current expedition totals once, or correctly grouped original events for event counts, and do "
     "not add cumulative snapshots or duplicate Propagation/Chain history. Per-detonation loot is a map-level "
     "comparison unless separately attributed loot observations exist."),
    ("field_tier", r"Tier", "Waystone tier",
     "Tier is the recorded waystone tier, currently configured as 15 or 16; older/imported records can differ. "
     "Stratify comparisons by tier rather than assuming a proportional loot multiplier. It is not a count "
     "of maps and does not by itself identify area level."),
    ("field_area", r"Area Level", "Derived area level",
     "The logger derives Area Level as 79 for Tier 15 or 80 for Tier 16, plus 1 for Irradiated and 1 for "
     "Ocean Map when enabled. It is a configuration-derived value, not independently measured monster "
     "level. Compare similar levels and account for the options used to derive it."),
    ("field_waystone", r"Waystone %|Waystone Name", "Waystone identity and drop chance",
     "Waystone Name is saved identity text. Waystone % is the recorded Waystone Drop Chance percentage "
     "in settings/history or dedicated exports, not item rarity, an absolute drop probability or a total "
     "loot multiplier. Compare waystone inventory gains with otherwise similar maps. The main Export "
     "uses Map Modifiers for text and omits this obsolete numeric layout column."),
    ("field_item_rarity", r"Item Rarity %", "Map item rarity",
     "Item Rarity % is the recorded map percentage; 50 means 50%, not 0.5 or a 50-times multiplier. "
     "It is separate from Gear Item Rarity %, and the logger does not combine them into a final game "
     "rarity formula. Compare defined acquired-item outcomes across comparable map rarity values, "
     "accounting for gear, Atlas, pack size, monster composition and other settings; blanks are unknown."),
    ("field_gear_rarity", r"Gear Item Rarity %", "Character gear rarity",
     "Gear Item Rarity % is the user-recorded character percentage in the saved Atlas setup, separate "
     "from map Item Rarity %. Blank is unrecorded, zero is explicit. Compare maps with recorded gear "
     "settings and account for map stats; do not add or multiply percentages without a documented "
     "game rule. Start/End setup changes can explain differences between phase snapshots."),
    ("field_monster_rarity", r"Monster Rarity %", "Recorded monster rarity",
     "Monster Rarity % is a saved map percentage, not a measured proportion of Rare kills. "
     "Compare outcomes alongside actual recorded Normal/Magic/Rare/Unique kill composition, map/gear "
     "rarity and other settings. The logger does not convert this percentage into a guaranteed monster "
     "distribution or drop formula; blank is unknown and zero is an explicit value."),
    ("field_pack_size", r"Pack Size %", "Recorded pack size",
     "Pack Size % is the saved map percentage, not the number of packs encountered or total kills. "
     "Compare per-map inventory gains and complete kill counts across comparable pack-size settings. "
     "Per-map and per-kill comparisons answer different questions; do not assume proportional gains "
     "or an interaction formula with other percentages. Blank is unknown, zero is explicit."),
    ("field_effectiveness", r"Effectiveness %", "Recorded effectiveness",
     "Effectiveness % is a saved map percentage, not a calculated strength for every propagated rune. "
     "Use the Effectiveness versus loot guidance for comparable-map inventory analysis. Do not apply "
     "this value to every rune, tablet or Atlas effect automatically; stacking and caps need documented "
     "game rules. Blank is unknown and zero is explicit."),
    ("field_categories", r"Biome|City Type", "Map categories",
     "Biome and City Type are selected categorical map descriptors. Compare group counts and outcomes "
     "between otherwise similar maps without assigning a numeric order or treating None as measured "
     "absence of every related mechanic. Keep selected None separate from missing imported data."),
    ("field_options", r"Irradiated|Ocean Map|Deli|Wisp", "Selected map options",
     "These Yes/No flags record enabled map options. They are not encounter counts or measured bonuses. "
     "Compare each option or well-supported combinations with similar tier, area, Atlas and other "
     "settings. Irradiated and Ocean Map also affect the logger-derived area level, so those variables "
     "are linked; do not assume they supply independent effects."),
    ("field_aldur", r"Aldur's Saga(?: Affix)?", "Aldur's Saga selection",
     "This categorical setting records None, All +5/+6/+7, Lucky or a guaranteed 7/8/9 roll mode. "
     "Compare recipe/family observations or map outcomes by selected category with adequate coverage. "
     "The logger does not apply a numeric reward multiplier or derive a probability distribution from "
     "the label; missing and selected None remain distinct."),
    ("field_master", r"Atlas Master|Perk [1-4]|(?:Jado|Doryani|Hilda) Configured Perk [1-4]", "Atlas Master and perks",
     "Atlas Master names the active selected master. Perk 1..4 are that snapshot's selected perk names, "
     "not ranked numeric strengths. Named Configured Perk fields retain settings for each master, "
     "including inactive ones; do not apply inactive-master perks. Compare master/perk categories "
     "with other saved settings. SQL master_perks supplies current tier/effect reference text, which "
     "is editable and not a frozen history of every effect revision."),
    ("field_mod_counts", r"Base Map Mods|Map Mods|Master \+Mods|Tablet Mods|Total Mods|# \+2 Mod Tablets|\+2 Tablet Mods", "Derived modifier counts",
     "Base Map Mods is the saved base modifier count. Master +Mods is 1 only for Jado with selected "
     "Unexpected Missions, otherwise 0; Map Mods = Base Map Mods + Master +Mods. Tablet Mods sums "
     "the active tablets' additional-random-modifier counts. Total Mods = Map Mods + Tablet Mods. "
     "# +2 Mod Tablets counts tablets contributing exactly 2, not all tablets or modifiers; the legacy "
     "+2 Tablet Mods header is the Tablet Mods subtotal. These correlated values must not be added "
     "again or treated as independent effects. They remain in history/SQL/dedicated exports, while "
     "the main Export omits obsolete modifier-count columns."),
    ("field_tablet_capacity", r"Tablets Used|Tablet Slot Capacity", "Tablet use and capacity",
     "Tablets Used counts populated active tablet positions (raw modifier text or a named affix). "
     "Tablet Slot Capacity is the configured available positions, up to four, not the number of filled "
     "affix slots; each tablet supports four recorded affix slots. Compare use/configurations separately "
     "from capacity and do not multiply capacity by the number of modifiers to infer loot."),
    ("field_modifier_text", r"Map Modifiers|Waystone Modifiers|Map Mod \d+|Tablet \d+ Modifiers", "Captured modifier text",
     "Map Modifiers/Waystone Modifiers hold the full saved newline-separated waystone text. "
     "Map Mod 1..10 are the first ten positions in that same list, so count neither representation "
     "twice and do not infer the full count from ten cells. Tablet N Modifiers is that tablet's "
     "saved text. Compare specific observed affixes/rolls with stated parsing rules; text is evidence, "
     "not a normalized total bonus, and individual unit/stacking meanings must be preserved."),
    ("field_tablet_affixes", r"Tablet \d+ Mod \d+ (?:Affix|%|Value|Unit)|Tablet \d+ Random Modifiers", "Tablet affix values and units",
     "Affix names the recorded modifier; Value with Unit is the authoritative pair (%/count/seconds/flag). "
     "The legacy % field is populated only for percent affixes; a blank there does not mean a flat "
     "modifier is absent. Flag 1 records presence, not effect magnitude. Random Modifiers is the "
     "tablet's additional-random-modifier count. Compare the same affix/unit across maps and retain "
     "separate modifiers rather than summing mixed units or counting raw text and slot values twice. "
     "Older settings may fall back to two modifiers for configured +2 tablets."),
    ("field_atlas_ids", r"Atlas Setup ID|Atlas Catalog ID|Atlas Data Version|Map IDs", "Saved Atlas references",
     "Atlas Setup ID identifies frozen allocation, choices, gear rarity and catalog, not a map sequence. "
     "Join it to Atlas Character Settings or SQL atlas_setups, then atlas_catalogs by catalog_id. "
     "Atlas Catalog ID is the dataset fingerprint; human Atlas Data Version alone is not a unique "
     "join key. Map IDs is a list of maps referring to one setup, and a current unused setup may have "
     "none. Compare setup groups using individual maps, not comma-list rows as extra observations."),
    ("field_atlas_nodes", r"Atlas Node: .+", "Atlas node allocation",
     "Dynamic Atlas Node headers include activity, node label and stable node ID. Yes/No means allocated/"
     "unallocated; blank means unavailable in the frozen catalog. Nodes allocated in every referenced "
     "setup can have no column, so an omitted column is not an unallocated node. Compare allocations "
     "within compatible catalogs and use saved node effects/stats for game-effect meaning. A workbook "
     "needs the matching catalog/SQL for omitted allocations and effect definitions."),
    ("field_atlas_choices", r"Atlas Choice: .+", "Atlas choice selection",
     "An Atlas Choice number is the one-based option order in that node's frozen catalog, with 0 for "
     "no selection and blank for unavailable/not applicable. SQL stores option IDs rather than these "
     "display ordinals. A remembered choice can belong to an unallocated node and is then not active. "
     "Compare named active options after joining allocation and catalog effects/stats; do not average "
     "choice numbers as strengths or compare ordinals across different nodes/catalogs."),
    ("field_labels", r"Currency|Item Name|Item Source", "Inventory item labels and source",
     "Currency and Item Name identify recorded inventory labels. Item Source describes the source "
     "such as Inventory or Ritual, not an acquisition guarantee. Compare quantities for canonical "
     "labels, preserving category information and case-insensitive current gain matching; do not "
     "treat a name or source label as a price or drop probability."),
    ("field_quantity", r"Quantity", "Recorded quantity by event type",
     "Quantity is the approved inventory count on Currency/Item history rows, or offered stack quantity "
     "on Ritual rows. It is not automatically newly acquired loot. Filter by Type and use current "
     "Start/End gain rules for acquisition; repeated rescans and offers are separate evidence, not "
     "quantities to add into current totals."),
    ("field_inventory_counts", r"Start Count|End Count|Net Change|Session Found Quantity", "Inventory counts and gains",
     "Start Count and End Count describe their approved inventory phases. Net Change is signed End "
     "minus Start; Session Found Quantity credits max(0, latest End minus latest Start) only to the "
     "current End history approval. Older/Start approvals receive zero credit. Compare defined "
     "per-item map gains, not every historical Quantity or negative spending as a drop count; a "
     "missing Start assumes empty and an unscanned End cannot establish gains."),
    ("field_inventory_status", r"Inventory Phase|Start Baseline|Current Inventory Snapshot|Currency Scan Status", "Inventory availability and selection",
     "Inventory Phase is Start or End; Start Baseline distinguishes Scanned from Assumed empty. "
     "Current Inventory Snapshot marks the latest saved phase approval, not recognition confidence. "
     "Currency Scan Status describes which approved phases exist for the map. Use these to select "
     "comparable complete maps and distinguish unscanned End, explicit empty End and assumed baseline."),
    ("field_item_columns", r"(?:Item|Currency|Omen): .+", "Named acquired-item columns",
     "Named item columns in Export contain that label's current positive inventory gain once per map. "
     "Many currency/Omen names use plain headers; Item:/Currency:/Omen: prefixes disambiguate categories "
     "or metadata-name collisions. These are item quantities, not values or recipe drop guarantees. "
     "Compare the same labels/outcomes and use a separate dated price source for value analysis."),
    ("field_ritual_page", r"Ritual Page|Page|Reward Type|Reward Name|Name", "Ritual page and reward identity",
     "Page/Ritual Page is the page number within a map. Reward Type and Reward Name classify a saved "
     "offer. Current pages replace earlier approvals with the same map/page, while history can repeat "
     "offers. Compare offered composition by current pages or correctly grouped historical visits, "
     "not row counts as unique purchased rewards."),
    ("field_ritual_cost", r"Tribute|Ritual Tribute Available|Ritual Rerolls Remaining", "Ritual cost and resources",
     "Tribute is the recorded displayed cost of an offer; blank is unread/unrecorded. Ritual Tribute "
     "Available is the page's recorded resource pool, and Ritual Rerolls Remaining is its remaining "
     "reroll count. Page values repeat across offer rows, so use one per saved page/event. Compare "
     "cost/offer/resource relationships without inferring purchases, spending or multiplying a "
     "displayed cost by stack quantity unless its pricing rule is known."),
    ("field_ritual_deferred", r"Deferred|New Find Quantity", "Deferred Ritual offers",
     "Deferred records the offer's deferred status. New Find Quantity equals the offered Quantity "
     "for non-deferred offers and zero for deferred ones. Neither field contributes to inventory "
     "gains or proves a purchase. Compare deferred versus other offers as offer populations, and "
     "use approved inventory changes for obtained-item analysis."),
    ("field_ocr", r"OCR Source|Page OCR Text", "Recognition evidence",
     "OCR Source and Page OCR Text retain text/source evidence supporting a saved reading, not a "
     "numeric accuracy score or ground truth. Use them to audit specific records; do not convert "
     "text length, presence or source labels into a probability that an outcome is correct."),
    ("field_phase_settings", r"(?:Start|End) .+", "Inventory-phase configuration prefixes",
     "Start/End before a configuration column means the saved setting at that phase's approval. "
     "Use the unprefixed field definition for its meaning/unit and flag changed settings between "
     "phases. These are two snapshots of one map's inventory workflow, not two independent maps."),
    ("field_legacy_stats", r"(?:Applied Stat|Stat): .+|Points", "Legacy or imported stat fields",
     "The current exporters do not calculate or populate general Stat:/Applied Stat: effect columns "
     "or Points. If imported data contains them, obtain the source definition, unit and aggregation "
     "rule before comparing values. Numeric spreadsheet formatting alone does not establish what "
     "a stat means or prove that an applied-game-effect formula was evaluated."),
)

# Store the field dictionary beside the broader analysis rules in both SQL and XLSX context.
ENTRIES += tuple((key, "Field guide", topic,
                  f"Column headers (full-match regular expression): {pattern}. {guidance}")
                 for key, pattern, topic, guidance in COLUMN_GUIDANCE)


def initialize(db):
    """Create or refresh bundled notes without modifying session records or their undo history."""
    db.execute("CREATE TABLE IF NOT EXISTS analysis_context ("
               "key TEXT PRIMARY KEY,position INTEGER NOT NULL,section TEXT NOT NULL,"
               "topic TEXT NOT NULL,guidance TEXT NOT NULL,context_version TEXT NOT NULL)")
    db.executemany("INSERT INTO analysis_context(key,position,section,topic,guidance,context_version) "
                   "VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                   "position=excluded.position,section=excluded.section,topic=excluded.topic,"
                   "guidance=excluded.guidance,context_version=excluded.context_version "
                   "WHERE position!=excluded.position OR section!=excluded.section OR topic!=excluded.topic "
                   "OR guidance!=excluded.guidance OR context_version!=excluded.context_version",
                   ((key, position, section, topic, guidance, CONTEXT_VERSION)
                    for position, (key, section, topic, guidance) in enumerate(ENTRIES, 1)))
    # This table contains only bundled metadata; remove notes retired by a later application version.
    db.execute("DELETE FROM analysis_context WHERE key NOT IN (" + ",".join("?" for _ in ENTRIES) + ")",
               [entry[0] for entry in ENTRIES])


def export_csv(db):
    """Serialize the stored notes in reading order on the caller's existing SQLite snapshot."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(db.execute("SELECT section,topic,guidance,context_version FROM analysis_context "
                               "ORDER BY position,key"))
    return output.getvalue().encode("utf-8-sig")
