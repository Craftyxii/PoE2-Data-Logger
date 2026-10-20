"""Hardware-independent contracts for the separately installed DirectML helper.

These use a fake runtime: passing tests do not assert that Windows/AMD execution
has been exercised on this Linux host.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from gpu_worker import worker


ADAPTERS = [
    {"name": "AMD Radeon Integrated", "index": 0, "vendor_id": 0x1002,
     "dedicated_memory": 512 * 1024**2, "flags": 0},
    {"name": "AMD Radeon RX 7900 XTX", "index": 3, "vendor_id": 0x1002,
     "dedicated_memory": 24 * 1024**3, "flags": 0},
    {"name": "Software", "index": 4, "vendor_id": 0x1414,
     "dedicated_memory": 48 * 1024**3, "flags": 2},
]


class FakeSession:
    def __init__(self, options, providers, *, gpu=True, output_transform=None, actual_providers=None):
        self.options = options
        self.providers = providers
        self.gpu = gpu
        self.output_transform = output_transform
        self.actual_providers = actual_providers or [worker.protocol.DML_PROVIDER, worker.protocol.CPU_PROVIDER]
        self.calls = []
        self.profile_calls = 0

    def get_providers(self):
        return self.actual_providers

    def get_inputs(self):
        return [SimpleNamespace(name="image", type="tensor(float)", shape=["batch", 3, 48, "width"])]

    @staticmethod
    def output(tensor):
        values = np.zeros((len(tensor), 3, worker.protocol.MODEL_CLASSES), dtype=np.float32)
        for row, sample in enumerate(tensor):
            token = int(sample[0, 0, 0]) + 10
            values[row, :, token] = np.array([0.25, 0.5, 0.75], dtype=np.float32)
            # argmax's first-index tie choice is part of the unchanged decoder.
            values[row, 1, token + 1] = 0.5
        return values

    def run(self, names, inputs):
        tensor = inputs["image"]
        self.calls.append(np.array(tensor, copy=True))
        output = self.output(tensor)
        return [self.output_transform(output) if self.output_transform else output]

    def end_profiling(self):
        self.profile_calls += 1
        path = Path(self.options.profile_file_prefix + ".json")
        path.write_text(json.dumps([
            {"cat": "Node", "args": {"provider": worker.protocol.DML_PROVIDER if self.gpu else worker.protocol.CPU_PROVIDER}},
            {"cat": "Session", "args": {"provider": worker.protocol.DML_PROVIDER}},
            {"cat": "Node", "args": {"provider": worker.protocol.CPU_PROVIDER}},
        ]), encoding="utf-8")
        return str(path)


class FakeRuntime:
    __version__ = "1.22.0"
    ExecutionMode = SimpleNamespace(ORT_SEQUENTIAL="sequential")
    GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")

    def __init__(self, **session_arguments):
        self.session_arguments = session_arguments
        self.sessions = []

    def get_available_providers(self):
        return [worker.protocol.DML_PROVIDER, worker.protocol.CPU_PROVIDER]

    @staticmethod
    def SessionOptions():
        return SimpleNamespace()

    def InferenceSession(self, model, *, sess_options, providers):
        self.model = model
        session = FakeSession(sess_options, providers, **self.session_arguments)
        self.sessions.append(session)
        return session


class GpuLabelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gpu-label-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.model = self.root / "model.onnx"
        self.model.write_bytes(b"contract model, no real inference")

    def make_worker(self, **arguments):
        runtime = FakeRuntime(**arguments)
        helper = worker.LabelWorker(runtime_loader=lambda: runtime, numpy_loader=lambda: np,
                                    adapter_loader=lambda: ADAPTERS, model_verifier=lambda path: self.model)
        self.addCleanup(helper.close)
        response = helper.dispatch({"op": "init", "protocol": 1, "threads": 2,
                                    "model_path": str(self.model), "profile_dir": str(self.root)})
        return helper, runtime, response

    def request(self, array=None, **changes):
        if array is None:
            array = np.zeros((7, *worker.protocol.LABEL_SHAPE), dtype=np.float32)
            array[:, 0, 0, 0] = np.arange(7)
        np.save(self.root / "input.npy", array)
        request = {"op": "infer", "protocol": 1, "input_path": str(self.root / "input.npy"),
                   "output_path": str(self.root / "output.npz"), "root": str(self.root), "batch_size": 6}
        request.update(changes)
        return request

    def test_pinned_model_hash_is_checked_before_runtime_import(self):
        load = Mock(side_effect=AssertionError("runtime must not load"))
        helper = worker.LabelWorker(runtime_loader=load)
        with self.assertRaisesRegex(worker.WorkerError, "pinned model"):
            helper.initialize({"model_path": str(self.model), "threads": 2, "profile_dir": str(self.root)})
        load.assert_not_called()
        digest = hashlib.sha256(self.model.read_bytes()).hexdigest()
        with patch.object(worker.protocol, "MODEL_SHA256", digest):
            self.assertEqual(worker._verified_model(str(self.model)), self.model)

    def test_dxgi_selection_uses_original_discrete_index_and_rejects_software(self):
        selected = worker.select_adapter(ADAPTERS)
        self.assertEqual(selected["index"], 3)
        self.assertEqual(selected["name"], "AMD Radeon RX 7900 XTX")
        for adapters in ([], [ADAPTERS[2]], [{**ADAPTERS[0], "flags": 2}]):
            with self.subTest(adapters=adapters), self.assertRaises(worker.WorkerError):
                worker.select_adapter(adapters)

    def test_vendor_neutral_selection_accepts_shared_memory_integrated_gpus(self):
        for vendor_id, name in ((0x10DE, "NVIDIA GeForce"), (0x8086, "Intel Graphics"),
                                (0x1002, "AMD Radeon")):
            for dedicated_memory in (0, 8 * 1024**3):
                adapter = {"name": name, "index": 5, "vendor_id": vendor_id,
                           "dedicated_memory": dedicated_memory, "flags": 0}
                with self.subTest(vendor_id=vendor_id, dedicated_memory=dedicated_memory):
                    self.assertEqual(worker.select_adapter([ADAPTERS[2], adapter]),
                                     {key: adapter[key] for key in ("name", "index", "vendor_id", "dedicated_memory")})

    def test_shared_memory_adapters_use_original_dxgi_order_for_ties(self):
        integrated = {**ADAPTERS[0], "dedicated_memory": 0, "index": 2}
        later = {**integrated, "name": "Intel Graphics", "vendor_id": 0x8086, "index": 7}
        self.assertEqual(worker.select_adapter([later, integrated])["index"], 2)
        self.assertEqual(worker.select_adapter([integrated, ADAPTERS[1]])["index"], 3)

    def test_session_options_preserve_threads_and_select_correct_dxgi_device(self):
        helper, runtime, response = self.make_worker()
        options = runtime.sessions[0].options
        self.assertEqual(options.execution_mode, "sequential")
        self.assertFalse(options.enable_mem_pattern)
        self.assertFalse(options.enable_cpu_mem_arena)
        self.assertEqual(options.intra_op_num_threads, 2)
        self.assertEqual(options.inter_op_num_threads, 1)
        self.assertEqual(options.graph_optimization_level, "all")
        self.assertEqual(runtime.sessions[0].providers,
                         [(worker.protocol.DML_PROVIDER, {"device_id": "3"}), worker.protocol.CPU_PROVIDER])
        self.assertEqual(response["provider"], worker.protocol.DML_PROVIDER)
        self.assertEqual(response["runtime"], "1.22.0")
        self.assertEqual(response["adapter"]["index"], 3)
        self.assertEqual(helper.gpu_nodes, 0)

    def test_missing_dml_provider_and_cpu_fallback_fail_closed(self):
        runtime = FakeRuntime()
        runtime.get_available_providers = lambda: [worker.protocol.CPU_PROVIDER]
        helper = worker.LabelWorker(runtime_loader=lambda: runtime, numpy_loader=lambda: np,
                                    adapter_loader=lambda: ADAPTERS, model_verifier=lambda path: self.model)
        with self.assertRaisesRegex(worker.WorkerError, "DirectML is unavailable"):
            helper.initialize({"model_path": str(self.model), "threads": 2, "profile_dir": str(self.root)})
        with self.assertRaisesRegex(worker.WorkerError, "did not select DirectML"):
            self.make_worker(actual_providers=[worker.protocol.CPU_PROVIDER])

    def test_first_inference_proves_gpu_then_preserves_batches_and_numpy_reduction(self):
        helper, runtime, _ = self.make_worker()
        request = self.request()
        expected = FakeSession.output(np.load(request["input_path"]))
        response = helper.dispatch(request)
        self.assertEqual(response, {"ok": True, "protocol": 1, "rows": 7, "steps": 3, "gpu_nodes": 1})
        with np.load(request["output_path"], allow_pickle=False) as saved:
            self.assertEqual(set(saved.files), {"indices", "probabilities"})
            np.testing.assert_array_equal(saved["indices"], expected.argmax(axis=2))
            np.testing.assert_array_equal(saved["probabilities"], expected.max(axis=2))
            self.assertEqual(saved["indices"].dtype, np.int64)
            self.assertEqual(saved["probabilities"].dtype, np.float32)
        session = runtime.sessions[0]
        self.assertEqual([len(batch) for batch in session.calls], [6, 6, 1])
        self.assertEqual(session.profile_calls, 1)
        self.assertFalse(list(self.root.glob("label-dml*.json")))
        request["output_path"] = str(self.root / "next.npz")
        helper.dispatch(request)
        self.assertEqual([len(batch) for batch in session.calls], [6, 6, 1, 6, 1])
        self.assertEqual(session.profile_calls, 1)

    def test_no_actual_dml_node_execution_fails_before_output(self):
        helper, runtime, _ = self.make_worker(gpu=False)
        request = self.request()
        with self.assertRaisesRegex(worker.WorkerError, "did not execute any DirectML"):
            helper.dispatch(request)
        self.assertFalse(Path(request["output_path"]).exists())
        self.assertEqual(len(runtime.sessions[0].calls), 1)
        with self.assertRaisesRegex(worker.WorkerError, "unavailable"):
            helper.dispatch(request)

    def test_invalid_arrays_and_batch_sizes_never_run_or_create_output(self):
        helper, runtime, _ = self.make_worker()
        arrays = [np.zeros((0, 3, 48, 320), np.float32), np.zeros((1, 3, 48, 319), np.float32),
                  np.zeros((1, 3, 48, 320), np.float64), np.zeros((3, 48, 320), np.float32),
                  np.full((1, 3, 48, 320), np.nan, np.float32), np.full((1, 3, 48, 320), np.inf, np.float32)]
        for array in arrays:
            with self.subTest(shape=array.shape, dtype=array.dtype), self.assertRaises(worker.WorkerError):
                helper.dispatch(self.request(array))
        for batch_size in (0, 65, True, 1.5, "6"):
            with self.subTest(batch_size=batch_size), self.assertRaises(worker.WorkerError):
                helper.dispatch(self.request(batch_size=batch_size))
        self.assertFalse(runtime.sessions[0].calls)
        self.assertFalse((self.root / "output.npz").exists())

    def test_input_bytes_and_rows_limits_are_enforced_without_large_allocations(self):
        helper, runtime, _ = self.make_worker()
        for name, limit in (("MAX_INPUT_BYTES", 16), ("MAX_ROWS", 1)):
            with self.subTest(limit=name), patch.object(worker.protocol, name, limit), self.assertRaises(worker.WorkerError):
                helper.dispatch(self.request())
        self.assertFalse(runtime.sessions[0].calls)

    def test_input_memmap_is_closed_before_ack_and_on_validation_failure(self):
        helper, _, _ = self.make_worker()
        request = self.request()
        original_load = np.load
        mappings = []
        def load(*args, **kwargs):
            result = original_load(*args, **kwargs)
            mappings.append(result)
            return result
        with patch.object(np, "load", side_effect=load):
            helper.dispatch(request)
            self.assertTrue(mappings[-1]._mmap.closed)
            with self.assertRaises(worker.WorkerError):
                helper.dispatch(self.request(np.zeros((1, 3, 48, 319), np.float32),
                                             output_path=str(self.root / "invalid.npz")))
            self.assertTrue(mappings[-1]._mmap.closed)

    def test_escaped_or_existing_paths_are_rejected(self):
        helper, runtime, _ = self.make_worker()
        request = self.request()
        outside = self.root.parent / "untrusted-output.npz"
        cases = [{"output_path": str(outside)}, {"input_path": str(self.model)},
                 {"root": str(self.model)}, {"output_path": str(self.root / "input.npy")},
                 {"output_path": str(self.root / "missing" / "output.npz")}]
        try:
            (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
            cases.append({"output_path": str(self.root / "escape" / "output.npz")})
        except OSError:
            pass
        (self.root / "output.npz").write_bytes(b"preserve")
        cases.append({})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises((worker.WorkerError, OSError)):
                helper.dispatch({**request, **changes})
        self.assertEqual((self.root / "output.npz").read_bytes(), b"preserve")
        self.assertFalse(runtime.sessions[0].calls)

    def test_object_pickle_payload_is_rejected(self):
        helper, runtime, _ = self.make_worker()
        with self.assertRaises(ValueError):
            helper.dispatch(self.request(np.array([object()], dtype=object)))
        self.assertFalse(runtime.sessions[0].calls)

    def test_invalid_outputs_fail_closed_and_leave_no_partial_file(self):
        transforms = [lambda output: output[:, :, :-1], lambda output: output.astype(np.float64),
                      lambda output: output[0], lambda output: output[:0],
                      lambda output: np.full_like(output, np.nan)]
        for transform in transforms:
            with self.subTest(transform=transform):
                helper, _, _ = self.make_worker(output_transform=transform)
                request = self.request()
                with self.assertRaisesRegex(worker.WorkerError, "output contract"):
                    helper.dispatch(request)
                self.assertFalse(Path(request["output_path"]).exists())
                self.assertFalse(list(self.root.glob("label-result-*.tmp")))

    def test_malformed_profile_and_inconsistent_batch_output_fail_closed(self):
        helper, runtime, _ = self.make_worker()
        session = runtime.sessions[0]
        original_end = session.end_profiling
        def malformed_profile():
            path = Path(original_end())
            path.write_text("invalid-json", encoding="utf-8")
            return str(path)
        session.end_profiling = malformed_profile
        request = self.request()
        with self.assertRaises(ValueError):
            helper.dispatch(request)
        self.assertFalse(Path(request["output_path"]).exists())
        self.assertFalse(list(self.root.glob("label-dml*.json")))

        helper, runtime, _ = self.make_worker()
        session = runtime.sessions[0]
        original_run = session.run
        def changing_output(names, inputs):
            outputs = original_run(names, inputs)
            return [outputs[0][:, :2]] if len(session.calls) > 2 else outputs
        session.run = changing_output
        with self.assertRaisesRegex(worker.WorkerError, "sequence length"):
            helper.dispatch(self.request())
        self.assertFalse(Path(request["output_path"]).exists())

    def test_incompatible_model_input_contract_is_rejected(self):
        for metadata in ([SimpleNamespace(name="image", type="tensor(double)", shape=["n", 3, 48, 320])],
                         [SimpleNamespace(name="image", type="tensor(float)", shape=["n", 3, 32, 320])],
                         [SimpleNamespace(name="image", type="tensor(float)", shape=["n", 3, 48, 640])], []):
            runtime = FakeRuntime()
            original_factory = runtime.InferenceSession
            def factory(*args, **kwargs):
                session = original_factory(*args, **kwargs)
                session.get_inputs = lambda: metadata
                return session
            runtime.InferenceSession = factory
            helper = worker.LabelWorker(runtime_loader=lambda: runtime, numpy_loader=lambda: np,
                                        adapter_loader=lambda: ADAPTERS, model_verifier=lambda path: self.model)
            with self.subTest(metadata=metadata), self.assertRaises(worker.WorkerError):
                helper.initialize({"model_path": str(self.model), "threads": 2, "profile_dir": str(self.root)})
            self.assertIsNone(helper.session)

    def test_write_failure_cleans_up_partial_output(self):
        helper, _, _ = self.make_worker()
        request = self.request()
        with patch.object(worker.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                helper.dispatch(request)
        self.assertFalse(Path(request["output_path"]).exists())
        self.assertFalse(list(self.root.glob("label-result-*.tmp")))

    def test_json_protocol_recovers_from_bad_request_and_reserves_stdout(self):
        helper = Mock()
        def dispatch(request):
            print("runtime fallback diagnostic")
            return {"ok": True, "protocol": 1, "shutdown": True}
        helper.dispatch.side_effect = dispatch
        output, unwanted, diagnostics = io.StringIO(), io.StringIO(), io.StringIO()
        with patch.object(worker.sys, "stdout", unwanted), patch.object(worker.sys, "stderr", diagnostics):
            worker.serve(helper, io.StringIO('invalid-json\n{"op":"shutdown","protocol":1}\n'), output)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertFalse(lines[0]["ok"])
        self.assertTrue(lines[1]["shutdown"])
        self.assertEqual(unwanted.getvalue(), "")
        self.assertIn("runtime fallback diagnostic", diagnostics.getvalue())

    def test_oversized_control_line_is_bounded_and_drained_as_one_request(self):
        helper = Mock()
        helper.dispatch.return_value = {"ok": True, "protocol": 1, "shutdown": True}
        requests = io.StringIO("x" * 140000 + '\n{"op":"shutdown","protocol":1}\n')
        output = io.StringIO()
        worker.serve(helper, requests, output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertIn("line limit", replies[0]["error"])
        self.assertTrue(replies[1]["shutdown"])
        helper.dispatch.assert_called_once_with({"op": "shutdown", "protocol": 1})

    def test_unknown_or_incompatible_protocol_does_not_import_runtime(self):
        load = Mock()
        helper = worker.LabelWorker(runtime_loader=load)
        for request in (None, [], {"protocol": 2, "op": "init"}, {"protocol": True, "op": "init"},
                        {"protocol": 1, "op": "unknown"}):
            with self.subTest(request=request), self.assertRaises(worker.WorkerError):
                helper.dispatch(request)
        load.assert_not_called()


class ParentLifetimeTests(unittest.TestCase):
    def test_windows_watch_uses_only_synchronize_and_exits_on_parent_death(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 123
        kernel.WaitForSingleObject.side_effect = [0x102, 0]
        exited = threading_event = __import__("threading").Event()
        watch = worker.ParentWatchdog(12345, platform="nt", kernel32=kernel,
                                      exit_process=lambda code: exited.set(), interval=0.01).start()
        self.assertTrue(threading_event.wait(1))
        watch.close()
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 12345)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_windows_watch_setup_fails_before_loading_heavy_runtime(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0
        with self.assertRaisesRegex(worker.WorkerError, "synchronize"):
            worker.ParentWatchdog(12345, platform="nt", kernel32=kernel).start()
        kernel.WaitForSingleObject.assert_not_called()
        with patch.object(worker, "ParentWatchdog") as watchdog, patch.object(worker, "LabelWorker") as helper, \
                patch.object(worker.sys, "stdout", io.StringIO()):
            watchdog.return_value.start.side_effect = worker.WorkerError("owner unavailable")
            self.assertEqual(worker.main(["--parent-pid", "12345"]), 1)
        helper.assert_not_called()

    def test_already_dead_owner_is_rejected_and_handle_closed(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 123
        kernel.WaitForSingleObject.return_value = 0
        with self.assertRaisesRegex(worker.WorkerError, "no longer available"):
            worker.ParentWatchdog(12345, platform="nt", kernel32=kernel).start()
        kernel.CloseHandle.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "posix", "POSIX parent-death contract")
    def test_actual_parent_death_terminates_idle_helper_even_when_stdin_stays_open(self):
        root = Path(__file__).resolve().parents[1]
        read_fd, write_fd = os.pipe()
        code = ("import os,subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-B','gpu_worker/worker.py','--parent-pid',str(os.getpid())],"
                "stdin=int(sys.argv[1]),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "print(p.pid,flush=True);sys.stdin.readline()")
        parent = subprocess.Popen([sys.executable, "-B", "-c", code, str(read_fd)], cwd=root,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, pass_fds=(read_fd,))
        child_pid = None
        try:
            child_pid = int(parent.stdout.readline().strip())
            time.sleep(0.15)  # Allow the isolated helper to arm its lightweight watcher.
            parent.terminate()
            parent.wait(timeout=3)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                stat = Path(f"/proc/{child_pid}/stat")
                if not stat.exists() or stat.read_text().split()[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("GPU helper survived owner death while its input pipe stayed open.")
        finally:
            os.close(read_fd)
            os.close(write_fd)
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)
            parent.stdin.close()
            parent.stdout.close()
            if child_pid is not None:
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
