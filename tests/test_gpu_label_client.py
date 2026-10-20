"""Optional label inference preserves the CPU decoder and whole-request fallback."""
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from PoE2_Data_Logger.core import ocr_runtime
from PoE2_Data_Logger.ocr import item_ocr, label_gpu


class _Session:
    def __init__(self):
        self.session = object()
        self.inputs = []

    def __call__(self, tensor):
        self.inputs.append(tensor.copy())
        result = np.zeros((len(tensor), 5, label_gpu.MODEL_CLASSES), dtype=np.float32)
        for row, image in enumerate(tensor):
            digit = 1 + int(abs(image[0, 0, 0]) * 10) % 8
            result[row, range(5), [digit, digit, 0, digit + 1, 0]] = [.91, .95, .99, .97, .99]
        return result


def _reader():
    from rapidocr.ch_ppocr_rec.main import TextRecognizer
    from rapidocr.ch_ppocr_rec.utils import CTCLabelDecode
    from omegaconf import OmegaConf

    cpu = TextRecognizer.__new__(TextRecognizer)
    cpu.rec_image_shape = [3, 48, 320]
    cpu.rec_batch_num = 6
    cpu.cfg = OmegaConf.create({"lang_type": "ch", "font_path": None})
    cpu.RTL_LANGS = set()
    cpu.postprocess_op = CTCLabelDecode(character=[str(i % 10) for i in range(18708)])
    cpu.session = _Session()
    return cpu


class LabelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(label_gpu._LOCAL, "state", None, create=True))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", True))
        self.stack.enter_context(patch.object(ocr_runtime, "_ACTIVE_THREADS", 2))
        self.cpu = _reader()

    def test_off_and_missing_helper_do_not_launch_or_replace_cpu(self):
        with patch.object(ocr_runtime, "_ACTIVE_GPU_LABELS", False), \
                patch.object(label_gpu, "_helper_path") as locate:
            self.assertIs(label_gpu.reader_for(self.cpu), self.cpu)
            locate.assert_not_called()
        with patch.object(label_gpu, "_helper_path", return_value=None), \
                patch.object(label_gpu, "_Client") as launch:
            self.assertIs(label_gpu.reader_for(self.cpu), self.cpu)
            launch.assert_not_called()

    def test_only_trusted_windows_bundle_or_development_build_is_used(self):
        with patch.object(label_gpu.sys, "platform", "linux"):
            self.assertIsNone(label_gpu._helper_path())
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(label_gpu.sys, "platform", "win32"), \
                patch.object(label_gpu.sys, "_MEIPASS", directory, create=True):
            self.assertIsNone(label_gpu._helper_path())
            expected = Path(directory) / "label_gpu" / "PoE2LabelGpu.exe"
            expected.parent.mkdir()
            expected.touch()
            self.assertEqual(label_gpu._helper_path(), expected)

    def test_original_sort_padding_batches_decoder_and_order_are_preserved(self):
        from rapidocr.ch_ppocr_rec.typings import TextRecInput

        images = [np.full((10 + i % 3, 18 + i % 7, 3), i * 13, dtype=np.uint8) for i in range(17)]
        args = TextRecInput(img=images, return_word_box=False)
        original = self.cpu(args)
        expected_batches = list(self.cpu.session.inputs)
        self.cpu.session.inputs.clear()
        reader = label_gpu._LabelReader(self.cpu, "unused")
        batches = []

        class Client:
            def infer(_, tensor, batch_size):
                for start in range(0, len(tensor), batch_size):
                    batch = tensor[start:start + batch_size]
                    batches.append(batch.copy())
                predictions = [self.cpu.session(batch) for batch in batches]
                return (np.concatenate([value.argmax(axis=2) for value in predictions]),
                        np.concatenate([value.max(axis=2) for value in predictions]))

        reader.client = Client()
        actual = reader(args)
        self.assertEqual(actual.txts, original.txts)
        self.assertEqual(actual.scores, original.scores)
        self.assertEqual([len(batch) for batch in batches], [6, 6, 5])
        for expected, actual in zip(expected_batches, batches):
            np.testing.assert_array_equal(actual, expected)

    def test_reader_session_configuration_changes_and_failure_invalidate_wrapper(self):
        with patch.object(label_gpu, "_helper_path", return_value=Path("owned.exe")):
            first = label_gpu.reader_for(self.cpu)
            self.assertIs(label_gpu.reader_for(self.cpu), first)
            first.failed = True
            self.assertIs(label_gpu.reader_for(self.cpu), self.cpu)
            self.cpu.session.session = object()
            second = label_gpu.reader_for(self.cpu)
            self.assertIsNot(first, second)
            self.cpu.cfg["font_path"] = "changed"
            self.assertIsNot(label_gpu.reader_for(self.cpu), second)

    def test_failure_retries_all_labels_including_prior_gpu_cache_hits_on_cpu(self):
        class Reader:
            rec_image_shape = [3, 48, 320]
            rec_batch_num = 6
            cfg = {}
            session = SimpleNamespace(session=object())
            postprocess_op = object()

            def __init__(self, value, fail_after=None):
                self.value, self.fail_after, self.calls = value, fail_after, []

            def __call__(self, args):
                self.calls.append(list(args.img))
                if self.fail_after is not None and len(self.calls) > self.fail_after:
                    raise label_gpu.GPUFallback("device removed")
                return SimpleNamespace(txts=(self.value,) * len(args.img), scores=(.99,) * len(args.img))

        cpu, gpu = Reader("14"), Reader("7", fail_after=1)
        image, new = np.zeros((10, 18, 3), dtype=np.uint8), np.ones((10, 18, 3), dtype=np.uint8)
        with patch.object(item_ocr, "_engine", return_value=SimpleNamespace(text_rec=cpu)), \
                patch.object(label_gpu, "reader_for", side_effect=[gpu, gpu, cpu]), \
                patch.object(item_ocr._INVENTORY_TEXT_CACHE, "state", None, create=True):
            self.assertEqual(item_ocr._inventory_patch_readings([image]), (("7", .99),))
            self.assertEqual(item_ocr._inventory_patch_readings([image, new]), (("14", .99), ("14", .99)))
            self.assertEqual(len(cpu.calls[0]), 2)
            self.assertEqual(item_ocr._inventory_patch_readings([image, new]), (("14", .99), ("14", .99)))
            self.assertEqual(len(cpu.calls), 1)

    def test_wide_crop_uses_original_cpu_without_disabling_healthy_gpu(self):
        gpu = label_gpu._LabelReader(self.cpu, "unused")
        with patch.object(item_ocr, "_engine", return_value=SimpleNamespace(text_rec=self.cpu)), \
                patch.object(label_gpu, "reader_for", return_value=gpu):
            result = item_ocr._inventory_patch_readings([np.zeros((10, 100, 3), dtype=np.uint8)])
        self.assertEqual(len(result), 1)
        self.assertFalse(gpu.failed)
        self.assertIsNone(gpu.client)

    def test_cleanup_error_still_requests_complete_cpu_retry(self):
        from rapidocr.ch_ppocr_rec.typings import TextRecInput
        from unittest.mock import Mock

        reader = label_gpu._LabelReader(self.cpu, "unused")
        reader.client = SimpleNamespace(infer=Mock(side_effect=OSError("device lost")),
                                        close=Mock(side_effect=OSError("cleanup failed")))
        with self.assertLogs(label_gpu._LOG, level="WARNING"):
            with self.assertRaises(label_gpu.GPUFallback):
                reader(TextRecInput(img=[np.zeros((10, 18, 3), dtype=np.uint8)], return_word_box=False))
        self.assertTrue(reader.failed)


