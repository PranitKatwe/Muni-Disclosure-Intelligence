from muni.ingest.pdf import PageText
from muni.extract.retrieval import FIELD_QUERIES, pages_containing, select_pages

PAGES = [
    PageText(1, "OFFICIAL STATEMENT cover page CUSIP 850123AB1 maturity schedule interest rate"),
    PageText(2, "THE PROJECT construction of a new elementary school"),
    PageText(3, "SECURITY FOR THE BONDS general obligation full faith and credit taxing power"),
    PageText(4, "TAX MATTERS exempt from federal income tax alternative minimum tax"),
    PageText(5, "CONTINUING DISCLOSURE annual report not later than 210 days after fiscal year"),
]


def test_select_pages_finds_relevant_sections():
    selected = select_pages(PAGES, ["pledge_type", "annual_filing_deadline"])
    numbers = {p.number for p in selected}
    assert 3 in numbers
    assert 5 in numbers


def test_all_schema_fields_have_queries():
    for name in ["issuer_name", "pledge_type", "fiscal_year_end", "annual_filing_deadline",
                 "tax_status", "call_features", "maturity_schedule"]:
        assert FIELD_QUERIES[name]


def test_pages_containing_cusip_ignores_whitespace():
    assert [p.number for p in pages_containing(PAGES, "850123 AB1")] == [1]
