"""Keep analysis guidance in SQLite so workbook and raw database exports explain the same data.

These application-owned notes describe stored observations and their aggregation
rules. They are metadata, independent of session resets and the undo journal.
"""

import csv
import io


CONTEXT_VERSION = "9"
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
     "use Currency gains with their baseline rules or Item counts from the latest approved inventory, "
     "not Ritual offers or recipe names. "
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
     "A remembered choice can coexist with an unallocated node. Blank means unavailable in that saved catalog. "
     "Analysis Context includes full frozen node/choice definitions, allocated nodes including omitted "
     "columns, selected option identities and source-scoped totals for each referenced setup."),
    ("history_sheet", "Workbook", "Scan History: event expansion",
     "Scan History expands saved commits and preserves surviving earlier approvals and corrections, "
     "with their recorded settings/details. One commit can produce many rows: recipes, inventory labels, "
     "Ritual offers or chain steps. Imported rows without a commit can also appear. "
     "Use Scan Commit # to group an event; do not treat each history row as another scan or acquired item."),
    ("snapshot", "Workbook", "Snapshot and current projections",
     "All worksheets use one SQLite read snapshot. Saved event details and settings remain frozen "
     "through later rescans or edits, while Current Inventory Snapshot, Session Found Quantity and "
     "ordinary Chain Status are projected from the current state at export time. Discard audit rows explicitly "
     "have Chain Status Discarded. History is not a purely historical-state table."),
    ("context_continuations", "Workbook", "Reading long Analysis Context guidance",
     "Analysis Context keeps Section, Topic, Guidance and Context Version in their original columns. "
     "When guidance exceeds Excel's 32,767-character cell limit, the workbook appends columns named "
     "Guidance continuation 1, Guidance continuation 2 and so on. For each row, concatenate Guidance "
     "and its numbered continuation columns in order without separators before reading or parsing JSON. "
     "Blank continuation cells add nothing. SQLite and CSV retain the complete guidance in one field."),
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
     "For Currency outcomes, prefer approved Start and End inventories timed to bracket that same map; "
     "flag or analyze Assumed empty baselines separately and exclude maps without an approved End. "
     "Item outcomes use the latest approved inventory scan regardless of phase, without Start subtraction. "
     "Currency gains and Item snapshot counts are not direct counts of ground drops: spending, transfers "
     "and incomplete scans can affect them. "
     "Ritual offers are not acquired loot. Compare similar maps or account for Item Rarity %, "
     "Monster Rarity %, Pack Size %, Gear Item Rarity %, Atlas Setup ID, map/tablet modifiers, tier, "
     "area level, biome, city, Irradiated, Ocean Map, Deli, Wisp and recorded rune chains. "
     "Show map counts, means/medians, variation and uncertainty; sparse or inseparable settings "
     "cannot establish an independent effectiveness effect. Normalize by recorded kills or "
     "remnants detonated only for a relevant outcome with complete, positive denominators. "
     "Do not divide map loot among recipe or chain-step rows: inventory gains and Item counts are map-level, "
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
     "Currency gains use the latest approved Start and End inventories for each Map ID. Item counts use "
     "the latest approved inventory scan for that map, whether Start or End, without subtraction. "
     "Rescans replace the applicable current snapshot rather than adding quantities. Two separate occupied "
     "Item footprints count twice within one scan. Currency scans also include registered equipment/items "
     "and Omens. Item name headers can carry a category prefix to avoid metadata collisions; "
     "labels differing only in case share current counts. New saves mark Item counting with "
     "item_accounting=latest_scan; "
     "legacy raw logs remain intact rather than being rewritten as new observations."),
    ("inventory_stages", "Inventory", "Currency scan IDs and stages within a map",
     "Each approved Currency scan has a Currency Scan ID and Currency Scan # within its Map ID, "
     "independent of Expedition ID. One scan can expand into several item rows: group by Currency "
     "Scan ID to recover the stage, not by worksheet row. The scan number is an ordering identifier, "
     "not an item quantity or an additional map. Historical stages retain their approved quantities "
     "and saved settings. Rescanning replaces the applicable current inventory; never add earlier "
     "stage quantities to a later scan. For example, End scans showing 1 Divine Orb then 3 Divine "
     "Orbs leave an End count of 3, not 4; Currency baseline and gain rules still apply. "
     "Item counts use the latest approved scan across both phases. Final Currency Scan identifies "
     "the latest approved End scan used for Currency gains at export time. "
     "A currency absent from an approved replacement inventory is not carried forward from an older "
     "stage. IDs for older records lacking a stored scan ID are derived from surviving Currency "
     "commit order during export; the historical commit payload is not rewritten."),
    ("remnant_review_policy", "Remnants", "Family matching and explicit confirmations",
     "With remnant auto-commit enabled, an opened reading with one matched family and a recognized "
     "first recipe in that family saves its catalog suffix, including unseen rows. Independent OCR "
     "confidence, heading, geometry and visible-socket warnings do not hold this unique match; the "
     "eligibility check does not rewrite those OCR fields. A repeated consecutive same-family scan still requires explicit "
     "confirmation. Ambiguous families and invalid or stale ownership remain unresolved. Propagation "
     "always requires manual approval. A recognized remnant waiting for a database writer retains "
     "its original capture and ownership for admission retry; this does not approve or save it. "
     "After a gap over 60 seconds since the last accepted chain "
     "entry, Yes retains the chain; No discards an older unapproved propagation review, completes "
     "the logged chain using Complete chain, and leaves the incoming part in the next expedition "
     "for explicit approval or Commit. Unknown legacy timing does not establish a gap."),
    ("inventory_gains", "Inventory", "Signed net versus acquired counts",
     "Currency Net Change is signed End Count minus Start Count. For Items saved with "
     "item_accounting=latest_scan, Net Change records that approval's Item quantity without subtraction. "
     "For Currency, Export's named columns and Session Found "
     "Quantity use max(0, latest End minus latest Start) per map and label. Negative deltas indicate inventory "
     "loss or spending and contribute zero Currency gains. For Items, these fields use the quantity in the "
     "latest approved inventory scan for the map, whether Start or End, without subtraction. Session totals "
     "sum each map's current contribution, not all historical approvals. These are Currency gains and Item "
     "snapshot counts; spending, transfers and inaccurate scans can affect the result."),
    ("inventory_baseline", "Inventory", "Missing Start or End",
     "For Currency, an approved End with no Start assumes an empty baseline: the entire End inventory counts, and Start Baseline "
     "is Assumed empty. A saved Start takes precedence even if recorded later than End. Without an approved End, "
     "current Currency acquisition counts stay blank. An approved empty End produces zero current Currency gains. "
     "Items need no Start baseline or End phase: the latest approved inventory scan supplies their counts, "
     "and an approved empty replacement scan produces zero. Use Currency Scan Status and the phase "
     "commit/timestamp fields to distinguish these cases."),
    ("inventory_history", "Inventory", "Historical quantities and current credits",
     "Quantity in a Currency/Item history row is that approval's inventory count. Historical Start Count, End Count "
     "reflect their recorded phases. Currency and unmarked legacy Net Change reflect the baseline "
     "available in commit order; new Item Net Change records that approval's quantity without subtraction. "
     "Currency Session Found Quantity credits "
     "only the current End approval, using the current Start; earlier and Start approvals receive zero Currency "
     "credit. Item Session Found Quantity credits the latest approved inventory scan across both phases; "
     "earlier approvals receive zero Item credit. A later scan can change current credits without rewriting "
     "an earlier approval's recorded details."),
    ("ritual_offers", "Ritual", "Offered rewards versus obtained inventory",
     "Ritual rows record rewards offered on a saved reward page, including Quantity, Tribute cost, "
     "Tribute Available and Rerolls Remaining. They do not record purchases or prove an item was obtained. "
     "New Find Quantity equals the offered Quantity for each non-deferred occurrence and zero for each deferred "
     "occurrence. Unique Items use this same per-occurrence rule as Omens, including mixed deferred states "
     "for the same name on one page. "
     "Neither offered quantities nor New Find Quantity contribute to current inventory acquisitions. "
     "Inventory counts come from approved inventory scans using the category's counting rule."),
    ("ritual_history", "Ritual", "Page replacements and repeated offers",
     "ritual_pages stores the latest saved page for (Map ID, page_number), while commits retain surviving earlier "
     "Ritual approvals. A map can contain more than 50 saved pages. Ritual and Currency do not maintain a "
     "waiting-capture backlog; waiting requests are not saved observations. Clear Ritual pages can still "
     "save automatically when enabled. Rescanning the same capture hash updates "
     "its page while retaining approval "
     "history. Different saved pages remain independent, even when the same reward appears on both. "
     "The same reward can appear on multiple pages or rescans. Do not sum every history offer "
     "as a unique reward. Deferred status is a recorded offer attribute, not an acquisition flag."),
    ("ritual_boundaries", "Ritual", "First and final map resources",
     "First/final Tribute Available and Rerolls Remaining are the earliest/latest surviving approved Ritual "
     "scan for the same Map ID, ordered by commit number. Pages means remaining rerolls, not the number of "
     "saved reward pages. Rejected or waiting reviews do not contribute. Reusing a page or capture hash does "
     "not erase its earlier approval. Each boundary retains its own unread fields as blank/NULL; do not "
     "substitute an interior reading, add readings together or infer spending from their difference. "
     "Ritual Reading Count counts approved readings, including rescans. Map settings Context JSON exposes "
     "the same boundaries and commit links in ritual_counters, derived from immutable Ritual commit details. "
     "An approved page with no saved rewards still records readable Tribute or rerolls, including zero. "
     "It contributes no item counts or learned icons; an empty page with neither counter known is not saved."),
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
     "In Excel, join Propagation Recipe ID to Recipe Database.Recipe ID to obtain Exact Rune Combo, "
     "then use Family Database rows with that Recipe ID and Propagation Family ID to verify membership "
     "and see Recipe # In Family. All required recipe/family references are included in this workbook; "
     "no separate reference pack is needed for these joins. "
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
    ("rune_meaning_scope", "Rune meanings", "Sources, scope and interpretation",
     "This dictionary explains Remnant/Runeshape rune effects, not same-named augmentation items or "
     "support gems. Sources are PoE2DB tooltip and keyword pages, retrieved 2026-10-13 UTC; their "
     "navigation identifies Runes of Aldur v0.5. These are current reference meanings, not frozen "
     "historical game-version definitions or measured effects in this dataset. Join rune names from "
     "the reconstructed effective rune list to these definitions, preserving repeated occurrences "
     "and the effect's target (Monsters, Remnant or Runes). Gasp is the local label for Volcanic; "
     "lowercase stone is a spelling variant of Stone. Unresolved is a placeholder, not a rune. "
     "Unknown/custom labels remain unresolved. Keyword defaults below are not individual rune "
     "strengths. These sources do not establish rune-specific magnitudes, stacking, caps, "
     "Effectiveness scaling or loot probabilities; never calculate those from names or occurrence "
     "counts. Named effects without detailed rules are explicitly left unexplained."),
    ("rune_meaning_adaptive", "Rune meanings", "Adaptive",
     "Monsters gain Adaptation: elemental hits make them take less damage from subsequent hits of "
     "that element. See the Adaptation keyword definition. Source: https://poe2db.tw/us/Adaptive_Rune"),
    ("rune_meaning_arcane", "Rune meanings", "Arcane",
     "Monsters gain extra Energy Shield and trigger a stunning nova when their Energy Shield is "
     "depleted. Source: https://poe2db.tw/us/Arcane_Rune"),
    ("rune_meaning_bloodletting", "Rune meanings", "Bloodletting",
     "Monsters gain Life Leech, cannot have Life Leeched from them, and inflict Corrupted Blood on "
     "hit. Source: https://poe2db.tw/us/Bloodletting_Rune"),
    ("rune_meaning_bond", "Rune meanings", "Bond",
     "Rare Monsters may transfer a modifier on death. The Remnant gains increased chance to spawn "
     "Rare Monsters, and Rare Monsters have more Monster Modifiers. This describes monster "
     "spawning/modifiers, not a direct acquired-item multiplier. Source: https://poe2db.tw/us/Bond_Rune"),
    ("rune_meaning_celestial", "Rune meanings", "Celestial",
     "Monsters gain chances for Fire, Cold and Lightning explosions on death. "
     "Source: https://poe2db.tw/us/Celestial_Rune"),
    ("rune_meaning_cold", "Rune meanings", "Cold",
     "Monsters gain extra Cold Damage. Source: https://poe2db.tw/us/Cold_Rune"),
    ("rune_meaning_cyclonic", "Rune meanings", "Cyclonic",
     "Monsters gain chance to inflict Exposure on hit, Armour Break on hit and Wither on hit. "
     "Source: https://poe2db.tw/us/Cyclonic_Rune"),
    ("rune_meaning_death", "Rune meanings", "Death",
     "Slain Monsters may merge into stronger Monsters. The tooltip does not specify the chance or "
     "merge-strength rule. Source: https://poe2db.tw/us/Death_Rune"),
    ("rune_meaning_earth", "Rune meanings", "Earth",
     "The Remnant conjures Earthly Spires. The tooltip does not specify their damage or mechanics. "
     "Source: https://poe2db.tw/us/Earth_Rune"),
    ("rune_meaning_electrocuting", "Rune meanings", "Electrocuting",
     "Monsters gain extra Lightning Damage, their Lightning Damage Electrocutes, and they leave "
     "Shocked Ground Trails. Source: https://poe2db.tw/us/Electrocuting_Rune"),
    ("rune_meaning_fire", "Rune meanings", "Fire",
     "Monsters gain extra Fire Damage. Source: https://poe2db.tw/us/Fire_Rune"),
    ("rune_meaning_life", "Rune meanings", "Life",
     "Monsters gain Shared Life. The sourced tooltip does not explain the sharing rule. "
     "Source: https://poe2db.tw/us/Life_Rune"),
    ("rune_meaning_lightning", "Rune meanings", "Lightning",
     "Monsters gain extra Lightning Damage. Source: https://poe2db.tw/us/Lightning_Rune"),
    ("rune_meaning_momentum", "Rune meanings", "Momentum",
     "Monsters gain increased Movement Speed, and their Movement Speed cannot be slowed below "
     "base. Source: https://poe2db.tw/us/Momentum_Rune"),
    ("rune_meaning_moon", "Rune meanings", "Moon",
     "The Remnant conjures moon beams. The tooltip does not specify their damage or mechanics. "
     "Source: https://poe2db.tw/us/Moon_Rune"),
    ("rune_meaning_oath", "Rune meanings", "Oath",
     "A Monster summons Allies. The tooltip does not specify which allies, their count or timing. "
     "Source: https://poe2db.tw/us/Oath_Rune"),
    ("rune_meaning_opulent", "Rune meanings", "Opulent",
     "Monsters gain increased Monster Rarity: increased chance for monsters to be Magic or Rare, "
     "and increased Monster Modifier Chance for Rare Monsters. Monster Rarity is distinct from "
     "Item Rarity. Source: https://poe2db.tw/us/Opulent_Rune ; "
     "keyword: https://poe2db.tw/us/Monster_Rarity"),
    ("rune_meaning_power", "Rune meanings", "Power",
     "Runes gain Empowered. The sourced tooltip does not explain the empowerment rule or amount. "
     "Do not substitute the unrelated same-named monster-minion Empowered modifier's numbers. "
     "Source: https://poe2db.tw/us/Power_Rune"),
    ("rune_meaning_prismatic", "Rune meanings", "Prismatic",
     "All Monster Damage can Shock, Chill and Ignite; Monsters also gain increased Elemental "
     "Resistances. Source: https://poe2db.tw/us/Prismatic_Rune"),
    ("rune_meaning_protective", "Rune meanings", "Protective",
     "Monsters periodically gain Verisium Proximity Shields. The sourced tooltip does not "
     "explain their precise rules or timing. Source: https://poe2db.tw/us/Protective_Rune"),
    ("rune_meaning_rage", "Rune meanings", "Rage",
     "Monsters periodically Enrage. The sourced tooltip does not specify Enrage bonuses or "
     "timing. Source: https://poe2db.tw/us/Rage_Rune"),
    ("rune_meaning_rebirth", "Rune meanings", "Rebirth",
     "Monsters gain chance to Rebirth on death. The sourced tooltip does not specify the "
     "probability or rebirth rules. Source: https://poe2db.tw/us/Rebirth_Rune"),
    ("rune_meaning_sky", "Rune meanings", "Sky",
     "The Remnant conjures Elemental Tornados. The tooltip does not specify their damage or "
     "mechanics. Source: https://poe2db.tw/us/Sky_Rune"),
    ("rune_meaning_soul", "Rune meanings", "Soul",
     "Monsters gain Union of Souls: slain pack members transfer their souls to other Monsters "
     "in their pack, increasing the recipients' Toughness and damage. See the separately sourced "
     "keyword defaults. Source: https://poe2db.tw/us/Soul_Rune"),
    ("rune_meaning_stone", "Rune meanings", "Stone",
     "Monsters gain Armoured, increased Stun Threshold and Earthly Prison. The sourced tooltip "
     "does not explain Earthly Prison's precise rules. Lowercase stone denotes this same rune. "
     "Source: https://poe2db.tw/us/Stone_Rune"),
    ("rune_meaning_tempest", "Rune meanings", "Tempest",
     "Monsters cannot be Shocked or Chilled; all their Damage contributes to chance to Shock "
     "and Chill Magnitude. Source: https://poe2db.tw/us/Tempest_Rune"),
    ("rune_meaning_tidal", "Rune meanings", "Tidal",
     "The Remnant conjures Tidal Waves. The tooltip does not specify their damage or mechanics. "
     "Source: https://poe2db.tw/us/Tidal_Rune"),
    ("rune_meaning_time", "Rune meanings", "Time",
     "Slain Monsters may respawn as a higher Rarity. This concerns monster rarity, not a direct "
     "item-rarity roll; probability and respawn rules are unspecified. "
     "Source: https://poe2db.tw/us/Time_Rune"),
    ("rune_meaning_toxic", "Rune meanings", "Toxic",
     "Monsters gain chance to Poison on hit and chance for Toxic Volatiles on death. All their "
     "Damage from Hits contributes to Poison Magnitude. Source: https://poe2db.tw/us/Toxic_Rune"),
    ("rune_meaning_vision", "Rune meanings", "Vision",
     "Monsters Reflect Curses and gain chance to Reflect Shock and Chill. "
     "Source: https://poe2db.tw/us/Vision_Rune"),
    ("rune_meaning_volcanic", "Rune meanings", "Volcanic / Gasp",
     "The local Gasp label denotes Volcanic. Monsters gain extra Fire Damage, all their Damage "
     "can Ignite, and they leave Ignited Ground Trails. The working source route uses Gasp_Rune "
     "but its tooltip title is Volcanic Rune. Source: https://poe2db.tw/us/Gasp_Rune"),
    ("rune_meaning_ward", "Rune meanings", "Ward",
     "Monsters are protected by Runic Ward, which absorbs damage that would otherwise be fatal; "
     "insufficient Ward can still allow death. Monsters with Runic Ward cannot be Culled. "
     "Source: https://poe2db.tw/us/Ward_Rune ; keyword: https://poe2db.tw/us/Runic_Ward"),
    ("rune_meaning_wisdom", "Rune meanings", "Wisdom",
     "Monsters gain Increased Experience. The sourced tooltip does not specify the amount or "
     "scaling rule. Source: https://poe2db.tw/us/Wisdom_Rune"),
    ("rune_keyword_scope", "Rune keywords", "Keyword defaults and source limits",
     "These supporting definitions explain terms in rune tooltips. Numerical values are general "
     "keyword defaults from current source pages, not captured rolls, individual rune strengths or "
     "historical game-version guarantees. They do not establish a final loot or rune-effect formula."),
    ("rune_keyword_adaptation", "Rune keywords", "Adaptation",
     "Taking Elemental Damage from Hits grants Adaptations that reduce damage taken from later "
     "Hits of that type. Unless otherwise specified, up to 3 Adaptations can coexist and they have "
     "no duration. Source: https://poe2db.tw/us/Adaptation"),
    ("rune_keyword_modifiers", "Rune keywords", "Monster Modifiers",
     "Magic and Rare Monsters have modifiers that make them more powerful and rewarding. By "
     "default Magic Monsters normally have one modifier and Rare Monsters have up to 4. Each "
     "modifier grants at least 100% increased Rarity of Items Dropped. Monster Modifier Chance "
     "increases the potential number of Rare Monster modifiers; each modifier above 4 requires "
     "twice as much Modifier Chance, and chance above the maximum increases the chance of having "
     "the maximum. Other mechanics can add modifiers to monsters of any rarity. These defaults "
     "do not reconstruct actual modifiers or drops from logged Rare kills. "
     "Source: https://poe2db.tw/us/Monster_Modifiers"),
    ("rune_keyword_remnant", "Rune keywords", "Verisium Remnant",
     "A Remnant has slots for inscribed runes and is first found with one rune already inscribed. "
     "The inscription enables reward selection; the recipe's required runes determine the "
     "Verisium-infused monster encounter used to complete it. Recipe rewards are distinct from "
     "observed inventory gains. Source: https://poe2db.tw/us/Verisium_Remnant"),
    ("rune_keyword_union", "Rune keywords", "Union of Souls",
     "Slain Monsters transfer their soul to another Monster in their pack, granting the recipient "
     "6% Toughness and 2% increased damage in the current keyword definition. These are keyword "
     "defaults, not measured Soul-rune strengths. Source: https://poe2db.tw/us/Union_of_Souls"),
    ("rune_keyword_armour", "Rune keywords", "Armoured / Armour",
     "Armour reduces damage taken from Hits and applies only to Physical Damage by default. "
     "Its reduction depends on hit size and is more effective against smaller hits. "
     "Source: https://poe2db.tw/us/Armour"),
    ("rune_keyword_damage_types", "Rune keywords", "All Damage",
     "Damage types are Physical, Fire, Cold, Lightning and Chaos. All Damage refers to these "
     "types, not a logged numeric total. Source: https://poe2db.tw/us/Damage_Types"),
    ("rune_keyword_contributes", "Rune keywords", "Damage contributing to Ailments",
     "An Ailment normally uses particular damage types in its calculations. Allowing another "
     "type to contribute sums the relevant types. For chance/buildup calculations this can let "
     "that damage inflict the Ailment; for magnitude-only calculations such as Bleeding/Poison "
     "a way to apply the Ailment is still needed (for example, Bleeding or Poison chance). "
     "Source: https://poe2db.tw/us/Damage_Contributing_to_Ailments"),
    ("rune_keyword_shock", "Rune keywords", "Shock",
     "Shock causes 20% increased damage taken by default and lasts 4 seconds on players or "
     "8 seconds on non-players. Lightning Hit Damage contributes to chance to Shock; by default "
     "the chance is 1% per 4% of the target's Ailment Threshold dealt. "
     "Source: https://poe2db.tw/us/Shock"),
    ("rune_keyword_chill", "Rune keywords", "Chill",
     "Chill slows a target and lasts 2 seconds on players or 8 seconds on non-players by default. "
     "Magnitude depends on Chilling Damage relative to Elemental Ailment Threshold, with a "
     "30% minimum and 50% default maximum. Cold Hits can Chill without application chance, "
     "but Chills below the minimum are ignored. Source: https://poe2db.tw/us/Chill"),
    ("rune_keyword_hits", "Rune keywords", "Hits",
     "Damage other than damage over time is Hit Damage. Damaging Ailments derive their damage "
     "from the originating Hit; modifiers to damage dealt are not then reapplied directly to "
     "the Ailment. Source: https://poe2db.tw/us/Hit_Damage"),
    ("rune_keyword_poison", "Rune keywords", "Poison",
     "Poison deals Chaos Damage over time, bypasses Energy Shield and lasts 2 seconds by default. "
     "Physical and Chaos Hit Damage contribute to magnitude, but application requires Poison "
     "chance. Default damage per second is 20% of the originating Hit's pre-mitigation Physical "
     "and Chaos Damage; damage-dealt modifiers are not reapplied. Enemy mitigation/debuffs can "
     "affect damage taken, while hit-only modifiers such as Penetration do not apply to Poison. "
     "Source: https://poe2db.tw/us/Poison"),
    ("rune_keyword_monster_rarity", "Rune keywords", "Monster Rarity",
     "Monster Rarity increases the chance for monsters to be Rare and Magic, and also increases "
     "Monster Modifier Chance for Rare Monsters. Each 1% increased Monster Rarity grants 1% increased "
     "Rare Monster chance and 1% increased Magic Monster chance: 50% Monster Rarity grants 50% to each "
     "of those bonuses. These are increased-chance bonuses, not percentage points of the observed "
     "kill distribution. Monster Rarity is distinct from Rarity of Items Found and "
     "from the measured proportion of Rare kills. Source: https://poe2db.tw/us/Monster_Rarity"),
    ("gaining_traction", "Map analysis", "Gaining Traction: Monster Rarity per completed remnant",
     "With Gaining Traction allocated, Verisium Remnants have +50% increased Monster Rarity per "
     "remnant completed in the area (the same map). After N completed remnants, its contribution "
     "is +(50 x N)% Monster Rarity, giving the same +(50 x N)% increased Rare Monster chance and "
     "increased Magic Monster chance for those remnants. "
     "For example, one completed remnant gives +50% to each chance bonus; two give +100% to each. "
     "The bonus accumulates across remnant chains on the same map and starts again on the next map. "
     "It depends on completed remnants, not recipe rows, scanned rewards, or the number of chains. "
     "Use saved Atlas allocation and completion-stage evidence when analysing it; a whole-map kill "
     "reading includes earlier stages and does not give each monster the final accumulated bonus. "
     "Do not add this contribution again where a recorded Monster Rarity value already includes it. "
     "These notes explain the mechanic without changing saved percentages, kill counts or game state. "
     "Source: bundled Atlas node AtlasExpeditionNotable12 (Gaining Traction), user-provided Monster "
     "Rarity scaling clarification, and Monster Rarity keyword: "
     "https://poe2db.tw/us/Monster_Rarity"),
    ("rune_keyword_runic_ward", "Rune keywords", "Runic Ward",
     "When damage would reduce Life to zero, Runic Ward leaves 1 Life and absorbs the remaining "
     "damage; insufficient Ward still allows death. It does not protect against Life loss that "
     "is not damage. The current keyword default regeneration is 5% per second. Monsters can "
     "also have Runic Ward and cannot be Culled while they have it. "
     "Source: https://poe2db.tw/us/Runic_Ward"),
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
    ("sql_context", "SQLite schema", "analysis_context / analysis_context_complete",
     "analysis_context has key (primary key), position (reading order), section, topic, guidance and context_version. "
     "analysis_context retains the static guide. Read SELECT section,topic,guidance,context_version "
     "FROM analysis_context_complete ORDER BY position,key for exactly the Analysis Context worksheet's "
     "static and data-specific rows. Context views calculate facts from the same saved SQLite snapshot "
     "without writes or user-defined SQL functions; they also work in a standalone database backup. "
     "Notes and translation rules are application-owned metadata, independent of session counters and Undo."),
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
    ("sql_tablet_sets", "SQLite schema", "tablet_sets and selected map settings",
     "tablet_sets is a reusable library keyed by set_id, with name, config_json and next_slot. Each named set "
     "keeps its own tablet slots, capacity, raw text, affix values and next scan slot. meta.selected_tablet_set_id "
     "selects the editor's set; it is not evidence that every saved set was used. Only the selected saved set's "
     "tablet_set_id, tablet_set_name and tablet configuration are frozen into the completed map snapshot. "
     "Library-only Tablet config commits have blank Map ID/Expedition ID and details.scope=tablet_library; "
     "do not count those edits as maps, map observations or uses of a tablet."),
    ("sql_ritual_rites", "SQLite schema", "ritual_rites / ritual_rite_map_settings / ritual_rite_completions",
     "ritual_rites stores named Rite identities; meta.selected_ritual_rite_id is an optional selection. "
     "ritual_rite_map_settings stores each map's editable affixes_json. ritual_rite_completions records "
     "completed map_id, map_number, rite_id, ordinal, snapshot_json and completed_at. Saved map/event "
     "snapshots retain ritual_rite_id, ritual_rite_name, ritual_rite_map_ordinal, ritual_rite_affixes and "
     "ritual_rite_inherited_sources. Read their frozen values rather than substituting the current editor. "
     "Rites apply only to maps whose Activity Type is Ritual: other maps carry no Rite fields, take no "
     "Rite stage and inherit nothing, even while a Rite stays selected. Rite affixes can be entered "
     "manually or read from a hovered Atlas map tooltip; unknown tooltip wording is saved as a custom "
     "affix of its Foretold kind. "
     "Each inherited source retains origin_map_id, origin_rite_id, origin_map_ordinal, origin_affix_position "
     "and affix_id, plus the source affix and occurrence count."),
    ("sql_scans", "SQLite schema", "scans / scan_links / seed_states",
     "scans stores reviewed seed screenshot metadata by id, including sockets, seed_slot, seed_rune, "
     "family text, rewards_json and mapping_status. scan_links maps remnant_id to scan_id; join scan_id to scans.id. "
     "image_name/file_name refer to image files and are not image bytes included in a database-only export. "
     "seed_states is a reference table keyed by (family,sockets), containing seed_slot, seed_rune, "
     "rewards_json and status; it describes catalog stages, not logged events."),
    ("sql_item_catalogs", "SQLite schema", "Item catalogs and recognition references",
     "currency_items, item_names and ritual_names register Currency, Item and Omen names. Their rows are "
     "reference labels, not acquired quantities. unique_item_catalog stores name and source_revision for "
     "all 441 bundled unique names before observation, matching the named CSV/XLSX export columns. "
     "Existing Currency/Omen category registrations retain their labels. Catalog metadata does not create "
     "approved observations or register every unique on the Currency page. currency_icons, item_icons, "
     "omen_icons and review_icon_examples "
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
    ("context_records", "Context records", "How to read the data-specific context",
     "After the Field guide, Guidance cells in Atlas setup/node/choice/allocation/stat-total, Atlas master perk, "
     "Map settings, Tablet stat total and Session stat total sections contain JSON objects. Parse their named "
     "properties; do not treat Topic strings as measured values. SQL NULL becomes JSON null, retaining "
     "missingness; zero remains numeric zero. Each row explains one definition, setup, contribution or "
     "scoped total. Definitions for all available options are not allocations. Archived unused setups are "
     "excluded; a current unused setup can have referenced_map_count 0. Atlas map reference rows link each "
     "setup to saved map/event references; a map with several historical references is still one map."),
    ("atlas_decode", "Atlas interpretation", "Decode an Atlas Setup ID without external files",
     "For an Export or Scan History Atlas Setup ID, find its Atlas setup Context row to get catalog_id, "
     "catalog_version and recorded gear_item_rarity. Atlas allocation rows enumerate ALL allocated nodes, "
     "including nodes omitted from the compact Atlas worksheet. Match catalog_id + node_id to Atlas node "
     "definition rows for the frozen name/activity/effects/stats. Match catalog_id + node_id + choice_number "
     "to Atlas choice definition rows to translate a worksheet ordinal into choice_id/name/effects/stats. "
     "Atlas choice rows show remembered selections; only active=true contributes to totals. Ordinal 0 "
     "means no choice and a blank means unavailable. Never apply an option merely because its definition "
     "appears here. Use stable node IDs and the setup's frozen catalog instead of a possibly newer worksheet "
     "header label. The A-prefixed setup ID hashes catalog version/identity, allocation, choices and gear "
     "rarity; it does NOT identify the active Atlas Master, master perks, Waystone or tablets. Those settings "
     "belong to the relevant saved map/event, even when the Atlas Setup ID is unchanged."),
    ("atlas_stat_totals", "Atlas interpretation", "Atlas contribution totals and displayed units",
     "For each setup and exact stat_id, raw_total sums the numeric constants from allocated nodes plus "
     "active selected choices, once per source. Dummy display-only stats are omitted. display_total and "
     "effect are supplied only when the frozen source hashes match a verified single-stat linear display "
     "rule; unit is then the catalog's displayed unit. This handles native scale differences: a permyriad "
     "raw value 2000 can display 20%, and raw 100 for a per-room chance can display 1%. Unsupported, coupled, "
     "constant/flag and offset rules retain raw_total with display_total/effect null; consult their node/choice "
     "effects instead of assuming raw 1 means 1%. These are configuration contributions, not final applied "
     "bonuses. Keep distinct stat IDs, targets, conditions, increased/more/reduced wording and caps separate. "
     "A percentage per closed Pit/tablet/monster modifier is a coefficient, not an already evaluated bonus. "
     "Preserved cap text does not mean a cap or conditional trigger was calculated. Do not add repeated "
     "setup totals once for every recipe/history row or add them to displayed map percentages automatically."),
    ("master_perk_effects", "Atlas interpretation", "Every Atlas Master perk and its function",
     "Atlas master perk Context rows provide each configured reference's master, name, tier and full effect "
     "text. Join the saved Atlas Master and selected Perk 1..4, or Map settings master/perks, to this dictionary "
     "using BOTH master and name. Only the active master's selected perks apply to that observation; named "
     "Configured Perk columns for inactive masters are remembered settings. A definition row or perk tier "
     "does not mean allocated, active, a multiplier or an extra event. Effects include their actual scope "
     "and conditions; do not apply every listed perk to every map. The dictionary is the editable master_perks "
     "reference at export, not a frozen record of historical effect edits. If a recorded name has no definition "
     "or the references were changed, keep its historical effect unresolved instead of substituting another perk. "
     "The logger specifically calculates Master +Mods=1 for active Jado with Unexpected Missions, else 0; "
     "the other effect text is explanatory metadata rather than an implemented game simulation."),
    ("source_subtotals", "Stat interpretation", "Map, tablet, Atlas and gear contributions",
     "Map settings Context rows reproduce each created map's saved numeric settings and derived counts, "
     "not current live settings. Item rarity concerns loot rarity, monster rarity concerns monster rarity "
     "categories, pack size concerns monsters per pack, and effectiveness is the saved Effectiveness "
     "property. These modifiers are distinct from observed quantities and category kills. The export "
     "does not establish the game's probability, stacking or reward-scaling formulas. Tablet stat total "
     "sums active slots only, grouped by Map ID + exact affix + unit; inactive capacity slots, raw text and "
     "legacy percent duplicates are not counted. Percent/count/seconds stay separate, and flag totals "
     "count occurrences rather than multiplying an effect. The established modifier-count formulas are "
     "Map Mods=Base Map Mods+Master +Mods; Tablet Mods=sum(active additional-random-modifier counts); "
     "Total Mods=Map Mods+Tablet Mods. Keep other tablet subtotals, Atlas totals, gear rarity and observed "
     "map percentages as separate source groups. A displayed map property can already reflect game "
     "modifiers; adding its sources again could double-count. There is no asserted final combined rarity, "
     "effectiveness or pack-size formula here."),
    ("session_stat_totals", "Stat interpretation", "Session sums, coverage and averages",
     "Session stat total rows summarize one frozen setting/current count per CREATED Map ID, not every "
     "recipe, remnant, inventory approval or history row. total/mean/minimum/maximum exclude missing "
     "numeric values; known_maps and missing_maps show coverage, and an entirely missing metric has "
     "total null rather than a fabricated zero. Percentage totals sum percentage-point observations "
     "ACROSS maps for bookkeeping; they are not a map bonus. Use per-map values or means for statistical "
     "comparisons. known_kills_total includes only known categories and can be partial; check Map settings "
     "kill_categories_complete and each category's coverage. Detonation counts use each current expedition "
     "once, retaining real totals after a chain discard. These summaries do not create acquired-item totals: "
     "those remain the once-per-map Currency gains and Item latest-inventory counts in Export under the "
     "Inventory rules. Queued "
     "inventory-only IDs before map creation do not increase the created-map denominator. Legacy empty "
     "map snapshots are explicitly marked unavailable; use their Export fields where recoverable rather "
     "than assigning present-day settings."),
    ("atlas_city_biome", "Atlas", "City biome classification and automatic selection",
     "Allocated Atlas city keystones can classify cities as an additional biome. These effects say "
     "'also considered' and do not claim to physically transform a map's terrain. The logger fills "
     "its existing Biome selection when a saved City Type or Atlas change has one matching active "
     "effect: Faridun can count as Desert or Mountain, Ezomyte as Grass or Swamp, and Vaal as Forest "
     "or Water. Resolve exact map_city_<city>_also_count_as_<biome> flags in the setup's frozen "
     "catalog; the node must be allocated and its saved choice active. Chance/weight bonuses, "
     "unallocated nodes, unanswered choices, missing definitions and conflicting classifications "
     "do not choose a biome. With no single applicable effect the manual value stays in place. "
     "Saving a manual Biome with an unchanged City Type remains supported. An active map's frozen "
     "Atlas, including one bound by a pre-map Start inventory, governs that map; new Atlas settings "
     "apply to the next map when its setup is already frozen. Historical snapshots are not rewritten. "
     "Biome is the saved analysis category, not proof of physical terrain, acquired loot or numerical "
     "bonus strength. The field alone does not identify whether the value was typed or automatically filled."),
    ("reference_sheets", "Workbook", "Included family and recipe databases",
     "Family Database, Recipe Database, Seed Database and Reference Aliases copy the current editable "
     "references on the same read snapshot as the logged data and Context. They are reference tables, "
     "not additional scans, maps, detections or acquired rewards. Family Database has one row per family "
     "membership in its recorded list order; an empty family gets one explicit Empty family row. A recipe "
     "shared by several families has several membership rows but one Recipe Database identity. Recipe "
     "Database contains the complete current catalog plus retained recipe-ID registry entries. A retired "
     "entry preserves its ID/name with blank combo/socket fields and Retired recipe ID status; do not "
     "invent its missing combo or attribute an unknown logged origin to a newly added reference. "
     "These are catalog definitions at export, not frozen copies of every historical combo revision. "
     "Logged recipe/family IDs stay frozen, while editable reference meanings can change. Raw SQLite "
     "exports already include recipes, recipe_ids, families, seed_states and aliases as actual tables."),
    ("reference_joins", "Propagation", "Workbook-only chain reconstruction",
     "Select current surviving chain rows from Export, not repeated Propagation/Chain append/completion/"
     "correction rows in Scan History. Group by Expedition ID and order by Chain Step #; gaps are valid. "
     "For step N, resolve its nonblank Propagation Recipe ID in Recipe Database, and check the matching "
     "Family Database membership using Propagation Family ID when its status is known. Recipe # In Family "
     "is the current one-based catalog position, not Recipe ID, Chain Step # or a logged occurrence count. "
     "Split Exact Rune Combo on '+' and trim each token, retaining order and repeated runes. Effective rune list at N "
     "is approved marked runes from EARLIER surviving steps plus this step's full recipe combo. Append only "
     "this step's marked Propagation Rune 1/2 for the next step; do not carry its whole recipe combo or add "
     "the current marked runes twice. Reset accumulation for each Expedition ID. Missing/retired combo, "
     "unknown origin or inconsistent known family membership stays explicitly unresolved. Keep known "
     "prior runes without pretending the entire effective list is known. This reconstructs rune occurrences, "
     "not numerical rune-effect strengths, stacking, caps or independently attributed loot drops."),
    ("reference_seed_aliases", "Propagation", "Seed stages and aliases",
     "Seed Database is one row per (Family ID, Socket Count) with reviewed reference Visible Seed Slot/Rune, "
     "Rewards (JSON) and Mapping Status. Parse Rewards as an ordered JSON array of labels, not quantities "
     "of acquired loot; use its exact canonical recipe name in Recipe Database where available. These rows "
     "describe possible catalog stages, not actual logged seed occurrences. Reference Aliases preserves "
     "each raw alias position, Alias, Matched Recipe target and registry ID if present. It describes label "
     "resolution, not family attribution or an acquired item. Duplicate/conflicting aliases are not a "
     "license to select the first target, and a logged matched recipe/ID takes precedence over trying to "
     "redo recognition from Recipe Input. Reference Status distinguishes unavailable catalog definitions "
     "instead of silently supplying an unrelated current recipe."),
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
    ("field_reference_ids", r"Recipe ID|Recipe # In Family|Alias Position", "Reference identity and list positions",
     "Recipe ID in the reference sheets is the same stable RCP- identity used by Propagation Recipe ID "
     "in the log. Recipe # In Family is one-based membership order in the current Family Database copy; "
     "it can change after catalog edits and is not a stable recipe identity or game count. Alias Position "
     "is the saved alias-table position, not a recipe ordinal or recognition-confidence score. Never sum "
     "these IDs/positions as activity or strength."),
    ("field_reference_meta", r"Top Socket Count|Family Valid|Level Band|Category|Reference Source|Reference Status", "Reference catalog attributes",
     "Top Socket Count is the family's recorded maximum reference stage, not the current remnant's socket "
     "count. Family Valid is the catalog's Yes/No flag, not recognition confidence or proof a drop occurred. "
     "Level Band, Category and Reference Source describe the recipe reference. Reference Status distinguishes "
     "current definitions, empty families, missing registry IDs and retired/missing recipe definitions. "
     "These are descriptive reference rows, not measured gameplay outcomes; unresolved definitions remain "
     "blank rather than receiving inferred rune combos."),
    ("field_reference_seed", r"Rewards \(JSON\)|Mapping Status|Alias", "Reference seed rewards and input aliases",
     "Rewards (JSON) is the seed-stage reference's ordered reward-label array, not an inventory or drop "
     "count. Mapping Status is the seed reference's recorded status. Alias is a raw recognition/input "
     "mapping to Matched Recipe in Reference Aliases; preserve conflicting mappings and use logged "
     "canonical IDs rather than treating aliases as new observed recipes or families."),
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
     "Each 1% Monster Rarity grants 1% increased Rare Monster chance and 1% increased Magic Monster "
     "chance, so 50% grants 50% to each bonus. With Gaining Traction allocated, Verisium Remnants gain "
     "+50% Monster Rarity per remnant completed on that map; see the Gaining Traction stage guidance before comparing "
     "readings or combining bonuses. "
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
     "absence of every related mechanic. Keep selected None separate from missing imported data. "
     "Biome can be automatically filled from one applicable allocated Atlas city-classification effect; "
     "see City biome classification and automatic selection. Its saved value does not establish physical "
     "terrain conversion or independently establish every conditional Atlas bonus."),
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
     "not a normalized total bonus, and individual unit/stacking meanings must be preserved. "
     "Hotkey captures waiting for new-affix registration retain their original image and context "
     "for retry before review; waiting does not save a new map setup."),
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
     "within compatible catalogs and use saved node effects/stats for game-effect meaning. The Context's "
     "Atlas allocation and Atlas node definition rows explain omitted allocations and frozen effects."),
    ("field_atlas_choices", r"Atlas Choice: .+", "Atlas choice selection",
     "An Atlas Choice number is the one-based option order in that node's frozen catalog, with 0 for "
     "no selection and blank for unavailable/not applicable. SQL stores option IDs rather than these "
     "display ordinals. Atlas choice definition Context rows give the corresponding option ID, name, "
     "effect and stats for each ordinal in each frozen catalog. A remembered choice can belong to an "
     "unallocated node and is then not active. "
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
     "Currency Start/End gains or Item latest-inventory counts; repeated rescans and offers are separate evidence, not "
     "quantities to add into current totals."),
    ("field_inventory_counts", r"Start Count|End Count|Net Change|Session Found Quantity", "Inventory counts and gains",
     "Start Count and End Count describe their approved inventory phases. Currency Net Change is signed "
     "End minus Start; Item Net Change records the approval's quantity for item_accounting=latest_scan. "
     "Unmarked legacy records retain their original rules. Currency Session Found Quantity credits "
     "max(0, latest End minus latest Start) only to the "
     "current End history approval. Item Session Found Quantity credits the latest approved inventory scan "
     "across Start and End, without subtraction. Superseded approvals receive zero current credit. Compare "
     "one current contribution per map and label, not every historical Quantity or negative spending as a "
     "drop count. Currency assumes an empty missing Start and needs an approved End; Items need an approved "
     "inventory scan in either phase."),
    ("field_inventory_status", r"Inventory Phase|Start Baseline|Current Inventory Snapshot|Currency Scan Status", "Inventory availability and selection",
     "Inventory Phase is Start or End; Start Baseline distinguishes Scanned from Assumed empty. "
     "Current Inventory Snapshot marks the latest saved phase approval, not recognition confidence. "
     "Item current credit instead selects the latest approved scan across both phases. "
     "Currency Scan Status describes which approved phases exist for the map. Use these to select "
     "comparable complete maps and distinguish unscanned End, explicit empty End and assumed baseline."),
    ("field_currency_scan_id", r"(?:(?:Start|End) )?Currency Scan ID", "Currency stage identity",
     "Currency Scan ID identifies one approved inventory scan within its Map ID, independently of "
     "Expedition ID and the session-wide Scan Commit #. Repeated item rows with the same ID belong "
     "to one scan. Start/End Currency Scan ID identifies the current saved phase. Blank is unavailable "
     "or not a Currency event, not an additional scan."),
    ("field_currency_scan_number", r"(?:(?:Start|End) )?Currency Scan #", "Currency stage order",
     "Currency Scan # orders approved Currency scans within the map. It is not an item quantity or "
     "a global commit number. Start/End prefixes identify the current saved phase; do not sum scan "
     "numbers or stage quantities to calculate a map total."),
    ("field_currency_scan_final", r"Final Currency Scan", "Current final currency approval",
     "Final Currency Scan marks the latest approved End Currency scan for the map at export time. "
     "Earlier approvals remain in history and are not additional currency gains. This is a current "
     "selection flag, not OCR confidence or proof that the user actually ended the map then."),
    ("field_item_columns", r"(?:Item|Currency|Omen): .+", "Named acquired-item columns",
     "Named columns in Export contain current Currency gains or Item latest-inventory counts once per map. "
     "All 441 bundled unique names have named export columns before observation, retaining existing "
     "Currency/Omen registrations where applicable. Columns and catalog metadata are not approved "
     "observations or Currency-page registrations. "
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
     "for each non-deferred occurrence and zero for each deferred occurrence, including unique Items "
     "under the Omen rule. A same-hash page correction replaces that current page; different pages remain "
     "independent. Neither field contributes to inventory "
     "gains or proves a purchase. Compare deferred versus other offers as offer populations, and "
     "use approved inventory scans and the category's counting rule for inventory analysis."),
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