class ClientValidationTests(unittest.TestCase):
    def client(self, directory, modify=None):
        client = label_gpu._Client.__new__(label_gpu._Client)
        client.close = lambda: None

        def exchange(payload):
            tensor = np.load(payload["input_path"], allow_pickle=False)
            indices = np.zeros((len(tensor), 5), dtype=np.int64)
            probabilities = np.full(indices.shape, .95, dtype=np.float32)
            response = {"ok": True, "protocol": 1, "rows": len(tensor), "steps": 5, "gpu_nodes": 2}
            arrays = {"indices": indices, "probabilities": probabilities}
            if modify:
                modify(response, arrays)
            np.savez(payload["output_path"], **arrays)
            return response

        client._exchange = exchange
        return client

    def test_arrays_are_closed_and_job_files_removed_after_success_or_rejection(self):
        tensor = np.zeros((2, 3, 48, 320), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory, patch.object(tempfile, "tempdir", directory):
            indices, probabilities = self.client(directory).infer(tensor, 6)
            self.assertEqual(indices.shape, (2, 5))
            self.assertEqual(probabilities.dtype, np.float32)
            self.assertEqual(list(Path(directory).iterdir()), [])
            mutations = [lambda r, a: r.update(rows=3), lambda r, a: r.update(gpu_nodes=0),
                         lambda r, a: a.update(indices=a["indices"].astype(np.int32)),
                         lambda r, a: a["indices"].fill(label_gpu.MODEL_CLASSES),
                         lambda r, a: a["probabilities"].fill(float("nan")),
                         lambda r, a: a["probabilities"].fill(1.1)]
            for mutation in mutations:
                with self.assertRaises(label_gpu.GPUFallback):
                    self.client(directory, mutation).infer(tensor, 6)
                self.assertEqual(list(Path(directory).iterdir()), [])


class ControlChannelTests(unittest.TestCase):
    """Exercise real IPC teardown without installing or claiming to run a GPU provider."""

    def start(self, code):
        directory = tempfile.TemporaryDirectory(prefix="poe2-gpu-ipc-test-")
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / "helper.py"
        script.write_text(code, encoding="utf-8")
        actual_popen = subprocess.Popen

        def launch(arguments, **options):
            return actual_popen([sys.executable, "-B", str(script), *arguments[1:]], **options)

        with patch.object(label_gpu.subprocess, "Popen", side_effect=launch), \
                patch.object(label_gpu, "REQUEST_TIMEOUT", .5):
            client = label_gpu._Client("owned-test-helper", "not-a-model", 2)
        self.addCleanup(client.close)
        return client

    def test_actual_channel_init_eof_and_reaping(self):
        client = self.start("import json,sys\nrequest=json.loads(sys.stdin.readline())\n"
                            "print(json.dumps({'ok':True,'protocol':1,'provider':'DmlExecutionProvider'}),flush=True)\n"
                            "sys.stdin.readline()\n")
        with self.assertRaisesRegex(label_gpu.GPUFallback, "closed its channel"):
            client._exchange({"op": "infer"})
        client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertNotIn(client, label_gpu._CLIENTS)

    def test_actual_blocked_channel_times_out_and_is_reaped(self):
        client = self.start("import json,sys,time\nrequest=json.loads(sys.stdin.readline())\n"
                            "print(json.dumps({'ok':True,'protocol':1,'provider':'DmlExecutionProvider'}),flush=True)\n"
                            "sys.stdin.readline()\ntime.sleep(60)\n")
        with patch.object(label_gpu, "REQUEST_TIMEOUT", .03):
            with self.assertRaisesRegex(label_gpu.GPUFallback, "timed out"):
                client._exchange({"op": "infer"})
        client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertNotIn(client, label_gpu._CLIENTS)

    def test_failed_inference_reaps_helper_before_removing_tensor_directory(self):
        client = self.start("import json,sys,time\nsys.stdin.readline()\n"
                            "print(json.dumps({'ok':True,'protocol':1,'provider':'DmlExecutionProvider'}),flush=True)\n"
                            "sys.stdin.readline()\ntime.sleep(60)\n")
        original = tempfile.TemporaryDirectory
        observed = []

        class CheckedDirectory(original):
            def __exit__(context, *args):
                observed.append(client.process.poll() is not None)
                return super().__exit__(*args)

        with patch.object(label_gpu, "REQUEST_TIMEOUT", .03), \
                patch.object(tempfile, "TemporaryDirectory", CheckedDirectory):
            with self.assertRaises(label_gpu.GPUFallback):
                client.infer(np.zeros((1, 3, 48, 320), dtype=np.float32), 6)
        self.assertEqual(observed, [True])

    def test_rejected_provider_init_reaps_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "wrong.py"
            script.write_text("import json,sys,time\nsys.stdin.readline()\n"
                              "print(json.dumps({'ok':True,'protocol':1,'provider':'CPUExecutionProvider'}),flush=True)\n"
                              "time.sleep(60)\n")
            actual_popen = subprocess.Popen
            processes = []

            def launch(arguments, **options):
                process = actual_popen([sys.executable, "-B", str(script)], **options)
                processes.append(process)
                return process

            with patch.object(label_gpu.subprocess, "Popen", side_effect=launch):
                with self.assertRaisesRegex(label_gpu.GPUFallback, "did not select DirectML"):
                    label_gpu._Client("owned-test-helper", "not-a-model", 2)
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
