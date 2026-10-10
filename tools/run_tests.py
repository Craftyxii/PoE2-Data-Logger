"""Run full discovery in bounded subprocess batches with Qt offscreen by default.

Targeted patterns keep the original single-process Qt lifetime. Child test or
process failures block success; no Qt widgets are forcibly destroyed.
Injected scan-result simulations and mocked Windows tests do not exercise native
game-overlay input or prove capture recognition."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


def run_module_batches(root, modules, *, batch_size=20):
    """Run each discovered module once in bounded child processes, rejecting test failures and abnormal exits."""
    if not modules or batch_size < 1 or len(set(modules)) != len(modules):
        raise ValueError("Use discovered modules, a positive batch size and unique module names.")
    batches = [modules[offset:offset + batch_size] for offset in range(0, len(modules), batch_size)]
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "successful": True,
               "batches": len(batches), "modules": list(modules), "batch_failures": []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="poe2-regression-batches-", ignore_cleanup_errors=True) as directory:
        for index, batch in enumerate(batches, 1):
            report = Path(directory) / f"batch-{index}.json"
            environment = dict(os.environ, RUNESHAPE_SCAN_DATA_DIR=str(Path(directory) / f"profile-{index}"))
            print(f"Batch {index}/{len(batches)}: {', '.join(batch)}", flush=True)
            completed = subprocess.run([sys.executable, str(root / "tools/run_tests.py"),
                "--modules", *batch, "--summary-file", str(report)], cwd=root, env=environment, check=False)
            child = None
            try:
                child = json.loads(report.read_text(encoding="utf-8"))
                if (not isinstance(child, dict) or not isinstance(child.get("successful"), bool)
                        or any(type(child.get(key)) is not int or child[key] < 0
                               for key in ("tests", "failures", "errors", "skipped"))):
                    child = None
            except (OSError, ValueError, TypeError):
                pass
            if child is not None:
                for key in ("tests", "failures", "errors", "skipped"):
                    summary[key] += child[key]
            if (completed.returncode != 0 or child is None or not child["successful"]
                    or child["failures"] or child["errors"]):
                summary["successful"] = False
                summary["batch_failures"].append({"modules": batch, "returncode": completed.returncode,
                                                   "summary_available": child is not None})
                if child is None or not child["failures"] and not child["errors"]:
                    summary["errors"] += 1
                print(f"Batch {index} failed: exit={completed.returncode}, valid_summary={child is not None}",
                      file=sys.stderr, flush=True)
    summary["elapsed_seconds"] = time.monotonic() - started
    print(f"\nRan {summary['tests']} tests in {summary['elapsed_seconds']:.3f}s "
          f"across {len(batches)} isolated batches", file=sys.stderr)
    if summary["successful"]:
        print(f"OK (skipped={summary['skipped']})" if summary["skipped"] else "OK", file=sys.stderr)
    else:
        print(f"FAILED (failures={summary['failures']}, errors={summary['errors']})", file=sys.stderr)
    return summary


def run_selected_tests(root, *, pattern=None, modules=None):
    """Run a targeted pattern or internal module batch with ordinary unittest and unchanged Qt lifetimes."""
    with tempfile.TemporaryDirectory(prefix="poe2-regression-data-", ignore_cleanup_errors=True) as test_data:
        # Smoke readers consult OCR settings before an individual fixture starts.
        os.environ.setdefault("RUNESHAPE_SCAN_DATA_DIR", test_data)
        if modules is None:
            suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern)
        else:
            suite = unittest.TestSuite(unittest.defaultTestLoader.discover(str(root / "tests"), pattern=name)
                                       for name in modules)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            for test, trace in result.failures + result.errors:
                trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error title={test.id()}::{trace}")
        return {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                "skipped": len(result.skipped), "successful": result.wasSuccessful()}


def main(argv=None):
    """Preserve targeted CLI runs and isolate full discovery in child batches without changing application lifetime."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    parser = argparse.ArgumentParser(description="Run isolated regression checks.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pattern", help="Test module filename pattern (single-process targeted run)")
    selection.add_argument("--modules", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("--summary-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.pattern is not None or args.modules is not None:
        summary = run_selected_tests(root, pattern=args.pattern, modules=args.modules)
    else:
        modules = sorted(path.name for path in (root / "tests").glob("test*.py") if path.is_file())
        summary = run_module_batches(root, modules)
    if args.summary_file is not None:
        args.summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
