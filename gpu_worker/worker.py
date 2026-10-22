"""Isolated, opt-in DirectML label inference; no application or capture imports.

The owning OCR process passes its PID explicitly. Its lifetime is watched before
loading numpy or ONNX Runtime, so terminating that process cannot orphan a GPU
session. Standard output is reserved for the JSON-lines protocol.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading


def _load_protocol():
    """Load shared wire constants directly without importing the application package."""
    if getattr(sys, "frozen", False):
        path = Path(sys._MEIPASS) / "gpu_label_protocol.py"
    else:
        path = Path(__file__).resolve().parents[1] / "PoE2_Data_Logger" / "ocr" / "gpu_label_protocol.py"
    spec = importlib.util.spec_from_file_location("_poe2_gpu_label_protocol", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("GPU label protocol is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = _load_protocol()


class WorkerError(ValueError):
    """An invalid or unsupported helper request, eligible for CPU retry."""


def _integer(value, name, minimum, maximum):
    """Reject booleans and out-of-range request integers before allocating worker resources."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WorkerError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


class ParentWatchdog:
    """Watch only the explicitly supplied owner, never an application/game process."""

    def __init__(self, parent_pid, *, platform=None, kernel32=None, exit_process=None, interval=0.1):
        """Bind lifetime monitoring to the explicit OCR owner and injectable process adapters."""
        self.parent_pid = _integer(parent_pid, "parent_pid", 1, 2**32 - 1)
        if self.parent_pid == os.getpid():
            raise WorkerError("The helper cannot watch itself as its owner.")
        self.platform = os.name if platform is None else platform
        self.kernel32 = kernel32
        self.exit_process = os._exit if exit_process is None else exit_process
        self.interval = interval
        self._stop = threading.Event()
        self._handle = None
        self._thread = None

    def start(self):
        """Verify owner liveness and start polling using only the required synchronization access."""
        if self.platform == "nt":
            if self.kernel32 is None:
                self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                self.kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                self.kernel32.OpenProcess.restype = ctypes.c_void_p
                self.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                self.kernel32.WaitForSingleObject.restype = ctypes.c_uint32
                self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                self.kernel32.CloseHandle.restype = ctypes.c_int
            # SYNCHRONIZE alone: no process read, write, query, or game access.
            self._handle = self.kernel32.OpenProcess(0x00100000, False, self.parent_pid)
            if not self._handle:
                raise WorkerError("Cannot synchronize with the GPU helper owner.")
            state = self.kernel32.WaitForSingleObject(self._handle, 0)
            if state != 0x00000102:  # WAIT_TIMEOUT means the owner is alive.
                self.close()
                raise WorkerError("GPU helper owner is no longer available.")
            target = self._watch_windows
        elif self.platform == "posix":
            if os.getppid() != self.parent_pid:
                raise WorkerError("GPU helper owner must be its current parent.")
            target = self._watch_posix
        else:
            raise WorkerError("GPU helper parent synchronization is unsupported.")
        self._thread = threading.Thread(target=target, name="label-gpu-owner", daemon=True)
        self._thread.start()
        return self

    def _watch_windows(self):
        """Terminate the helper when its owner signals or waiting fails, except during explicit shutdown."""
        while not self._stop.is_set():
            state = self.kernel32.WaitForSingleObject(self._handle, max(1, int(self.interval * 1000)))
            if state != 0x00000102:
                if not self._stop.is_set():
                    self.exit_process(71)
                return

    def _watch_posix(self):
        """Terminate a reparented helper so its GPU session cannot outlive the original OCR owner."""
        while not self._stop.wait(self.interval):
            if os.getppid() != self.parent_pid:
                self.exit_process(71)
                return

    def close(self):
        """Stop polling, join the watcher when possible and release the Windows owner handle."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, 2 * self.interval))
        if self._handle:
            self.kernel32.CloseHandle(self._handle)
            self._handle = None


def enumerate_dxgi_adapters():
    """Return DXGI EnumAdapters1 ordinals, which are DirectML device IDs."""
    if os.name != "nt":
        raise WorkerError("DirectML adapter selection requires Windows.")
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        """Represent a DXGI interface identifier with the native Windows GUID field layout."""
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class DESC(ctypes.Structure):
        """Match DXGI_ADAPTER_DESC1 fields used for device identity, memory and software flags."""
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", wintypes.UINT),
                    ("DeviceId", wintypes.UINT), ("SubSysId", wintypes.UINT),
                    ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                    ("AdapterLuidLow", wintypes.DWORD), ("AdapterLuidHigh", wintypes.LONG),
                    ("Flags", wintypes.UINT)]

    # IID_IDXGIFactory1, EnumAdapters1 vtable slot 12; IDXGIAdapter1 GetDesc1 slot 10.
    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    dxgi = ctypes.WinDLL("dxgi", use_last_error=True)
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    factory = ctypes.c_void_p()
    if create(ctypes.byref(iid), ctypes.byref(factory)) < 0 or not factory:
        raise WorkerError("DXGI adapter enumeration failed.")

    def method(obj, slot, result, *arguments):
        """Bind a COM vtable entry with the specified ctypes result and argument calling convention."""
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[slot])

    adapters = []
    try:
        enumerate_one = method(factory, 12, ctypes.c_int32, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            status = enumerate_one(factory, index, ctypes.byref(adapter))
            if status & 0xFFFFFFFF == 0x887A0002:  # DXGI_ERROR_NOT_FOUND.
                break
            if status < 0 or not adapter:
                raise WorkerError("DXGI adapter enumeration failed.")
            try:
                desc = DESC()
                if method(adapter, 10, ctypes.c_int32, ctypes.POINTER(DESC))(adapter, ctypes.byref(desc)) < 0:
                    raise WorkerError("DXGI adapter description failed.")
                adapters.append({"index": index, "name": desc.Description, "vendor_id": int(desc.VendorId),
                                 "dedicated_memory": int(desc.DedicatedVideoMemory), "flags": int(desc.Flags)})
            finally:
                method(adapter, 2, ctypes.c_uint32)(adapter)
            index += 1
    finally:
        method(factory, 2, ctypes.c_uint32)(factory)
    return adapters


def select_adapter(adapters):
    """Choose the hardware adapter with most dedicated memory, breaking ties by the lowest DXGI index."""
    usable = [adapter for adapter in adapters
              if not adapter.get("flags", 0) & 2]
    if not usable:
        raise WorkerError("No hardware DirectML adapter was found.")
    chosen = max(usable, key=lambda adapter: (adapter["dedicated_memory"], -adapter["index"]))
    return {key: chosen[key] for key in ("name", "index", "vendor_id", "dedicated_memory")}


def _verified_model(path):
    """Hash the complete local model and require the pinned label-model digest before session setup."""
    model = Path(path).resolve(strict=True)
    if not model.is_file():
        raise WorkerError("The label model is not a file.")
    digest = hashlib.sha256()
    with model.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != protocol.MODEL_SHA256:
        raise WorkerError("The GPU label model does not match the pinned model.")
    return model


def _request_paths(request):
    """Resolve a bounded NPY input and unused NPZ output beneath the same request directory."""
    root = Path(request["root"]).resolve(strict=True)
    if not root.is_dir():
        raise WorkerError("Tensor root must be a directory.")
    source = Path(request["input_path"]).resolve(strict=True)
    target = Path(request["output_path"]).resolve(strict=False)
    for path in (source, target):
        if path == root or not path.is_relative_to(root):
            raise WorkerError("Tensor paths must remain inside the request root.")
    if source == target or source.suffix != ".npy" or target.suffix != ".npz":
        raise WorkerError("Tensor input/output paths must be separate NPY/NPZ files.")
    if not source.is_file() or not target.parent.is_dir() or target.exists():
        raise WorkerError("Tensor input or fresh output path is unavailable.")
    if source.stat().st_size > protocol.MAX_INPUT_BYTES + 65536:
        raise WorkerError("Tensor input exceeds the helper size limit.")
    return root, source, target


class LabelWorker:
    """Injectable runtime and adapter seams keep contract tests hardware independent."""

    def __init__(self, *, runtime_loader=None, numpy_loader=None, adapter_loader=None, model_verifier=None):
        """Keep inference imports lazy so owner monitoring can start before runtime allocation."""
        self.runtime_loader = runtime_loader or (lambda: __import__("onnxruntime"))
        self.numpy_loader = numpy_loader or (lambda: __import__("numpy"))
        self.adapter_loader = adapter_loader or enumerate_dxgi_adapters
        self.model_verifier = model_verifier or _verified_model
        self.session = None
        self.np = None
        self.adapter = None
        self.gpu_nodes = 0
        self.profile_dir = None
        self._profile_finished = False
        self._failed = False

    def initialize(self, request):
        """Select the verified model and device, then validate one DirectML session's fixed label input."""
        if self.session is not None or self._failed:
            raise WorkerError("GPU label helper is already initialized or unavailable.")
        threads = _integer(request.get("threads"), "threads", 1, 64)
        model = self.model_verifier(request["model_path"])
        self.profile_dir = Path(request["profile_dir"]).resolve(strict=True)
        if not self.profile_dir.is_dir():
            raise WorkerError("GPU profiling directory is unavailable.")
        self.adapter = select_adapter(self.adapter_loader())
        self.np = self.numpy_loader()
        runtime = self.runtime_loader()
        if protocol.DML_PROVIDER not in runtime.get_available_providers():
            raise WorkerError("DirectML is unavailable in the isolated helper runtime.")
        options = runtime.SessionOptions()
        options.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.enable_cpu_mem_arena = False
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = runtime.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.enable_profiling = True
        # Different OCR owners may initialize within the same second in the
        # shared temp directory; ORT's timestamp suffix alone is not unique.
        options.profile_file_prefix = str(self.profile_dir / f"label-dml-{os.getpid()}-{id(self)}")
        providers = [(protocol.DML_PROVIDER, {"device_id": str(self.adapter["index"])}), protocol.CPU_PROVIDER]
        self.session = runtime.InferenceSession(str(model), sess_options=options, providers=providers)
        if not self.session.get_providers() or self.session.get_providers()[0] != protocol.DML_PROVIDER:
            self.close()
            raise WorkerError("The label session did not select DirectML.")
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or inputs[0].type != "tensor(float)":
            self.close()
            raise WorkerError("The label model input contract is incompatible.")
        shape = inputs[0].shape
        if len(shape) != 4 or shape[1:3] != [3, 48] or isinstance(shape[3], int) and shape[3] != 320:
            self.close()
            raise WorkerError("The label model input shape is incompatible.")
        self.input_name = inputs[0].name
        return {"ok": True, "protocol": protocol.PROTOCOL_VERSION, "provider": protocol.DML_PROVIDER,
                "adapter": self.adapter, "runtime": str(runtime.__version__)}

    def _finish_profile(self):
        """Require evidence of executed DirectML nodes and remove the bounded profiling file."""
        if self._profile_finished or self.session is None:
            return self.gpu_nodes
        self._profile_finished = True
        profile = Path(self.session.end_profiling()).resolve(strict=True)
        # Never read/unlink an arbitrary path returned by a broken runtime.
        if not profile.is_relative_to(self.profile_dir) or not profile.is_file() or profile.stat().st_size > 64 * 1024 * 1024:
            raise WorkerError("GPU execution profile is unavailable.")
        try:
            with profile.open("r", encoding="utf-8") as stream:
                events = json.load(stream)
            if not isinstance(events, list):
                raise WorkerError("GPU execution profile is malformed.")
            self.gpu_nodes = sum(1 for event in events if isinstance(event, dict) and event.get("cat") == "Node"
                                 and isinstance(event.get("args"), dict)
                                 and event["args"].get("provider") == protocol.DML_PROVIDER)
        finally:
            profile.unlink(missing_ok=True)
        if self.gpu_nodes == 0:
            raise WorkerError("The label model did not execute any DirectML nodes.")
        return self.gpu_nodes

    def _run(self, tensor):
        """Run one batch and reject malformed or nonfinite model scores before decoding."""
        outputs = self.session.run(None, {self.input_name: tensor})
        if not isinstance(outputs, (list, tuple)) or not outputs:
            raise WorkerError("The label model returned no outputs.")
        output = outputs[0]
        if not isinstance(output, self.np.ndarray) or output.dtype != self.np.float32 or output.ndim != 3 \
                or output.shape[0] != tensor.shape[0] or output.shape[1] < 1 or output.shape[2] != protocol.MODEL_CLASSES \
                or not self.np.isfinite(output).all():
            raise WorkerError("The label model output contract is incompatible.")
        return output

    def infer(self, request):
        """Validate request paths and batch limits before processing the bounded input tensor."""
        if self.session is None or self._failed:
            raise WorkerError("The GPU label helper is unavailable.")
        batch_size = _integer(request.get("batch_size"), "batch_size", protocol.MIN_BATCH_SIZE, protocol.MAX_BATCH_SIZE)
        _, source, target = _request_paths(request)
        with self._tensor(source) as tensor:
            return self._infer_tensor(tensor, target, batch_size)

    @contextlib.contextmanager
    def _tensor(self, source):
        """Map a finite label tensor without pickle and release its file mapping on every exit path."""
        tensor = self.np.load(source, allow_pickle=False, mmap_mode="r")
        try:
            if not isinstance(tensor, self.np.ndarray) or tensor.dtype != self.np.float32 or tensor.ndim != 4 \
                    or tuple(tensor.shape[1:]) != protocol.LABEL_SHAPE or not 1 <= tensor.shape[0] <= protocol.MAX_ROWS \
                    or tensor.nbytes > protocol.MAX_INPUT_BYTES or not self.np.isfinite(tensor).all():
                raise WorkerError("Label tensors must be finite float32 N x 3 x 48 x 320 within the helper limits.")
            yield tensor
        finally:
            # Windows cannot remove a request's input while its memmap is open.
            mapping = getattr(tensor, "_mmap", None)
            if mapping is not None:
                mapping.close()
            elif hasattr(tensor, "close"):
                tensor.close()

    def _infer_tensor(self, tensor, target, batch_size):
        """Prove GPU execution before atomically publishing character indices and probabilities."""
        temporary = None
        try:
            indices, probabilities = [], []
            steps = None
            for start in range(0, tensor.shape[0], batch_size):
                output = self._run(tensor[start:start + batch_size])
                if not self.gpu_nodes:
                    # Reuse the first batch only after DirectML execution is proven.
                    self._finish_profile()
                if steps is not None and output.shape[1] != steps:
                    raise WorkerError("The label model changed sequence length between batches.")
                steps = output.shape[1]
                indices.append(output.argmax(axis=2))
                probabilities.append(output.max(axis=2))
            with tempfile.NamedTemporaryFile(mode="wb", prefix="label-result-", suffix=".tmp", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                self.np.savez(stream, indices=self.np.concatenate(indices).astype(self.np.int64, copy=False),
                              probabilities=self.np.concatenate(probabilities).astype(self.np.float32, copy=False))
            os.replace(temporary, target)
            temporary = None
            return {"ok": True, "protocol": protocol.PROTOCOL_VERSION, "rows": int(tensor.shape[0]),
                    "steps": int(steps), "gpu_nodes": self.gpu_nodes}
        except Exception:
            self._failed = True
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def close(self):
        """Finish pending profiling when possible and release the inference session reference."""
        if self.session is not None and not self._profile_finished:
            try:
                self._finish_profile()
            except Exception:
                pass
        self.session = None

    def dispatch(self, request):
        """Check the wire version and route initialization, inference or shutdown requests."""
        if not isinstance(request, dict) or type(request.get("protocol")) is not int \
                or request["protocol"] != protocol.PROTOCOL_VERSION:
            raise WorkerError("Unsupported GPU label protocol.")
        operation = request.get("op")
        if operation == "init":
            return self.initialize(request)
        if operation == "infer":
            return self.infer(request)
        if operation == "shutdown":
            self.close()
            return {"ok": True, "protocol": protocol.PROTOCOL_VERSION, "shutdown": True}
        raise WorkerError("Unknown GPU label operation.")


def serve(worker, input_stream, output_stream):
    """Answer bounded JSON-lines requests while keeping runtime diagnostics off protocol stdout."""
    while True:
        line = input_stream.readline(65537)
        if not line:
            break
        try:
            if len(line) > 65536:
                # Drain one invalid request in bounded chunks, without retaining
                # an unbounded control line or interpreting its tail as a scan.
                while not line.endswith("\n"):
                    line = input_stream.readline(65537)
                    if not line:
                        break
                raise WorkerError("GPU request exceeds the protocol line limit.")
            request = json.loads(line)
            # ORT's Python fallback diagnostic uses print(); it must not corrupt
            # stdout even when DirectML construction fails and CPU retry follows.
            with contextlib.redirect_stdout(sys.stderr):
                response = worker.dispatch(request)
        except Exception as error:
            response = {"ok": False, "protocol": protocol.PROTOCOL_VERSION, "error": str(error)[:1000]}
        output_stream.write(json.dumps(response, allow_nan=False, separators=(",", ":")) + "\n")
        output_stream.flush()
        if response.get("shutdown"):
            break


def main(argv=None):
    """Watch the explicit owner before creating the helper and close both resources when serving ends."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args(argv)
    watchdog = None
    worker = None
    try:
        watchdog = ParentWatchdog(args.parent_pid).start()
        worker = LabelWorker()
        serve(worker, sys.stdin, sys.stdout)
        return 0
    except Exception as error:
        sys.stdout.write(json.dumps({"ok": False, "protocol": protocol.PROTOCOL_VERSION, "error": str(error)[:1000]}) + "\n")
        sys.stdout.flush()
        return 1
    finally:
        if worker is not None:
            worker.close()
        if watchdog is not None:
            watchdog.close()


if __name__ == "__main__":
    raise SystemExit(main())
