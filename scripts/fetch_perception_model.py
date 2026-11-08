#!/usr/bin/env python3
"""Fetch the pinned, hash-checked OpenCV Zoo NanoDet ONNX (3.8 MB). No GPU."""
import hashlib
from pathlib import Path
from urllib.request import urlopen

REVISION = '48b10fa505aeb51fbd127fc60a5c660040e823b7'
MODEL_SHA256 = '4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186'
MODEL_PATH = Path(__file__).resolve().parents[1] / 'models/object_detection_nanodet_2022nov.onnx'
URL = (f'https://media.githubusercontent.com/media/opencv/opencv_zoo/{REVISION}/'
       'models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx')


def main():
    if MODEL_PATH.exists() and hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256:
        print(f'Already verified: {MODEL_PATH}')
        return
    with urlopen(URL, timeout=60) as response:
        data = response.read(4_000_001)
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise RuntimeError('Model SHA-256 mismatch; no model installed')
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MODEL_PATH.with_suffix('.download')
    temporary.write_bytes(data)
    temporary.replace(MODEL_PATH)
    print(f'Installed verified CPU detector: {MODEL_PATH}')


if __name__ == '__main__':
    main()
