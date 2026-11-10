import numpy as np

from platedemo.formats import PlateRules
from platedemo.voting import PlateVoter


def voter(**kw):
    return PlateVoter(PlateRules.from_spec("ar"), **kw)


def test_character_vote_beats_single_bad_frames():
    v = voter()
    v.add("AC611JD", [0.9] * 7, 140, 1)
    v.add("AC6I1JD", [0.9, 0.9, 0.9, 0.4, 0.9, 0.9, 0.9], 120, 2)  # I fixed to 1 by position
    v.add("AE611JO", [0.9, 0.5, 0.9, 0.9, 0.9, 0.9, 0.4], 80, 3)
    v.add("AC611JD", [0.8] * 7, 100, 4)
    r = v.accepted()
    assert r is not None and r.text == "AC611JD" and r.valid and r.readings == 4


def test_bigger_plates_weigh_more():
    v = voter()
    v.add("GEE291", [0.7] * 6, 150, 1)
    v.add("GFE291", [0.7] * 6, 40, 2)
    v.add("GFE291", [0.7] * 6, 40, 3)
    assert v.result().text == "GEE291"


def test_needs_enough_readings_and_a_valid_format():
    v = voter(min_readings=2)
    v.add("GEE291", [0.95] * 6, 150, 1)
    assert v.accepted() is None  # a single frame is not enough
    w = voter()
    for i in range(3):
        w.add("12345", np.full(5, 0.9), 150, i)  # no Argentine format has 5 characters
    assert w.accepted() is None


def test_majority_length_wins():
    v = voter()
    v.add("AC611JD", [0.8] * 7, 120, 1)
    v.add("AC611J", [0.8] * 6, 120, 2)
    v.add("AC611JD", [0.8] * 7, 120, 3)
    assert v.result().text == "AC611JD"
