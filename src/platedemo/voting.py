"""Combine many readings of the same plate into one, character by character.

A car stays in view for one or two seconds, so its plate is read in many frames:
some sharp, some blurred or partly covered. Each reading has a per-character
confidence from the OCR. The voter:

1. normalises each reading and corrects letter/digit confusions using the
   accepted formats (a reading that fits no format still counts, with less weight);
2. groups readings by length and keeps the group with the most total weight;
3. for every position, adds up the weight of each candidate character and keeps
   the heaviest one.

A reading's weight is its mean character confidence times a size factor, so a
plate seen 140 px wide counts more than the same plate seen 60 px wide. Only the
``top_k`` heaviest readings vote: a vehicle is usually first seen far away and
then up close, and the close readings should decide.

The final confidence combines how much the readings agree (share of the vote of
each winning character) with how sure the OCR was, and how many readings there
were. It is a score for ranking and thresholds, not a calibrated probability.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from platedemo.formats import PlateRules, normalize


@dataclass
class Reading:
    text: str
    char_probs: np.ndarray
    plate_width: float  # pixels in the source frame
    frame: int


@dataclass
class VoteResult:
    text: str
    confidence: float
    readings: int
    agree: int  # readings whose fixed text equals the result
    valid: bool


@dataclass
class PlateVoter:
    rules: PlateRules
    min_readings: int = 2
    min_confidence: float = 0.35
    ref_width: float = 120.0  # plate width (px) that earns full size weight
    top_k: int = 12  # vote with the best readings only (biggest and most confident)
    readings: list[Reading] = field(default_factory=list)

    def add(self, text: str, char_probs, plate_width: float, frame: int) -> None:
        t = normalize(text)
        if not t:
            return
        probs = np.asarray(char_probs, dtype=float).ravel()
        self.readings.append(Reading(t, probs, float(plate_width), frame))

    def _prepared(self) -> list[tuple[str, np.ndarray, float, bool]]:
        out = []
        for r in self.readings:
            fixed, fmt = self.rules.validate(r.text)
            valid = fixed is not None
            text = fixed if valid else r.text
            probs = r.char_probs[: len(text)]
            if len(probs) < len(text):
                probs = np.pad(probs, (0, len(text) - len(probs)), constant_values=float(probs.mean()) if len(probs) else 0.5)
            size = min(1.0, r.plate_width / self.ref_width) ** 1.5
            # squared confidence: one sharp reading at 0.9 outweighs two blurry ones at 0.6
            weight = float(probs.mean()) ** 2 * (0.3 + 0.7 * size) * (1.0 if valid else 0.35)
            out.append((text, probs, weight, valid))
        # a vehicle seen far away first and close later: the close, sharp readings decide
        out.sort(key=lambda item: item[2], reverse=True)
        return out[: self.top_k]

    def result(self) -> VoteResult | None:
        prepared = self._prepared()
        if not prepared:
            return None
        by_len: dict[int, list] = defaultdict(list)
        for item in prepared:
            by_len[len(item[0])].append(item)
        group = max(by_len.values(), key=lambda g: sum(w for _, _, w, _ in g))
        length = len(group[0][0])
        chars, shares, probs = [], [], []
        for i in range(length):
            votes: dict[str, float] = defaultdict(float)
            pvals: dict[str, list[float]] = defaultdict(list)
            for text, p, w, _ in group:
                votes[text[i]] += w * float(p[i])
                pvals[text[i]].append(float(p[i]))
            c = max(votes, key=votes.get)
            chars.append(c)
            total = sum(votes.values())
            shares.append(votes[c] / total if total else 0.0)
            probs.append(float(np.mean(pvals[c])))
        text = "".join(chars)
        fixed, fmt = self.rules.validate(text)
        valid = fixed is not None
        if valid:
            text = fixed
        n = len(group)
        support = min(1.0, n / 4)  # four or more agreeing frames give full support
        confidence = float(np.mean(shares)) * float(np.mean(probs)) * (0.6 + 0.4 * support) * (1.0 if valid else 0.5)
        agree = sum(1 for t, _, _, _ in group if t == text)
        return VoteResult(text, round(confidence, 3), n, agree, valid)

    def accepted(self) -> VoteResult | None:
        """The vote, only if it is good enough to report as a reading."""
        r = self.result()
        if r is None or r.readings < self.min_readings or r.confidence < self.min_confidence or not r.valid:
            return None
        return r
