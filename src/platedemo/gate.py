"""Entry/exit by line crossing, and the register of who is still inside.

The line is drawn from (x1, y1) to (x2, y2) in the frame. Each tracked vehicle
is reduced to one point (bottom-centre of its box). When the point moves from
one side of the line to the other, that is a crossing. By convention, moving to
the right-hand side of the line as drawn is ``entra`` (for a horizontal line
drawn left to right, that is moving down the image); ``--flip`` swaps it.

Two rules keep crossings stable when boxes jitter (same as the counting demo):
a side only counts once the point is ``margin`` pixels away from the line, and a
vehicle is counted at most once.

``Register`` keeps the vehicles inside. An entry adds the plate; an exit removes
the closest plate inside (tolerant to OCR confusions). It can start from a
previous state (who was already inside when the video starts), which is how a
real gate would carry the register from one day or camera to the next.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from platedemo.formats import normalize
from platedemo.matching import plate_distance

IN, OUT = "entra", "sale"


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def parse(cls, text: str) -> Line:
        parts = [float(v) for v in text.replace(" ", "").split(",")]
        if len(parts) != 4:
            raise ValueError(f"line must be x1,y1,x2,y2, got {text!r}")
        line = cls(*parts)
        if line.length == 0:
            raise ValueError("line endpoints must differ")
        return line

    @property
    def length(self) -> float:
        return float(np.hypot(self.x2 - self.x1, self.y2 - self.y1))

    def scaled(self, f: float) -> Line:
        return Line(self.x1 * f, self.y1 * f, self.x2 * f, self.y2 * f)

    def signed_distance(self, point) -> float:
        """Positive on the right-hand side of the line as drawn."""
        x, y = float(point[0]), float(point[1])
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        return (dx * (y - self.y1) - dy * (x - self.x1)) / self.length

    def along(self, point) -> float:
        x, y = float(point[0]), float(point[1])
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        return ((x - self.x1) * dx + (y - self.y1) * dy) / self.length


@dataclass
class _Side:
    side: int = 0
    counted: bool = False
    pending: int = 0  # consecutive observations clearly on the other side
    last: tuple[float, float] | None = None


class CrossingDetector:
    """See the module docstring. Extra guards against false crossings, all seen in real footage:

    * ``confirm``: the vehicle must be seen on the new side this many times in a row,
      so a box that flickers for one frame does not count;
    * ``confirm_distance``: to confirm, the point must be at least this far past the
      line (not just past the dead zone), so a box that grows or shrinks a little
      (a trailer that is sometimes included in the box) does not count;
    * jumps: if a track's point jumps farther than ``jump_limit`` between two
      observations, the tracker probably swapped two vehicles' IDs (for example a
      motorcycle passing right next to a parked car) or the box changed shape
      abruptly. The track's side is forgotten, so the jump itself is never a crossing.
    """

    def __init__(self, line: Line, margin: float = 6.0, flip: bool = False, extend: float = 0.0,
                 confirm: int = 1, confirm_distance: float = 0.0):
        self.line = line
        self.margin = margin
        self.flip = flip
        self.extend = extend  # accept crossings this many pixels beyond the segment ends
        self.confirm = max(1, confirm)
        self.confirm_distance = max(confirm_distance, margin)
        self._tracks: dict[int, _Side] = {}

    def update(self, track_id: int, point, jump_limit: float | None = None, reset: bool = False) -> str | None:
        """Feed one position of one track; return ``entra``/``sale`` when it crosses, else None.

        ``reset=True`` forgets the track's side (the caller saw the box change abruptly).
        """
        st = self._tracks.setdefault(int(track_id), _Side())
        x, y = float(point[0]), float(point[1])
        jumped = st.last is not None and jump_limit is not None and np.hypot(x - st.last[0], y - st.last[1]) > jump_limit
        if reset or jumped:
            st.side, st.pending = 0, 0  # identity or box jump: start over (counted stays as it was)
        st.last = (x, y)
        d = self.line.signed_distance(point)
        s = self.line.along(point)
        if abs(d) < self.margin or not (-self.extend <= s <= self.line.length + self.extend):
            return None
        side = 1 if d > 0 else -1
        if not st.side:
            st.side = side
            return None
        if side == st.side:
            st.pending = 0
            return None
        if abs(d) < self.confirm_distance:
            return None  # on the other side, but not clearly yet
        st.pending += 1
        if st.pending < self.confirm:
            return None
        st.side, st.pending = side, 0
        if st.counted:
            return None
        st.counted = True
        positive = side > 0
        return IN if positive != self.flip else OUT

    def uncount(self, track_id: int) -> None:
        """Forget that this track was counted (the caller decided the crossing was not real)."""
        st = self._tracks.get(int(track_id))
        if st:
            st.counted = False

    def side_of(self, track_id: int) -> int:
        st = self._tracks.get(int(track_id))
        return st.side if st else 0


@dataclass
class InsideRecord:
    key: str  # plate, or "#<track id>" when the plate was not read
    plate: str
    status: str
    since: str  # clock time of entry
    label: str = ""
    previous: bool = False  # came from the initial state, not from this video


@dataclass
class Register:
    max_distance: float = 1.0
    inside: dict[str, InsideRecord] = field(default_factory=dict)
    entries: int = 0
    exits: int = 0
    exits_without_entry: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None, max_distance: float = 1.0) -> Register:
        reg = cls(max_distance=max_distance)
        if path and Path(path).exists():
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    plate = normalize(row.get("patente", ""))
                    if plate:
                        reg.inside[plate] = InsideRecord(plate, plate, row.get("estado", "").strip(),
                                                         row.get("hora_ingreso", "").strip(), row.get("nombre", "").strip(),
                                                         previous=True)
        return reg

    def enter(self, plate: str | None, track_id: int, status: str, clock: str, label: str = "") -> None:
        self.entries += 1
        key = normalize(plate or "") or f"#{track_id}"
        self.inside[key] = InsideRecord(key, normalize(plate or ""), status, clock, label)

    def exit(self, plate: str | None, track_id: int) -> InsideRecord | None:
        """Remove the vehicle from the register; return the matching record or None if it was not inside."""
        self.exits += 1
        plate = normalize(plate or "")
        if not plate:
            rec = self.inside.pop(f"#{track_id}", None)
            if rec is None:
                self.exits_without_entry.append(f"#{track_id}")
            return rec
        best_key, best_d = None, None
        for key, rec in self.inside.items():
            if not rec.plate:
                continue
            d = plate_distance(plate, rec.plate)
            if d <= self.max_distance and (best_d is None or d < best_d):
                best_key, best_d = key, d
        if best_key is None:
            self.exits_without_entry.append(plate)
            return None
        return self.inside.pop(best_key)

    def still_inside(self) -> list[InsideRecord]:
        return sorted(self.inside.values(), key=lambda r: r.since)

    def save(self, path: str | Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["patente", "estado", "hora_ingreso", "nombre"])
            for r in self.still_inside():
                w.writerow([r.plate or r.key, r.status, r.since, r.label])
