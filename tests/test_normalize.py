from muni.extract.normalize import values_equivalent


def test_percent_formats_match():
    assert values_equivalent("5.000%", "5.00%")
    assert values_equivalent("5.000%", "5%")
    assert not values_equivalent("5.000%", "5.25%")
    assert not values_equivalent("5%", "5")  # percent vs bare number differ


def test_date_formats_match():
    assert values_equivalent("Dec. 1, 2025", "December 1, 2025")
    assert values_equivalent("2025-12-01", "Dec 1 2025")
    assert not values_equivalent("Dec. 1, 2025", "Dec. 1, 2026")
    assert values_equivalent("December 31", "Dec. 31")  # fiscal year end, no year


def test_text_is_whitespace_and_case_insensitive():
    assert values_equivalent("City of  Evanston", "city of evanston")
    assert not values_equivalent("City of Evanston", "City of Chicago")


def test_null_handling():
    assert values_equivalent(None, None)
    assert not values_equivalent(None, "x")
    assert not values_equivalent("x", None)
