from platedemo.gate import IN, OUT, CrossingDetector, Line, Register


def test_crossing_direction_and_once_per_vehicle():
    line = Line.parse("0,100,200,100")  # horizontal, drawn left to right: moving down = entra
    c = CrossingDetector(line, margin=5)
    assert c.update(1, (50, 80)) is None
    assert c.update(1, (50, 98)) is None  # inside the dead zone
    assert c.update(1, (50, 120)) == IN
    assert c.update(1, (50, 80)) is None  # same vehicle back: not counted twice
    assert c.update(2, (60, 130)) is None
    assert c.update(2, (60, 70)) == OUT


def test_flip_and_segment_limits():
    line = Line.parse("0,100,200,100")
    c = CrossingDetector(line, margin=5, flip=True)
    c.update(1, (50, 80))
    assert c.update(1, (50, 120)) == OUT
    c2 = CrossingDetector(line, margin=5, extend=0)
    c2.update(3, (500, 80))
    assert c2.update(3, (500, 120)) is None  # crossed beside the segment, not through it


def test_flicker_jumps_and_parked_vehicles_do_not_count():
    line = Line.parse("0,100,500,100")
    c = CrossingDetector(line, margin=5, confirm=2, confirm_distance=20)
    # parked truck whose box bottom moves a little across the line (trailer in/out of the box)
    for y in (108, 92, 109, 93, 110, 112):
        assert c.update(1, (50, y), jump_limit=40) is None
    # ID swap: a parked car above the line suddenly "is" the motorcycle passing below it
    assert c.update(3, (300, 60), jump_limit=40) is None
    assert c.update(3, (300, 61), jump_limit=40) is None
    assert c.update(3, (380, 160), jump_limit=40) is None  # jump: side forgotten
    assert c.update(3, (382, 165), jump_limit=40) is None
    assert c.update(3, (300, 60), jump_limit=40) is None  # jump back
    assert c.update(3, (300, 61), jump_limit=40) is None
    # a car that really drives through
    for y in (40, 60, 80, 95):
        assert c.update(2, (60, y), jump_limit=40) is None
    assert c.update(2, (60, 112), jump_limit=40) is None  # past the dead zone, not clearly yet
    assert c.update(2, (60, 125), jump_limit=40) is None  # first clear frame: waits for confirmation
    assert c.update(2, (60, 140), jump_limit=40) == IN


def test_register_still_inside(tmp_path):
    start = tmp_path / "estado.csv"
    start.write_text("patente,estado,hora_ingreso,nombre\nGEE291,proveedor,07:40:00,Fletes\n", encoding="utf-8")
    reg = Register.load(start)
    reg.enter("AC611JD", 5, "residente", "10:15:10", "Lote 14")
    reg.enter(None, 9, "no leída", "10:15:20")
    assert {r.key for r in reg.still_inside()} == {"GEE291", "AC611JD", "#9"}
    # exit read with a look-alike error still closes the right record
    rec = reg.exit("GEE29I", 11)
    assert rec is not None and rec.plate == "GEE291" and rec.previous
    assert reg.exit("ZZZ999", 12) is None
    assert reg.exits_without_entry == ["ZZZ999"]
    assert reg.exit(None, 9).key == "#9"
    assert [r.plate for r in reg.still_inside()] == ["AC611JD"]
    out = tmp_path / "final.csv"
    reg.save(out)
    assert "AC611JD" in out.read_text(encoding="utf-8")
