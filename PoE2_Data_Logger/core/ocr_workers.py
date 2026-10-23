"""Run bounded OCR jobs in reusable, killable children without sharing the HUD's I/O threads."""

from collections import deque
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass, field
from functools import partial
import multiprocessing
from pathlib import Path
import pickle
import tempfile
import threading
import time

from PoE2_Data_Logger.core import ocr_runtime, store


MAX_WORKERS = 2
MAX_QUEUED = 2
SCAN_TIMEOUT_SECONDS = 90
POLL_INTERVAL = .025
SHUTDOWN_JOIN_SECONDS = 3
_BROKER = None
_RETIRING = []
_BROKER_LOCK = threading.Lock()


def _data_directory():
    """Capture the profile path without initializing OCR or starting a child on the GUI thread."""
    return str(store.DATA_DIR)


@dataclass(frozen=True)
class Request:
    """Freeze an importable recognizer, its inputs and process preferences for one admission."""

    function: object
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    data_dir: str = field(default_factory=_data_directory)
    cpu_threads: int = field(default_factory=ocr_runtime.active_threads)
    deadline: float | None = None
    prepare_artwork: bool = False

    def __call__(self):
        """Support captured test/UI work callbacks through the same bounded child transport."""
        return submit(self).result()


def _application_callable(function):
    """Distinguish importable application recognizers from injected mocks or local test callbacks."""
    while isinstance(function, partial):
        function = function.func
    module = getattr(function, "__module__", "")
    return isinstance(module, str) and module.startswith("PoE2_Data_Logger.")


def request(function, *args, **kwargs):
    """Freeze real OCR work without spawning; keep injected callbacks on their caller's normal path."""
    if not _application_callable(function):
        return partial(function, *args, **kwargs)
    target = function
    while isinstance(target, partial):
        target = target.func
    artwork = (getattr(target, "__module__", "") == "PoE2_Data_Logger.ocr.item_ocr"
               and getattr(target, "__name__", "") in ("scan_inventory_grid", "scan_ritual_page"))
    return Request(function, tuple(args), dict(kwargs), _data_directory(),
                   ocr_runtime.active_threads(), time.monotonic() + SCAN_TIMEOUT_SECONDS,
                   prepare_artwork=artwork)


def run(function, *args, **kwargs):
    """Wait for isolated application OCR or execute an injected reader inline without pickling it."""
    work = request(function, *args, **kwargs)
    return submit(work).result() if isinstance(work, Request) else work()


def _prepare_artwork():
    """Fill the recognition banks and load the text models in this child without capturing pixels."""
    import os
    from PoE2_Data_Logger.core import logger_store as logger
    from PoE2_Data_Logger.ocr import item_ocr

    item_ocr.prepare_artwork(logger.inventory_icons(), logger.ritual_icons())
    item_ocr.warm_text_models()
    return os.getpid()


def _child_loop(connection, data_dir, cpu_threads, artwork_preload=None):
    """Own recognition models in one profile-bound child and exchange only small control messages."""
    from PoE2_Data_Logger.core import logger_store as logger

    store.DATA_DIR = Path(data_dir)
    logger._READY = False
    ocr_runtime._ACTIVE_THREADS = cpu_threads
    artwork_attempted = False
    artwork_answer = None
    artwork_error = None
    try:
        while True:
            job_id, directory = connection.recv()
            root = Path(directory)
            with (root / "request.pkl").open("rb") as source:
                work = pickle.load(source)
            previous_tempdir = tempfile.tempdir
            tempfile.tempdir = str(root)
            try:
                try:
                    preparing = artwork_preload is not None and work.function is artwork_preload.function
                    if artwork_preload is not None and (preparing or work.prepare_artwork) and not artwork_attempted:
                        artwork_attempted = True
                        try:
                            artwork_answer = artwork_preload.function(*artwork_preload.args, **artwork_preload.kwargs)
                        except Exception as error:
                            # A failed optional bulk preparation must not lose a
                            # captured scan. Its usual on-demand path remains
                            # available, without retrying the bulk load per scan.
                            artwork_error = error.with_traceback(None)
                    if preparing and artwork_error is not None:
                        raise artwork_error
                    answer = ("result", artwork_answer if preparing else work.function(*work.args, **work.kwargs))
                except Exception as error:
                    # Preserve reader validation exceptions when their type is
                    # importable, while retaining useful text for unusual errors.
                    try:
                        pickle.dumps(error)
                    except Exception:
                        error = RuntimeError(f"{type(error).__name__}: {error}")
                    answer = ("error", error)
            finally:
                # Native termination bypasses the recognizer's own finally
                # blocks. Its temporary screenshots must remain inside the
                # parent-owned job directory so the supervisor can remove them.
                tempfile.tempdir = previous_tempdir
            with (root / "response.pkl").open("wb") as destination:
                pickle.dump(answer, destination, protocol=pickle.HIGHEST_PROTOCOL)
            connection.send(job_id)
            del work, answer
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class _OCRFuture(Future):
    """Allow cancellation of an active native reader because its owned process can be terminated."""

    def __init__(self, wake):
        """Keep active state separate from Future's noncancellable RUNNING state."""
        super().__init__()
        self._active = False
        self._wake = wake
        self._cancel_notification_lock = threading.Lock()
        self._cancel_notified = False

    def cancel(self):
        """Retire the result immediately and wake the supervisor to reap any active child."""
        cancelled = super().cancel()
        if cancelled:
            with self._cancel_notification_lock:
                if not self._cancel_notified:
                    self._cancel_notified = True
                    # No native job enters Future's RUNNING state, so the
                    # supervisor must still notify wait()/as_completed()
                    # waiters after cancel wins admission or execution.
                    self.set_running_or_notify_cancel()
            self._wake(self)
        return cancelled

    def running(self):
        """Report execution while retaining cancellation of both queued and native work."""
        return self._active and not self.done()