# These additive notes describe saved map metadata without changing earlier rune definitions.
ENTRIES += (
    ("tablet_set_context", "Map analysis", "Independent named tablet sets",
     "A saved tablet set is a reusable configuration with a stable ID and editable name. Compare maps using "
     "the selected set and tablet values frozen in that map's snapshot; later set selection, renaming or "
     "editing does not revise a completed map. The library can contain unused sets. Never merge all saved "
     "sets into one map or assign blank-Map-ID library history to a nearby gameplay event. A set ID or "
     "name alone does not establish the exact affix revision; retain the saved slot values and units."),
    ("ritual_rite_context", "Ritual", "Optional Rite metadata and proliferation origins",
     "Rite metadata records the selected Rite, map ordinal, that map's chosen affixes and inherited source "
     "observations. A missing historical Rite selection remains unknown. Only Foretold Proliferation on "
     "completed earlier maps in the same selected Rite supplies effects to remaining maps in that Rite; "
     "ordinary local affixes and Foretold Bounty do not proliferate. Each source's own proliferation choices "
     "are carried once with origin map/Rite/affix IDs; inherited entries do not become new origins. Keep "
     "separate occurrences and source IDs instead of inventing a combined bonus, monster density or "
     "stacking formula. The bundled catalog is the published 73-row pool verified 2026-10-19, including "
     "three explicitly untranslated raw rows; it does not prove live eligibility or roll weights. "
     "rite:* and logger:rite:* are internal application identifiers, and custom:rite:* denotes unverified "
     "typed wording. Sources: https://poe2db.tw/us/Rite_of_the_Nameless ; "
     "https://www.vaal.tools/mechanics/foretold_proliferation/ ; https://poe2ref.com/mechanics/ritual"),
    ("prepared_reference_cache", "Recognition", "Prepared references are a performance cache",
     "Prepared recognition references reuse work for an unchanged reference revision. Cache reuse is a "
     "performance optimization; it does not change matching thresholds, confidence gates, accepted labels "
     "or scan ownership. Reference edits require refreshed prepared data. A cache hit is not a saved scan, "
     "an approval, an accuracy measurement or an additional observation."),
    ("scan_hold_scope", "Recognition", "One waiting remnant-to-propagation request",
     "Hold/defer applies only to the accepted remnant-to-propagation sequence, with at most one waiting "
     "request bound to its original capture and map/expedition context. It does not approve that request "
     "or create a general scan queue. Ritual and Currency do not accumulate a waiting-capture backlog. "
     "Use saved approval commits for recorded observations rather than counting held requests."),
    ("map_activity_context", "Map analysis", "One selected activity per map",
     "Activity Type is one user-selected category: None, Breach, Expedition, Grand Expedition, Abyss or Ritual. "
     "It is not an automatically detected encounter, an encounter count or proof that other mechanics were absent. "
     "Keep Expedition and Grand Expedition distinct. Selected None is different from unavailable historical "
     "data, which stays blank/null. Use the saved Map ID's activity, City Type and Biome together when comparing "
     "Atlas configuration contributions or map-level outcomes. Current character settings do not replace "
     "these saved map and event values."),
    ("map_setup_context", "Map analysis", "Map setup availability",
     "Map Scanned is Yes when the current map's waystone setup was saved through manual map stats or an applied "
     "waystone scan, and No when its setup was explicitly unsaved. The label does not mean OCR was used: "
     "manual saved stats also produce Yes. Other scan modes do not establish this status, and a pending or "
     "rejected waystone scan is not an applied setup. Missing historical waystone_setup_saved stays blank/null; "
     "do not infer Yes from default tier values, inventory scans or a saved remnant. New maps clear this status, "
     "while Undo restores the saved workflow state."),
    ("map_atlas_effect_context", "Atlas interpretation", "Compact Atlas effects for the saved map context",
     "Atlas Effects JSON is the frozen atlas_effect_snapshot from that map or event, rather than a new copy of "
     "the full tree. Parse its schema_version, atlas_setup_id, catalog_id/catalog_version, activity_type, "
     "biome, city_type, ocean, deli and effective_biomes; a saved Ocean Map option can supply Ocean classification "
     "independently of the Biome selection. Saved Deli=Yes includes allocated Delirium tree coefficients "
     "alongside the selected Activity tree; their catalog conditions still apply. Older aggregates without "
     "deli may omit those coefficients; consult the full frozen allocation. totals keeps stat_id, raw_total, display_total, unit and "
     "conditions for allocated minor/notable nodes and active selected "
     "choices applicable to that saved city/biome/activity context. The complete setup and definitions remain "
     "in Atlas Character Settings and the Context/SQL Atlas references. A conditional coefficient remains "
     "conditional: a per-monster, per-tablet or per-closed-Pit bonus is not multiplied by an unrecorded trigger. "
     "Condition tags retain unevaluated requirements: runtime_count needs the recorded trigger count, "
     "catalog_trigger needs the source's stated condition, catalog_target preserves the catalog's target scope, "
     "area_state_unrecorded lacks the required area state, "
     "biome_unselected:<biome> lacks a saved biome classification, and vendor_unlock is an unlock rather than "
     "a map bonus. unresolved identifies missing nodes, unanswered choices or semantic flags by source ID "
     "and reason. Recover full meanings from the frozen catalog/Context definitions using setup/catalog IDs "
     "and source stat_id; these descriptions are not duplicated inside each map object. In schema_version 2, "
     "known applicable general, biome and city bonuses for the same metric combine into one total only "
     "when pinned source translations verify equal units and signed raw scales. For example, applicable "
     "general and Desert Pack Size bonuses share map_pack_size_+%; Magic Pack Size remains a different metric. "
     "source_stat_ids lists the exact contributing identities for a merged metric: use these to resolve its "
     "wording even when the normalized stat_id has no catalog entry. Missing source_stat_ids means stat_id "
     "is the sole source identity. These are numeric sums, with no repeated node breakdown. Unknown biome, "
     "runtime and trigger requirements do not merge into unconditional totals. Earlier schema_version 1 "
     "retains exact-ID totals and may include contribution_count; it does not combine equivalent area IDs. "
     "To keep the object compact, absent display_total/unit means unknown and absent conditions means an "
     "empty list. Never treat unresolved evidence as zero or an unconditional bonus. Native raw units remain "
     "distinct from verified display units. "
     "These totals are Atlas configuration contributions, not observed map properties or a simulated final "
     "game multiplier; never add them again to Item Rarity %, Monster Rarity %, Pack Size % or Effectiveness % "
     "without a separately established stacking rule. Export carries this object only once per Map ID; "
     "Scan History retains each event's own saved object, and SQL preserves it in snapshot_json. Missing "
     "historical snapshots remain blank/null instead of being reconstructed from today's Atlas."),
)

