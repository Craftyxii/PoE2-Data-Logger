#!/usr/bin/env python3
"""Rebuild the bundled Atlas catalog offline from pinned game-data inputs.

Normal use: python tools/build_atlas_catalog.py
Refresh input subset: --research-directory PATH (the filenames/URLs are listed
in atlas/SOURCES.md). --art-directory PATH additionally rebuilds cropped art
from the pinned original sprite files and requires Pillow.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "PoE2_Data_Logger" / "atlas"
INPUT_NAMES = [
    "repoe_data_passive_skill_trees_Atlas.json", "raw_csv_PassiveSkills.csv",
    "raw_csv_PassiveSkillVariants.csv", "raw_csv_PassiveSkillVariantTypes.csv",
    "raw_csv_AtlasPassiveSkillSubTrees.csv", "raw_csv_Stats.csv",
    "latest_atlas_variant_stat_descriptions.json", "latest_atlas_stat_descriptions.json",
    "latest_stat_descriptions.json", "latest_endgame_map_stat_descriptions.json",
    "latest_map_stat_descriptions.json", "latest_passive_skill_stat_descriptions.json",
]
# Prefer the complete effect wording; variant-only descriptions are often just
# the short dropdown label ("Shrines", for example).
TRANSLATION_NAMES = INPUT_NAMES[7:] + [INPUT_NAMES[6]]
ACTIVITIES = ["Main Atlas", "Breach", "Expedition", "Ritual", "Delirium", "Abyss", "Incursion"]


def write_gzip(path: Path, value: dict) -> None:
    """Write stable sorted JSON as gzip with zero mtime so unchanged source inputs reproduce
    the same bytes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Zero mtime makes rebuilding from the same sources byte-for-byte repeatable.
    path.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode(), mtime=0))


