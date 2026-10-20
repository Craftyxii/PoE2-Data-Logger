"""Explain saved settings and source-scoped totals without changing logged observations.

Ordinary SQLite views keep the workbook context and a standalone SQL backup in
sync. They read frozen Atlas catalogs and map snapshots, rather than applying
today's character settings to old maps or inventing a combined game multiplier.
"""


VIEWS = {
    "analysis_context_atlas_references": """
        SELECT json_extract(snapshot_json,'$.atlas_setup_id') AS setup_id,map_id FROM maps
        UNION SELECT json_extract(snapshot_json,'$.atlas_setup_id'),map_id FROM commits WHERE map_id<>''
        UNION SELECT json_extract(snapshot_json,'$.atlas_setup_id'),map_id FROM currency_snapshots
        UNION SELECT json_extract(snapshot_json,'$.atlas_setup_id'),map_id FROM ritual_pages
        UNION SELECT s.setup_id,'' FROM atlas_setups s JOIN meta m ON m.key='settings'
        WHERE s.catalog_id=json_extract(m.value,'$.atlas_settings.catalog_id')
          AND json_extract(s.settings_json,'$.catalog_version')=json_extract(m.value,'$.atlas_settings.catalog_version')
          AND json_extract(s.settings_json,'$.allocated')=json_extract(m.value,'$.atlas_settings.allocated')
          AND json_extract(s.settings_json,'$.choices')=json_extract(m.value,'$.atlas_settings.choices')
          AND json_extract(s.settings_json,'$.gear_item_rarity') IS json_extract(m.value,'$.atlas_settings.gear_item_rarity')
    """,
    "analysis_context_atlas_setups": """
        SELECT s.setup_id,s.catalog_id,s.settings_json,c.version AS catalog_version,c.catalog_json,
               EXISTS(SELECT 1 FROM analysis_context_atlas_references r
                      WHERE r.setup_id=s.setup_id AND r.map_id='') AS is_current,
               (SELECT COUNT(DISTINCT r.map_id) FROM analysis_context_atlas_references r
                WHERE r.setup_id=s.setup_id AND r.map_id<>'') AS referenced_map_count
        FROM atlas_setups s JOIN atlas_catalogs c ON c.catalog_id=s.catalog_id
        WHERE EXISTS(SELECT 1 FROM analysis_context_atlas_references r WHERE r.setup_id=s.setup_id)
    """,
    "analysis_context_atlas_nodes": """
        SELECT c.catalog_id,n.key AS node_id,n.value AS node_json,
               json_extract(n.value,'$.name') AS name,json_extract(n.value,'$.activity') AS activity,
               COALESCE(json_extract(n.value,'$.effects'),'[]') AS effects_json,
               COALESCE(json_extract(n.value,'$.stats'),'{}') AS stats_json
        FROM atlas_catalogs c,json_each(c.catalog_json,'$.nodes') n
        WHERE json_extract(n.value,'$.allocatable')=1
          AND EXISTS(SELECT 1 FROM analysis_context_atlas_setups s WHERE s.catalog_id=c.catalog_id)
    """,
    "analysis_context_atlas_options": """
        SELECT n.catalog_id,n.node_id,n.activity,CAST(o.key AS INTEGER)+1 AS choice_number,
               json_extract(o.value,'$.id') AS choice_id,json_extract(o.value,'$.name') AS name,
               COALESCE(json_extract(o.value,'$.effects'),'[]') AS effects_json,
               COALESCE(json_extract(o.value,'$.stats'),'{}') AS stats_json
        FROM analysis_context_atlas_nodes n,json_each(n.node_json,'$.choices') o
    """,
    "analysis_context_atlas_allocations": """
        SELECT s.setup_id,n.* FROM analysis_context_atlas_setups s,
             json_each(s.settings_json,'$.allocated') a
        JOIN analysis_context_atlas_nodes n ON n.catalog_id=s.catalog_id AND n.node_id=a.value
    """,
    "analysis_context_atlas_choices": """
        SELECT s.setup_id,o.*,EXISTS(SELECT 1 FROM json_each(s.settings_json,'$.allocated') a
                                  WHERE a.value=o.node_id) AS active
        FROM analysis_context_atlas_setups s,json_each(s.settings_json,'$.choices') c
        JOIN analysis_context_atlas_options o
             ON o.catalog_id=s.catalog_id AND o.node_id=c.key AND o.choice_id=c.value
    """,
    "analysis_context_atlas_contributions": """
        SELECT a.setup_id,a.catalog_id,a.node_id,'' AS choice_id,v.key AS stat_id,v.value AS amount
        FROM analysis_context_atlas_allocations a,json_each(a.stats_json) v
        WHERE v.type IN ('integer','real') AND substr(v.key,1,14)<>'dummy_display_'
        UNION ALL
        SELECT c.setup_id,c.catalog_id,c.node_id,c.choice_id,v.key,v.value
        FROM analysis_context_atlas_choices c,json_each(c.stats_json) v
        WHERE c.active=1 AND v.type IN ('integer','real') AND substr(v.key,1,14)<>'dummy_display_'
    """,
    "analysis_context_atlas_totals": """
        WITH totals AS (
            SELECT setup_id,catalog_id,stat_id,SUM(amount) AS raw_total,COUNT(*) AS contribution_count
            FROM analysis_context_atlas_contributions GROUP BY setup_id,catalog_id,stat_id
        )
        SELECT t.*,r.unit,r.template,r.number_format,
               t.raw_total*r.multiplier/r.divisor AS display_total
        FROM totals t JOIN atlas_catalogs c ON c.catalog_id=t.catalog_id
        LEFT JOIN analysis_context_stat_rules r ON r.rule_key=(
            SELECT rule_key FROM analysis_context_stat_rules candidate
            WHERE candidate.source_hashes_json=json_extract(c.catalog_json,'$.provenance.input_hashes')
              AND candidate.stat_id=t.stat_id
              AND (candidate.min_raw IS NULL OR t.raw_total>=candidate.min_raw)
              AND (candidate.max_raw IS NULL OR t.raw_total<=candidate.max_raw)
            ORDER BY candidate.position,candidate.rule_key LIMIT 1)
    """,
    "analysis_context_tablet_values": """
        SELECT m.map_id,CAST(p.key AS INTEGER)/4+1 AS tablet_number,
               CAST(p.key AS INTEGER)%4+1 AS slot_number,
               json_extract(p.value,'$.affix') AS affix,json_extract(p.value,'$.value') AS amount,
               COALESCE(json_extract(p.value,'$.unit'),'%') AS unit
        FROM maps m,json_each(m.snapshot_json,'$.tablet_affixes') p
        WHERE CAST(p.key AS INTEGER)/4<COALESCE(json_extract(m.snapshot_json,'$.tablet_capacity'),
                                              json_extract(m.snapshot_json,'$.tablets_used'),0)
          AND COALESCE(json_extract(p.value,'$.affix'),'')<>''
          AND json_type(p.value,'$.value') IN ('integer','real')
    """,
    "analysis_context_tablet_totals": """
        SELECT map_id,affix,unit,SUM(amount) AS total,COUNT(*) AS contribution_count
        FROM analysis_context_tablet_values GROUP BY map_id,affix,unit
    """,
    "analysis_context_ritual_counters": """
        WITH boundaries AS (
            SELECT map_id,COUNT(*) AS reading_count,MIN(number) AS first_commit_number,
                   MAX(number) AS final_commit_number
            FROM commits WHERE kind='Ritual' AND map_id<>'' GROUP BY map_id
        )
        SELECT b.map_id,json_object(
            'reading_count',b.reading_count,
            'first_commit_number',b.first_commit_number,'final_commit_number',b.final_commit_number,
            'first_tribute_available',json_extract(first.details_json,'$.tribute_available'),
            'first_rerolls_remaining',json_extract(first.details_json,'$.rerolls_remaining'),
            'final_tribute_available',json_extract(final.details_json,'$.tribute_available'),
            'final_rerolls_remaining',json_extract(final.details_json,'$.rerolls_remaining')) AS guidance
        FROM boundaries b JOIN commits first ON first.number=b.first_commit_number
        JOIN commits final ON final.number=b.final_commit_number
    """,
    "analysis_context_map_values": """
        SELECT m.map_id,json_object(
            'map_id',m.map_id,'source','maps.snapshot_json; one created map',
            'snapshot_available',json(CASE WHEN m.snapshot_json='{}' THEN 'false' ELSE 'true' END),
            'atlas_setup_id',json_extract(m.snapshot_json,'$.atlas_setup_id'),
            'item_rarity',json_extract(m.snapshot_json,'$.item_rarity'),
            'monster_rarity',json_extract(m.snapshot_json,'$.monster_rarity'),
            'pack_size',json_extract(m.snapshot_json,'$.pack_size'),
            'effectiveness',json_extract(m.snapshot_json,'$.effectiveness'),
            'gear_item_rarity',json_extract(m.snapshot_json,'$.gear_item_rarity'),
            'tier',json_extract(m.snapshot_json,'$.tier'),'area',json_extract(m.snapshot_json,'$.area'),
            'base_map_mods',json_extract(m.snapshot_json,'$.base_map_mods'),
            'map_mods',json_extract(m.snapshot_json,'$.map_mods'),
            'master_adds_mod',json_extract(m.snapshot_json,'$.master_adds_mod'),
            'tablet_mods',json_extract(m.snapshot_json,'$.tablet_mods'),
            'total_mods',json_extract(m.snapshot_json,'$.total_mods'),
            'tablets_used',json_extract(m.snapshot_json,'$.tablets_used'),
            'tablet_capacity',json_extract(m.snapshot_json,'$.tablet_capacity'),
            'tablet_set_id',json_extract(m.snapshot_json,'$.tablet_set_id'),
            'tablet_set_name',json_extract(m.snapshot_json,'$.tablet_set_name'),
            'ritual_rite_id',json_extract(m.snapshot_json,'$.ritual_rite_id'),
            'ritual_rite_name',json_extract(m.snapshot_json,'$.ritual_rite_name'),
            'ritual_rite_map_ordinal',json_extract(m.snapshot_json,'$.ritual_rite_map_ordinal'),
            'ritual_rite_affixes',json(CASE WHEN json_type(m.snapshot_json,'$.ritual_rite_affixes')='array'
                THEN json_extract(m.snapshot_json,'$.ritual_rite_affixes') ELSE 'null' END),
            'ritual_rite_inherited_sources',json(CASE
                WHEN json_type(m.snapshot_json,'$.ritual_rite_inherited_sources')='array'
                THEN json_extract(m.snapshot_json,'$.ritual_rite_inherited_sources') ELSE 'null' END),
            'ritual_counters',json(COALESCE((SELECT r.guidance FROM analysis_context_ritual_counters r
                WHERE r.map_id=m.map_id),'{"reading_count":0,"first_commit_number":null,"final_commit_number":null,
                "first_tribute_available":null,"first_rerolls_remaining":null,
                "final_tribute_available":null,"final_rerolls_remaining":null}')),
            'master',json_extract(m.snapshot_json,'$.master'),
            'perks',json(COALESCE(json_extract(m.snapshot_json,'$.perks'),'[]')),
            'biome',json_extract(m.snapshot_json,'$.biome'),'city_type',json_extract(m.snapshot_json,'$.city_type'),
            'activity_type',json_extract(m.snapshot_json,'$.activity_type'),
            'waystone_setup_saved',json(CASE json_type(m.snapshot_json,'$.waystone_setup_saved')
                WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' ELSE 'null' END),
            'atlas_effect_snapshot',json(CASE WHEN json_type(m.snapshot_json,'$.atlas_effect_snapshot')='object'
                THEN json_extract(m.snapshot_json,'$.atlas_effect_snapshot') ELSE 'null' END),
            'irradiated',json_extract(m.snapshot_json,'$.irradiated'),'ocean',json_extract(m.snapshot_json,'$.ocean'),
            'deli',json_extract(m.snapshot_json,'$.deli'),'wisp',json_extract(m.snapshot_json,'$.wisp'),
            'normal_kills',json_extract(m.kills_json,'$[0]'),
            'magic_kills',json_extract(m.kills_json,'$[1]'),
            'rare_kills',json_extract(m.kills_json,'$[2]'),'unique_kills',u.unique_kills,
            'known_kills_total',CASE WHEN json_type(m.kills_json,'$[0]') IN ('integer','real')
                OR json_type(m.kills_json,'$[1]') IN ('integer','real')
                OR json_type(m.kills_json,'$[2]') IN ('integer','real') OR u.unique_kills IS NOT NULL
                THEN COALESCE(json_extract(m.kills_json,'$[0]'),0)+COALESCE(json_extract(m.kills_json,'$[1]'),0)
                    +COALESCE(json_extract(m.kills_json,'$[2]'),0)+COALESCE(u.unique_kills,0) END,
            'kill_categories_complete',json(CASE WHEN json_type(m.kills_json,'$[0]') IN ('integer','real')
                AND json_type(m.kills_json,'$[1]') IN ('integer','real')
                AND json_type(m.kills_json,'$[2]') IN ('integer','real') AND u.unique_kills IS NOT NULL
                THEN 'true' ELSE 'false' END),
            'remnants_detonated',CASE WHEN EXISTS(SELECT 1 FROM expeditions e WHERE e.map_id=m.map_id)
                THEN (SELECT SUM(e.detonated) FROM expeditions e WHERE e.map_id=m.map_id) END
            ) AS guidance
        FROM maps m LEFT JOIN map_unique_kills u ON u.map_id=m.map_id
    """,
    "analysis_context_metric_totals": """
        SELECT v.key AS metric,COUNT(*) AS map_count,
               SUM(CASE WHEN v.type IN ('integer','real') THEN 1 ELSE 0 END) AS known_maps,
               SUM(CASE WHEN v.type IN ('integer','real') THEN v.value END) AS total,
               AVG(CASE WHEN v.type IN ('integer','real') THEN v.value END) AS mean,
               MIN(CASE WHEN v.type IN ('integer','real') THEN v.value END) AS minimum,
               MAX(CASE WHEN v.type IN ('integer','real') THEN v.value END) AS maximum
        FROM analysis_context_map_values m,json_each(m.guidance) v
        WHERE v.key IN ('item_rarity','monster_rarity','pack_size','effectiveness','gear_item_rarity',
                        'base_map_mods','map_mods','master_adds_mod','tablet_mods','total_mods',
                        'tablets_used','tablet_capacity','normal_kills','magic_kills','rare_kills',
                        'unique_kills','known_kills_total','remnants_detonated')
        GROUP BY v.key
    """,
}


