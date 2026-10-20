# Isolated label GPU helper

This is a separate Windows x64 Python3.12 executable for optional DirectML label
inference. The main application keeps `onnxruntime==1.22.1`. Only the helper's
separate build environment contains `onnxruntime-directml==1.22.0` and
`numpy==1.26.4`; these wheels are never installed into the main app environment.

DirectML supports compatible AMD, NVIDIA and Intel hardware on Windows. Adapter
selection prefers dedicated video memory and also permits integrated GPUs; it
does not require an AMD card. Unsupported devices fall back to the CPU reader.

`Build.bat` builds the helper first, then packages its complete directory at
`PoE2-Data-Logger/_internal/label_gpu/`. Runtime GPU use remains optional. The
parent launches the trusted `PoE2LabelGpu.exe` there with the owning OCR process's
explicit PID. The helper has its own nested `_internal` Python/DLL directory and
bundles only the pure protocol constants from the app, not the app or its OCR
packages. The verified model path is supplied by the parent; no model is
downloaded or duplicated in this bundle.

Standalone helper build, from the repository root on Windows:

```powershell
py -3.12 tools/build_label_gpu.py
```

This creates `build/label_gpu_env`, installs only `gpu_worker/requirements.txt`
there, freezes `dist/PoE2LabelGpu`, and verifies the x64 runtime, DirectML DLL,
native NumPy extension, protocol and source hashes. An already provisioned
isolated environment can use `--skip-install`. The main application spec rejects
missing or stale helper output. No Windows or physical-GPU validation is implied
by passing Linux contract tests; native Windows building and an actual selected
GPU run are separate release gates.

Implementation references: [ONNX Runtime DirectML configuration](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html#configuration-options)
defines sequential execution, disabled memory patterns and DXGI adapter indices;
[DXGI EnumAdapters1](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgifactory1-enumadapters1)
defines that adapter order. The parent retains RapidOCR3.9.2's preprocessing,
batch size and CTC decoder instead of introducing a second text/count parser.

Dependency notices are bundled through both wheels' distribution metadata.
ONNX Runtime uses Microsoft's MIT license; NumPy is distributed under its
BSD3-Clause license with bundled-component notices. DirectML and other native
components retain the license terms/notices distributed in their wheels. The helper uses
these libraries only for inference on supplied screenshots' normalized label
pixels. It does not inspect the game, its files or its processes; the PID supplied
to its watchdog is the Data Logger OCR process.
