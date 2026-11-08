"""Optical-flow measurements inside NVIDIA boxes; no object recognition or depth."""
from __future__ import annotations
import base64
import cv2
import numpy as np


def gray_frame(data_url):
    image = cv2.imdecode(np.frombuffer(base64.b64decode(data_url.split(',', 1)[1]), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Cannot decode frame for motion measurement')
    height, width = image.shape
    return cv2.resize(image, (320, max(1, round(height*320/width))))


def fit_flow(previous, current, mask):
    points = cv2.goodFeaturesToTrack(previous, maxCorners=200, qualityLevel=.005, minDistance=3, mask=mask)
    if points is None or len(points) < 6:
        return None
    options = dict(winSize=(31,31), maxLevel=4,
                   criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, .01))
    forward, good, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None, **options)
    if forward is None:
        return None
    backward, back_good, _ = cv2.calcOpticalFlowPyrLK(current, previous, forward, None, **options)
    if backward is None:
        return None
    valid = ((good.ravel() == 1) & (back_good.ravel() == 1)
             & (np.linalg.norm(points-backward, axis=2).ravel() < 1.0))
    source, target = points[valid].reshape(-1,2), forward[valid].reshape(-1,2)
    if len(source) < 6:
        return None
    affine, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC,
        ransacReprojThreshold=1.5, maxIters=1000, confidence=.99)
    if affine is None or not np.isfinite(affine).all() or inliers is None:
        return None
    fraction = float(np.mean(inliers))
    if fraction < .7:
        return None
    inlier_points = source[inliers.ravel().astype(bool)]
    # A tiny cluster cannot establish object expansion.
    if np.ptp(inlier_points[:,0]) < 6 or np.ptp(inlier_points[:,1]) < 6:
        return None
    return affine, int(np.sum(inliers)), fraction


def mask_box(shape, box, value=255):
    height, width = shape
    mask = np.zeros(shape, dtype=np.uint8)
    x1,y1,x2,y2 = box
    # Slight expansion absorbs imperfect NVIDIA grounding without selecting the full scene.
    dx,dy = (x2-x1)*.1, (y2-y1)*.1
    left,right = max(0,int((x1-dx)*width)), min(width,int((x2+dx)*width)+1)
    top,bottom = max(0,int((y1-dy)*height)), min(height,int((y2+dy)*height)+1)
    mask[top:bottom,left:right] = value
    return mask


class MotionPair:
    def __init__(self, previous_url, current_url, elapsed, previous_boxes):
        self.previous, self.current = gray_frame(previous_url), gray_frame(current_url)
        self.elapsed = elapsed
        self.valid = self.previous.shape == self.current.shape and .05 <= elapsed <= 5
        self.camera_motion = 'unknown'
        if not self.valid:
            return
        background = np.full(self.previous.shape, 255, dtype=np.uint8)
        for box in previous_boxes:
            background[mask_box(self.previous.shape,box) > 0] = 0
        fit = fit_flow(self.previous,self.current,background)
        if fit is not None and fit[1] >= 12:
            affine = fit[0]
            height,width = self.previous.shape
            probes = np.array([[0,0,1],[width,0,1],[0,height,1],[width,height,1]], dtype=float)
            movement = np.linalg.norm(probes@affine.T-probes[:,:2], axis=1).mean()/width/elapsed
            self.camera_motion = 'still' if movement < .003 else 'moving' if movement > .01 else 'unknown'

    def object(self, box):
        unknown = {'relative_motion':'unknown','motion_source':'optical_flow',
                   'motion_status':'insufficient_features','predicted_box':box,
                   'scale_ratio':None,'horizontal_velocity':None,'flow_points':0}
        if not self.valid:
            return unknown
        fit = fit_flow(self.previous,self.current,mask_box(self.previous.shape,box))
        if fit is None:
            return unknown
        affine, count, fraction = fit
        scale = float(np.hypot(affine[0,0],affine[1,0]))
        if not .5 <= scale <= 2:
            return unknown
        height,width = self.previous.shape
        x1,y1,x2,y2 = box
        corners = np.array([[x1*width,y1*height,1],[x2*width,y1*height,1],
                            [x1*width,y2*height,1],[x2*width,y2*height,1]])
        projected = corners@affine.T
        predicted = [float(projected[:,0].min()/width),float(projected[:,1].min()/height),
                     float(projected[:,0].max()/width),float(projected[:,1].max()/height)]
        vx = ((predicted[0]+predicted[2])-(x1+x2))/(2*self.elapsed)
        rate = float(np.log(scale)/self.elapsed)
        motion = ('approaching' if rate > .04 else 'away' if rate < -.04
                  else 'right' if vx > .02 else 'left' if vx < -.02
                  else 'still' if abs(rate) < .02 and abs(vx) < .005 else 'unknown')
        return {'relative_motion':motion,'motion_source':'optical_flow','motion_status':'measured',
                'predicted_box':predicted,'scale_ratio':scale,'horizontal_velocity':vx,
                'flow_points':count,'flow_inlier_fraction':fraction}