def read_csv(path: Path) -> list[dict]:
    """Load a UTF-8 source table as dictionaries keyed by its header row."""
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def refresh_inputs(directory: Path) -> dict:
    """Select Atlas passives, their choices and relevant stat translations from pinned local
    research files, recording input hashes.
    """
    atlas = json.loads((directory / INPUT_NAMES[0]).read_text(encoding="utf-8"))
    passives = read_csv(directory / INPUT_NAMES[1])
    variants = read_csv(directory / INPUT_NAMES[2])
    stat_rows = read_csv(directory / INPUT_NAMES[5])
    by_id = {row["Id"]: row for row in passives}
    selected = {node["id"] for node in atlas["passives"].values()}
    required_types = {by_id[node]["VariantType"] for node in selected} - {"0", ""}
    options = [v for v in variants if v["Type"] in required_types]
    selected.update(passives[int(v["Variant"])]["Id"] for v in options)
    rows = {}
    for row in passives:
        if row["Id"] not in selected:
            continue
        indexes = json.loads(row["Stats"])
        rows[row["Id"]] = {
            "id": row["Id"], "name": row["Name"], "variant_type": row["VariantType"],
            "stats": {stat_rows[index]["Id"]: int(row[f"Stat{position + 1}Value"])
                      for position, index in enumerate(indexes)},
        }
    stats = {stat for row in rows.values() for stat in row["stats"]}
    translations = []
    for filename in TRANSLATION_NAMES:
        entries = json.loads((directory / filename).read_text(encoding="utf-8"))
        translations.extend({"ids": e["ids"], "English": e.get("English"),
                             "hidden": e.get("hidden")}
                            for e in entries if set(e["ids"]) & stats)
    sources = {
        name: {"sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest()}
        for name in INPUT_NAMES
    }
    return {"atlas": atlas, "passive_rows": rows,
            "variants": {type_id: [passives[int(v["Variant"])]["Id"] for v in options
                                    if v["Type"] == type_id]
                         for type_id in sorted(required_types)},
            "subtrees": read_csv(directory / INPUT_NAMES[4]),
            "translations": translations, "sources": sources}


def plain_text(text: str) -> str:
    """Strip game markup while retaining the displayed label after a link separator."""
    return re.sub(r"\[([^\[\]]+)\]", lambda match: match.group(1).split("|")[-1], text).strip()


def translated_effects(stats: dict, translations: list[dict]) -> list[str]:
    """Apply the exported condition and formatter rules, never infer effects."""
    effects = []
    consumed = set()
    for entry in translations:
        ids = entry["ids"]
        if entry.get("hidden") or not set(ids).issubset(stats) or set(ids) & consumed:
            continue
        values = [stats[stat] for stat in ids]
        for english in entry.get("English") or []:
            conditions = english.get("condition") or []
            if len(conditions) != len(values):
                continue
            if not all((c.get("min") is None or v >= c["min"]) and
                       (c.get("max") is None or v <= c["max"])
                       for v, c in zip(values, conditions)):
                continue
            transformed = list(values)
            for i, handlers in enumerate(english.get("index_handlers") or []):
                for handler in handlers:
                    if handler == "negate":
                        transformed[i] = -transformed[i]
                    elif handler == "subtract_one":
                        transformed[i] -= 1
                    else:
                        raise ValueError(f"Unsupported Atlas stat formatter: {handler}")
            formats = english.get("format") or []
            if any(fmt not in ("#", "ignore") for fmt in formats):
                raise ValueError(f"Unsupported Atlas stat formats: {formats}")
            text = plain_text(english["string"].format(*transformed))
            if text and text not in effects:
                effects.append(text)
            consumed.update(ids)
            break
    return effects


def positions(atlas: dict) -> tuple[dict, dict]:
    """Resolve orbit coordinates and bidirectional neighbors, choosing duplicate placements by
    their connected-node distances.
    """
    candidates = defaultdict(list)
    neighbors = defaultdict(set)
    for group in atlas["groups"]:
        for passive in group["passives"]:
            node_hash = str(passive["hash"])
            orbit = passive["radius"]
            angle = 2 * math.pi * passive["position_clockwise"] / atlas["skills_per_orbit"][orbit]
            radius = atlas["orbit_radii"][orbit]
            candidates[node_hash].append((group["x"] + radius * math.sin(angle),
                                          group["y"] - radius * math.cos(angle)))
            for target in passive["connections"]:
                neighbors[node_hash].add(str(target))
                neighbors[str(target)].add(node_hash)
    chosen = {}
    for node_hash, placements in candidates.items():
        def score(point):
            """Rank one duplicate node placement by its nearest distances to candidate neighbor
            placements.
            """
            return sum(min(math.dist(point, other) for other in candidates[target])
                       for target in neighbors[node_hash] if target in candidates)
        chosen[node_hash] = min(placements, key=score)
    return chosen, neighbors


def icon_name(path: str) -> str:
    # Original stem remains legible; suffix prevents case/path collisions.
    """Keep the icon stem and add a path hash to avoid filename collisions."""
    return Path(path).stem + "-" + hashlib.sha256(path.encode()).hexdigest()[:8] + ".png"


def edge_curves(atlas: dict, coords: dict, hash_to_id: dict) -> dict:
    """Recover circle centers from the graph's signed orbit-radius indexes."""
    curves = {}
    for group in atlas["groups"]:
        for passive in group["passives"]:
            source = str(passive["hash"])
            for target, spline in zip(passive["connections"], passive["splines"]):
                target = str(target)
                if not (0 < abs(spline) < len(atlas["orbit_radii"])):
                    continue
                if source not in hash_to_id or target not in hash_to_id:
                    continue
                key = "|".join(sorted((hash_to_id[source], hash_to_id[target])))
                if key in curves:
                    continue
                x1, y1 = coords[source]
                x2, y2 = coords[target]
                distance = math.hypot(x2 - x1, y2 - y1)
                radius = atlas["orbit_radii"][abs(spline)]
                if distance == 0 or distance > radius * 2:
                    continue
                offset = math.sqrt(max(0.0, radius * radius - distance * distance / 4))
                side = -1 if spline > 0 else 1
                curves[key] = {
                    "x": round((x1 + x2) / 2 + side * (y1 - y2) / distance * offset, 3),
                    "y": round((y1 + y2) / 2 + side * (x2 - x1) / distance * offset, 3),
                }
    return curves


def make_catalog(inputs: dict) -> dict:
    """Build node/effect/choice, edge and artwork metadata from local inputs, then validate the
    pinned catalog shape.
    """
    atlas = inputs["atlas"]
    coords, neighbors = positions(atlas)
    hash_to_id = {key: node["id"] for key, node in atlas["passives"].items()}
    nodes = {}
    for node_hash, passive in atlas["passives"].items():
        row = inputs["passive_rows"][passive["id"]]
        if row["stats"] != passive["stats"]:
            raise ValueError(f"Passive data versions disagree for {passive['id']}")
        if node_hash not in coords:
            raise ValueError(f"Missing layout for {passive['id']}")
        choices = []
        for option_id in inputs["variants"].get(row["variant_type"], []):
            option = inputs["passive_rows"][option_id]
            name = option["name"]
            if name.startswith(passive["name"] + ": "):
                name = name[len(passive["name"]) + 2:]
            choices.append({"id": option_id, "name": name,
                            "effects": translated_effects(option["stats"], inputs["translations"]),
                            "stats": option["stats"]})
        kind = ("root" if passive["is_atlas_root"] else
                "decorative" if passive["is_icon_only"] else
                "keystone" if passive["is_keystone"] else
                "notable" if passive["is_notable"] else "small")
        activity = passive.get("atlas_subtree", {}).get("id", "Main Atlas")
        x, y = coords[node_hash]
        nodes[passive["id"]] = {
            "id": passive["id"], "hash": passive["hash"],
            "name": passive["name"] or (activity if kind == "root" else ""),
            "activity": activity, "kind": kind,
            "allocatable": kind not in ("root", "decorative"),
            "max_allocation": 1 if kind not in ("root", "decorative") else 0,
            "x": round(x, 3), "y": round(y, 3),
            "effects": [plain_text(effect) for effect in passive["stat_text"]],
            "stats": passive["stats"], "choices": choices,
            "icon": "icons/" + icon_name(passive["icon"]),
        }
    edges = sorted({tuple(sorted((hash_to_id[h], hash_to_id[target])))
                    for h, targets in neighbors.items() for target in targets
                    if h in hash_to_id and target in hash_to_id and h != target})
    subtrees = {row["Id"]: row for row in inputs["subtrees"]}
    backgrounds = {}
    for node in nodes.values():
        if node["kind"] != "root" or node["activity"] == "Main Atlas":
            continue
        activity = node["activity"]
        row = subtrees[activity]
        backgrounds[activity] = {"path": f"art/{activity.lower()}-bg.webp",
                                 "x": node["x"] + float(row["IllustrationX"]),
                                 "y": node["y"] + float(row["IllustrationY"]),
                                 "width": 600 * float(row["f32_104"]),
                                 "height": 600 * float(row["f32_104"])}
    # The source artwork's facade anchor includes an offset. Its 2/3 ratio to
    # activity-panel scale is calibrated upstream, not a passive/gameplay rule.
    general_root = next(n for n in nodes.values()
                        if n["activity"] == "Main Atlas" and n["kind"] == "root")
    main_nodes = [n for n in nodes.values()
                  if n["activity"] == "Main Atlas" and n["kind"] != "root"]
    facade_size = 3840 * 3.0
    facade_layout = {
        "x": round(general_root["x"] - (0.50016 - 0.5) * facade_size, 3),
        "y": round((min(n["y"] for n in main_nodes) + max(n["y"] for n in main_nodes)) / 2
                   - (0.52188 - 0.5) * facade_size, 3),
        "width": facade_size, "height": facade_size, "opacity": 0.85,
    }
    data = {"schema_version": 1, "version": "4.5.5.2", "nodes": nodes,
            "edges": [list(edge) for edge in edges], "activities": ACTIVITIES,
            "edge_curves": edge_curves(atlas, coords, hash_to_id),
            "art": {"background": "art/atlas-bg-general.webp",
                    "background_layout": facade_layout,
                    "activity_backgrounds": backgrounds,
                    "frames": {kind: {state: f"art/{kind}-{state}.png"
                                      for state in ("allocated", "unallocated")}
                               for kind in ("small", "notable", "keystone")}},
            "provenance": {
                "atlas_commit": "283159358ddd925e6ac0b4ca2f54dc5737ab0c9d",
                "atlas_date": "2026-09-06", "atlas_export_version": "4.5.5.1.5",
                "tables_commit": "16088913cc941c15484b3e0bab96481106ae8879",
                "tables_date": "2026-09-15", "tables_export_version": "4.5.5.2",
                "art_commit": "27f5dad0d0979aa23a604defd11fcf7ae4668444",
                "art_export_version": "4.5.4.3", "copyright": "Grinding Gear Games",
                "input_hashes": inputs["sources"],
            }}
    validate(data)
    return data


def validate(data: dict) -> None:
    """Assert expected pinned node/choice counts, finite coordinates, unique identities and
    valid graph/effect references.
    """
    nodes = data["nodes"]
    assert len(nodes) == 575, len(nodes)
    assert sum(n["allocatable"] for n in nodes.values()) == 530
    assert sum(bool(n["choices"]) for n in nodes.values()) == 44
    assert sum(len(n["choices"]) for n in nodes.values()) == 142
    assert sum(n["kind"] == "root" for n in nodes.values()) == 7
    assert sum(n["kind"] == "decorative" for n in nodes.values()) == 38
    assert len({n["hash"] for n in nodes.values()}) == len(nodes)
    for node_id, node in nodes.items():
        assert node_id == node["id"] and node["activity"] in ACTIVITIES
        assert math.isfinite(node["x"]) and math.isfinite(node["y"])
        assert len({c["id"] for c in node["choices"]}) == len(node["choices"])
        assert all("{" not in e and "[" not in e for e in node["effects"])
        assert all("{" not in e and "[" not in e for c in node["choices"] for e in c["effects"])
    assert all(a in nodes and b in nodes and a != b for a, b in data["edges"])


def rebuild_art(directory: Path, data: dict, output: Path) -> None:
    """Crop pinned sprite sheets into runtime icons/backgrounds/frames and retain provenance
    without machine-specific paths.
    """
    from PIL import Image
    (output / "icons").mkdir(parents=True, exist_ok=True)
    (output / "art").mkdir(parents=True, exist_ok=True)
    icons = json.loads((directory / "atlasIcons.json").read_text())
    sheet = Image.open(directory / "atlas-icons.webp")
    for icon_path, crop in icons.items():
        if icon_path.startswith("_"):
            continue
        x, y, w, h = [crop[key] for key in ("x", "y", "w", "h")]
        sheet.crop((x, y, x + w, y + h)).save(output / "icons" / icon_name(icon_path))
    bg_meta = json.loads((directory / "atlasBg.json").read_text())
    sheet = Image.open(directory / "atlas-bg.webp")
    for activity in ACTIVITIES[1:]:
        crop = bg_meta[activity.lower()]
        x, y, w, h = [crop[key] for key in ("x", "y", "w", "h")]
        sheet.crop((x, y, x + w, y + h)).save(output / "art" / f"{activity.lower()}-bg.webp",
                                           lossless=True)
    shutil.copyfile(directory / "atlas-bg-general.webp", output / "art" / "atlas-bg-general.webp")
    frames = json.loads((directory / "frame.json").read_text())["frames"]
    sheet = Image.open(directory / "frame.webp")
    for kind, names in {
        "small": ("PSSkillFrameActive", "PSSkillFrame"),
        "notable": ("NotableFrameAllocated", "NotableFrameUnallocated"),
        "keystone": ("KeystoneFrameAllocated", "KeystoneFrameUnallocated"),
    }.items():
        for state, name in zip(("allocated", "unallocated"), names):
            crop = frames["frame:" + name]["frame"]
            x, y, w, h = [crop[key] for key in ("x", "y", "w", "h")]
            sheet.crop((x, y, x + w, y + h)).save(output / "art" / f"{kind}-{state}.png")
    manifest = json.loads((directory / "manifest.json").read_text())
    # Keep upstream URLs and hashes, not ephemeral execution-machine paths.
    for entry in manifest["assets"]:
        entry.pop("local_path", None)
    (output / "art-provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    """Parse local rebuild options, refresh inputs/art only when requested and write the
    validated catalog bundle.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-directory", type=Path)
    parser.add_argument("--art-directory", type=Path)
    parser.add_argument("--output", type=Path, default=BUNDLE)
    args = parser.parse_args()
    inputs_path = args.output / "sources" / "inputs.json.gz"
    if args.research_directory:
        inputs = refresh_inputs(args.research_directory)
        write_gzip(inputs_path, inputs)
    else:
        with gzip.open(inputs_path, "rt", encoding="utf-8") as handle:
            inputs = json.load(handle)
    data = make_catalog(inputs)
    if args.art_directory:
        rebuild_art(args.art_directory, data, args.output)
    write_gzip(args.output / "catalog.json.gz", data)
    print(f"Atlas {data['version']}: {len(data['nodes'])} nodes, {len(data['edges'])} edges, "
          "530 allocatable nodes, 44 choice nodes / 142 options")


if __name__ == "__main__":
    main()
