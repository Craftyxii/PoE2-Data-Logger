"""Guard publication against skipped gates, source drift and mislabeled installer artifacts."""

import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from tools import publish_verified_beta as publisher


class VerifiedBetaPublicationTests(unittest.TestCase):
    def setUp(self):
        """Create a first-attempt successful run with all gates in its one build job."""
        self.sha = "a" * 40
        self.run = {"id": 12, "repository": {"full_name": "owner/repo", "id": 7},
                    "head_repository": {"full_name": "owner/repo", "id": 7},
                    "workflow_id": 9, "path": ".github/workflows/release.yml", "event": "workflow_dispatch",
                    "head_sha": self.sha, "status": "completed", "conclusion": "success", "run_attempt": 1}
        self.jobs = {"jobs": [{"name": "build", "status": "completed", "conclusion": "success", "run_attempt": 1,
                              "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                                        for name in publisher.REQUIRED_STEPS]}]}
        self.artifacts = {"total_count": 3, "artifacts": [
            {"name": name, "id": i, "expired": False, "size_in_bytes": 10,
             "workflow_run": {"id": 12, "head_sha": self.sha, "repository_id": 7, "head_repository_id": 7}}
            for i, name in enumerate(sorted(publisher.ARTIFACTS), 100)]}

    def check(self):
        """Validate the exact requested repository, run, workflow and source."""
        return publisher.validate_provenance(self.run, self.jobs, self.artifacts,
            repository="owner/repo", run_id=12, sha=self.sha, workflow_id=9)

    def test_valid_first_attempt_returns_immutable_artifact_ids(self):
        """Artifact downloads retain the identities whose metadata was verified."""
        self.assertEqual(set(self.check()), publisher.ARTIFACTS)
        self.assertEqual(set(self.check().values()), {100, 101, 102})

    def test_source_event_attempt_and_gate_drift_are_rejected(self):
        """Success alone cannot certify another source, rerun or skipped native gate."""
        original = copy.deepcopy(self.run)
        for field, value in (("head_sha", "b" * 40), ("run_attempt", 2), ("event", "push"), ("workflow_id", 10)):
            with self.subTest(field=field):
                self.run = {**original, field: value}
                with self.assertRaises(ValueError):
                    self.check()
        self.run = original
        self.jobs["jobs"][0]["steps"][0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            self.check()
        self.jobs["jobs"].append(copy.deepcopy(self.jobs["jobs"][0]))
        with self.assertRaises(ValueError):
            self.check()

    def test_duplicate_expired_or_wrong_source_artifacts_are_rejected(self):
        """The publisher cannot select replacement artifacts by name after validating another ID."""
        original = copy.deepcopy(self.artifacts)
        for change in ("duplicate", "expired", "sha", "incomplete"):
            with self.subTest(change=change):
                self.artifacts = copy.deepcopy(original)
                if change == "duplicate":
                    self.artifacts["artifacts"].append(copy.deepcopy(self.artifacts["artifacts"][0]))
                    self.artifacts["total_count"] += 1
                elif change == "expired":
                    self.artifacts["artifacts"][0]["expired"] = True
                elif change == "sha":
                    self.artifacts["artifacts"][0]["workflow_run"]["head_sha"] = "b" * 40
                else:
                    self.artifacts["total_count"] += 1
                with self.assertRaises(ValueError):
                    self.check()

    def test_missing_native_and_session_proof_fails_closed(self):
        """Missing failures or a skipped native capture cannot be interpreted as a successful gate."""
        native = {"status": "passed", "qt_platform": "windows", "input_backend": "Win32 SendInput", "native_input_events": 2}
        session = {"completed_maps": 100, "failures": []}
        publisher.validate_reports(native, session)
        with self.assertRaises(KeyError):
            publisher.validate_reports(native, {"completed_maps": 100})
        with self.assertRaises(ValueError):
            publisher.validate_reports({**native, "native_input_events": 0}, session)
        with self.assertRaises(ValueError):
            publisher.validate_reports(native, {**session, "failures": ["ledger mismatch"]})

    def test_installer_label_bytes_checksum_and_extra_files_are_checked(self):
        """Release assets must have the exact beta name and matching executable bytes."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            name = "PoE2-Data-Logger-Setup-v1.3.2.13-beta.exe"
            (root / name).write_bytes(b"verified binary")
            digest = hashlib.sha256(b"verified binary").hexdigest()
            (root / (name + ".sha256")).write_text(f"{digest}  {name}\n")
            self.assertEqual(publisher.validate_installer(root, "v1.3.2.13-beta")[2], digest)
            (root / name).write_bytes(b"different binary")
            with self.assertRaises(ValueError):
                publisher.validate_installer(root, "v1.3.2.13-beta")
            (root / name).write_bytes(b"verified binary")
            (root / "other.exe").touch()
            with self.assertRaises(ValueError):
                publisher.validate_installer(root, "v1.3.2.13-beta")


if __name__ == "__main__":
    unittest.main()
