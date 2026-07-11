"""Grounding tests: the pipeline must fail closed when the LLM fabricates.

Uses a fake extractor so no API key or network is needed.
"""

from muni.extract.go_bond import extract_go_profile
from muni.extract.llm import IssueRaw, MaturityRaw, RawField
from muni.ingest.pdf import PageText

PAGES = [
    PageText(1, "OFFICIAL STATEMENT\nCity of Springfield, Illinois\nGeneral Obligation Bonds, Series 2024\nMaturity Schedule: CUSIP 850123AB1  5.00%  June 1, 2034"),
    PageText(2, "SECURITY FOR THE BONDS\nThe Bonds are general obligations of the City and the full faith and credit of the City are pledged."),
    PageText(3, "CONTINUING DISCLOSURE\nThe City will provide its annual report not later than 210 days after the end of each fiscal year ending December 31."),
]


def _nd() -> RawField:
    return RawField()


class FakeExtractor:
    """Returns preset raw extractions regardless of the prompt."""

    model = "fake"

    def __init__(self, issue: IssueRaw, maturity: MaturityRaw | None = None):
        self._issue = issue
        self._maturity = maturity

    def extract(self, output_model, system, user_prompt):
        if output_model is IssueRaw:
            return self._issue
        return self._maturity


def make_issue(**overrides) -> IssueRaw:
    base = dict(
        issuer_name=_nd(), issue_purpose=_nd(), pledge_type=_nd(), revenue_source=_nd(),
        debt_service_reserve=_nd(), fiscal_year_end=_nd(), annual_filing_deadline=_nd(),
        key_covenants=[],
    )
    base.update(overrides)
    return IssueRaw(**base)


def test_verified_field_is_kept_with_provenance():
    issue = make_issue(
        issuer_name=RawField(
            value="City of Springfield, Illinois",
            page=1,
            snippet="City of Springfield, Illinois",
        )
    )
    profile = extract_go_profile(PAGES, "doc1", FakeExtractor(issue), double_run=False)
    field = profile.issue.issuer_name
    assert field.value == "City of Springfield, Illinois"
    assert field.provenance.page == 1
    assert field.confidence > 0


def test_fabricated_snippet_fails_closed():
    issue = make_issue(
        pledge_type=RawField(
            value="revenue bond backed by water fees",
            page=2,
            snippet="payable solely from net revenues of the water system",  # not on page 2
        )
    )
    profile = extract_go_profile(PAGES, "doc1", FakeExtractor(issue), double_run=False)
    assert profile.issue.pledge_type.value is None  # "not disclosed", never a guess
    assert profile.issue.pledge_type.provenance is None


def test_diagnostics_explain_rejections():
    issue = make_issue(
        pledge_type=RawField(
            value="revenue bond backed by water fees",
            page=2,
            snippet="payable solely from net revenues of the water system",
        )
    )
    diagnostics: list[str] = []
    extract_go_profile(PAGES, "doc1", FakeExtractor(issue), double_run=False,
                       diagnostics=diagnostics)
    assert any("pledge_type" in line and "snippet not found" in line for line in diagnostics)
    assert any("issuer_name" in line and "null" in line for line in diagnostics)


def test_wrong_page_citation_fails_closed():
    issue = make_issue(
        annual_filing_deadline=RawField(
            value="210 days",
            page=1,  # snippet actually lives on page 3
            snippet="not later than 210 days after the end of each fiscal year",
        )
    )
    profile = extract_go_profile(PAGES, "doc1", FakeExtractor(issue), double_run=False)
    assert profile.issue.annual_filing_deadline.value is None


def test_missing_fields_are_not_disclosed():
    profile = extract_go_profile(PAGES, "doc1", FakeExtractor(make_issue()), double_run=False)
    assert profile.issue.revenue_source.value is None
    assert profile.holding is None  # no CUSIP supplied


def test_maturity_row_extracted_for_cusip():
    maturity = MaturityRaw(
        cusip=RawField(value="850123AB1", page=1, snippet="CUSIP 850123AB1"),
        coupon=RawField(value="5.00%", page=1, snippet="850123AB1  5.00%  June 1, 2034"),
        maturity_date=RawField(value="June 1, 2034", page=1, snippet="850123AB1  5.00%  June 1, 2034"),
        call_features=_nd(),
        tax_status=_nd(),
    )
    profile = extract_go_profile(
        PAGES, "doc1", FakeExtractor(make_issue(), maturity), cusip="850123AB1", double_run=False
    )
    assert profile.holding is not None
    assert profile.holding.coupon.value == "5.00%"
    assert profile.holding.call_features.value is None
