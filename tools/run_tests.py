"""Run full discovery in bounded subprocess batches with Qt offscreen by default.

Targeted patterns keep the original single-process Qt lifetime. Child test or
process failures block success; no Qt widgets are forcibly destroyed.
Injected scan-result simulations and mocked Windows tests do not exercise native
game-overlay input or prove capture recognition."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest


def run_module_batches(root, modules, *, batch_size=20, jobs=1, shard=None):
    """Run each discovered module once in bounded child processes, rejecting test failures and abnormal exits.

    Batches are already isolated (own process and data profile), so ``jobs`` may
    run several at once; their output is buffered and printed in batch order.
    ``shard`` (index, count) keeps every count-th batch from index, so the shards
    of one discovery together run each batch exactly once.
    """
    if not modules or batch_size < 1 or jobs < 1 or len(set(modules)) != len(modules):
        raise ValueError("Use discovered modules, a positive batch size and job count, and unique module names.")
    batches = [modules[offset:offset + batch_size] for offset in range(0, len(modules), batch_size)]
    total = len(batches)
    numbered = list(enumerate(batches, 1))
    if shard is not None:
        index, count = shard
        if not 1 <= index <= count:
            raise ValueError("Use a shard index from 1 to the shard count.")
        numbered = [(number, batch) for number, batch in numbered if (number - 1) % count == index - 1]
        if not numbered:
            raise ValueError(f"Shard {index}/{count} has no batches; use at most {total} shards.")
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "successful": True,
               "batches": len(numbered), "modules": [name for _, batch in numbered for name in batch],
               "discovered_modules": list(modules), "shard": list(shard) if shard else None,
               "batch_failures": []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="poe2-regression-batches-", ignore_cleanup_errors=True) as directory:
        def run_batch(index, batch):
            """Launch one isolated child and return its exit status, report path and buffered output."""
            report = Path(directory) / f"batch-{index}.json"
            environment = dict(os.environ, RUNESHAPE_SCAN_DATA_DIR=str(Path(directory) / f"profile-{index}"))
            command = [sys.executable, str(root / "tools/run_tests.py"),
                       "--modules", *batch, "--summary-file", str(report)]
            if jobs == 1:
                print(f"Batch {index}/{total}: {', '.join(batch)}", flush=True)
                return subprocess.run(command, cwd=root, env=environment, check=False), report, None
            log = Path(directory) / f"batch-{index}.log"
            with log.open("w", encoding="utf-8", errors="replace") as output:
                completed = subprocess.run(command, cwd=root, env=environment, check=False,
                                           stdout=output, stderr=subprocess.STDOUT)
            return completed, report, log.read_text(encoding="utf-8", errors="replace")

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            pending = [(index, batch, pool.submit(run_batch, index, batch)) for index, batch in numbered]
            for index, batch, future in pending:
                completed, report, output = future.result()
                if output is not None:
                    # A killed child can stop mid-line; keep the failure notice on its own line.
                    print(f"Batch {index}/{total}: {', '.join(batch)}\n{output}",
                          end="" if output.endswith("\n") else "\n", flush=True)
                child = None
                try:
                    child = json.loads(report.read_text(encoding="utf-8"))
                    if (not isinstance(child, dict) or not isinstance(child.get("successful"), bool)
                            or any(type(child.get(key)) is not int or child[key] < 0
                                   for key in ("tests", "failures", "errors", "skipped"))
                            or child["tests"] == 0):
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
          f"across {len(numbered)} isolated batches", file=sys.stderr)
    if summary["successful"]:
        print(f"OK (skipped={summary['skipped']})" if summary["skipped"] else "OK", file=sys.stderr)
    else:
        print(f"FAILED (failures={summary['failures']}, errors={summary['errors']})", file=sys.stderr)
    return summary


def discovered_modules(root):
    """Return the full-discovery test modules in batch order."""
    return sorted(path.name for path in (root / "tests").glob("test*.py") if path.is_file())


def verify_shards(root, reports):
    """Require shard reports of this discovery to have run every module exactly once and passed.

    Each shard alone proves only its own batches; together they must cover the
    discovery with no module skipped, repeated or failed.
    """
    modules = discovered_modules(root)
    summaries = [json.loads(Path(report).read_text(encoding="utf-8")) for report in reports]
    shards = [summary.get("shard") for summary in summaries]
    if (not summaries or any(type(shard) is not list or len(shard) != 2 for shard in shards)
            or len({count for _, count in shards}) != 1
            or sorted(index for index, _ in shards) != list(range(1, shards[0][1] + 1))):
        raise ValueError("Shard reports must be exactly shards 1 to N of one shard count.")
    for summary in summaries:
        if (summary.get("discovered_modules") != modules or summary.get("successful") is not True
                or summary.get("failures") != 0 or summary.get("errors") != 0
                or type(summary.get("tests")) is not int or summary["tests"] <= 0
                or summary.get("batch_failures") != []):
            raise ValueError(f"Shard {summary['shard'][0]}/{summary['shard'][1]} did not pass this discovery.")
    ran = [name for summary in summaries for name in summary["modules"]]
    if len(ran) != len(set(ran)) or sorted(ran) != modules:
        missing, repeated = sorted(set(modules) - set(ran)), sorted({name for name in ran if ran.count(name) > 1})
        raise ValueError(f"Shards must run each module once; missing {missing}, repeated {repeated}.")
    totals = {key: sum(summary[key] for summary in summaries) for key in ("tests", "skipped")}
    print(f"{len(summaries)} shards ran all {len(modules)} modules once: "
          f"{totals['tests']} tests passed (skipped={totals['skipped']})", file=sys.stderr)
    return {**totals, "modules": len(modules), "shards": len(summaries)}


def run_selected_tests(root, *, pattern=None, modules=None):
    """Reject selectors without tests before running a targeted suite with unchanged Qt lifetimes."""
    with tempfile.TemporaryDirectory(prefix="poe2-regression-data-", ignore_cleanup_errors=True) as test_data:
        # Smoke readers consult OCR settings before an individual fixture starts.
        os.environ.setdefault("RUNESHAPE_SCAN_DATA_DIR", test_data)
        selections = [pattern] if modules is None else modules
        suites = [unittest.defaultTestLoader.discover(str(root / "tests"), pattern=name)
                  for name in selections]
        empty = [name for name, suite in zip(selections, suites) if suite.countTestCases() == 0]
        if empty or not suites:
            print(f"No tests matched selectors: {', '.join(empty) or '(empty selection)'}", file=sys.stderr)
            return {"tests": 0, "failures": 0, "errors": max(1, len(empty)),
                    "skipped": 0, "successful": False}
        suite = unittest.TestSuite(suites)
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
    parser.add_argument("--jobs", type=int, default=1,
                        help="Run this many isolated full-discovery batches at once (default 1)")
    selection.add_argument("--shard", metavar="K/N",
                           help="Run full discovery's shard K of N; the N shards run every batch once")
    selection.add_argument("--verify-shards", metavar="REPORT", nargs="+", type=Path,
                           help="Check that these shard summary files ran every module once and passed")
    args = parser.parse_args(argv)
    if args.verify_shards is not None:
        try:
            verify_shards(root, args.verify_shards)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"Shard verification failed: {error}", file=sys.stderr)
            return 1
        return 0
    if args.pattern is not None or args.modules is not None:
        summary = run_selected_tests(root, pattern=args.pattern, modules=args.modules)
    else:
        shard = None
        if args.shard is not None:
            match = re.fullmatch(r"([0-9]+)/([0-9]+)", args.shard)
            if not match:
                parser.error("--shard takes K/N, for example 2/6")
            shard = (int(match.group(1)), int(match.group(2)))
        summary = run_module_batches(root, discovered_modules(root), jobs=args.jobs, shard=shard)
    if args.summary_file is not None:
        args.summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
