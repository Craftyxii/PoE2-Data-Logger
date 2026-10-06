import os
import sys
import tempfile


def run():
    if "--smoke-test" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="poe2-build-check-") as directory:
            os.environ["RUNESHAPE_SCAN_DATA_DIR"] = directory
            from PoE2_Data_Logger.ui.native_desktop import main
            return main(smoke_test=True)
    from PoE2_Data_Logger.ui.native_desktop import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
