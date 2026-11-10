import time

import cv2
import numpy as np

from platedemo.formats import PlateRules
from platedemo.live import LiveSource, PlateTracker
from platedemo.voting import PlateVoter


def _video(path, frames=60, fps=30):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    for i in range(frames):
        img = np.full((120, 160, 3), i * 4 % 255, np.uint8)
        w.write(img)
    w.release()


def test_realtime_source_drops_frames_when_the_consumer_is_slow(tmp_path):
    p = tmp_path / "v.mp4"
    _video(p, frames=60, fps=30)  # 2 seconds of video
    src = LiveSource(str(p), realtime=True).start()
    got = []
    t0 = time.perf_counter()
    while True:
        item = src.latest(timeout=3)
        if item is None:
            break
        if item == "wait":
            continue
        got.append(item[0])
        time.sleep(0.2)  # a consumer that needs 200 ms per frame (5 per second)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0  # kept the video's pace instead of falling behind
    assert 5 <= len(got) <= 16  # about 5 frames per second, the rest dropped
    assert got == sorted(got)


def test_file_source_without_realtime_delivers_every_frame(tmp_path):
    p = tmp_path / "v.mp4"
    _video(p, frames=20)
    src = LiveSource(str(p), realtime=False).start()
    got = []
    while (item := src.latest(timeout=3)) is not None:
        if item != "wait":
            got.append(item[0])
    assert got == list(range(20))


def test_plate_tracker_keeps_ids_and_expires():
    tr = PlateTracker(max_age=1.0)
    make = lambda: PlateVoter(PlateRules.from_spec("ar"))  # noqa: E731
    a = tr.update([(100, 100, 180, 130)], 0.0, make)[0][0]
    b = tr.update([(110, 105, 190, 135)], 0.2, make)[0][0]  # moved a little: same plate
    assert a.tid == b.tid
    c = tr.update([(600, 100, 680, 130)], 0.3, make)[0][0]  # far away: another plate
    assert c.tid != a.tid
    assert {t.tid for t in tr.expired(2.0)} == {a.tid, c.tid}


def test_decided_plate_expires_sooner_and_does_not_absorb_the_next_car():
    tr = PlateTracker(max_age=3.0, max_age_done=1.5)
    make = lambda: PlateVoter(PlateRules.from_spec("ar"))  # noqa: E731
    waiting = tr.update([(100, 100, 180, 130)], 0.0, make)[0][0]
    decided = tr.update([(400, 100, 480, 130)], 0.0, make)[0][0]
    decided.done = True
    # after a short gap, a box 3.5 plate widths away: an undecided track may reach it, a decided one may not
    nxt = tr.update([(680, 100, 760, 130)], 0.6, make)[0][0]
    assert nxt.tid != decided.tid
    gone = {t.tid for t in tr.expired(2.0)}
    assert decided.tid in gone and waiting.tid not in gone
