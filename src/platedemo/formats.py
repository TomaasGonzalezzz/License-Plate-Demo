"""License plate formats: validation, display and position-aware OCR correction.

A format is a pattern of ``L`` (letter) and ``N`` (digit), with spaces only for
display. The two Argentine formats are:

* old (1995-2016): ``LLL NNN``, for example ``ABC 123``;
* Mercosur (2016 onwards): ``LL NNN LL``, for example ``AB 123 CD``.

Motorcycles have their own: ``NNN LLL`` (old) and ``L NNN LLL`` (Mercosur).
The ``ar`` preset accepts all four; ``ar-autos`` only the two car formats.

Because every position is known to be a letter or a digit, typical OCR
confusions can be undone safely: an ``O`` read where a digit must go becomes
``0``, a ``1`` read where a letter must go becomes ``I``, and so on. This is
applied only when the length already matches a format; nothing is invented.

Other countries are supported by passing a different pattern, for example
``--format "LLL NNNN"`` or several separated by ``|``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Characters that OCR commonly swaps between letters and digits.
TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "J": "1", "T": "7",
            "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4"}
TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A", "3": "B"}

# Pairs that look alike; used by the tolerant list matching (see matching.py).
CONFUSABLE = {frozenset(p) for p in [
    ("O", "0"), ("Q", "0"), ("D", "0"), ("O", "D"), ("O", "Q"), ("I", "1"), ("L", "1"), ("I", "L"),
    ("J", "1"), ("T", "1"), ("T", "7"), ("Z", "2"), ("S", "5"), ("B", "8"), ("G", "6"), ("A", "4"),
    ("B", "3"), ("E", "F"), ("P", "R"), ("M", "N"), ("H", "N"), ("V", "Y"), ("U", "V"), ("C", "G"),
    ("K", "X"), ("8", "3"), ("6", "5"),
]}


def normalize(text: str) -> str:
    """Uppercase and keep only A-Z and 0-9 (drops spaces, dashes, dots and OCR padding)."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


@dataclass(frozen=True)
class PlateFormat:
    name: str
    pattern: str  # e.g. "LLL NNN"

    @property
    def slots(self) -> str:
        return self.pattern.replace(" ", "")

    @property
    def length(self) -> int:
        return len(self.slots)

    def matches(self, text: str) -> bool:
        t = normalize(text)
        if len(t) != self.length:
            return False
        return all((c.isalpha() if s == "L" else c.isdigit()) for c, s in zip(t, self.slots))

    def coerce(self, text: str, max_fixes: int = 2) -> str | None:
        """Fix letter/digit confusions position by position.

        Returns None if the length does not fit, or if more than ``max_fixes``
        characters would have to change: at that point the reading is more likely
        a different format (or garbage) than a near miss of this one.
        """
        t = normalize(text)
        if len(t) != self.length:
            return None
        out, fixes = [], 0
        for c, s in zip(t, self.slots):
            if s == "N" and not c.isdigit():
                c, fixes = TO_DIGIT.get(c, c), fixes + 1
            elif s == "L" and not c.isalpha():
                c, fixes = TO_LETTER.get(c, c), fixes + 1
            out.append(c)
        fixed = "".join(out)
        return fixed if fixes <= max_fixes and self.matches(fixed) else None

    def display(self, text: str) -> str:
        """Insert the pattern's spaces: ``AB123CD`` -> ``AB 123 CD``."""
        t = normalize(text)
        if len(t) != self.length:
            return t
        out, i = [], 0
        for s in self.pattern:
            if s == " ":
                out.append(" ")
            else:
                out.append(t[i])
                i += 1
        return "".join(out)


AR_OLD = PlateFormat("ar-viejo", "LLL NNN")
AR_MERCOSUR = PlateFormat("ar-mercosur", "LL NNN LL")
AR_MOTO_OLD = PlateFormat("ar-moto-viejo", "NNN LLL")
AR_MOTO_MERCOSUR = PlateFormat("ar-moto-mercosur", "L NNN LLL")

PRESETS: dict[str, list[PlateFormat]] = {
    # car formats first: when a garbled reading could be coerced into both, the car format wins
    "ar": [AR_MERCOSUR, AR_OLD, AR_MOTO_MERCOSUR, AR_MOTO_OLD],
    "ar-autos": [AR_MERCOSUR, AR_OLD],
    "ar-viejo": [AR_OLD],
    "ar-mercosur": [AR_MERCOSUR],
    "any": [],  # accept any 4-8 character reading, no position correction
}


def parse_formats(spec: str) -> list[PlateFormat]:
    """``"ar"`` (preset) or patterns separated by ``|``, e.g. ``"LLL NNNN|NNNN LLL"``."""
    spec = spec.strip()
    if spec in PRESETS:
        return list(PRESETS[spec])
    formats = []
    for i, p in enumerate(spec.split("|")):
        p = p.strip().upper()
        if not p or set(p.replace(" ", "")) - {"L", "N"}:
            raise ValueError(f"format must use only L, N and spaces, got {p!r}")
        formats.append(PlateFormat(f"custom-{i + 1}", p))
    return formats


class PlateRules:
    """The set of accepted formats for one deployment."""

    def __init__(self, formats: list[PlateFormat], min_len: int = 4, max_len: int = 8):
        self.formats = formats
        self.min_len, self.max_len = min_len, max_len

    @classmethod
    def from_spec(cls, spec: str) -> PlateRules:
        return cls(parse_formats(spec))

    def validate(self, text: str) -> tuple[str | None, PlateFormat | None]:
        """Return ``(fixed_text, format)`` for the first format the text fits, or ``(None, None)``."""
        t = normalize(text)
        if not self.formats:
            return (t, None) if self.min_len <= len(t) <= self.max_len else (None, None)
        for f in self.formats:
            if f.matches(t):
                return t, f
        for f in self.formats:
            fixed = f.coerce(t)
            if fixed:
                return fixed, f
        return None, None

    def display(self, text: str) -> str:
        t = normalize(text)
        for f in self.formats:
            if f.matches(t):
                return f.display(t)
        return t

    def lengths(self) -> set[int]:
        return {f.length for f in self.formats} if self.formats else set(range(self.min_len, self.max_len + 1))
