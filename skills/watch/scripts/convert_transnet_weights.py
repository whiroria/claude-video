"""Developer helper: convert official local TransNet V2 TF weights to safe NPZ.

Requires TensorFlow and PyTorch only on the conversion machine. The desktop
runtime needs PyTorch, NumPy and OpenCV, not TensorFlow. No automatic downloads.
Usage: python convert_transnet_weights.py /path/to/TransNetV2 /path/to/output.npz
"""
import argparse
import importlib.util
from pathlib import Path
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('upstream', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    root = args.upstream.resolve()
    sys.path.insert(0, str(root / 'inference-pytorch'))
    spec = importlib.util.spec_from_file_location('official_conversion', root / 'inference-pytorch' / 'convert_weights.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    model, _ = mod.convert_weights(str(root / 'inference' / 'transnetv2-weights'))
    import numpy as np
    np.savez_compressed(args.output, **{k: v.numpy() for k, v in model.state_dict().items()},
                        __license__=np.array((root / 'LICENSE').read_text()),
                        __source__=np.array('https://github.com/soCzech/TransNetV2'))


if __name__ == '__main__':
    main()
