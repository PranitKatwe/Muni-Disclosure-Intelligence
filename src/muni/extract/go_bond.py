"""GO-bond extraction pipeline: retrieve pages -> constrained LLM extraction ->
mechanical verification -> fail-closed BondProfile.

Every value that survives carries {doc_id, page, snippet} provenance. A value
whose snippet cannot be found on the cited page is discarded ("not disclosed"),
never kept as a guess.
"""

from __future__ import annotations

from ..ingest.pdf import PageText
from .llm import ClaudeExtractor, IssueRaw, MaturityRaw, RawField
from .provenance import compute_confidence, normalize, value_in_snippet, verify_snippet
from .retrieval import pages_containing, select_pages
from .schema import BondProfile, ExtractedField, IssueProfile, MaturityProfile, Provenance

ISSUE_FIELDS = [
    "issuer_name",
    "issue_purpose",
    "pledge_type",
    "revenue_source",
    "debt_service_reserve",
    "fiscal_year_end",
    "annual_filing_deadline",
    "key_covenants",
]
MATURITY_FIELDS = ["maturity_schedule", "tax_status", "call_features"]

_RULES = """Rules — these are non-negotiable:
1. Use ONLY the document pages provided below. No outside knowledge, no inference beyond the text.
2. For every extracted value, `snippet` must be a VERBATIM quote (max 300 characters) copied
   exactly from the page it came from, and `page` must be the number in that page's [PAGE N] header.
3. `value` should be copied in the form it appears in the document (do not reformat dates or numbers).
4. If a fact is not stated on the provided pages, return null for value, page, and snippet.
   Returning null is correct behavior, never a failure. NEVER guess or fabricate.
5. `self_confidence` is your own 0-1 estimate; it is audited against the document afterwards."""

SYSTEM_PRIMARY = (
    "You are a precise extraction engine for U.S. municipal bond disclosure documents "
    "(Official Statements and continuing disclosures). You extract facts with exact "
    "provenance for a tool that informs retail investors; a fabricated value costs "
    "someone real money.\n\n" + _RULES
)

# Deliberately different phrasing/persona so the second pass is a semi-independent
# check for cross-run agreement, not a cache hit of the first.
SYSTEM_SECONDARY = (
    "You are an auditor re-extracting facts from a U.S. municipal bond disclosure "
    "document, independently of a previous pass you have not seen. Be conservative: "
    "when a fact is ambiguous or only implied, return null rather than a value.\n\n" + _RULES
)


def _context(pages: list[PageText], per_page_chars: int = 8000) -> str:
    return "\n\n".join(f"[PAGE {p.number}]\n{p.text[:per_page_chars]}" for p in pages)


