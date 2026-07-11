from muni.extract.provenance import (
    SnippetMatch,
    compute_confidence,
    value_in_snippet,
    verify_snippet,
)

PAGE = (
    "SECURITY FOR THE BONDS\n"
    "The Bonds are general obligations of the City of Springfield, and the full faith\n"
    "and credit of the City are pledged for the payment of principal and interest.\n"
    "The Bonds bear interest at the rate of 5.00% per annum."
)


def test_exact_match_ignores_whitespace_and_case():
    m = verify_snippet("the full faith and credit of the city are pledged", PAGE)
    assert m.kind == "exact"


def test_fuzzy_match_tolerates_ocr_noise():
    # OCR-ish corruption of a real sentence from the page
    m = verify_snippet("the fu1l faith and cred1t of the City are p1edged for the payrnent", PAGE)
    assert m.kind == "fuzzy"
    assert m.score >= 85


def test_fabricated_snippet_fails():
    m = verify_snippet("the bonds are secured by water system revenues only", PAGE)
    assert m.kind == "none"


def test_missing_inputs_fail_closed():
    assert verify_snippet(None, PAGE).kind == "none"
    assert verify_snippet("anything", None).kind == "none"


def test_value_in_snippet_string_and_numeric():
    assert value_in_snippet("5.00%", "interest at the rate of 5.00% per annum")
    assert value_in_snippet("City of Springfield", "obligations of the City of Springfield")
    assert value_in_snippet("5.00", "rate of 5.00% per annum")  # numeric fallback
    assert not value_in_snippet("6.25%", "rate of 5.00% per annum")
    assert not value_in_snippet(None, "anything")


def test_confidence_is_mechanical():
    exact = SnippetMatch("exact", 100.0)
    assert compute_confidence(exact, value_ok=True, cross_run_agree=True) == 1.0
    assert compute_confidence(exact, value_ok=True, cross_run_agree=False) == 0.8
    assert compute_confidence(exact, value_ok=False, cross_run_agree=False) == 0.5
    fuzzy = SnippetMatch("fuzzy", 90.0)
    assert compute_confidence(fuzzy, value_ok=True, cross_run_agree=True) == 0.95
    none = SnippetMatch("none", 40.0)
    assert compute_confidence(none, value_ok=True, cross_run_agree=True) == 0.0


def test_self_report_cannot_raise_score():
    none = SnippetMatch("none", 40.0)
    assert compute_confidence(none, True, True, self_report=0.99) == 0.0
