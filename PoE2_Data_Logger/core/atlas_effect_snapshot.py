"""Summarize selected Atlas source effects for one map without altering observations.

Verified equivalent area stats share one total; distinct targets retain their
source IDs. Numeric values describe allocated source coefficients, not game outcomes.
Conditions that need unrecorded monster, encounter or map state stay explicit.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import re


_BIOMES = ("water", "mountain", "grass", "forest", "swamp", "desert", "ocean", "island")
_CITIES = ("faridun", "ezomyte", "vaal")
_GENERAL_ACTIVITIES = frozenset(("Main Atlas", "General", "Atlas"))
# These source identities differ only by an area classification. They retain
# the same target and modifier type; encounter-specific and per-unit effects
# are deliberately absent from this equivalence list.
_AREA_STAT_EQUIVALENTS = frozenset((
    "map_additional_rare_in_rare_pack_chance_+%", "map_essence_greater_essence_+%",
    "map_experience_gain_+%", "map_gold_+%", "map_item_drop_quantity_+%",
    "map_item_drop_rarity_+%", "map_magic_monster_additional_modifier_chance_%",
    "map_magic_pack_size_+%", "map_monolith_chance_+%", "map_monster_potency_+%",
    "map_number_of_magic_packs_+%", "map_number_of_rare_packs_+%", "map_pack_size_+%",
    "map_rare_chest_amount_+%", "map_rare_monster_modifiers_chance_%",
    "map_rogue_exile_chance_+%", "map_rogue_exiles_are_doubled_chance_%",
    "map_sacred_spirit_chance_+%", "map_shrine_chance_+%",
    "map_shrine_granted_player_buff_effect_+%", "map_shrines_additional_packs_+",
    "map_stone_circle_chance_+%", "map_strongbox_chance_+%",
    "map_tormented_spirit_chance_+%", "map_tower_augment_quantity_+%",
    "map_unique_monster_item_rarity_+%",
))
# Pinned translations use both spellings for increased Summoning Circle
# chance; this alias is explicit rather than generalizing chance percentage IDs.
_AREA_STAT_ALIASES = {"map_stone_circle_chance_%": "map_stone_circle_chance_+%"}


def _number(value):
    """Accept finite catalog amounts without treating boolean flags as numbers."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _effects(settings, data):
    """Yield each allocated base effect and its one active saved choice once."""
    choices = settings.get("choices") or {}
    for node_id in sorted(set(settings.get("allocated") or [])):
        node = (data.get("nodes") or {}).get(node_id)
        if not node or not node.get("allocatable"):
            yield node_id, "", None, None
            continue
        yield node_id, "", node, node
        if node.get("choices"):
            choice_id = choices.get(node_id)
            choice = next((item for item in node["choices"] if item.get("id") == choice_id), None)
            yield node_id, choice_id or "", node, choice


def _tree_applies(tree, activity, deli):
    """Select shared/encounter trees plus Delirium when its independent map flag is set."""
    return (tree in _GENERAL_ACTIVITIES
            or tree == ("Expedition" if activity == "Grand Expedition" else activity)
            or (tree == "Delirium" and (deli is True or deli == "Yes")))


