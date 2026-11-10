"""Compare a plate reading with the access lists, tolerating OCR confusions.

Lists are plain CSV files that anyone can edit in Excel:

* ``residentes.csv``: ``patente,nombre,detalle``
* ``proveedores.csv``: ``patente,nombre,detalle``
* ``visitas.csv``: ``patente,nombre,detalle,desde,hasta`` (authorised time window, ``HH:MM``)

The distance between two plates is an edit distance where swapping two
characters that look alike (``O``/``0``, ``I``/``1``, ``B``/``8``...) costs 0.25
instead of 1. A reading matches a list entry when the distance is at most
``max_distance`` (1.0 by default: one wrong character, or up to four look-alike
swaps). The closest entry wins; on a tie the order is residente, proveedor, visita.

Statuses: ``residente``, ``visita``, ``proveedor``, ``desconocido`` and ``no leída``.
A visit outside its time window is reported as ``desconocido`` with a note.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from platedemo.formats import CONFUSABLE, normalize

RESIDENT, VISIT, SUPPLIER, UNKNOWN, UNREAD = "residente", "visita", "proveedor", "desconocido", "no leída"
STATUSES = (RESIDENT, VISIT, SUPPLIER, UNKNOWN, UNREAD)
_PRIORITY = {RESIDENT: 0, SUPPLIER: 1, VISIT: 2}


def sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.25 if frozenset((a, b)) in CONFUSABLE else 1.0


def plate_distance(a: str, b: str) -> float:
    """Weighted Levenshtein distance between two normalised plates."""
    a, b = normalize(a), normalize(b)
    prev = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [float(i)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub_cost(ca, cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class Entry:
    plate: str
    category: str
    name: str = ""
    detail: str = ""
    start: time | None = None
    end: time | None = None

    def allowed_at(self, t: time | None) -> bool:
        if t is None or self.start is None or self.end is None:
            return True
        if self.start <= self.end:
            return self.start <= t <= self.end
        return t >= self.start or t <= self.end  # window that crosses midnight


@dataclass
class MatchResult:
    status: str
    plate: str = ""
    entry: Entry | None = None
    distance: float | None = None
    note: str = ""

    @property
    def label(self) -> str:
        return self.entry.name if self.entry else ""


@dataclass
class AccessLists:
    entries: list[Entry] = field(default_factory=list)
    max_distance: float = 1.0

    @classmethod
    def load(cls, folder: str | Path | None, max_distance: float = 1.0) -> AccessLists:
        lists = cls(max_distance=max_distance)
        if folder is None:
            return lists
        folder = Path(folder)
        for fname, category in (("residentes.csv", RESIDENT), ("proveedores.csv", SUPPLIER), ("visitas.csv", VISIT)):
            path = folder / fname
            if not path.exists():
                continue
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    plate = normalize(row.get("patente", ""))
                    if not plate:
                        continue
                    lists.entries.append(Entry(
                        plate, category, (row.get("nombre") or "").strip(), (row.get("detalle") or "").strip(),
                        _parse_time(row.get("desde")), _parse_time(row.get("hasta")),
                    ))
        return lists

    def match(self, plate: str | None, at: time | None = None) -> MatchResult:
        plate = normalize(plate or "")
        if not plate:
            return MatchResult(UNREAD)
        best: tuple[float, int, Entry] | None = None
        for e in self.entries:
            d = plate_distance(plate, e.plate)
            if d <= self.max_distance:
                key = (d, _PRIORITY[e.category], e)
                if best is None or key[:2] < best[:2]:
                    best = key
        if best is None:
            return MatchResult(UNKNOWN, plate)
        d, _, e = best
        if e.category == VISIT and not e.allowed_at(at):
            window = f"{e.start:%H:%M}-{e.end:%H:%M}"
            return MatchResult(UNKNOWN, plate, e, d, f"visita fuera de horario ({window})")
        note = "" if d == 0 else f"coincide con {e.plate} (distancia {d:g})"
        return MatchResult(e.category, plate, e, d, note)


def _parse_time(text: str | None) -> time | None:
    if not text or not text.strip():
        return None
    h, m = text.strip().split(":")[:2]
    return time(int(h), int(m))
