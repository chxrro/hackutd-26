#!/usr/bin/env python3
"""Create calibration targets or fit camera intrinsics from checkerboard photos."""
import argparse
import json
from pathlib import Path

from session_recording import private_write

# Repo-root paths, so running from another directory still lands in git-ignored /data/.
CALIBRATION_DIR = Path(__file__).resolve().parents[1] / 'data' / 'calibration'


def targets(folder):
    import cv2
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 10 x 7 squares = 9 x 6 INNER corners; measure printed square size.
    board = np.full((900, 1200), 255, dtype=np.uint8)
    for y in range(7):
        for x in range(10):
            board[100+y*100:100+(y+1)*100, 100+x*100:100+(x+1)*100] = 0 if (x+y) % 2 == 0 else 255
    marker = np.full((700, 700), 255, dtype=np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker[100:600, 100:600] = cv2.aruco.generateImageMarker(dictionary, 0, 500)
    for name, image in [('checkerboard_9x6.png', board), ('aruco_4x4_50_id0.png', marker)]:
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise OSError('Could not encode target')
        private_write(folder / name, encoded.tobytes())
    print(f'Targets saved in {folder}; measure printed dimensions, not PNG pixels.')


def calibrate(paths, output, square_size_m):
    import cv2
    import numpy as np
    if not np.isfinite(square_size_m) or square_size_m <= 0:
        raise ValueError('Measure checkerboard square size in meters')
    points = np.zeros((9*6, 3), dtype=np.float32)
    points[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2) * square_size_m
    world, image_points, used = [], [], []
    size = None
    for path in paths:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        found, corners = cv2.findChessboardCornersSB(image, (9, 6))
        if not found:
            continue
        current = image.shape[::-1]
        if size is not None and current != size:
            raise ValueError('All calibration images must have the same resolution/crop')
        size = current
        world.append(points.copy())
        image_points.append(corners)
        used.append(path.name)
    if len(world) < 8:
        raise ValueError(f'Need at least 8 successful diverse checkerboard views; found {len(world)}')
    rms, matrix, distortion, _, _ = cv2.calibrateCamera(world, image_points, size, None, None)
    if not np.isfinite(rms) or not np.isfinite(matrix).all() or not np.isfinite(distortion).all() or rms > 2:
        raise ValueError(f'Calibration failed fit-quality gate (RMS {rms:.3f} pixels); recapture views')
    result = {'schema_version': 1, 'image_width': size[0], 'image_height': size[1],
        'camera_matrix': matrix.tolist(), 'distortion_coefficients': distortion.ravel().tolist(),
        'reprojection_rms_px': rms, 'checkerboard_inner_corners': [9, 6],
        'square_size_m': square_size_m, 'views': used, 'validated_on_real_measurements': False,
        'required_validation': 'Same camera, crop, zoom and focus. Held-out tape-measured target distances. '
                               'Low reprojection error does not prove accurate distance.'}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(output, (json.dumps(result, indent=2, allow_nan=False) + '\n').encode())
    print(f'{output}: {len(used)} views; fit RMS {rms:.3f}px; physical range accuracy unvalidated')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    target = commands.add_parser('targets')
    target.add_argument('--output-dir', type=Path, default=CALIBRATION_DIR / 'targets')
    fit = commands.add_parser('fit')
    fit.add_argument('images', type=Path)
    fit.add_argument('--square-size-m', type=float, required=True)
    fit.add_argument('--output', type=Path, default=CALIBRATION_DIR / 'camera.json')
    args = parser.parse_args()
    if args.command == 'targets':
        targets(args.output_dir)
    else:
        paths = sorted(p for p in args.images.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png'))
        calibrate(paths, args.output, args.square_size_m)


if __name__ == '__main__':
    main()
