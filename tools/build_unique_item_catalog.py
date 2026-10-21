#!/usr/bin/env python3
"""Build the offline Currency/Ritual unique-art catalog from immutable RePoE exports.

Original PNG bytes, alpha and proportions are retained. No image URL is guessed:
the source's exported DDS path identifies its published PNG in the same export.
The application never calls this development tool or downloads scan-time assets.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "PoE2_Data_Logger" / "unique_item_assets"
REPOSITORY = "https://github.com/repoe-fork/poe2"
SOURCE_SHA = "75885a099f2e9be1619aa3c793fe6ab0754a511f"
SOURCE_DATE = "2026-10-19T04:56:41Z"
GENERATOR_SHA = "6041ef0f2847bf9063713336d85b43067d057a2f"
SOURCE_HASHES = {
    "data/uniques.json": "5c303e991763d4f89c7e41306561c1eb02bb55d79ebe0c26f43520906f9c059b",
    "data/base_items.json": "cacb04644b6bfb52d04042dab8958ad34a83502b409f523e8b113d3199afe6fa",
    "version.txt": "0f0dc147ae1195274ffa9f46c1913b8bfdfb4e449676450b7b153e0c22214d71",
    "exported-version.txt": "c1eb3c1d79366a4c44f637fd90b28f29aac122393df284ff8f9d90fbc00925e5",
    "schema.txt": "d5c85cb699356e2174a08fdc64025d01dbba643e04fadacdcde5aedeba6b0eb6",
}
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_DIMENSION = 2048


def sha256(raw):
    """Hash the exact retained bytes, rather than a re-encoded image."""
    return hashlib.sha256(raw).hexdigest()


def source_url(revision, path):
    """Construct a URL only inside the selected immutable export."""
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Use a complete lowercase source commit SHA.")
    return f"https://raw.githubusercontent.com/repoe-fork/poe2/{revision}/{urllib.parse.quote(path, safe='/')}"


def download(url, maximum):
    """Fetch bounded HTTPS bytes, retrying transient transport failures only."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise ValueError("Catalog downloads must use the pinned RePoE export.")
    request = urllib.request.Request(url, headers={"User-Agent": "PoE2-Data-Logger-catalog-builder/1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > maximum:
                    raise ValueError(f"Oversized source asset: {url}")
                raw = response.read(maximum + 1)
            if len(raw) > maximum or not raw:
                raise ValueError(f"Empty or oversized source asset: {url}")
            return raw
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise
        time.sleep(.5 * (attempt + 1))
    raise RuntimeError("Source download did not finish.")


def read_source(cache, revision, path, *, offline=False, maximum=MAX_SOURCE_BYTES):
    """Reuse a revision-specific cache; verify legacy pinned research inputs."""
    cache = Path(cache)
    target = cache / revision / path
    legacy = cache / path.replace("/", "_")
    if target.is_file():
        raw = target.read_bytes()
    elif revision == SOURCE_SHA and path in SOURCE_HASHES and legacy.is_file():
        raw = legacy.read_bytes()
    elif offline:
        raise FileNotFoundError(f"Source cache is missing {revision}/{path}")
    else:
        raw = download(source_url(revision, path), maximum)
    if not raw or len(raw) > maximum:
        raise ValueError(f"Empty or oversized cached asset: {path}")
    expected = SOURCE_HASHES.get(path) if revision == SOURCE_SHA else None
    if expected is not None and sha256(raw) != expected:
        raise ValueError(f"Pinned source hash mismatch: {path}")
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return raw


def artwork_path(row):
    """Require an exported artwork identity and safe source-relative DDS path."""
    visual = row.get("visual_identity")
    if not isinstance(visual, dict):
        raise ValueError("Unique item has no exported visual identity.")
    art = visual.get("dds_file")
    if not isinstance(art, str):
        raise ValueError("Unique item has no exported artwork path.")
    path = PurePosixPath(art)
    if (not art.startswith("Art/") or path.suffix.lower() != ".dds"
            or ".." in path.parts or "\\" in art or any(ord(c) < 32 for c in art)):
        raise ValueError(f"Unsupported source artwork path: {art!r}")
    return art, "data/" + str(path.with_suffix(".png"))


def validate_image(raw):
    """Inspect original PNG dimensions/mode without modifying its bytes."""
    from PIL import Image

    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Unique artwork must be a bounded original PNG.")
    with Image.open(io.BytesIO(raw)) as image:
        if (image.format != "PNG" or image.mode not in ("RGB", "RGBA")
                or min(image.size) < 1 or max(image.size) > MAX_IMAGE_DIMENSION):
            raise ValueError("Unique artwork has unsupported dimensions or color mode.")
        dimensions, mode = image.size, image.mode
        image.verify()
    return dimensions, mode


def validate_unique(identifier, row):
    """Validate canonical source fields and supported complete inventory footprints."""
    if not isinstance(identifier, str) or not identifier or not isinstance(row, dict):
        raise ValueError("Unique source entries must have string identifiers and records.")
    for key in ("id", "name", "item_class"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError(f"Unique {identifier} has invalid {key}.")
    width, height = row.get("inventory_width"), row.get("inventory_height")
    if type(width) is not int or type(height) is not int or not 1 <= width <= 2 or not 1 <= height <= 4:
        raise ValueError(f"Unique {identifier} has an unsupported inventory footprint.")
    if type(row.get("is_alternate_art")) is not bool:
        raise ValueError(f"Unique {identifier} has no alternate-art status.")
    artwork_path(row)


def make_manifest(uniques, bases, images, source):
    """Group every unique and source-backed ordinary-art alternative conservatively."""
    groups = {}
    paths = defaultdict(set)
    for identifier, row in sorted(uniques.items()):
        validate_unique(identifier, row)
        art, png = artwork_path(row)
        raw = images[png]
        dimensions, mode = validate_image(raw)
        digest = sha256(raw)
        group = groups.setdefault(digest, {"id": digest, "names": set(), "unique_ids": [], "base_items": []})
        group["names"].add(row["name"])
        group["unique_ids"].append(identifier)
        paths[art.casefold()].add(digest)
    for identifier, row in sorted(bases.items()):
        if not isinstance(row, dict):
            raise ValueError("Base item source contains a malformed record.")
        visual = row.get("visual_identity") or {}
        art = visual.get("dds_file")
        if not isinstance(art, str) or art.casefold() not in paths:
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Shared ordinary artwork has no canonical base name.")
        alternative = {"id": identifier, "name": name, "columns": row.get("inventory_width"),
                       "rows": row.get("inventory_height"), "release_state": row.get("release_state")}
        for digest in paths[art.casefold()]:
            groups[digest]["names"].add(name)
            groups[digest]["base_items"].append(alternative)
    for group in groups.values():
        group["names"] = sorted(group["names"])
    items = []
    for identifier, row in sorted(uniques.items()):
        art, png = artwork_path(row)
        raw = images[png]
        dimensions, mode = validate_image(raw)
        digest = sha256(raw)
        group = groups[digest]
        items.append({"id": identifier, "source_id": row["id"], "name": row["name"],
                      "item_class": row["item_class"], "base_id": None,
                      "columns": row["inventory_width"], "rows": row["inventory_height"],
                      "icon": f"icons/{digest}.png", "image_sha256": digest,
                      "source_url": source_url(source["revision"], png),
                      "art_id": row["visual_identity"]["id"], "art_path": art,
                      "art_group": digest, "shared_names": [name for name in group["names"] if name != row["name"]],
                      "shared_base_items": group["base_items"],
                      "image_width": dimensions[0], "image_height": dimensions[1], "image_mode": mode,
                      "is_alternate_art": row["is_alternate_art"]})
    return {"schema_version": 1, "source": source, "items": items,
            "art_groups": [groups[key] for key in sorted(groups)], "exclusions": []}


def build(output=DEFAULT_OUTPUT, *, source_cache, source_sha=SOURCE_SHA, workers=6, offline=False):
    """Fetch immutable inputs, verify all assets, then publish an offline manifest last."""
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("Use from one to eight download workers.")
    source_url(source_sha, "version.txt")
    files, contents = {}, {}
    for path in SOURCE_HASHES:
        raw = read_source(source_cache, source_sha, path, offline=offline)
        contents[path] = raw
        files[path] = {"url": source_url(source_sha, path), "sha256": sha256(raw)}
    uniques = json.loads(contents["data/uniques.json"])
    bases = json.loads(contents["data/base_items.json"])
    if not isinstance(uniques, dict) or not uniques or not isinstance(bases, dict):
        raise ValueError("Source unique/base tables must be populated dictionaries.")
    paths = set()
    for identifier, row in uniques.items():
        validate_unique(identifier, row)
        paths.add(artwork_path(row)[1])
    images, failures = {}, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(read_source, source_cache, source_sha, path,
                               offline=offline, maximum=MAX_IMAGE_BYTES): path for path in sorted(paths)}
        for completed in as_completed(pending):
            path = pending[completed]
            try:
                raw = completed.result()
                validate_image(raw)
                images[path] = raw
            except Exception as error:
                failures.append(f"{path} ({source_url(source_sha, path)}): {type(error).__name__}: {error}")
    if failures:
        raise ValueError("Catalog artwork is incomplete; no catalog was written:\n" + "\n".join(sorted(failures)))
    version = contents["version.txt"].decode("utf-8").strip()
    exported = contents["exported-version.txt"].decode("utf-8").strip()
    if not version or version != exported:
        raise ValueError("Source version and exported version disagree.")
    schema = contents["schema.txt"].decode("utf-8").split()[0]
    if not re.fullmatch(r"[a-f0-9]{40}", schema):
        raise ValueError("Source schema revision is unavailable.")
    source = {"repository": REPOSITORY, "revision": source_sha, "version": version,
              "schema_revision": schema, "files": files,
              "license": "Generated game data and artwork © Grinding Gear Games; subject to GGG terms. "
                         "RePoE software is MIT; artwork is not MIT.",
              "generator_reference": {"repository": "https://github.com/repoe-fork/repoe", "revision": GENERATOR_SHA,
                                      "license_url": f"https://github.com/repoe-fork/repoe/blob/{GENERATOR_SHA}/LICENSE.md",
                                      "note": "Inspected generator source; the export does not record its generating software revision."},
              "mapping_note": "This export does not provide a unique-to-base Metadata ID mapping. "
                              "base_id is null; shared_base_items records shared artwork, not an inferred base relationship.",
              "eligibility_note": "The source cannot confirm that exported uniques are available in-game, "
                                  "drop-enabled or eligible for Ritual rewards."}
    if source_sha == SOURCE_SHA:
        source["revision_date"] = SOURCE_DATE
    manifest = make_manifest(uniques, bases, images, source)
    output = Path(output)
    icons = output / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for raw in images.values():
        (icons / f"{sha256(raw)}.png").write_bytes(raw)
    raw_manifest = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = output / "manifest.json.tmp"
    temporary.write_bytes(raw_manifest)
    temporary.replace(output / "manifest.json")
    return manifest


def main(argv=None):
    """Expose explicit development-time online and cached/offline rebuild modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-sha", default=SOURCE_SHA)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--offline", action="store_true", help="Require all source and PNG bytes in the cache")
    args = parser.parse_args(argv)
    manifest = build(args.output, source_cache=args.source_cache, source_sha=args.source_sha,
                     workers=args.workers, offline=args.offline)
    print(json.dumps({"source_revision": manifest["source"]["revision"], "items": len(manifest["items"]),
                      "images": len(manifest["art_groups"]), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
