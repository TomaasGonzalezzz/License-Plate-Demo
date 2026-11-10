from datetime import time

from platedemo.matching import (RESIDENT, SUPPLIER, UNKNOWN, UNREAD, VISIT, AccessLists, Entry,
                                plate_distance)


def lists():
    return AccessLists([
        Entry("AC611JD", RESIDENT, "Lote 14"),
        Entry("GEE291", SUPPLIER, "Fletes"),
        Entry("HME272", VISIT, "Visita lote 8", start=time(9, 0), end=time(12, 0)),
    ])


def test_distance_is_cheap_for_lookalikes():
    assert plate_distance("AC611JD", "AC611JD") == 0
    assert plate_distance("AC611J0", "AC611JD") == 0.25  # 0 vs D
    assert plate_distance("ACG11JD", "AC611JD") == 0.25  # G vs 6
    assert plate_distance("AC611JX", "AC611JD") == 1.0  # one real difference
    assert plate_distance("AC611J", "AC611JD") == 1.0  # one missing character


def test_exact_and_tolerant_matches():
    m = lists()
    assert m.match("AC611JD").status == RESIDENT
    r = m.match("AC6I1J0")  # I for 1 and 0 for D
    assert r.status == RESIDENT and r.entry.name == "Lote 14" and r.distance == 0.5
    assert m.match("GEE29T").status == SUPPLIER  # T vs 1


def test_unknown_unread_and_visit_window():
    m = lists()
    assert m.match("ZZZ999").status == UNKNOWN
    assert m.match("").status == UNREAD
    assert m.match(None).status == UNREAD
    assert m.match("HME272", time(10, 30)).status == VISIT
    late = m.match("HME272", time(18, 0))
    assert late.status == UNKNOWN and "fuera de horario" in late.note


def test_two_differences_is_not_a_match():
    assert lists().match("AC611XX").status == UNKNOWN
