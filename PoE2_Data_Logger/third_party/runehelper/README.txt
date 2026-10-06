RuneHelper, commit ee81c24e2ab40a986e4de792a8362e9b1472df80
https://github.com/Denzeriko/RuneHelper
License: MIT (see LICENSE.txt)

english.onnx is a layer-for-layer conversion of
RuneHelper/resources/text_model.bin, with unchanged weights and charset.
The runtime adapter in runehelper_ocr.py follows the native LineReader,
PanelPreparation, RowFinder, and TextStart code paths.

The logger uses the local Family/Recipe DB after reading the text. RuneHelper's
price lookup and overlay are not included.

The local ONNX session uses two inference threads and one inter-operation
thread. The hotkey quick path verifies the panel heading with the existing
bundled generic OCR model and retains the original commit confidence checks.
Model weights, charset and recipe-family matching rules are unchanged.

Unconfigured remnant hotkey captures use a left-side window,
refined by RuneHelper's FindPanel edge-density checks. PanelPreparation.cpp
from the revision above is included as the source for that adapter. User-selected
capture regions remain adjustable. The general tooltip OCR retains its original
text boxes so opened rewards are not joined to adjacent item-tooltip text.