COLUMN_GUIDANCE += (
    ("field_ritual_boundary_count", r"Ritual Reading Count", "Approved Ritual readings per map",
     "Number of surviving approved Ritual commits for this Map ID, including rescans of the same reward page. "
     "Zero means no saved Ritual reading; waiting or rejected reviews are excluded. This is not a count of "
     "remaining rerolls, unique pages, purchased rewards or Ritual encounters."),
    ("field_ritual_boundary_resources", r"Ritual (?:First|Final) (?:Tribute Available|Rerolls Remaining)",
     "First and final approved Ritual resource readings",
     "First uses the earliest approved Ritual commit for this Map ID; Final uses the latest, ordered by "
     "commit number rather than page number. Values are displayed resource snapshots, not cumulative gains. "
     "Zero is an explicit reading; blank means this boundary's field was unread or no reading was saved. "
     "Keep both fields from the same boundary even when an interior reading has a known value."),
    ("field_ritual_boundary_commits", r"Ritual (?:First|Final) Commit #", "Ritual boundary audit links",
     "The saved commit number supplying the first/final approved Ritual resource reading for this Map ID. "
     "Join commits.number or Scan History's Scan Commit #; a blank link means no saved Ritual reading. "
     "These are not reward-page numbers and page replacements do not change their chronological meaning."),
    ("field_activity_type", r"Activity Type", "Selected map activity",
     "One saved activity category per map: None, Breach, Expedition, Grand Expedition, Abyss or Ritual. "
     "This is a user selection, not an encounter count or detection. Group outcomes by Map ID and activity "
     "with biome/city and compatible Atlas settings; selected None and missing historical data differ."),
    ("field_map_scanned", r"Map Scanned", "Saved manual or scanned map setup",
     "Yes means saved manual map stats or an applied waystone scan; No means explicitly unsaved setup. "
     "Other scan modes and unapplied pending scans do not establish Yes. Blank is unavailable historical "
     "state. This reports setup availability, not OCR confidence or completeness of every logged metric."),
    ("field_atlas_effect_snapshot", r"Atlas Effects JSON", "Atlas contributions scoped to the saved map",
     "Parse the frozen JSON object by named fields; it retains setup/catalog identity, saved activity/city/"
     "biome context, combined applicable stat totals, units, condition tags and unresolved source IDs/reasons. "
     "Schema 2 merges verified equivalent area stats and lists their source_stat_ids; schema 1 retains "
     "exact-ID totals. Different targets and unknown requirements remain separate. Join frozen "
     "Context/catalog definitions for full meanings. Count it once "
     "per Map ID in Export, not once per recipe or repeated history event. Conditional coefficients remain "
     "unevaluated without their triggers; do not add configuration contributions to already recorded map "
     "percentages automatically. Blank means that historical snapshot is unavailable."),
)

