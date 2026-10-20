"""Optional isolated DirectML inference for Currency/Ritual count and tier crops.

The collector retains its CPU runtime, RapidOCR preprocessing, dictionary and
decoder. Only fixed-width label tensors cross into the separately bundled GPU
runtime. A failed helper invalidates its evidence and retries the whole label
request on the original CPU reader.
"""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

import numpy as np

from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.ocr.gpu_label_protocol import (
    LABEL_SHAPE, MAX_INPUT_BYTES, MAX_ROWS, MODEL_CLASSES, PROTOCOL_VERSION,
)


_LOG = logging.getLogger(__name__)
_LOCAL = threading.local()
_CLIENTS = set()
_CLIENTS_LOCK = threading.Lock()
# Keep helper startup/inference bounded well below the outer 90-second scan
# deadline, leaving time for a complete CPU retry after a failed GPU request.
REQUEST_TIMEOUT = 15


class GPUFallback(RuntimeError):
    """The entire current label request must be retried without GPU evidence."""


def _helper_path():
    """Use only the application-owned optional helper, never an executable on PATH."""
    if sys.platform != "win32":
        return None
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) / "label_gpu" if bundle else Path(__file__).resolve().parents[2] / "dist" / "PoE2LabelGpu"
    path = root / "PoE2LabelGpu.exe"
    return path if path.is_file() else None