def _values_agree(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and normalize(a) == normalize(b)


def _reconcile(
    raw: RawField | None,
    other: RawField | None,
    pages_by_number: dict[int, str],
    doc_id: str,
    name: str = "",
    diagnostics: list[str] | None = None,
) -> ExtractedField:
    """Turn one untrusted LLM candidate into a verified ExtractedField, or fail closed."""

    def note(msg: str) -> None:
        if diagnostics is not None:
            diagnostics.append(f"{name}: {msg}")

    if raw is None or raw.value is None:
        note("model returned null (not found on the provided pages)")
        return ExtractedField.not_disclosed()
    if raw.snippet is None or raw.page is None:
        note(f"value {raw.value!r} had no snippet/page citation; discarded")
        return ExtractedField.not_disclosed()

    page_text = pages_by_number.get(raw.page)
    if page_text is None:
        note(f"cited page {raw.page} does not exist in the document; discarded")
        return ExtractedField.not_disclosed()
    match = verify_snippet(raw.snippet, page_text)
    if match.kind == "none":
        note(
            f"snippet not found on cited page {raw.page} "
            f"(match score {match.score:.0f}); discarded value {raw.value!r}"
        )
        return ExtractedField.not_disclosed()  # snippet not on cited page -> discard

    confidence = compute_confidence(
        match,
        value_ok=value_in_snippet(raw.value, raw.snippet),
        cross_run_agree=_values_agree(raw.value, other.value if other else None),
        self_report=raw.self_confidence,
    )
    return ExtractedField(
        value=raw.value,
        provenance=Provenance(doc_id=doc_id, page=raw.page, snippet=raw.snippet),
        confidence=confidence,
    )


def _reconcile_list(
    raws: list[RawField],
    others: list[RawField],
    pages_by_number: dict[int, str],
    doc_id: str,
    name: str = "",
    diagnostics: list[str] | None = None,
) -> list[ExtractedField]:
    out = []
    for raw in raws:
        # agreement = any second-run item with the same normalized value
        twin = next((o for o in others if _values_agree(raw.value, o.value)), None)
        field = _reconcile(raw, twin, pages_by_number, doc_id, name, diagnostics)
        if field.value is not None:
            out.append(field)
    return out


def extract_go_profile(
    pages: list[PageText],
    doc_id: str,
    extractor: ClaudeExtractor,
    cusip: str | None = None,
    double_run: bool = True,
    diagnostics: list[str] | None = None,
) -> BondProfile:
    pages_by_number = {p.number: p.text for p in pages}

    # --- issue-level fields ---
    issue_pages = select_pages(pages, ISSUE_FIELDS, cap=20)
    if diagnostics is not None:
        diagnostics.append(f"issue-level context pages: {[p.number for p in issue_pages]}")
    issue_prompt = (
        "Extract the issue-level fields for this municipal bond issue.\n\n"
        + _context(issue_pages)
    )
    run_a: IssueRaw = extractor.extract(IssueRaw, SYSTEM_PRIMARY, issue_prompt)
    run_b: IssueRaw | None = (
        extractor.extract(IssueRaw, SYSTEM_SECONDARY, issue_prompt) if double_run else None
    )

    def issue_field(name: str) -> ExtractedField:
        return _reconcile(
            getattr(run_a, name),
            getattr(run_b, name) if run_b else None,
            pages_by_number,
            doc_id,
            name,
            diagnostics,
        )

    issue = IssueProfile(
        issuer_name=issue_field("issuer_name"),
        issue_purpose=issue_field("issue_purpose"),
        pledge_type=issue_field("pledge_type"),
        revenue_source=issue_field("revenue_source"),
        debt_service_reserve=issue_field("debt_service_reserve"),
        fiscal_year_end=issue_field("fiscal_year_end"),
        annual_filing_deadline=issue_field("annual_filing_deadline"),
        key_covenants=_reconcile_list(
            run_a.key_covenants,
            run_b.key_covenants if run_b else [],
            pages_by_number,
            doc_id,
            "key_covenants",
            diagnostics,
        ),
    )

    # --- the user's maturity row (only if a CUSIP was supplied) ---
    holding: MaturityProfile | None = None
    if cusip:
        candidates = {p.number: p for p in pages_containing(pages, cusip)}
        if len(cusip) == 9:
            # full CUSIP = 6-char issuer prefix + 3-char suffix; tables often print them
            # apart ("(299228)" header, then "KL9" per row), so also match the suffix
            for p in pages_containing(pages, cusip[6:]):
                candidates.setdefault(p.number, p)
        for p in select_pages(pages, MATURITY_FIELDS, cap=8):
            candidates.setdefault(p.number, p)
        maturity_pages = sorted(candidates.values(), key=lambda p: p.number)[:12]
        if diagnostics is not None:
            diagnostics.append(f"maturity context pages: {[p.number for p in maturity_pages]}")
        maturity_prompt = (
            f"Extract the fields for the single maturity with CUSIP {cusip}. The per-maturity "
            "facts (coupon, maturity date) usually appear as one row of the maturity schedule "
            "table; call features and tax status may be stated for the issue as a whole.\n"
            "Table layout warning: PDF text extraction often garbles multi-column tables, so a "
            "row's year may not sit adjacent to its CUSIP in the text. Cross-check with any "
            "exhibit that maps years of maturity to CUSIP numbers before deciding. The maturity "
            "date usually combines a fixed day/month from the table heading (e.g. 'Due Dec. 1') "
            "with the row's year.\n\n"
            + _context(maturity_pages)
        )
        m_a: MaturityRaw = extractor.extract(MaturityRaw, SYSTEM_PRIMARY, maturity_prompt)
        m_b: MaturityRaw | None = (
            extractor.extract(MaturityRaw, SYSTEM_SECONDARY, maturity_prompt)
            if double_run
            else None
        )

        def m_field(name: str) -> ExtractedField:
            return _reconcile(
                getattr(m_a, name),
                getattr(m_b, name) if m_b else None,
                pages_by_number,
                doc_id,
                f"holding.{name}",
                diagnostics,
            )

        holding = MaturityProfile(
            cusip=m_field("cusip"),
            coupon=m_field("coupon"),
            maturity_date=m_field("maturity_date"),
            call_features=m_field("call_features"),
            tax_status=m_field("tax_status"),
        )

    return BondProfile(issue=issue, holding=holding)