# Screenshot observations: every reading is kept, and the latest one sets the map's kill totals.
ENTRIES += (
    ("kill_count_readings", "Kill counts", "Kill Count Readings: screenshot observations",
     "Kill Count Readings is an independent sequence of screenshot observations, with one row per saved "
     "Kill Snapshot ID. Normal kills correspond to the white skull, Magic to the purple/blue skull, Rare "
     "to the yellow skull and Unique to the orange skull. The four counts are the cumulative numbers "
     "visible at capture time, not kills gained since the previous reading. Total Kills (Observed) is "
     "the sum of those four categories within that single reading. Never sum rows, scopes or repeated "
     "readings to calculate map kills. An image alone does not establish when the game counters reset "
     "or whether the observation covers the whole map. Each saved reading also becomes the map's kill "
     "totals (the Map Kills fields, Maps and Export kill columns) until a later reading or a manual save "
     "replaces them; one Undo removes the reading and restores the previous totals."),
    ("kill_count_scopes", "Kill counts", "Optional chain links and final map readings",
     "Current screenshot capture uses only the dedicated kill-count key, has map-only ownership, and records "
     "map_final as a compatibility scope label for each cumulative snapshot; Currency, "
     "General and other scan keys do not capture kill counts. Historical Kill Count Scope can be "
     "chain_stage for a reading captured with an active remnant chain, map_stage for "
     "a reading without that link, or map_final for a dedicated final-map kill-count scan. Expedition ID "
     "is optional and is present only for chain_stage; no chain is required to save map_stage or map_final. "
     "A chain link describes capture context and does not prove the observed kills came solely from "
     "that chain. These are not chain-only deltas or kills per remnant. A map_final row is the observed "
     "reading requested through the final-map scan path; it represents the actual end of the map only "
     "if the user captures it then. The app does not obtain an independent game-computed final total. "
     "Multiple readings per map are allowed. Current Map Kill Reading selects the latest surviving "
     "approval by Scan Commit # for that Map ID, independently of expedition. Counts of 10 then 20 "
     "mean 20 observed kills, not 30. Select the latest row, not the largest count or a sum of readings. "
     "The map's kill totals hold the latest reading or manual save, whichever came last; this sheet keeps every reading."),
    ("kill_count_provenance", "Kill counts", "Capture provenance and missing readings",
     "Each saved observation retains its Map ID, optional Expedition ID, stable Kill Snapshot ID, "
     "request identity, source mode, capture/recording times, image hash and JSON recognition/capture "
     "context. Use that frozen context rather than current live settings. Only complete valid OCR "
     "readings are automatically persisted. A missing, incomplete or unreadable scan is unknown, not "
     "zero, and does not create a zero observation. Explicitly recognized zero counts remain zero. "
     "OCR evidence supports auditing but is not ground truth. Saving does not approve a propagation "
     "draft or create a chain. Undo/session reset can remove these observations; they are surviving "
     "session records rather than an immutable audit archive."),
    ("sql_kill_count_readings", "SQLite schema", "kill_count_snapshots",
     "kill_count_snapshots stores one surviving observed reading per snapshot_id, with a unique "
     "request_id, captured session generation, map_id, optional expedition_id, scope, normal, magic, "
     "rare and unique_kills integer counts, scan_commit_number, source_mode, captured_at and recorded_at. "
     "image_sha256 identifies image_png evidence when supplied. ocr_json retains the original result "
     "and context_json retains the captured map/session ownership; payload_sha256 checks duplicate "
     "request consistency. Join scan_commit_number to commits.number for the saved event and its "
     "frozen map configuration. This table is separate from maps.kills_json and map_unique_kills; "
     "it neither replaces manual totals nor stores per-interval gains."),
)
COLUMN_GUIDANCE += (
    ("field_tablet_set", r"Tablet Set ID|Tablet Set Name", "Saved tablet set identity",
     "The selected saved tablet set's ID and name are frozen with this map/event's tablet configuration. "
     "They do not refer to every reusable set in the library. Compare frozen slot values and units as well "
     "as IDs; a later rename/edit does not revise a completed map. Library-only history has blank Map ID "
     "and is not a map use. Missing legacy set identity stays blank rather than using the current selection."),
    ("field_ritual_rite_identity", r"Ritual Rite ID|Ritual Rite Name|Ritual Rite Map Ordinal",
     "Optional saved Rite identity and map order",
     "These fields retain the selected Rite's internal ID, saved name and map ordinal within that Rite. "
     "They are identifiers/order, not modifier strength or encounter counts. Use the saved observation's "
     "selection, and keep missing historical metadata unknown rather than substituting today's Rite."),
    ("field_ritual_rite_affixes", r"Ritual Rite Affixes JSON", "This map's Rite affix selections",
     "This JSON list records the map's own choices with id, text, kind, occurrence count, value/unit and "
     "source provenance. kind distinguishes local, bounty and proliferation; only proliferation choices "
     "can supply inherited effects after this map is completed. The catalog's IDs/stat IDs are explicitly "
     "internal, raw untranslated rows stay raw, and typed custom wording remains unverified. No general "
     "bonus or monster-density formula is calculated."),
    ("field_ritual_rite_inheritance", r"Ritual Rite Inherited Sources JSON", "Rite proliferation source observations",
     "This JSON list contains effects from completed previous maps in the same selected Rite, retaining "
     "origin_map_id, origin_rite_id, origin_map_ordinal, origin_affix_position and affix_id. Only the origin "
     "map's own proliferation affixes are carried; ordinary affixes, bounties and previously inherited "
     "entries do not create new sources. Retain counts and provenance without assuming an additive or "
     "multiplicative combined game bonus. Missing metadata is unknown rather than a zero bonus."),
    ("field_ritual_rite_continuations",
     r"(?:Rite metadata \| )*(?:Ritual Rite Affixes JSON|Ritual Rite Inherited Sources JSON) continuation [1-9]\d*",
     "Long Rite JSON in Excel",
     "When a Rite JSON field exceeds Excel's cell limit, concatenate the original field and its numbered "
     "continuation columns in order without separators before parsing. Continuations are appended only "
     "when needed. A repeated Rite metadata | prefix distinguishes generated continuation headers from "
     "an existing item column with the same name. SQL and CSV retain the complete JSON in one field."),
    ("field_kill_observation_current", r"Current Map Kill Reading", "Latest cumulative map reading",
     "True marks the latest surviving approved kill snapshot for its Map ID by Scan Commit #. "
     "Its four categories and Total Kills (Observed) are the current OCR counts; earlier rows remain "
     "history and must not be added. This selection has no expedition dependency, does not replace "
     "manual end-map totals, and does not prove the user has finished the map. Undo selects the "
     "previous surviving observation; no observation means unknown rather than zero."),
    ("field_kill_observation_counts", r"(?:Normal|Magic|Rare|Unique|Total) Kills \(Observed\)",
     "Observed cumulative kill counts",
     "These integers are screenshot-observed cumulative counts for one Kill Snapshot ID. Total Kills "
     "(Observed) adds its four categories only. They are independent of manual map totals and are not "
     "increments, chain-attributed kills, loot quantities or values to sum across readings."),
    ("field_kill_observation_identity", r"Kill Snapshot ID|Request ID|Kill Count Scope|Count Interpretation",
     "Kill-reading identity and scope",
     "Kill Snapshot ID identifies one surviving observation and Request ID identifies the capture "
     "request. Use these as identifiers rather than numeric measurements. Kill Count Scope is "
     "chain_stage, map_stage or map_final; Count Interpretation records cumulative observation "
     "semantics. A scope or chain link does not turn the displayed counts into interval deltas. "
     "No active chain is required for a map reading."),
    ("field_kill_observation_evidence", r"Source Mode|Captured UTC|Image SHA256|OCR JSON|Capture Context JSON",
     "Kill-reading capture evidence",
     "Source Mode records which scan path requested the kill reading. Captured UTC is the image "
     "capture time, distinct from Recorded UTC. Image SHA256 identifies supplied screenshot bytes; "
     "it is not an accuracy score. OCR JSON preserves recognition evidence and Capture Context JSON "
     "preserves the map/session context frozen for the request. Parse JSON properties by name; "
     "do not substitute current settings or infer gameplay attribution from this metadata."),
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
    from PoE2_Data_Logger.core import analysis_context_data
    analysis_context_data.initialize(db, CONTEXT_VERSION)


def export_csv(db):
    """Serialize the stored notes in reading order on the caller's existing SQLite snapshot."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(db.execute("SELECT section,topic,guidance,context_version FROM analysis_context_complete "
                               "ORDER BY position,key"))
    return output.getvalue().encode("utf-8-sig")