def _complete(future, value=None, error=None):
    """Publish a result only when concurrent cancellation has not already retired this admission."""
    if future.done():
        return
    try:
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)
    except InvalidStateError:
        # Cancellation can win after the initial done check.
        pass


def _stop_child(process, connection):
    """Terminate and boundedly reap a native reader before discarding its control pipe."""
    if process is not None:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(.5)
            if process.is_alive():
                process.kill()
                process.join(.5)
        if not process.is_alive():
            process.close()
    if connection is not None:
        connection.close()


class _Broker:
    """Bound retained jobs and let two daemon supervisors each own at most one OCR child."""

    def __init__(self):
        """Start lightweight supervisors; model processes remain lazy until work is admitted."""
        self.condition = threading.Condition()
        self.queue = deque()
        self.active = {}
        self.closing = False
        self.sequence = 0
        self.preload_work = None
        self.preload_futures = ()
        self.threads = [threading.Thread(target=self._serve, args=(index,), name=f"poe2-ocr-supervisor-{index}",
                                         daemon=True) for index in range(MAX_WORKERS)]
        self.threads.append(threading.Thread(target=self._watch_queue, name="poe2-ocr-admission-watchdog",
                                             daemon=True))
        for thread in self.threads:
            thread.start()

    def _cancelled(self, future):
        """Release queued images immediately and wake an active reader's supervising thread."""
        with self.condition:
            self.queue = deque(job for job in self.queue if job[1] is not future)
            self.condition.notify_all()

    def submit(self, work, *, affinity=None):
        """Admit at most two running and two waiting requests, rejecting overflow immediately."""
        future = _OCRFuture(self._cancelled)
        with self.condition:
            if self.closing:
                _complete(future, error=RuntimeError("OCR workers are shutting down. Scan again."))
            elif len(self.queue) + len(self.active) >= MAX_WORKERS + MAX_QUEUED:
                _complete(future, error=RuntimeError("OCR queue is full. Reject a reading or try again."))
            else:
                self.sequence += 1
                deadline = work.deadline if work.deadline is not None else time.monotonic() + SCAN_TIMEOUT_SECONDS
                self.queue.append((self.sequence, future, work, deadline, affinity))
                self.condition.notify_all()
        return future

    def preload(self, work):
        """Prepare each owned child once using bounded admissions with explicit supervisor affinity."""
        with self.condition:
            context = (work.data_dir, work.cpu_threads)
            previous = self.preload_work
            if previous is not None and (previous.data_dir, previous.cpu_threads) == context:
                return self.preload_futures
            if self.closing or len(self.queue) + len(self.active) + MAX_WORKERS > MAX_WORKERS + MAX_QUEUED:
                futures = tuple(_OCRFuture(self._cancelled) for _ in range(MAX_WORKERS))
                for future in futures:
                    _complete(future, error=RuntimeError("OCR artwork preparation could not be admitted."))
                return futures
            self.preload_work = work
            self.preload_futures = tuple(self.submit(work, affinity=index) for index in range(MAX_WORKERS))
            return self.preload_futures

    def _watch_queue(self):
        """Expire queued admissions at their own deadlines even when both native readers remain busy."""
        while True:
            with self.condition:
                if self.closing:
                    return
                now = time.monotonic()
                expired = [job for job in self.queue if job[3] <= now]
                self.queue = deque(job for job in self.queue if job[3] > now)
                if not expired:
                    deadline = min((job[3] for job in self.queue), default=now + SCAN_TIMEOUT_SECONDS)
                    self.condition.wait(max(POLL_INTERVAL, deadline - now))
                    continue
            for _, future, _, _, _ in expired:
                _complete(future, error=TimeoutError("OCR timed out before recognition could start."))

    def _serve(self, index):
        """Reuse a healthy child, but terminate failed, cancelled or overdue work before accepting more."""
        process = connection = context = process_preload = None
        try:
            while True:
                with self.condition:
                    selected = None
                    while not self.closing:
                        selected = next((offset for offset, job in enumerate(self.queue)
                                         if job[4] is None or job[4] == index), None)
                        if selected is not None:
                            break
                        self.condition.wait()
                    if self.closing:
                        return
                    job_id, future, work, deadline, _ = self.queue[selected]
                    del self.queue[selected]
                    self.active[job_id] = future
                    future._active = True
                healthy = True
                try:
                    if future.cancelled():
                        continue
                    if time.monotonic() >= deadline:
                        _complete(future, error=TimeoutError("OCR timed out before recognition could start."))
                        continue
                    next_context = (work.data_dir, work.cpu_threads)
                    with self.condition:
                        preload = self.preload_work
                        if preload is not None and (preload.data_dir, preload.cpu_threads) != next_context:
                            preload = None
                    if process is not None and (context != next_context or process_preload is not preload
                                                or not process.is_alive()):
                        _stop_child(process, connection)
                        process = connection = None
                    with tempfile.TemporaryDirectory(prefix="poe2-ocr-job-") as directory:
                        with (Path(directory) / "request.pkl").open("wb") as destination:
                            pickle.dump(work, destination, protocol=pickle.HIGHEST_PROTOCOL)
                        if self.closing or future.cancelled():
                            continue
                        if time.monotonic() >= deadline:
                            _complete(future, error=TimeoutError("OCR timed out while preparing its inputs."))
                            continue
                        if process is None:
                            spawn = multiprocessing.get_context("spawn")
                            connection, child_connection = spawn.Pipe()
                            process = spawn.Process(target=_child_loop,
                                                    args=(child_connection, *next_context, preload), daemon=True,
                                                    name="poe2-ocr-worker")
                            try:
                                process.start()
                            finally:
                                child_connection.close()
                            context = next_context
                            process_preload = preload
                        healthy = False
                        connection.send((job_id, directory))
                        while True:
                            if self.closing or future.cancelled():
                                break
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                _complete(future, error=TimeoutError("OCR timed out. Scan again."))
                                break
                            if connection.poll(min(POLL_INTERVAL, remaining)):
                                if connection.recv() != job_id:
                                    raise RuntimeError("OCR worker returned an unexpected scan response.")
                                with (Path(directory) / "response.pkl").open("rb") as source:
                                    status, answer = pickle.load(source)
                                healthy = True
                                if status == "error":
                                    _complete(future, error=answer)
                                elif status == "result":
                                    _complete(future, value=answer)
                                else:
                                    raise RuntimeError("OCR worker returned an invalid response.")
                                break
                            if not process.is_alive():
                                raise RuntimeError(f"OCR worker stopped unexpectedly (exit {process.exitcode}). Scan again.")
                        if not healthy:
                            _stop_child(process, connection)
                            process = connection = None
                except Exception as error:
                    if isinstance(error, (EOFError, BrokenPipeError)):
                        error = RuntimeError("OCR worker stopped unexpectedly. Scan again.")
                    _complete(future, error=error)
                    if process is not None:
                        _stop_child(process, connection)
                        process = connection = None
                finally:
                    future._active = False
                    with self.condition:
                        self.active.pop(job_id, None)
                        self.condition.notify_all()
                    # Avoid retaining the previous screenshot/reference bank
                    # while this supervisor waits for another request.
                    work = None
                    future = answer = None
        finally:
            _stop_child(process, connection)

    def close(self):
        """Cancel queued and active results immediately while supervisors reap children asynchronously."""
        with self.condition:
            self.closing = True
            futures = [job[1] for job in self.queue] + list(self.active.values())
            self.queue.clear()
            self.condition.notify_all()
        for future in futures:
            future.cancel()

    def join(self, deadline):
        """Bound final shutdown across all supervisors without joining on the GUI close path."""
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))


