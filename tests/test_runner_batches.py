"""Regression harness bounds Qt lifetime with subprocess isolation rather than deleting shared widgets."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import run_tests


class RunnerBatchTests(unittest.TestCase):
    """Check full discovery coverage, profile isolation and reliable failure propagation without loading Qt."""

    def run_quietly(self, root, modules, **arguments):
        """Suppress routine batch progress while retaining the returned structured aggregate."""
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run_tests.run_module_batches(root, modules, **arguments)

    def test_batches_cover_each_module_once_with_fresh_profiles_and_exact_totals(self):
        """Preserve module order and coverage while overriding an inherited profile for every child."""
        calls = []

        def child(command, **arguments):
            """Simulate completed children with distinct reports and inspect the launched module lists."""
            names = command[command.index("--modules") + 1:command.index("--summary-file")]
            report = Path(command[-1])
            calls.append((names, arguments["env"]["RUNESHAPE_SCAN_DATA_DIR"]))
            report.write_text(json.dumps({"tests": len(names) * 3, "failures": 0, "errors": 0,
                                          "skipped": 1, "successful": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        modules = [f"test_fixture_{index}.py" for index in range(5)]
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(run_tests.os.environ, {"RUNESHAPE_SCAN_DATA_DIR": "inherited-profile"}), \
                patch.object(run_tests.subprocess, "run", side_effect=child):
            summary = self.run_quietly(Path(folder), modules, batch_size=2)
        self.assertEqual([names for names, _ in calls], [modules[:2], modules[2:4], modules[4:]])
        self.assertEqual([name for names, _ in calls for name in names], modules)
        self.assertEqual(len({profile for _, profile in calls}), 3)
        self.assertTrue(all(profile != "inherited-profile" for _, profile in calls))
        self.assertEqual((summary["tests"], summary["skipped"], summary["batches"], summary["successful"]),
                         (15, 3, 3, True))

    def test_child_crashes_missing_invalid_and_inconsistent_summaries_block_success(self):
        """Reject fatal exits even after passing assertions, and reject missing or inconsistent child evidence."""
        passed = {"tests": 2, "failures": 0, "errors": 0, "skipped": 0, "successful": True}
        cases = [(139, passed), (-11, None), (0, None), (0, {"tests": "invalid"}),
                 (0, {**passed, "failures": 1}), (0, {**passed, "errors": 1}),
                 (0, {**passed, "successful": False}), (0, {**passed, "tests": 0})]
        for returncode, report in cases:
            with self.subTest(returncode=returncode, report=report):
                def child(command, **_arguments):
                    """Return the chosen process status after optionally writing a child result."""
                    if report is not None:
                        Path(command[-1]).write_text(json.dumps(report), encoding="utf-8")
                    return subprocess.CompletedProcess(command, returncode)

                with tempfile.TemporaryDirectory() as folder, \
                        patch.object(run_tests.subprocess, "run", side_effect=child):
                    summary = self.run_quietly(Path(folder), ["test_fixture.py"])
                self.assertFalse(summary["successful"])
                self.assertEqual(len(summary["batch_failures"]), 1)
                self.assertGreater(summary["failures"] + summary["errors"], 0)

    def test_assertion_failures_aggregate_once_and_later_batches_still_run(self):
        """Retain real failure counts while completing all remaining discovered module batches."""
        called = []

        def child(command, **_arguments):
            """Fail the first child assertion and pass the second child."""
            called.append(command)
            failed = len(called) == 1
            Path(command[-1]).write_text(json.dumps({"tests": 2, "failures": int(failed), "errors": 0,
                                                     "skipped": 0, "successful": not failed}), encoding="utf-8")
            return subprocess.CompletedProcess(command, int(failed))

        with tempfile.TemporaryDirectory() as folder, patch.object(run_tests.subprocess, "run", side_effect=child):
            summary = self.run_quietly(Path(folder), ["test_first.py", "test_second.py"], batch_size=1)
        self.assertEqual(len(called), 2)
        self.assertEqual((summary["tests"], summary["failures"], summary["errors"], summary["successful"]),
                         (4, 1, 0, False))

    def test_parallel_jobs_keep_isolated_profiles_ordered_output_and_failures(self):
        """Run isolated batches concurrently while reporting them in batch order and still failing on any child."""
        launched = []

        def child(command, **arguments):
            """Write each child's report and output; fail only the second batch."""
            names = command[command.index("--modules") + 1:command.index("--summary-file")]
            launched.append((names, arguments["env"]["RUNESHAPE_SCAN_DATA_DIR"]))
            failed = names == ["test_c.py", "test_d.py"]
            # A failed child may be killed before finishing its last line.
            arguments["stdout"].write(f"ran {' '.join(names)}" + ("" if failed else "\n"))
            Path(command[-1]).write_text(json.dumps({"tests": 2, "failures": int(failed), "errors": 0,
                                                     "skipped": 0, "successful": not failed}), encoding="utf-8")
            return subprocess.CompletedProcess(command, int(failed))

        modules = ["test_a.py", "test_b.py", "test_c.py", "test_d.py", "test_e.py"]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, patch.object(run_tests.subprocess, "run", side_effect=child), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            summary = run_tests.run_module_batches(Path(folder), modules, batch_size=2, jobs=3)
        self.assertEqual(sorted(name for names, _ in launched for name in names), modules)
        self.assertEqual(len({profile for _, profile in launched}), 3)
        text = output.getvalue()
        self.assertLess(text.index("ran test_a.py test_b.py"), text.index("ran test_c.py test_d.py"))
        self.assertLess(text.index("ran test_c.py test_d.py"), text.index("ran test_e.py"))
        self.assertIn("ran test_c.py test_d.py\nBatch 3/3:", text)
        self.assertEqual((summary["tests"], summary["failures"], summary["successful"]), (6, 1, False))
        self.assertEqual([failure["modules"] for failure in summary["batch_failures"]], [["test_c.py", "test_d.py"]])

    def test_empty_duplicate_or_unbounded_module_requests_are_rejected(self):
        """Prevent empty discovery or malformed batch configuration from appearing to pass."""
        for modules, batch_size in (([], 20), (["test_a.py", "test_a.py"], 20), (["test_a.py"], 0)):
            with self.subTest(modules=modules, batch_size=batch_size), self.assertRaises(ValueError):
                self.run_quietly(Path("unused"), modules, batch_size=batch_size)

    def run_shards(self, root, modules, count, *, batch_size=2, failing=()):
        """Run every shard of one discovery with simulated children and return their summaries."""
        launched = []

        def child(command, **_arguments):
            """Record each launched batch and report it passed unless its first module should fail."""
            names = command[command.index("--modules") + 1:command.index("--summary-file")]
            launched.append(names)
            failed = int(names[0] in failing)
            Path(command[-1]).write_text(json.dumps({"tests": len(names), "failures": failed, "errors": 0,
                                                     "skipped": 0, "successful": not failed}), encoding="utf-8")
            return subprocess.CompletedProcess(command, failed)

        with patch.object(run_tests.subprocess, "run", side_effect=child):
            summaries = [self.run_quietly(root, modules, batch_size=batch_size, shard=(index, count))
                         for index in range(1, count + 1)]
        return summaries, launched

    def verify_quietly(self, root, summaries):
        """Write shard summaries as the shard jobs upload them and check them together."""
        reports = []
        for summary in summaries:
            report = root / f"shard-{summary['shard'][0]}.json"
            report.write_text(json.dumps(summary), encoding="utf-8")
            reports.append(report)
        with redirect_stderr(io.StringIO()):
            return run_tests.verify_shards(root, reports)

    def test_shards_together_run_every_batch_once_and_verification_proves_it(self):
        """Every count-th batch per shard: the shards partition discovery, and only all of them pass."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            modules = [f"test_fixture_{index}.py" for index in range(7)]
            for name in modules:
                (root / "tests" / name).write_text("", encoding="utf-8")
            summaries, launched = self.run_shards(root, modules, 3)
            # Seven modules in pairs make four batches: shard 1 runs batches 1 and 4.
            self.assertEqual([summary["modules"] for summary in summaries],
                             [modules[:2] + modules[6:], modules[2:4], modules[4:6]])
            self.assertEqual(sorted(name for names in launched for name in names), modules)
            self.assertEqual(self.verify_quietly(root, summaries),
                             {"tests": 7, "skipped": 0, "modules": 7, "shards": 3})
            rejected = {
                "a missing shard": summaries[:2],
                "a repeated shard": summaries[:2] + [dict(summaries[1], shard=[3, 3])],
                "a failed shard": summaries[:2] + [self.run_shards(root, modules, 3, failing={modules[4]})[0][2]],
                "another shard count": summaries[:2] + [dict(summaries[2], shard=[3, 4])],
                "another discovery": summaries[:2] + [dict(summaries[2], discovered_modules=modules[:6])],
                "a dropped module": summaries[:2] + [dict(summaries[2], modules=modules[4:5])],
                "a module run twice": summaries[:2] + [dict(summaries[2], modules=modules[3:6])],
            }
            for case, reports in rejected.items():
                with self.subTest(case=case), self.assertRaises(ValueError):
                    self.verify_quietly(root, reports)
            (root / "tests" / "test_fixture_new.py").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.verify_quietly(root, summaries)

    def test_out_of_range_or_empty_shards_are_rejected(self):
        """A shard outside 1..N, or one left without batches, cannot pass by running nothing."""
        modules = [f"test_fixture_{index}.py" for index in range(4)]
        for shard in ((0, 2), (3, 2), (5, 5)):
            with self.subTest(shard=shard), self.assertRaises(ValueError):
                self.run_quietly(Path("unused"), modules, batch_size=1, shard=shard)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run_tests.main(["--shard", "2-6"])

    def test_explicit_pattern_keeps_original_single_process_routing(self):
        """Preserve existing native, session and wildcard targeted invocations without launching child batches."""
        passed = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0, "successful": True}
        for pattern in ("test_native_windows_hud.py", "test_hundred_map_session.py", "test_region_*.py"):
            with self.subTest(pattern=pattern), \
                    patch.object(run_tests, "run_selected_tests", return_value=passed) as selected, \
                    patch.object(run_tests, "run_module_batches", side_effect=AssertionError("Target was batched")):
                self.assertEqual(run_tests.main(["--pattern", pattern]), 0)
                self.assertEqual(selected.call_args.kwargs, {"pattern": pattern, "modules": None})

    def test_unmatched_target_returns_nonzero_and_failed_summary(self):
        """Reject a typo in a targeted filename instead of reporting a zero-test success."""
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'result.json'
            with patch.object(unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite()), \
                    redirect_stderr(io.StringIO()):
                code = run_tests.main(['--pattern', 'test_missing_scan.py', '--summary-file', str(report)])
            summary = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual(code, 1)
        self.assertEqual(summary, {'tests': 0, 'failures': 0, 'errors': 1,
                                  'skipped': 0, 'successful': False})

    def test_one_unmatched_module_blocks_a_mixed_target_before_any_tests_run(self):
        """Prevent a valid selector from hiding an unmatched selector in the same request."""
        executed = []
        valid = unittest.TestSuite([unittest.FunctionTestCase(lambda: executed.append(True))])
        with patch.object(unittest.defaultTestLoader, 'discover', side_effect=[valid, unittest.TestSuite()]), \
                redirect_stderr(io.StringIO()):
            summary = run_tests.run_selected_tests(Path('unused'),
                                                  modules=['test_valid.py', 'test_missing.py'])
        self.assertFalse(summary['successful'])
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(executed, [])

    def test_valid_target_executes_its_tests_and_reports_success(self):
        """Keep ordinary filename and wildcard selection functional after coverage guards."""
        executed = []
        valid = unittest.TestSuite([unittest.FunctionTestCase(lambda: executed.append(True))])
        with patch.object(unittest.defaultTestLoader, 'discover', return_value=valid), \
                redirect_stderr(io.StringIO()):
            summary = run_tests.run_selected_tests(Path('unused'), pattern='test_fixture_*.py')
        self.assertEqual(executed, [True])
        self.assertEqual(summary, {'tests': 1, 'failures': 0, 'errors': 0,
                                  'skipped': 0, 'successful': True})


if __name__ == "__main__":
    unittest.main()
