"""Validate the separately frozen label helper before copying it into the app."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


HELPER_NAME = "PoE2LabelGpu"
HELPER_DESTINATION = "label_gpu"
RUNTIME_VERSIONS = {"numpy": "1.26.4", "onnxruntime-directml": "1.22.0"}
SOURCE_FILES = ("gpu_worker/worker.py", "gpu_worker/requirements.txt",
                "gpu_worker/PoE2LabelGpu.spec", "PoE2_Data_Logger/ocr/gpu_label_protocol.py")


def sha256(path):
    """Hash a file in bounded blocks for build provenance and stale-artifact checks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def helper_bundle_data(project):
    """Reject missing/stale helper output instead of silently shipping a broken toggle."""
    project = Path(project)
    helper = project / "dist" / HELPER_NAME
    executable = helper / (HELPER_NAME + ".exe")
    info_path = helper / "build-info.json"
    protocol = helper / "_internal" / "gpu_label_protocol.py"
    if not executable.is_file() or not info_path.is_file() or not protocol.is_file():
        raise FileNotFoundError("Build the isolated label GPU helper with tools/build_label_gpu.py first.")
    if info_path.stat().st_size > 65536:
        raise ValueError("Oversized GPU helper build metadata.")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if (info.get("format") != 1 or info.get("python") != [3, 12] or info.get("bits") != 64 or
            info.get("runtime_versions") != RUNTIME_VERSIONS or
            "DmlExecutionProvider" not in info.get("available_providers", ())):
        raise ValueError("GPU helper has incompatible Python/runtime/provider metadata.")
    expected = {name: sha256(project / name) for name in SOURCE_FILES}
    if info.get("source_sha256") != expected:
        raise ValueError("GPU helper is stale; rebuild it for the current worker/protocol sources.")
    if (info.get("executable_sha256") != sha256(executable) or
            sha256(protocol) != expected["PoE2_Data_Logger/ocr/gpu_label_protocol.py"]):
        raise ValueError("GPU helper executable/protocol does not match its build metadata.")
    return [(str(helper), HELPER_DESTINATION)]