def _scope(stat, biomes, city, activity, base_biome_known):
    """Evaluate recorded area tags while preserving unknown runtime conditions."""
    conditions = []
    biome_match = re.match(r"map_(water|mountain|grass|forest|swamp|desert|ocean|island)_", stat)
    if biome_match and biome_match[1] not in biomes:
        if base_biome_known:
            return False, []
        conditions.append("biome_unselected:" + biome_match[1])
    city_match = re.match(r"map_city_(faridun|ezomyte|vaal)_", stat)
    city_effect = stat.startswith("map_city_") or "when_opening_a_city_map" in stat or stat == "map_stone_circle_city_citadel_boss_chance_%"
    if city_effect and city not in _CITIES:
        return False, []
    if city_match and city_match[1] != city:
        return False, []
    if stat == "map_stone_circle_non_city_deadly_boss_chance_%" and city in _CITIES:
        return False, []
    if stat.startswith("map_expedition_oceans_count_as_") and not ({"ocean", "island"} & biomes):
        return False, []
    if stat in ("map_expedition_logbook_level_+", "dummy_expedition_logbooks_have_up_to_3_modifiers") and activity != "Grand Expedition":
        return False, []
    if stat == "dummy_expedition_farrow_sells_reroll_remnant_currency":
        conditions.append("vendor_unlock")
    if re.search(r"(?:^|_)(?:rare|magic|unique|boss|runic|possessed|touched|strongbox|stone_circle|shrine|essence)(?:_|$)", stat):
        conditions.append("catalog_target")
    if "_per_" in stat:
        conditions.append("runtime_count")
    if "_if_" in stat or "_for_each_" in stat or ("_when_" in stat and "when_opening_a_" not in stat):
        conditions.append("catalog_trigger")
    if stat == "map_delirium_giga_mirror_chance_+%_at_max_depth":
        # This retained coefficient requires the catalog's boss-within-fog
        # trigger; the Deli map flag alone does not record that event.
        conditions.append("catalog_trigger")
    if any(token in stat for token in ("corrupted", "cleansed", "powerful_boss", "unique_lake", "unique_moment_of_zen", "unique_selenite", "unique_untainted", "_anomaly_")):
        conditions.append("area_state_unrecorded")
    return True, conditions


def _display_rule(stat, amount, rules, hashes):
    """Select a pinned source's first translation valid for this raw total."""
    for rule in rules:
        if rule.get("stat_id") != stat or rule.get("source_hashes_json") != hashes:
            continue
        if rule.get("min_raw") is not None and amount < rule["min_raw"]:
            continue
        if rule.get("max_raw") is not None and amount > rule["max_raw"]:
            continue
        return rule
    return None


def _display(stat, amount, rules, hashes):
    """Use only a pinned source translation valid for this raw total."""
    rule = _display_rule(stat, amount, rules, hashes)
    if rule is not None:
        value = amount * rule["multiplier"] / rule["divisor"]
        value = int(value) if float(value).is_integer() else value
        text = ("+" if rule.get("format") == "+#" and value >= 0 else "") + str(value)
        return value, rule["unit"], rule["template"].format(text)
    return None, None, None


def _combined_totals(rows, rules, hashes):
    """Combine equivalent applicable area bonuses into one numeric stat total.

    Keep unknown biome, runtime and trigger requirements separate. Matching
    display units and raw scales are required before merging distinct IDs.
    Source IDs identify the combined metric without repeating node breakdowns.
    """
    groups = defaultdict(list)
    for row in rows:
        stat = row["stat_id"]
        suffix = re.match(r"^map_(?:city_(?:faridun|ezomyte|vaal)_|city_|"
                          r"water_|mountain_|grass_|forest_|swamp_|desert_|ocean_|island_)(.+)$", stat)
        equivalent = "map_" + suffix[1] if suffix else stat
        equivalent = _AREA_STAT_ALIASES.get(equivalent, equivalent)
        conditions = tuple(row.get("conditions", []))
        known = not any(tag != "catalog_target" for tag in conditions)
        # Only verified translations authorize cross-ID arithmetic. If a
        # catalog's unit or scale is unknown, its exact-ID sum stays intact.
        rule = _display_rule(stat, row["raw_total"], rules, hashes)
        unit = rule["unit"] if rule is not None else None
        scale = rule["multiplier"] / rule["divisor"] if rule is not None else None
        key = (equivalent, unit, scale, conditions) if (known and scale is not None
                and equivalent in _AREA_STAT_EQUIVALENTS) else (stat, None, None, conditions)
        groups[key].append(row)
    result = []
    for (stat, unit, scale, conditions), members in groups.items():
        sources = sorted({row["stat_id"] for row in members})
        if len(sources) == 1:
            row = dict(members[0])
        else:
            total = sum(row["raw_total"] for row in members)
            # Each source's unit and signed scale were verified above. Sum
            # those displayed contributions even when the canonical metric
            # has no catalog translation; source IDs retain the full meanings.
            amount = sum(row["display_total"] for row in members)
            row = {"stat_id": stat, "raw_total": total, "display_total": amount,
                   "unit": unit, "source_stat_ids": sources}
            if conditions:
                row["conditions"] = list(conditions)
        row.pop("contribution_count", None)
        result.append(row)
    return sorted(result, key=lambda row: (row["stat_id"], tuple(row.get("conditions", []))))