def initialize(db, context_version):
    """Install context-owned read views and verified linear translation metadata idempotently."""
    from PoE2_Data_Logger.core.analysis_context_rules import linear_stat_rules

    db.execute("CREATE TABLE IF NOT EXISTS analysis_context_stat_rules ("
               "rule_key TEXT PRIMARY KEY,position INTEGER NOT NULL,source_hashes_json TEXT NOT NULL,"
               "stat_id TEXT NOT NULL,min_raw REAL,max_raw REAL,divisor REAL NOT NULL,"
               "multiplier REAL NOT NULL,template TEXT NOT NULL,unit TEXT NOT NULL,number_format TEXT NOT NULL)")
    rules = linear_stat_rules()
    db.executemany("INSERT INTO analysis_context_stat_rules VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                   "ON CONFLICT(rule_key) DO UPDATE SET position=excluded.position "
                   "WHERE position!=excluded.position",
                   ((r['rule_key'], position, r['source_hashes_json'], r['stat_id'], r['min_raw'], r['max_raw'],
                     r['divisor'], r['multiplier'], r['template'], r['unit'], r['format'])
                    for position, r in enumerate(rules, 1)))
    if rules:
        # Keep historical source datasets, but retire superseded wording rules
        # for this pinned dataset so a previous context revision cannot win.
        db.execute("DELETE FROM analysis_context_stat_rules WHERE source_hashes_json=? AND rule_key NOT IN ("
                   + ",".join("?" for _ in rules) + ")",
                   [rules[0]['source_hashes_json'], *(r['rule_key'] for r in rules)])
    for name, body in VIEWS.items():
        _view(db, name, body)
    _view(db, "analysis_context_complete", _complete_view(context_version))


