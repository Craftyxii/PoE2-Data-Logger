"""Publish one successful installer run with Actions credentials and explicit provenance guards."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


REQUIRED_STEPS = {
    "Check release version", "Check Windows display and export fixtures",
    "Check native Windows HUD interaction", "Verify 100-map user workflow",
    "Run regression tests", "Build and verify installer",
    "Check installer, update and saved-file retention", "Create checksum",
}
ARTIFACTS = {"native-windows-hud-verification", "release-session-verification", "windows-installer"}


def validate_provenance(run, jobs, artifacts, *, repository, run_id, sha, workflow_id):
    """Require one successful first attempt, its build gates and uniquely attributable artifacts."""
    if (not re.fullmatch(r"[0-9a-f]{40}", sha) or run["id"] != run_id
            or run["repository"]["full_name"] != repository
            or run["head_repository"]["full_name"] != repository
            or run["workflow_id"] != workflow_id or run["path"] != ".github/workflows/release.yml"
            or run["event"] != "workflow_dispatch" or run["head_sha"] != sha
            or run["status"] != "completed" or run["conclusion"] != "success"
            or run["run_attempt"] != 1):
        raise ValueError("Run is not the requested successful installer source/first attempt.")
    builds = [job for job in jobs["jobs"] if job["name"] == "build"]
    if (len(builds) != 1 or builds[0]["status"] != "completed" or builds[0]["conclusion"] != "success"
            or builds[0]["run_attempt"] != 1):
        raise ValueError("Require a single successful build job from the selected attempt.")
    for name in REQUIRED_STEPS:
        matching = [step for step in builds[0]["steps"] if step["name"] == name]
        if len(matching) != 1 or matching[0]["status"] != "completed" or matching[0]["conclusion"] != "success":
            raise ValueError(f"Required gate did not pass: {name}")
    if artifacts["total_count"] != len(artifacts["artifacts"]):
        raise ValueError("Artifact listing is incomplete.")
    verified = {}
    for name in ARTIFACTS:
        matching = [artifact for artifact in artifacts["artifacts"] if artifact["name"] == name]
        if len(matching) != 1:
            raise ValueError(f"Artifact missing or ambiguous: {name}")
        artifact = matching[0]
        origin = artifact["workflow_run"]
        if (artifact["expired"] or artifact["size_in_bytes"] <= 0 or origin["id"] != run_id
                or origin["head_sha"] != sha or origin["repository_id"] != run["repository"]["id"]
                or origin["head_repository_id"] != run["head_repository"]["id"]):
            raise ValueError(f"Artifact is not attributable to the verified run: {name}")
        verified[name] = artifact["id"]
    return verified


def validate_reports(native, session):
    """Reject absent, skipped or failed native/session proof instead of assuming success."""
    if (native["status"] != "passed" or native["qt_platform"] != "windows"
            or native["input_backend"] != "Win32 SendInput" or native["native_input_events"] <= 0
            or session["completed_maps"] != 100 or session["failures"] != []):
        raise ValueError("Native HUD or 100-map proof did not pass.")


def validate_installer(directory, tag):
    """Bind the only two artifact files to a strict beta label and verify the actual executable hash."""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+(?:\.[0-9]+){0,2}-beta", tag):
        raise ValueError("Use a numeric beta tag.")
    name = f"PoE2-Data-Logger-Setup-{tag}.exe"
    exe = directory / name
    checksum = directory / (name + ".sha256")
    if {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()} != {name, checksum.name}:
        raise ValueError("Installer artifact contains unexpected or missing files.")
    parts = checksum.read_text(encoding="ascii").split()
    if len(parts) != 2 or parts[1] != name or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
        raise ValueError("Installer checksum label is invalid.")
    with exe.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != parts[0]:
        raise ValueError("Installer checksum mismatch.")
    return exe, checksum, actual


def gh(*args):
    """Use the injected Actions token without login or retry loops."""
    try:
        return subprocess.run(["gh", *map(str, args)], check=True, text=True, capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        # Retain the API's reason so permission failures can be diagnosed once.
        detail = (error.stderr or "GitHub CLI returned no error detail.").strip()
        raise RuntimeError(f"GitHub CLI failed ({error.returncode}): {detail}") from error


def api(endpoint, *args):
    """Decode API metadata without exposing authentication configuration."""
    return json.loads(gh("api", endpoint, *args))


def download_artifact(prefix, artifact_id, destination):
    """Download by immutable ID and reject archive paths outside the isolated artifact directory."""
    archive = destination.with_suffix(".zip")
    with archive.open("wb") as stream:
        subprocess.run(["gh", "api", f"{prefix}/actions/artifacts/{artifact_id}/zip"],
                       check=True, stdout=stream, stderr=subprocess.PIPE)
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise ValueError("Artifact archive path is unsafe.")
        zipped.extractall(destination)


def main():
    """Verify source gates/artifacts before creating a fixed-source tag and publishing its installer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["VERIFIED_RUN_ID"])
    sha, tag = os.environ["VERIFIED_SHA"], os.environ["BETA_TAG"]
    notes = os.environ["BETA_RELEASE_NOTES"]
    prefix = f"repos/{repository}"
    run = api(f"{prefix}/actions/runs/{run_id}")
    workflow = api(f"{prefix}/actions/workflows/release.yml")
    jobs = api(f"{prefix}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    artifacts = api(f"{prefix}/actions/runs/{run_id}/artifacts?per_page=100")
    verified = validate_provenance(run, jobs, artifacts, repository=repository, run_id=run_id, sha=sha, workflow_id=workflow["id"])
    if sha not in notes or f"/actions/runs/{run_id}" not in notes:
        raise ValueError("Release notes must identify the verified source and run.")
    with tempfile.TemporaryDirectory(prefix="verified-beta-") as temp:
        directory = Path(temp)
        for name, artifact_id in verified.items():
            download_artifact(prefix, artifact_id, directory / name)
        native = json.loads((directory / "native-windows-hud-verification/native-hud-report.json").read_text())
        session = json.loads((directory / "release-session-verification/metrics.json").read_text())
        validate_reports(native, session)
        exe, checksum, digest = validate_installer(directory / "windows-installer", tag)
        print(f"Verified installer source {sha}; SHA-256 {digest}", flush=True)
        if args.check_only:
            return
        refs = api(f"{prefix}/git/matching-refs/tags/{tag}")
        matching = [ref for ref in refs if ref["ref"] == f"refs/tags/{tag}"]
        if not matching:
            api(f"{prefix}/git/refs", "--method", "POST", "-f", f"ref=refs/tags/{tag}", "-f", f"sha={sha}")
        ref = api(f"{prefix}/git/ref/tags/{tag}")
        if ref["object"]["type"] != "commit" or ref["object"]["sha"] != sha:
            raise ValueError("Tag does not point directly to the verified source; no retagging allowed.")
        notes_file = directory / "release-notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        print(gh("release", "create", tag, exe, checksum, "--repo", repository, "--verify-tag",
                 "--prerelease", "--latest=false", "--title", f"PoE2 Data Logger {tag[1:-5]} Beta",
                 "--notes-file", notes_file), flush=True)


if __name__ == "__main__":
    main()
