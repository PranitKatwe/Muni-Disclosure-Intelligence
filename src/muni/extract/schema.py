"""Target schema. Every field is an ExtractedField carrying value AND provenance.

Two-level shape: an Official Statement describes an *issue* (many maturities,
each with its own CUSIP/coupon/maturity date), so issue-scoped facts live on
IssueProfile and per-CUSIP facts on MaturityProfile. See DESIGN.md section 4.1.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class Provenance(BaseModel):
    doc_id: str
    page: int
    snippet: str  # the exact source text the value came from


class ExtractedField(BaseModel):
    value: str | float | None = None
    provenance: Provenance | None = None  # None ONLY when value is None ("not disclosed")
    confidence: float = 0.0  # computed mechanically (provenance.py), never the model's self-report

    @model_validator(mode="after")
    def _no_bare_values(self) -> "ExtractedField":
        if self.value is not None and self.provenance is None:
            raise ValueError(
                "non-null value without provenance; fail closed to value=None ('not disclosed')"
            )
        return self

    @classmethod
    def not_disclosed(cls) -> "ExtractedField":
        return cls(value=None, provenance=None, confidence=0.0)


class MaturityProfile(BaseModel):
    """One per CUSIP within the issue."""

    cusip: ExtractedField
    coupon: ExtractedField
    maturity_date: ExtractedField
    call_features: ExtractedField
    tax_status: ExtractedField  # tax-exempt / AMT / taxable — can vary by maturity


class IssueProfile(BaseModel):
    """Shared across the series; from the OS narrative."""

    issuer_name: ExtractedField
    issue_purpose: ExtractedField
    pledge_type: ExtractedField  # "GO (full faith & credit)" | "revenue" | ...
    revenue_source: ExtractedField  # for revenue bonds: what actually pays
    debt_service_reserve: ExtractedField
    key_covenants: list[ExtractedField] = []
    financial_highlights: list[ExtractedField] = []  # populated in M3
    fiscal_year_end: ExtractedField  # from the continuing-disclosure agreement
    annual_filing_deadline: ExtractedField  # baseline for missing-filing detection


class BondProfile(BaseModel):
    """What the user sees for THEIR holding."""

    issue: IssueProfile
    holding: MaturityProfile | None = None  # the maturity row matching the user's CUSIP
