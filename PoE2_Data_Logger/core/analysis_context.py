"""Keep analysis guidance in SQLite so workbook and raw database exports explain the same data.

These application-owned notes describe stored observations and their aggregation
rules. They are metadata, independent of session resets and the undo journal.
"""

import csv
import io


CONTEXT_VERSION = "2"
HEADERS = ("Section", "Topic", "Guidance", "Context Version")
ENTRIES = (
    ("purpose", "Overview", "How to use this export",
     "This is a Path of Exile 2 logging dataset of saved, user-reviewed observations. "
     "Use these notes to interpret the workbook or SQLite database. Report missing data, "
     "scan coverage and assumptions when comparing maps; observations alone do not prove causation or game mechanics."),
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
     "IDs are derived from the trimmed, case-insensitive canonical name. Approved chain provenance stores the ID. "
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
     "marked runes again: they are already part of its recipe combo. Carry only the approved marked rune(s) "
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
