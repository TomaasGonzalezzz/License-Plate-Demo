"""Keep the gate line attached to the road when the camera moves a little.

Cameras on poles sway with the wind, and hand-held or gimbal footage drifts. The
gate line is drawn on the first frame; if the image shifts or zooms, a parked
car can "cross" a line that stayed fixed on the screen.

Each frame is matched to the first one with ORB keypoints taken from the
background only (vehicle boxes are masked out), and a similarity transform
(shift, rotation, zoom) is estimated with RANSAC. Vehicle positions are mapped
back to the first frame before checking the line, and the line is drawn where
it falls in the current frame. If the estimate fails, the last good one is kept.
"""

from __future__ import annotations

import cv2
import numpy as np


class Stabilizer:
    def __init__(self, work_width: int = 640, features: int = 1500, min_inliers: int = 25):
        self.work_width = work_width
        self.orb = cv2.ORB_create(features)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.min_inliers = min_inliers
        self.ref_kp = None
        self.ref_des = None
        self.scale = 1.0
        self.to_ref_m = np.array([[1, 0, 0], [0, 1, 0]], dtype=float)  # current -> reference (working px)
        self.ok_frames = 0
        self.failed_frames = 0

    def _features(self, frame_bgr: np.ndarray, boxes) -> tuple:
        h, w = frame_bgr.shape[:2]
        self.scale = self.work_width / w
        small = cv2.resize(frame_bgr, (self.work_width, int(h * self.scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = np.full(gray.shape, 255, np.uint8)
        for x1, y1, x2, y2 in boxes:
            pad = 8
            cv2.rectangle(mask, (int(x1 * self.scale) - pad, int(y1 * self.scale) - pad),
                          (int(x2 * self.scale) + pad, int(y2 * self.scale) + pad), 0, -1)
        return self.orb.detectAndCompute(gray, mask)

    def update(self, frame_bgr: np.ndarray, boxes) -> np.ndarray:
        """Feed the working frame and the vehicle boxes; returns the current->reference 2x3 matrix."""
        kp, des = self._features(frame_bgr, boxes)
        if self.ref_des is None:
            self.ref_kp, self.ref_des = kp, des
            return self.to_ref_m
        if des is None or len(kp) < self.min_inliers:
            self.failed_frames += 1
            return self.to_ref_m
        matches = self.matcher.match(des, self.ref_des)
        if len(matches) < self.min_inliers:
            self.failed_frames += 1
            return self.to_ref_m
        src = np.float32([kp[m.queryIdx].pt for m in matches]) / self.scale
        dst = np.float32([self.ref_kp[m.trainIdx].pt for m in matches]) / self.scale
        m, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
        if m is None or inliers is None or int(inliers.sum()) < self.min_inliers:
            self.failed_frames += 1
            return self.to_ref_m
        self.to_ref_m = m
        self.ok_frames += 1
        return m

    def to_ref(self, point) -> tuple[float, float]:
        x, y = float(point[0]), float(point[1])
        m = self.to_ref_m
        return (m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2])

    def from_ref(self, point) -> tuple[float, float]:
        inv = cv2.invertAffineTransform(self.to_ref_m)
        x, y = float(point[0]), float(point[1])
        return (inv[0, 0] * x + inv[0, 1] * y + inv[0, 2], inv[1, 0] * x + inv[1, 1] * y + inv[1, 2])

    @property
    def shift(self) -> float:
        """How far the current view is from the first one (pixels at the frame centre, rough)."""
        return float(np.hypot(self.to_ref_m[0, 2], self.to_ref_m[1, 2]))
