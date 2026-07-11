# Muni Disclosure Intelligence

Reads the disclosure PDFs for the municipal bonds you actually own, extracts them into a structured, plain-language bond profile **with field-level provenance**, and (later milestones) watches for new, changed, or missing filings.

**Two rules the whole system is built on:**

1. **Provenance or it didn't happen.** Every extracted value carries `{doc_id, page, snippet}` pointing at the exact source text, and the snippet is mechanically verified against the cited page. If the verification fails, the field becomes `"not disclosed"`, never a guess.
2. **Informs, never advises.** The tool describes what documents say. It never recommends buy/sell/hold.

See [DESIGN.md](DESIGN.md) for the full design.

## Status

| Milestone | State |
|---|---|
| M0 — ingestion (upload PDFs, SHA-256 dedup, page-anchored text) | Done |
| M1 — GO extraction engine (retrieval + constrained LLM + fail-closed verification) | Core built; gold-set evals pending |
| M2 — plain-language cards + grounded Q&A | Not started |
| M3 — financial trend extraction | Not started |
| M4 — monitoring (MyEMMA alert ingestion, missing-filing detection) | Not started |

## Quickstart

```powershell
cd muni-intel
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest            # runs without an API key

copy .env.example .env           # then set ANTHROPIC_API_KEY for extraction

.venv\Scripts\muni ingest path\to\official-statement.pdf
.venv\Scripts\muni docs
.venv\Scripts\muni extract 1 --cusip 850123AB1
```

`muni extract` prints a `BondProfile` as JSON: issue-level facts (issuer, purpose, pledge type, reserve fund, covenants, continuing-disclosure deadline) plus, when a CUSIP is given, that holding's maturity row (coupon, maturity date, call features, tax status). Every non-null value includes its page number and verbatim source snippet.

## How confidence works (computed, not self-reported)

The LLM proposes `value + page + snippet` candidates. Code then:

1. Verifies the snippet appears on the cited page (exact match, or fuzzy for OCR'd pages). No match: the field is discarded ("not disclosed").
2. Checks the value actually appears in the snippet.
3. Optionally runs a second, differently-prompted extraction pass and checks agreement.

`confidence = 0.5·(snippet match) + 0.3·(value in snippet) + 0.2·(cross-run agreement)`. The model's self-reported confidence is logged but can never raise the score.

## Data access

Holdings-scoped by design: input is user-uploaded PDFs (and later, MyEMMA alert emails). No crawling or bulk fetching of the EMMA portal, ever.

## Disclaimer

This tool summarizes disclosure documents; it is not investment advice, a credit rating, or a guarantee of completeness. No single source has every filing.