def build_snapshot(context, data, rules=()):
    """Return compact context-filtered source totals from the map's frozen setup.

    Identical IDs and verified equivalent applicable area stats are summed.
    Unsupported display translations retain
    raw values; full wording resolves through the frozen setup/catalog identity.
    Concise condition tags preserve unevaluated runtime coefficients.
    The result never feeds back into captured rarity, pack size or effectiveness.
    """
    settings = context.get("atlas_settings") or {}
    activity = context.get("activity_type", "None")
    biome = str(context.get("biome") or "None")
    city = str(context.get("city_type") or "None")
    biomes = {biome.lower()} if biome.lower() in _BIOMES else set()
    ocean = context.get("ocean", "No")
    deli = context.get("deli", "No")
    if ocean is True or ocean == "Yes":
        biomes.add("ocean")
    active, unresolved = [], []
    for node_id, choice_id, node, effect in _effects(settings, data):
        if node is None:
            unresolved.append({"node_id": node_id, "choice_id": choice_id, "reason": "node_missing"})
        elif _tree_applies(node.get("activity", "Main Atlas"), activity, deli):
            if effect is None:
                unresolved.append({"node_id": node_id, "choice_id": choice_id, "reason": "choice_unresolved"})
            else:
                active.append((node_id, choice_id, node, effect))
    # Additional classifications are flags, not a replacement of the entered
    # biome; each classification and each contribution are counted once.
    for _, _, _, effect in active:
        for stat, amount in (effect.get("stats") or {}).items():
            if not _number(amount) or amount != 1:
                continue
            prefix = f"map_city_{city.lower()}_also_count_as_"
            if city.lower() in _CITIES and stat.startswith(prefix) and stat[len(prefix):] in _BIOMES:
                biomes.add(stat[len(prefix):])
    if {"ocean", "island"} & biomes:
        for _, _, _, effect in active:
            for stat, amount in (effect.get("stats") or {}).items():
                prefix = "map_expedition_oceans_count_as_"
                if _number(amount) and amount == 1 and stat.startswith(prefix) and stat[len(prefix):] in _BIOMES:
                    biomes.add(stat[len(prefix):])
    totals = defaultdict(lambda: {"raw_total": 0, "contribution_count": 0, "conditions": set()})
    for node_id, choice_id, node, effect in active:
        for stat, amount in (effect.get("stats") or {}).items():
            if stat.startswith("dummy_display_") or stat == "dummy_atlas_pack_size_selector":
                continue
            applies, conditions = _scope(stat, biomes, city.lower(), activity, biome.lower() in _BIOMES)
            if not applies:
                continue
            if not _number(amount) or stat.startswith("dummy_"):
                unresolved.append({"node_id": node_id, "choice_id": choice_id, "stat_id": stat, "raw_value": amount,
                                   "reason": "semantic_flag", "conditions": conditions})
                continue
            value = totals[stat]
            value["raw_total"] += amount
            value["contribution_count"] += 1
            value["conditions"].update(conditions)
    hashes = json.dumps(data.get("provenance", {}).get("input_hashes"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result = []
    for stat, total in sorted(totals.items()):
        amount, unit, _ = _display(stat, total["raw_total"], rules, hashes)
        row = {"stat_id": stat, "raw_total": total["raw_total"]}
        if amount is not None:
            row.update({"display_total": amount, "unit": unit})
        if total["conditions"]:
            row["conditions"] = sorted(total["conditions"])
        if total["contribution_count"] != 1:
            row["contribution_count"] = total["contribution_count"]
        result.append(row)
    return {"schema_version": 2, "atlas_setup_id": context.get("atlas_setup_id"),
            "catalog_id": context.get("atlas_catalog_id", settings.get("catalog_id")),
            "catalog_version": settings.get("catalog_version"), "activity_type": activity,
            "biome": biome, "city_type": city, "ocean": ocean, "deli": deli,
            "effective_biomes": sorted(item.title() for item in biomes),
            "totals": _combined_totals(result, rules, hashes), "unresolved": unresolved}
