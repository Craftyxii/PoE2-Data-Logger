The catalog artwork transform cache adapts the numerical order of crossCorr and
matchTemplateMask from OpenCV 4.10.0, not a new recognition model.

Source:
https://github.com/opencv/opencv/blob/4.10.0/modules/imgproc/src/templmatch.cpp
https://github.com/opencv/opencv/blob/4.10.0/modules/core/src/matrix_expressions.cpp

templmatch.cpp SHA256:
1e0c8fbde4aa87ae95f24721a6dc4746de9249b435c6c792dcc2fec5d418613a

Adaptation: retain immutable source Fourier transforms and fixed binary-mask
normalization between calls. Keep every candidate, translation, masked float32
operation, float64 transform and per-channel float32 accumulation. The pinned
CPU backend is checked against the original full response before reuse. No scan
capture, input automation, thresholds, identities or review policy are added.

Copyright (C) 2000, Intel Corporation. See LICENSE.txt for the complete original
notice, conditions and disclaimer. The runtime OpenCV dependency also carries
its own upstream library notices.