class _Client:
    """One serialized, bounded JSON control channel to a parent-watched helper."""

    def __init__(self, executable, model_path, threads):
        self.process = None
        self.responses = queue.Queue(maxsize=2)
        self.closed = False
        try:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            self.process = subprocess.Popen(
                [str(executable), "--parent-pid", str(os.getpid())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1, **options)
            threading.Thread(target=self._read_responses, name="poe2-label-gpu-control", daemon=True).start()
            with _CLIENTS_LOCK:
                _CLIENTS.add(self)
            response = self._exchange({"op": "init", "protocol": PROTOCOL_VERSION,
                                       "model_path": str(model_path), "threads": threads,
                                       "profile_dir": tempfile.gettempdir()})
            if response.get("provider") != "DmlExecutionProvider":
                raise GPUFallback("GPU helper did not select DirectML.")
        except Exception:
            self.close()
            raise

    def _read_responses(self):
        """Bound each control line and retained replies; EOF wakes the current request."""
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line or len(line) > 65536 or not line.endswith("\n"):
                    break
                try:
                    self.responses.put_nowait(line)
                except queue.Full:
                    break
        except (OSError, ValueError):
            pass
        try:
            self.responses.put_nowait(None)
        except queue.Full:
            pass

    def _exchange(self, payload):
        import json

        if self.closed or self.process.poll() is not None:
            raise GPUFallback("GPU helper stopped.")
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
            line = self.responses.get(timeout=REQUEST_TIMEOUT)
            if line is None:
                raise GPUFallback("GPU helper closed its channel.")
            response = json.loads(line)
            if (not isinstance(response, dict) or response.get("protocol") != PROTOCOL_VERSION
                    or response.get("ok") is not True):
                reason = str(response.get("error", "invalid reply"))[:200] if isinstance(response, dict) else "invalid reply"
                raise GPUFallback(f"GPU helper rejected the request: {reason}")
            return response
        except (OSError, ValueError, queue.Empty) as error:
            raise GPUFallback("GPU helper failed or timed out.") from error

    def infer(self, tensor, batch_size):
        """Exchange one whole crop request; large tensors never travel through JSON."""
        with tempfile.TemporaryDirectory(prefix="poe2-label-gpu-") as directory:
            try:
                return self._infer_files(tensor, batch_size, directory)
            except BaseException:
                # A timed-out helper may still map its input on Windows. Reap
                # it before TemporaryDirectory tries to remove those files.
                self.close()
                raise

    def _infer_files(self, tensor, batch_size, directory):
        """Validate the complete helper result while the request directory is still owned."""
        root = Path(directory).resolve()
        source, destination = root / "input.npy", root / "output.npz"
        np.save(source, tensor, allow_pickle=False)
        response = self._exchange({"op": "infer", "protocol": PROTOCOL_VERSION,
                                   "input_path": str(source), "output_path": str(destination),
                                   "root": str(root), "batch_size": batch_size})
        if not destination.is_file() or destination.stat().st_size > 16 * 1024 * 1024:
            raise GPUFallback("GPU helper returned an invalid result file.")
        with np.load(destination, allow_pickle=False) as result:
            if set(result.files) != {"indices", "probabilities"}:
                raise GPUFallback("GPU helper returned invalid result arrays.")
            indices, probabilities = result["indices"], result["probabilities"]
        if (indices.dtype != np.int64 or probabilities.dtype != np.float32
                or indices.ndim != 2 or indices.shape != probabilities.shape
                or indices.shape[0] != len(tensor) or not 1 <= indices.shape[1] <= 1024
                or response.get("rows") != len(tensor) or response.get("steps") != indices.shape[1]
                or (indices < 0).any() or (indices >= MODEL_CLASSES).any()
                or not np.isfinite(probabilities).all()
                or (probabilities < 0).any() or (probabilities > 1).any()
                or type(response.get("gpu_nodes")) is not int or response["gpu_nodes"] <= 0):
            raise GPUFallback("GPU helper returned invalid recognition evidence.")
        return indices, probabilities

    def close(self):
        """Terminate and reap this owned helper; its watchdog covers outer-worker death."""
        self.closed = True
        process = self.process
        if process is not None:
            for stop in (process.terminate, process.kill):
                if process.poll() is not None:
                    break
                try:
                    stop()
                    process.wait(timeout=.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                _LOG.warning("GPU helper did not stop within its shutdown bound.")
                return
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        with _CLIENTS_LOCK:
            _CLIENTS.discard(self)


def _shutdown():
    with _CLIENTS_LOCK:
        clients = tuple(_CLIENTS)
    for client in clients:
        client.close()


atexit.register(_shutdown)


class _LabelReader:
    """Reuse the pinned RapidOCR reader's preprocessing and CTC decoding on GPU evidence."""

    def __init__(self, cpu, executable):
        self.cpu = cpu
        self.executable = executable
        self.rec_image_shape = cpu.rec_image_shape
        self.rec_batch_num = cpu.rec_batch_num
        self.cfg = cpu.cfg
        self.postprocess_op = cpu.postprocess_op
        self.session = SimpleNamespace(session=object())
        self.client = None
        self.failed = False

    def __call__(self, args):
        try:
            images = args.img
            if args.return_word_box or tuple(self.rec_image_shape) != LABEL_SHAPE:
                raise GPUFallback("GPU labels require the original fixed-width crop configuration.")
            if (not 1 <= len(images) <= MAX_ROWS or self.rec_batch_num != 6
                    or len(images) * 3 * 48 * 320 * 4 > MAX_INPUT_BYTES):
                raise GPUFallback("GPU label batch is unsupported.")
            widths = [image.shape[1] / float(image.shape[0]) for image in images]
            maximum = self.rec_image_shape[2] / self.rec_image_shape[1]
            if max(widths) > maximum:
                raise GPUFallback("Wide crops retain the CPU reader's original padding.")
            # Match RapidOCR 3.9.2 TextRecognizer's sort, batch6 and normalization.
            order = np.argsort(np.array(widths))
            tensors = np.concatenate([self.cpu.resize_norm_img(images[index], maximum)[None, :]
                                      for index in order]).astype(np.float32, copy=False)
            if tensors.shape != (len(images), *LABEL_SHAPE):
                raise GPUFallback("GPU label preprocessing has an unexpected shape.")
            if self.client is None:
                import rapidocr
                model = Path(rapidocr.__file__).resolve().parent / "models" / "PP-OCRv6_rec_small.onnx"
                self.client = _Client(self.executable, model, ocr_runtime.active_threads())
            indices, probabilities = self.client.infer(tensors, self.rec_batch_num)
            readings = [None] * len(images)
            for start in range(0, len(images), self.rec_batch_num):
                end = min(len(images), start + self.rec_batch_num)
                decoded, _ = self.postprocess_op.decode(
                    indices[start:end], probabilities[start:end], False,
                    [widths[index] for index in order[start:end]], maximum, remove_duplicate=True)
                if len(decoded) != end - start:
                    raise GPUFallback("GPU decoder returned an incomplete label batch.")
                for offset, value in enumerate(decoded):
                    readings[order[start + offset]] = value
            txts, scores = zip(*readings)
            return SimpleNamespace(txts=txts, scores=scores)
        except Exception as error:
            self.failed = True
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    _LOG.exception("GPU helper cleanup failed; retaining CPU retry.")
            _LOG.warning("Currency/Ritual GPU labels unavailable; retrying on CPU: %s", error)
            raise GPUFallback("Retry the complete label request on CPU.") from error


def reader_for(cpu):
    """Leave every default/unsupported path on CPU and retain one healthy wrapper per reader."""
    if (tuple(getattr(cpu, "rec_image_shape", ())) != LABEL_SHAPE
            or getattr(cpu, "rec_batch_num", None) != 6
            or not callable(getattr(cpu, "resize_norm_img", None))
            or not callable(getattr(getattr(cpu, "postprocess_op", None), "decode", None))):
        return cpu
    if not ocr_runtime.active_gpu_labels():
        return cpu
    executable = _helper_path()
    if executable is None:
        return cpu
    state = getattr(_LOCAL, "state", None)
    owners = (cpu, cpu.session, getattr(cpu.session, "session", None), cpu.postprocess_op)
    signature = repr(cpu.cfg)
    if state is None or any(a is not b for a, b in zip(state[0], owners)) or state[1] != signature:
        if state is not None and state[2].client is not None:
            state[2].client.close()
        state = (owners, signature, _LabelReader(cpu, executable))
        _LOCAL.state = state
    return cpu if state[2].failed else state[2]
