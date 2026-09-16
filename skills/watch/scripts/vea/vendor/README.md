# TransNet V2 inference

`transnetv2_pytorch.py` is the unmodified MIT-licensed upstream implementation:
https://github.com/soCzech/TransNetV2/blob/85cef72af9a916bdfd7cc94a670c9cdfbf12d1ed/inference-pytorch/transnetv2_pytorch.py

Copyright 2020 Tomáš Souček. See TRANSNET-LICENSE.txt.

Weights are provided separately as `transnetv2-weights.npz` and loaded with
`allow_pickle=False`. They were converted using the official conversion script,
from that revision's TensorFlow saved model. No retraining or tuning of weights.
The NPZ contains the upstream license and source as Unicode string entries.
To reproduce the conversion, see `scripts/convert_transnet_weights.py`.

Runtime: optional PyTorch (CPU), NumPy, OpenCV; ffmpeg and ffprobe. Core watch
functionality continues to work without these optional Python packages.
