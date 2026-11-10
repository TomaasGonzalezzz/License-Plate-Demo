import cv2
import numpy as np

from platedemo.stabilize import Stabilizer


def textured(w=640, h=360, seed=0):
    rng = np.random.default_rng(seed)
    img = (rng.random((h // 8, w // 8)) * 255).astype(np.uint8)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(cv2.GaussianBlur(img, (3, 3), 0), cv2.COLOR_GRAY2BGR)


def test_recovers_a_camera_shift():
    base = textured()
    shifted = cv2.warpAffine(base, np.float32([[1, 0, 12], [0, 1, -9]]), (640, 360), borderMode=cv2.BORDER_REFLECT)
    s = Stabilizer(work_width=640)
    s.update(base, [])
    s.update(shifted, [])
    # a point at (300, 200) in the shifted view is (288, 209) in the first view
    x, y = s.to_ref((300, 200))
    assert abs(x - 288) < 1.5 and abs(y - 209) < 1.5
    bx, by = s.from_ref((288, 209))
    assert abs(bx - 300) < 1.5 and abs(by - 200) < 1.5
