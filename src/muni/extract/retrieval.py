"""Page selection for long documents: BM25 per query, combined with RRF.

Pages are the retrieval unit so provenance page numbers survive the whole
pipeline. Embeddings/pgvector can be added later without changing callers.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from ..ingest.pdf import PageText

# Query variants per schema field. Multiple phrasings per field feed RRF.
FIELD_QUERIES: dict[str, list[str]] = {
    "issuer_name": [
        "official statement issuer city county district authority",
        "the bonds are issued by",
    ],
    "issue_purpose": [
        "purpose of the bonds proceeds will be used to finance",
        "plan of finance application of proceeds",
    ],
    "pledge_type": [
        "security for the bonds general obligation full faith and credit taxing power",
        "ad valorem taxes levied against all taxable property without limitation as to rate or amount",
    ],
    "revenue_source": [
        "pledged revenues net revenues of the system payable solely from",
        "source of payment for the bonds",
    ],
    "debt_service_reserve": [
        "debt service reserve fund reserve requirement",
        "reserve account funded in the amount",
    ],
    "key_covenants": [
        "covenants of the issuer rate covenant additional bonds test",
        "the issuer covenants and agrees",
    ],
    "fiscal_year_end": [
        "fiscal year ending annual financial information disclosure",
        "continuing disclosure undertaking annual report fiscal year ends december",
    ],
    "annual_filing_deadline": [
        "continuing disclosure undertaking annual report not later than days after the last day of its fiscal year",
        "annual financial information will be provided within 180 210 270 days after fiscal year end EMMA",
    ],
    "maturity_schedule": [
        "maturity schedule cusip interest rate price yield principal amount",
        "exhibit cusip numbers year of maturity",
    ],
    "tax_status": [
        "tax matters exempt from federal income tax alternative minimum tax",
        "opinion of bond counsel interest on the bonds",
    ],
    "call_features": [
        "optional redemption the bonds are subject to redemption prior to maturity",
        "mandatory sinking fund redemption redemption price",
    ],
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class PageIndex:
    def __init__(self, pages: list[PageText]):
        if not pages:
            raise ValueError("cannot index an empty document")
        self.pages = pages
        self._bm25 = BM25Okapi([_tokens(p.text) or ["_empty_"] for p in pages])

    def _rank(self, query: str) -> list[int]:
        scores = self._bm25.get_scores(_tokens(query))
        return sorted(range(len(self.pages)), key=lambda i: scores[i], reverse=True)

    def rrf(self, queries: list[str], top_k: int = 6, k: int = 60) -> list[PageText]:
        fused: dict[int, float] = {}
        for q in queries:
            for rank, idx in enumerate(self._rank(q)):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
        best = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
        return [self.pages[i] for i in best]


def select_pages(
    pages: list[PageText],
    field_names: list[str],
    per_field_k: int = 4,
    cap: int = 15,
) -> list[PageText]:
    """Union of top RRF pages across fields, in document order, capped."""
    index = PageIndex(pages)
    chosen: dict[int, PageText] = {}
    for name in field_names:
        queries = FIELD_QUERIES.get(name)
        if not queries:
            continue
        for page in index.rrf(queries, top_k=per_field_k):
            chosen.setdefault(page.number, page)
    ordered = sorted(chosen.values(), key=lambda p: p.number)
    return ordered[:cap]


def pages_containing(pages: list[PageText], needle: str) -> list[PageText]:
    n = re.sub(r"\s+", "", needle).lower()
    hits = []
    for p in pages:
        if n and n in re.sub(r"\s+", "", p.text).lower():
            hits.append(p)
    return hits
