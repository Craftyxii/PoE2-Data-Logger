import os
from pathlib import Path
import sys
import unittest


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
suite = unittest.defaultTestLoader.discover(str(root / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if os.environ.get("GITHUB_ACTIONS") == "true":
    for test, trace in result.failures + result.errors:
        trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={test.id()}::{trace}")
raise SystemExit(0 if result.wasSuccessful() else 1)
