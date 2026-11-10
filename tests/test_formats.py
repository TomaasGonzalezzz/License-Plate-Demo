from platedemo.formats import AR_MERCOSUR, AR_OLD, PlateRules, normalize, parse_formats


def test_normalize_drops_spaces_and_symbols():
    assert normalize(" ab-123.cd ") == "AB123CD"
    assert normalize("abc_123__") == "ABC123"


def test_argentine_formats():
    assert AR_OLD.matches("ABC123")
    assert AR_MERCOSUR.matches("AB 123 CD")
    assert not AR_OLD.matches("AB123CD")
    assert not AR_MERCOSUR.matches("ABC123")
    assert AR_MERCOSUR.display("AB123CD") == "AB 123 CD"
    assert AR_OLD.display("ABC123") == "ABC 123"


def test_position_aware_correction():
    rules = PlateRules.from_spec("ar")
    # O read where a digit goes, 8 read where a letter goes
    assert rules.validate("AB1O3CD") == ("AB103CD", AR_MERCOSUR)
    assert rules.validate("G8E291")[0] == "GBE291"
    assert rules.validate("GEE29I")[0] == "GEE291"
    # wrong length: nothing is invented
    assert rules.validate("AB12CD") == (None, None)


def test_motorcycle_formats():
    rules = PlateRules.from_spec("ar")
    assert rules.validate("382EMY")[0] == "382EMY"
    assert rules.validate("A035HFA")[0] == "A035HFA"
    assert rules.display("A035HFA") == "A 035 HFA"
    cars = PlateRules.from_spec("ar-autos")
    assert cars.validate("A035HFA")[0] is None


def test_custom_and_any_formats():
    rules = PlateRules(parse_formats("LLL NNNN"))
    assert rules.validate("ABC1234")[0] == "ABC1234"
    assert rules.validate("ABC123")[0] is None
    anything = PlateRules.from_spec("any")
    assert anything.validate("XYZ9")[0] == "XYZ9"
