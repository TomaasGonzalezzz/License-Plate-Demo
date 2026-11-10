"""Video in and out, one frame at a time.

Each frame is kept at two sizes: the original (used to crop plates, where every
pixel counts) and a working copy at most ``max_width`` wide (used to detect and
track vehicles, and to draw the output). Frames are never all held in memory.
The source can also be an RTSP URL from a recorder (``rtsp://user:pass@ip:554/...``).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    width: int  # working size
    height: int
    fps: float
    source_width: int
    source_height: int
    frames: int

    @property
    def scale(self) -> float:
        """Working pixels per source pixel."""
        return self.width / self.source_width


def probe(source: str | Path, max_width: int = 1280, fps: float | None = None) -> VideoInfo:
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {source}")
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    reported = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    scale = min(1.0, max_width / sw)
    w, h = round(sw * scale), round(sh * scale)
    return VideoInfo(w - w % 2, h - h % 2, fps or reported or 25.0, sw, sh, n)


def read_frames(source: str | Path, info: VideoInfo, start: int = 0, end: int | None = None,
                stride: int = 1) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    """Yield ``(frame_index, full_frame, working_frame)`` every ``stride`` frames in ``[start, end)``."""
    cap = cv2.VideoCapture(str(source))
    index = 0
    try:
        if start:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            index = start
        while end is None or index < end:
            if (index - start) % stride:
                if not cap.grab():
                    break
                index += 1
                continue
            ok, frame = cap.read()
            if not ok:
                break
            small = frame
            if frame.shape[1] != info.width or frame.shape[0] != info.height:
                small = cv2.resize(frame, (info.width, info.height), interpolation=cv2.INTER_AREA)
            yield index, frame, small
            index += 1
    finally:
        cap.release()


class VideoWriter:
    def __init__(self, path: str | Path, width: int, height: int, fps: float):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        self._w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not self._w.isOpened():
            raise RuntimeError(f"cannot open video writer for {path}")

    def write(self, frame: np.ndarray) -> None:
        self._w.write(frame)

    def close(self) -> None:
        self._w.release()


def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def to_h264(src: Path, dst: Path, crf: int = 26, width: int | None = None) -> bool:
    """Re-encode to H.264 (plays in browsers). Returns False if ffmpeg is not available."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    vf = ["-vf", f"scale={width}:-2"] if width else []
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(src), *vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-crf", str(crf), "-preset", "slow", "-movflags", "+faststart", "-an", str(dst)]
    return subprocess.run(cmd).returncode == 0


def to_gif(src: Path, dst: Path, start: float, duration: float, width: int = 720, fps: int = 8) -> bool:
    exe = ffmpeg_exe()
    if not exe:
        return False
    filt = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    cmd = [exe, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(duration), "-i", str(src),
           "-filter_complex", filt, str(dst)]
    return subprocess.run(cmd).returncode == 0
