import os
from pathlib import Path
import sys
import tempfile
import traceback


def run():
    if "--smoke-test" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="poe2-build-check-") as directory:
            os.environ["RUNESHAPE_SCAN_DATA_DIR"] = directory
            try:
                from PoE2_Data_Logger.ui.native_desktop import main
                return main(smoke_test=True)
            except Exception:
                detail = traceback.format_exc()
                report = os.environ.get("POE2_SMOKE_REPORT")
                if report:
                    Path(report).write_text(detail, encoding="utf-8")
                if sys.stderr is not None:
                    print(detail, file=sys.stderr)
                return 1
    from PoE2_Data_Logger.ui.native_desktop import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