def submit(work):
    """Lazily share a fixed-size OCR broker and return its cancellable admission future."""
    if not isinstance(work, Request):
        raise TypeError("Submit a frozen OCR Request.")
    global _BROKER
    with _BROKER_LOCK:
        if _BROKER is None:
            _BROKER = _Broker()
        return _BROKER.submit(work)


def preload_artwork():
    """Asynchronously fill both Currency/Ritual workers' existing artwork banks at desktop startup."""
    global _BROKER
    work = Request(_prepare_artwork, deadline=time.monotonic() + SCAN_TIMEOUT_SECONDS)
    with _BROKER_LOCK:
        if _BROKER is None:
            _BROKER = _Broker()
        return _BROKER.preload(work)


def shutdown(wait=False):
    """Detach OCR admissions and cancel children immediately, optionally waiting a bounded final interval."""
    global _BROKER
    broker = None
    with _BROKER_LOCK:
        _RETIRING[:] = [broker for broker in _RETIRING if any(thread.is_alive() for thread in broker.threads)]
        if _BROKER is not None:
            broker, _BROKER = _BROKER, None
            _RETIRING.append(broker)
        retiring = tuple(_RETIRING)
    if broker is not None:
        broker.close()
    if wait:
        deadline = time.monotonic() + SHUTDOWN_JOIN_SECONDS
        for broker in retiring:
            broker.join(deadline)
        with _BROKER_LOCK:
            _RETIRING[:] = [broker for broker in _RETIRING if any(thread.is_alive() for thread in broker.threads)]
