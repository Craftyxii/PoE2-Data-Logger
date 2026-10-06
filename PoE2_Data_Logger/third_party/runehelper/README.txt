RuneHelper, commit ee81c24e2ab40a986e4de792a8362e9b1472df80
https://github.com/Denzeriko/RuneHelper
License: MIT (see LICENSE.txt)

english.onnx is a layer-for-layer ONNX conversion of
RuneHelper/resources/text_model.bin, using its weights and charset.
The runtime adapter in runehelper_ocr.py follows the native LineReader,
PanelPreparation, RowFinder, and TextStart code paths.

The logger uses the local Family/Recipe DB after reading the text. RuneHelper's
price lookup and overlay are not included.

The local ONNX session uses two inference threads and one inter-operation
thread. The hotkey quick path checks the panel heading with the bundled
generic OCR model before applying the confidence checks.

Unconfigured remnant hotkey captures use a left-side window,
refined by RuneHelper's FindPanel edge-density checks. PanelPreparation.cpp
from the revision above is included as the source for that adapter. Capture
regions are adjustable. General tooltip OCR uses separate text boxes for opened
rewards and adjacent item-tooltip text.