def _view(db, name, body):
    """Replace only changed context-owned definitions; reads require no application functions."""
    statement = f"CREATE VIEW {name} AS {body.strip()}"
    previous = db.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (name,)).fetchone()
    if previous is not None and previous[0] == statement:
        return
    db.execute(f"DROP VIEW IF EXISTS {name}")
    db.execute(statement)


def _complete_view(version):
    """Combine the static guide with small, self-contained JSON facts and scoped sums."""
    if not str(version).isdigit():
        raise ValueError("Context version must be numeric.")
    return f"""
        SELECT key,position,section,topic,guidance,context_version FROM analysis_context
        UNION ALL SELECT 'setup:'||s.setup_id,1000,'Atlas setup',s.setup_id,
            json_object('setup_id',s.setup_id,'catalog_id',s.catalog_id,'catalog_version',s.catalog_version,
                'is_current',json(CASE WHEN s.is_current THEN 'true' ELSE 'false' END),
                'allocated_count',json_array_length(s.settings_json,'$.allocated'),
                'referenced_map_count',s.referenced_map_count,
                'gear_item_rarity',json_extract(s.settings_json,'$.gear_item_rarity')), '{version}'
            FROM analysis_context_atlas_setups s
        UNION ALL SELECT 'setup-map:'||setup_id||':'||map_id,1001,'Atlas map reference',map_id,
            json_object('setup_id',setup_id,'map_id',map_id,'scope','saved snapshot reference'),'{version}'
            FROM analysis_context_atlas_references WHERE setup_id IS NOT NULL AND map_id<>''
        UNION ALL SELECT 'node-definition:'||catalog_id||':'||node_id,1100,'Atlas node definition',name||' ['||node_id||']',
            json_object('catalog_id',catalog_id,'node_id',node_id,'name',name,'activity',activity,
                'effects',json(effects_json),'stats',json(stats_json)), '{version}'
            FROM analysis_context_atlas_nodes
        UNION ALL SELECT 'choice-definition:'||catalog_id||':'||node_id||':'||choice_id,1101,
            'Atlas choice definition',name||' ['||choice_id||']',
            json_object('catalog_id',catalog_id,'node_id',node_id,'choice_id',choice_id,
                'choice_number',choice_number,'name',name,'activity',activity,
                'effects',json(effects_json),'stats',json(stats_json)), '{version}'
            FROM analysis_context_atlas_options
        UNION ALL SELECT 'allocation:'||setup_id||':'||node_id,1200,'Atlas allocation',setup_id||' | '||name,
            json_object('setup_id',setup_id,'catalog_id',catalog_id,'node_id',node_id,'name',name,'activity',activity,
                'effects',json(effects_json),'stats',json(stats_json)), '{version}'
            FROM analysis_context_atlas_allocations
        UNION ALL SELECT 'choice:'||setup_id||':'||node_id,1201,'Atlas choice',setup_id||' | '||name,
            json_object('setup_id',setup_id,'catalog_id',catalog_id,'node_id',node_id,'choice_id',choice_id,
                'choice_number',choice_number,'name',name,'activity',activity,
                'active',json(CASE WHEN active THEN 'true' ELSE 'false' END),
                'effects',json(effects_json),'stats',json(stats_json)), '{version}'
            FROM analysis_context_atlas_choices
        UNION ALL SELECT 'atlas-total:'||setup_id||':'||stat_id,1300,'Atlas stat total',setup_id||' | '||stat_id,
            json_object('setup_id',setup_id,'catalog_id',catalog_id,'stat_id',stat_id,'raw_total',raw_total,
                'display_total',display_total,'unit',COALESCE(unit,'raw catalog units'),
                'effect',CASE WHEN template IS NOT NULL THEN replace(template,'{{0}}',
                    CASE WHEN number_format='+#' AND display_total>0 THEN '+' ELSE '' END||printf('%g',display_total)) END,
                'contribution_count',contribution_count,
                'scope','allocated nodes and active choices; configuration contribution, not an applied game total'),'{version}'
            FROM analysis_context_atlas_totals
        UNION ALL SELECT 'master:'||master||':'||name,1400,'Atlas master perk',master||' | '||name,
            json_object('master',master,'name',name,'tier',tier,'effect',effect,
                'reference_scope','current editable master_perks reference at export; not a frozen effect revision'),'{version}'
            FROM master_perks
        UNION ALL SELECT 'map:'||map_id,1500,'Map settings',map_id,guidance,'{version}'
            FROM analysis_context_map_values
        UNION ALL SELECT 'tablet-total:'||map_id||':'||affix||':'||unit,1600,'Tablet stat total',map_id||' | '||affix,
            json_object('map_id',map_id,'affix',affix,'unit',unit,'total',total,
                'contribution_count',contribution_count,
                'scope',CASE WHEN unit='flag' THEN 'recorded occurrences; not effect multiplication'
                     ELSE 'sum of active tablet slots with this exact affix and unit; not an applied game total' END),'{version}'
            FROM analysis_context_tablet_totals
        UNION ALL SELECT 'session-metric:'||metric,1700,'Session stat total',metric,
            json_object('metric',metric,'created_maps',map_count,'known_maps',known_maps,
                'missing_maps',map_count-known_maps,'total',total,'mean',mean,'minimum',minimum,'maximum',maximum,
                'scope','one saved observation per created Map ID; percentage totals are descriptive sums across maps, not a game bonus'),'{version}'
            FROM analysis_context_metric_totals
    """
