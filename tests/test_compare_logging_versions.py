"""Logging and export match an earlier release cell by cell, apart from intended changes.

The scripted session runs twice on the current source to prove it is deterministic, so a
difference against another tree is a real change. Set POE2_COMPARE_OLD_TREE to a folder
holding an earlier release's PoE2_Data_Logger package (for example 1.3.2.27, extracted
with git archive) to compare against it.
"""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compare_logging_versions", ROOT / "tools" / "compare_logging_versions.py")
compare_logging_versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_logging_versions)


class LoggingVersionComparisonTests(unittest.TestCase):
    def run_session(self, tree):
        with tempfile.TemporaryDirectory(prefix="poe2-compare-test-") as folder:
            return compare_logging_versions.run(tree, folder)

    def test_scripted_session_is_deterministic_and_every_step_succeeds(self):
        first, second = self.run_session(ROOT), self.run_session(ROOT)
        self.assertEqual([result for _, result in first["steps"]], ["ok"] * len(first["steps"]))
        self.assertEqual(compare_logging_versions.compare(first, second), [])
        self.assertTrue(all(first["exports"][name] for name in compare_logging_versions.EXPORTS))

    @unittest.skipUnless(os.environ.get("POE2_COMPARE_OLD_TREE"), "set POE2_COMPARE_OLD_TREE to an earlier release tree")
    def test_logging_matches_the_earlier_release_except_intended_changes(self):
        old = self.run_session(Path(os.environ["POE2_COMPARE_OLD_TREE"]))
        new = self.run_session(ROOT)
        unexpected = [entry for entry in compare_logging_versions.compare(old, new) if not entry["intended"]]
        self.assertEqual(unexpected, [])


if __name__ == "__main__":
    unittest.main()
