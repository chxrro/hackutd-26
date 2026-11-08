"""CPU replay baseline. Track IDs and target ranges are experimental, not alerts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

COCO_CLASSES = ('person bicycle car motorcycle airplane bus train truck boat traffic_light '
    'fire_hydrant stop_sign parking_meter bench bird cat dog horse sheep cow elephant bear zebra '
    'giraffe backpack umbrella handbag tie suitcase frisbee skis snowboard sports_ball kite '
    'baseball_bat baseball_glove skateboard surfboard tennis_racket bottle wine_glass cup fork '
    'knife spoon bowl banana apple sandwich orange broccoli carrot hot_dog pizza donut cake '
    'chair couch potted_plant bed dining_table toilet tv laptop mouse remote keyboard cell_phone '
    'microwave oven toaster sink refrigerator book clock vase scissors teddy_bear hair_drier '
    'toothbrush').split()


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda box: max(0, box[2] - box[0]) * max(0, box[3] - box[1])
    return intersection / max(1e-12, area(a) + area(b) - intersection)


class ObjectTracker:
    """Class-aware greedy IoU association; expire IDs by capture time, not FPS.

    No re-identification or camera-motion compensation. Crossing/occlusion and
    fast movement can switch IDs. Missing objects are never reported as clear.
    """
    def __init__(self, max_gap_seconds=3.0, match_iou=0.25):
        self.max_gap = max_gap_seconds
        self.match_iou = match_iou
        self.tracks = {}
        self.next_id = 1
        self.last_timestamp = None

    def update(self, detections, captured_at, image_width):
        if not math.isfinite(captured_at) or image_width <= 0:
            raise ValueError('Finite capture time and positive image width required')
        if self.last_timestamp is not None and captured_at <= self.last_timestamp:
            raise ValueError('Tracking requires strictly increasing capture timestamps')
        self.last_timestamp = captured_at
        self.tracks = {key: track for key, track in self.tracks.items()
                       if captured_at - track['captured_at'] <= self.max_gap}
        pairs = sorted([(iou(track['box'], detection['box']), key, index)
                        for key, track in self.tracks.items()
                        for index, detection in enumerate(detections)
                        if track['label'] == detection['label']], reverse=True)
        assigned, used = {}, set()
        for score, key, index in pairs:
            if score >= self.match_iou and key not in used and index not in assigned:
                assigned[index] = key
                used.add(key)
        current = []
        for index, detection in enumerate(detections):
            key = assigned.get(index)
            if key is None:
                key, self.next_id = self.next_id, self.next_id + 1
            previous = self.tracks.get(key)
            box = detection['box']
            center = (box[0] + box[2]) / 2 / image_width
            item = {**detection, 'track_id': key, 'captured_at': captured_at,
                    'direction': 'left' if center < 1/3 else 'right' if center > 2/3 else 'center',
                    'collision_risk': 'unknown', 'motion_evidence': 'insufficient_history'}
            if previous:
                old_height = previous['box'][3] - previous['box'][1]
                ratio = (box[3] - box[1]) / max(1e-6, old_height)
                item['box_height_ratio'] = round(ratio, 3)
                item['motion_evidence'] = 'image_expansion' if ratio > 1.1 else 'no_large_image_expansion'
                # Expansion can be caused by camera rotation or detector jitter.
            self.tracks[key] = item
            current.append(item)
        missing = [{'track_id': key, 'label': track['label'], 'state': 'missing',
                    'seconds_since_seen': captured_at - track['captured_at']}
                   for key, track in self.tracks.items() if track['captured_at'] != captured_at]
        return {'objects': current, 'missing_tracks': missing,
                'collision_risk': 'unknown', 'path_state': 'unverified'}


class NanoDetector:
    def __init__(self, model_path):
        import cv2
        from vendor.nanodet.nanodet import NanoDet
        from fetch_perception_model import MODEL_SHA256
        if hashlib.sha256(Path(model_path).read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('Unexpected NanoDet model; run fetch_perception_model.py')
        self.cv2 = cv2
        self.detector = NanoDet(str(model_path))

    def detect(self, image):
        import numpy as np
        height, width = image.shape[:2]
        scale = min(416 / width, 416 / height)
        new_width, new_height = round(width * scale), round(height * scale)
        xpad, ypad = (416 - new_width) // 2, (416 - new_height) // 2
        padded = np.zeros((416, 416, 3), dtype=np.uint8)
        padded[ypad:ypad+new_height, xpad:xpad+new_width] = self.cv2.resize(image, (new_width, new_height))
        # Use actual rounded resize factors when mapping back to original pixels.
        results = []
        for x1, y1, x2, y2, confidence, class_id in self.detector.infer(padded):
            box = [max(0., min(float(width), (x1-xpad)*width/new_width)),
                   max(0., min(float(height), (y1-ypad)*height/new_height)),
                   max(0., min(float(width), (x2-xpad)*width/new_width)),
                   max(0., min(float(height), (y2-ypad)*height/new_height))]
            if box[2] > box[0] and box[3] > box[1]:
                results.append({'label': COCO_CLASSES[int(class_id)], 'confidence': float(confidence),
                                'box': box, 'range': {'status': 'unknown', 'meters': None,
                                'reason': 'No validated depth for ordinary objects'}})
        return results


class MarkerRange:
    """Metric pose only for a known-size ArUco target and calibrated camera.

    Intrinsics must describe the same camera/view/crop. Scaling the saved image
    is allowed only at the same aspect ratio. This is not general object depth.
    """
    def __init__(self, calibration_path, marker_size_m):
        import cv2
        import numpy as np
        if not math.isfinite(marker_size_m) or marker_size_m <= 0:
            raise ValueError('Measure the printed marker side in meters')
        self.cv2, self.np = cv2, np
        self.calibration = json.loads(Path(calibration_path).read_text())
        c = self.calibration
        self.matrix = np.asarray(c['camera_matrix'], dtype=float)
        self.distortion = np.asarray(c['distortion_coefficients'], dtype=float)
        if (self.matrix.shape != (3, 3) or not np.isfinite(self.matrix).all()
                or self.matrix[0, 0] <= 0 or self.matrix[1, 1] <= 0
                or not np.allclose(self.matrix[2], [0, 0, 1])
                or self.distortion.size not in (4, 5, 8, 12, 14)
                or not np.isfinite(self.distortion).all()
                or min(c['image_width'], c['image_height']) <= 0):
            raise ValueError('Invalid camera intrinsics/distortion')
        half = marker_size_m / 2
        self.points = np.array([[-half, half, 0], [half, half, 0],
                                [half, -half, 0], [-half, -half, 0]], dtype=np.float32)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.detector = cv2.aruco.ArucoDetector(dictionary)

    def detect(self, image):
        cv2, np, c = self.cv2, self.np, self.calibration
        height, width = image.shape[:2]
        sx, sy = width / c['image_width'], height / c['image_height']
        if abs(sx / sy - 1) > 0.01:
            raise ValueError('Calibration aspect ratio differs; recalibrate for this camera/crop')
        matrix = self.matrix.copy()
        matrix[0] *= sx
        matrix[1] *= sy
        corners, ids, _ = self.detector.detectMarkers(image)
        results = []
        for corners_one, marker_id in zip(corners, [] if ids is None else ids.flatten()):
            pixels = corners_one.reshape(4, 2)
            lengths = [float(np.linalg.norm(pixels[i] - pixels[(i+1) % 4])) for i in range(4)]
            range_result = {'status': 'unknown', 'meters': None, 'reason': 'target_too_small_or_clipped'}
            if (min(lengths) >= 20 and pixels[:, 0].min() > 2 and pixels[:, 1].min() > 2
                    and pixels[:, 0].max() < width - 3 and pixels[:, 1].max() < height - 3):
                found, rotations, translations, errors = cv2.solvePnPGeneric(
                    self.points, pixels, matrix, self.distortion, flags=cv2.SOLVEPNP_IPPE_SQUARE)
                if found:
                    reprojection = float(np.asarray(errors).ravel()[0])
                    distance = float(np.linalg.norm(translations[0]))
                    depth = float(translations[0].ravel()[2])
                    ambiguous = (len(translations) > 1 and float(np.asarray(errors).ravel()[1]) < reprojection + 0.5
                        and abs(float(np.linalg.norm(translations[1])) - distance) > distance * 0.1)
                    if depth > 0 and math.isfinite(distance) and reprojection <= 2 and not ambiguous:
                        range_result = {'status': 'experimental', 'meters': distance,
                            'forward_meters': depth, 'method': 'known_size_marker_pose',
                            'reprojection_error_px': reprojection,
                            'validated_on_real_measurements': False}
                    else:
                        range_result['reason'] = 'ambiguous_or_poor_pose'
            results.append({'label': f'aruco_4x4_50:{int(marker_id)}', 'confidence': None,
                'box': [float(pixels[:, 0].min()), float(pixels[:, 1].min()),
                        float(pixels[:, 0].max()), float(pixels[:, 1].max())], 'range': range_result})
        return results
