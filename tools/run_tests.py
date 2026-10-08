import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Smoke readers consult OCR settings before an individual test fixture starts.
# Keep that initialization away from the user's saved application profile.
test_data = tempfile.TemporaryDirectory(prefix="poe2-regression-data-", ignore_cleanup_errors=True)
os.environ.setdefault("RUNESHAPE_SCAN_DATA_DIR", test_data.name)
parser = argparse.ArgumentParser(description="Run isolated regression checks.")
parser.add_argument("--pattern", default="test*.py", help="Test module filename pattern")
args = parser.parse_args()
suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if os.environ.get("GITHUB_ACTIONS") == "true":
    for test, trace in result.failures + result.errors:
        trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={test.id()}::{trace}")
raise SystemExit(0 if result.wasSuccessful() else 1)
