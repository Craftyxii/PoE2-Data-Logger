"""Keep frozen child handling before desktop imports and verify the real child smoke route."""

import builtins
from concurrent.futures import Future
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PoE2_Data_Logger import __main__ as entry


class LaunchOCRWorkerTests(unittest.TestCase):
    """Check the installed entry point can handle worker arguments before any Qt desktop startup."""

    def test_freeze_support_precedes_desktop_import_and_startup(self):
        """Frozen multiprocessing children are handled before importing the module that creates desktop controls."""
        events = []
        main = Mock(side_effect=lambda: events.append("main") or 0)
        original_import = builtins.__import__

        def import_desktop(name, *args, **kwargs):
            """Observe the desktop import while preserving every unrelated import normally."""
            if name == "PoE2_Data_Logger.ui.native_desktop":
                events.append("desktop-import")
                return SimpleNamespace(main=main)
            return original_import(name, *args, **kwargs)

        with patch.object(entry.sys, "argv", ["PoE2-Data-Logger.exe"]), \
                patch.object(entry.multiprocessing, "freeze_support", side_effect=lambda: events.append("freeze")), \
                patch.object(builtins, "__import__", side_effect=import_desktop):
            self.assertEqual(entry.run(), 0)
        self.assertEqual(events, ["freeze", "desktop-import", "main"])

    def test_handled_child_exits_without_importing_the_desktop(self):
        """When freeze_support handles a child and exits, the launcher does not open another application window."""
        original_import = builtins.__import__

        def forbid_desktop(name, *args, **kwargs):
            """Fail if child launch reaches the normal desktop import."""
            if name == "PoE2_Data_Logger.ui.native_desktop":
                raise AssertionError("Worker launch reached desktop startup.")
            return original_import(name, *args, **kwargs)

        with patch.object(entry.multiprocessing, "freeze_support", side_effect=SystemExit(0)), \
                patch.object(builtins, "__import__", side_effect=forbid_desktop):
            with self.assertRaises(SystemExit):
                entry.run()

    def test_smoke_timeout_reaps_workers_before_installer_outer_limit(self):
        """The 60-second installer guard cannot kill the parent before its smoke reader's shorter deadline and cleanup."""
        from PoE2_Data_Logger.ui import native_desktop as desktop
        failed = Future()
        failed.set_exception(TimeoutError("Bundled recognition stalled."))
        application, lock = Mock(), Mock()
        before = time.monotonic()
        with patch.object(desktop, "QApplication", return_value=application), \
                patch.object(desktop, "QIcon"), \
                patch.object(desktop, "instance_lock", return_value=lock), \
                patch.object(desktop.logger, "initialize"), \
                patch.object(desktop.ocr_runtime, "active_threads", return_value=2), \
                patch.object(desktop.ocr_workers, "submit", return_value=failed) as submit, \
                patch.object(desktop.ocr_workers, "shutdown") as shutdown:
            with self.assertRaisesRegex(TimeoutError, "Bundled recognition stalled"):
                desktop.main(smoke_test=True)
        descriptor = submit.call_args.args[0]
        self.assertIs(descriptor.function, desktop._bundled_ocr_smoke)
        self.assertGreaterEqual(descriptor.deadline, before + 45)
        self.assertLessEqual(descriptor.deadline, time.monotonic() + 45)
        self.assertLess(descriptor.deadline - before + desktop.ocr_workers.SHUTDOWN_JOIN_SECONDS, 60)
        shutdown.assert_called_once_with(wait=True)
        lock.unlock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
