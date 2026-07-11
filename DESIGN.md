# DESIGN.md — Muni Disclosure Intelligence

A tool that makes a retail investor's (or advisor's) municipal-bond holdings **legible and monitored**: it reads the disclosure PDFs for the bonds you actually own, extracts them into a structured, plain-language profile **with provenance**, and watches for new/changed/missing filings — flagging what's material and explaining why, without ever giving buy/sell advice.

> **Read this first — two load-bearing principles.**
> 1. **This is a document-extraction project, not a modeling project.** The crown jewel is the extraction engine that turns a heterogeneous 200-page Official Statement into a structured, *grounded* bond profile. Build and validate that against a hand-labeled gold set before anything else.
> 2. **Holdings-scoped, not market-scoped — by design.** There is no free programmatic bulk feed from EMMA, and large-scale scraping risks its Terms of Use. The user supplies their own small watchlist (CUSIPs and/or uploaded PDFs). Per-security, low-volume, human-scale access to documents for one's own holdings is the clean path. **Never** build bulk ingestion of the EMMA portal. Monitoring rides EMMA's official free alert mechanism (MyEMMA), not a scraper.

---

## 0. The data-access reality (memorize this; it shapes the whole architecture)

| Path | Cost | Use it for |
|---|---|---|
| EMMA per-security pages — downloadable PDFs (OS, continuing disclosures, event notices) | Free, human-facing | Fetching docs for a **small** user watchlist (polite, low-volume) |
| User-uploaded PDFs | Free | Primary, 100%-clean ingestion path — the user owns these docs |
| **MyEMMA email alerts** (per-CUSIP, posting notifications) | Free | The **monitoring** half — ingest the alert emails, add the AI layer |
| MSRB Continuing Disclosure / Primary Market Subscription (real-time XML feed) | **Paid** | Out of scope — note it exists, don't depend on it |
| ICE Consolidated Feed | **Paid/enterprise** | Out of scope |
| MSRB Data Sets for Academics | Free/reduced, **university-affiliated only** | Not available to us |

**Design consequence:** the system's input is always *a specific user's bonds*, supplied as uploads or a CUSIP watchlist. It is never a crawler.

---

## 1. What the product does (scope = combine "extract" + "monitor")

1. **Extract & structure** — OS / disclosure PDF → a structured bond profile with field-level provenance (issuer, purpose-of-issue, security/pledge type [GO vs. revenue], coupon, maturity, CUSIP, call/redemption features, tax status, debt-service-reserve, key covenants, financial highlights).
2. **Translate** — render that profile in plain, retail-readable language ("this is a *revenue* bond backed only by water-system fees, not the city's full taxing power — generally riskier than a GO bond").
3. **Track finances** — pull issuer financials from continuing-disclosure ACFRs across years → a simple multi-year trend (fund balance, debt-service coverage).
4. **Monitor** — on a new/changed/**missing** filing for a watchlist CUSIP, classify the event (the ~15 Rule 15c2-12 categories — rating change, default, reserve draw, etc.), summarize it, and flag materiality.

> **Guardrail (non-negotiable, like the MBS project's "engine does the math"):** the system **informs, it does not advise.** It describes what a document says and how figures trended; it never recommends buy/sell/hold and never asserts a number that isn't traceable to a source document. Every extracted fact carries a provenance pointer (document + page/section). If a value isn't in the docs, the answer is "not disclosed," never a guess.

---

## 2. Tech stack

- **Python** 3.11+
- **PDF/extraction:** `pymupdf` (text + layout), `pdfplumber` (tables), an OCR fallback (`ocrmypdf`/Tesseract) for scanned older OS
- **LLM extraction:** Anthropic SDK (`claude-opus-4-8` for hard extraction, `claude-sonnet-5` for translation/summarize); structured output validated with **pydantic**
- **Retrieval (for Q&A + long-doc extraction):** chunk + embed; hybrid search with **RRF** (reuse the DocuSense pattern); `pgvector` on Postgres (already in the stack — one store, one less moving part)
- **Store:** Postgres (SQLAlchemy 2.x + psycopg); `pgvector` extension for embeddings
- **Dedup:** SHA-256 content hashing of documents/sections (reuse the 68-vendor-parser pattern) so re-fetched/re-posted filings aren't reprocessed
- **API/UI:** FastAPI; a minimal static UI. (GraphQL optional — see §6; not necessary here.)
- **Monitoring:** an email-ingestion worker for MyEMMA alerts (IMAP poll of a dedicated inbox) **or** a polite scheduled re-check of watchlist security pages
- **pytest** for the eval/gold-set harness
- **Docker** + free deploy (HF Spaces + Neon) — see §8

---

## 3. Repository structure

```
muni-intel/
├── README.md                  # the resume artifact: schema, extraction approach, eval results, the advice-avoidance stance
├── DESIGN.md                  # this file
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── src/muni/
│   ├── config.py
│   ├── ingest/
│   │   ├── uploads.py         # user PDF intake; SHA-256 dedup
│   │   ├── fetch.py           # per-CUSIP document fetch (low-volume, watchlist only)
│   │   └── pdf.py             # text/table extraction + OCR fallback; page-anchored chunks
│   ├── extract/               # THE CROWN JEWEL
│   │   ├── schema.py          # IssueProfile, MaturityProfile, BondProfile, ExtractedField[provenance] (pydantic)
│   │   ├── go_bond.py         # extractor for GO bonds (start here — simplest pledge)
│   │   ├── revenue_bond.py    # extractor for revenue bonds (later milestone)
│   │   ├── financials.py      # ACFR financial-line extraction across filings
│   │   └── provenance.py      # every field -> {doc_id, page, snippet}; fail closed if missing
│   ├── translate/
│   │   └── plain_language.py  # profile -> retail-readable cards (grounded, no advice)
│   ├── monitor/
│   │   ├── alerts.py          # ingest MyEMMA alert emails -> new-filing events
│   │   ├── events.py          # classify Rule 15c2-12 event type; detect MISSING annual filings
│   │   └── materiality.py     # descriptive materiality triage (+ "why this can matter")
│   ├── store/
│   │   ├── models.py          # securities, issuers, documents, extracted_fields, financials, watchlist
│   │   └── db.py
│   ├── qa/
│   │   └── ask.py             # grounded Q&A over a holding (RRF retrieval + cite-or-refuse)
│   └── api/
│       └── app.py             # FastAPI; minimal UI
├── tests/
│   ├── gold/                  # LLM-proposed, human-verified ground truth (see §7)
│   │   ├── docs/              # 5→15 real OS/disclosure PDFs
│   │   └── labels/            # verified fields + provenance per doc (yaml)
│   ├── test_extraction.py     # field-level precision/recall vs gold
│   ├── test_provenance.py     # every extracted value has a valid pointer
│   ├── test_grounding.py      # refuses to fabricate; "not disclosed" when absent
│   ├── test_no_advice.py      # never emits buy/sell/hold language
│   └── test_events.py         # event-type classification accuracy
└── scripts/
    └── eval.py                # runs the gold set; reports the §7 metrics
```

---

## 4. The extraction engine (the crown jewel)

### 4.1 Target schema (`extract/schema.py`)
Every field is an `ExtractedField` carrying its value **and** provenance — never a bare value.

> **Issue vs. maturity — the shape matters.** An Official Statement almost never describes one bond. It describes an *issue*: a series of serial/term maturities (often 15–30), each with its own CUSIP, coupon, maturity date, and sometimes different call protection or tax treatment (e.g., one maturity is AMT). The schema is therefore two-level: issue-scoped facts once, maturity-scoped facts per CUSIP. Extraction for a user's holding = find *their* CUSIP's row in the maturity schedule table + the issue-level narrative.

```python
class Provenance(BaseModel):
    doc_id: str
    page: int
    snippet: str            # the exact source text the value came from

class ExtractedField(BaseModel):
    value: str | float | None
    provenance: Provenance | None   # None ONLY when value is None ("not disclosed")
    confidence: float               # computed mechanically post-hoc (§4.3), never the model's self-report alone

class MaturityProfile(BaseModel):   # one per CUSIP within the issue
    cusip: ExtractedField
    coupon: ExtractedField
    maturity_date: ExtractedField
    call_features: ExtractedField
    tax_status: ExtractedField      # tax-exempt / AMT / taxable — CAN vary by maturity

class IssueProfile(BaseModel):      # shared across the series; from the OS narrative
    issuer_name: ExtractedField
    issue_purpose: ExtractedField
    pledge_type: ExtractedField         # "GO (full faith & credit)" | "revenue" | ...
    revenue_source: ExtractedField      # for revenue bonds: what actually pays
    debt_service_reserve: ExtractedField
    key_covenants: list[ExtractedField]
    financial_highlights: list[ExtractedField]
    fiscal_year_end: ExtractedField         # from the continuing-disclosure agreement (CDA) section
    annual_filing_deadline: ExtractedField  # e.g. "180 days after FYE" — the baseline for missing-filing detection (§5)
    maturities: list[MaturityProfile]

class BondProfile(BaseModel):       # what the user sees for THEIR holding
    issue: IssueProfile
    holding: MaturityProfile        # the maturity row matching the user's CUSIP
```

### 4.2 Approach
- Start with **GO bonds only** (simplest, most uniform pledge), then add revenue bonds. Don't try to handle every issuer's layout at once.
- Long-doc strategy: page-anchored chunking → RRF hybrid retrieval to pull the right sections per field → constrained LLM extraction returning the pydantic schema. Keep page numbers through the whole pipeline so provenance survives.
- **Maturity schedule = table extraction.** The per-CUSIP facts (coupon, maturity, price, CUSIP) live in the maturity schedule table, usually on/near the cover page — this is where `pdfplumber` earns its keep. The user's holding is one row of that table.
- **Fail closed:** if the model can't point to a source snippet, the field is `value=None` ("not disclosed"), not a guess. This is the single most important rule — it's what makes the tool trustworthy for someone's money.
- Dedup with SHA-256 so the same OS isn't re-extracted; cache extractions keyed by document hash.

### 4.3 Confidence: computed, not self-reported
The model MAY self-report a confidence, but that is only an *input*. The `confidence` the user sees is computed by code after extraction:

| Check | Contribution |
|---|---|
| **Snippet verification** | Exact string match on the cited page → full marks. Fuzzy match (OCR pages) → scaled by normalized edit distance. No match → **fail closed**: field becomes "not disclosed", confidence 0. |
| **Value-in-snippet** | The parsed value must actually appear in / follow from the snippet text. |
| **Cross-run agreement** | A second extraction pass (different prompt or model) landed on the same value. |
| **LLM self-report** | Logged; may break ties; can never *raise* the score above what the mechanical checks support. |

Result: a score that means something ("0.95 = exact page match, both runs agree"), not a vibe from the model. The same checks double as the labeling machinery for the gold set (§7).

---

## 5. Monitoring (`monitor/`)

- **Input:** MyEMMA alert emails for the user's watchlist (user forwards them to / the app polls a dedicated inbox). Free, sanctioned, no scraping.
- **New filing** → fetch that one document → extract → classify event type (Rule 15c2-12 categories) → summarize → materiality triage.
- **Missing filing** is a first-class signal: if an issuer's expected annual financial filing doesn't appear on schedule, surface it — *absence* of disclosure is itself a recognized red flag.
- **The baseline for "missing" comes from the OS itself:** the continuing-disclosure agreement states the fiscal year end and the filing deadline (commonly 180 or 270 days after FYE; it varies by issuer). These are extracted into `fiscal_year_end` / `annual_filing_deadline` (§4.1). No baseline extracted → no missing-filing claims for that holding (fail closed, again).
- **Materiality stays descriptive:** classify + summarize + "here's why this category of event can matter to a holder." Never "you should act."

---

## 6. Interface

- **Minimal UI:** per-holding "bond cards" (the structured profile in plain language, every claim hover-able to its source snippet) + a watchlist view with monitoring flags + a grounded Q&A box.
- **Grounded Q&A (`qa/ask.py`):** RRF retrieval over the holding's docs, answer **only** from retrieved text with citations; if unsupported, say so. Same cite-or-refuse discipline as extraction.
- **GraphQL? Optional, not necessary.** A small REST API is sufficient here. (Unlike the MBS project, there's no strong agent-consumes-a-graph argument; don't add GraphQL just for the résumé word — say "REST, because the surface is small and the value is in extraction" if asked.)

---

## 7. Eval discipline (this is what makes it credible — the muni analogue of golden tests)

Hallucination here costs someone real money, so evals are central, not optional.

- **Gold set — LLM-assisted, human-verified (no expert hand-labeling required):** start with ~5 real OS/disclosure PDFs, grow toward ~15 (mix of GO and revenue, including ≥2 scanned/OCR cases). Labels are produced by this protocol, not by reading 200-page documents cover to cover:
  1. Run extraction **twice** per document with two different configurations (different model or different prompt/retrieval), each returning value + page + snippet.
  2. Where the runs **agree**, the value is a candidate label. Where they **disagree**, the field is flagged for closer review.
  3. A human verifies every candidate by opening the PDF at the cited page and checking the snippet actually says the value. This is reading comprehension, not fintech expertise — the fields are factual (coupon, maturity date, issuer name, "tax-exempt"). Minutes per document, not hours.
  4. **Recall check via the cover page:** the OS cover page lists issuer, purpose, dated date, tax status, and the full maturity/coupon/CUSIP table in 1–2 pages. "Is anything on the cover page missing from the profile?" is a novice-friendly completeness check.
  5. **Integrity rule:** human verification is what turns LLM output into a gold set. Never evaluate the pipeline against unverified output of the same model + prompt — that only measures agreement with itself.
- **Value normalization:** define canonical formats per field type before labeling ("5.00%" ≡ 0.05; "June 1, 2034" ≡ 2034-06-01), or the precision metric measures string formatting, not extraction quality.
- **Extraction metrics:** per-field **precision/recall**; report a value as correct only if value **and** provenance page match. Track separately for GO vs. revenue.
- **Provenance test:** every non-null extracted field must carry a resolvable `{doc_id, page, snippet}`; snippet must actually appear on that page — exact match for born-digital PDFs, fuzzy match (normalized edit-distance threshold) for OCR'd pages, since OCR noise makes exact matching fail forever. Fail the run otherwise.
- **Grounding test:** inject questions whose answers are *not* in the docs; assert the system returns "not disclosed," never a fabricated value.
- **No-advice test:** assert outputs never contain buy/sell/hold or suitability language (keyword + LLM-judge check).
- **Event-classification accuracy:** labeled event notices → correct Rule 15c2-12 category.

Targets to start: GO-bond field precision ≥ 0.90 with valid provenance; zero fabricated values on the grounding set; zero advice-language hits.

---

## 8. Milestones (open-ended, each independently demonstrable)

| # | Milestone | Done when |
|---|---|---|
| **M0** | **Data-access spike** | For ~5 test CUSIPs, can reliably get OS + continuing disclosures into the system via **upload** (the sole ingestion path for M0–M1); MyEMMA alert-email ingestion proven on a test inbox. Automated per-CUSIP fetch is deferred to an **M4 spike** — EMMA pages sit behind a ToU click-through and load documents dynamically, so verify feasibility then; the honest fallback is "the alert email says what was filed, the user downloads and drops the PDF in." |
| **M1** | **GO extraction engine + gold set** | `BondProfile` extracted with provenance for GO bonds; §7 metrics pass on the gold set. No fabrication. |
| **M2** | **Plain-language cards + grounded Q&A** | One holding rendered as a retail-readable card with source-linked claims; Q&A cites or refuses. First "wow" demo. |
| **M3** | **Financial trend extraction** | Issuer financials pulled across multiple ACFR filings → multi-year trend (e.g., debt-service coverage), each figure source-anchored. |
| **M4** | **Monitoring** | New/changed/**missing** filing detection on a watchlist; event-type classification + materiality summary. |
| **M5** *(stretch)* | **Revenue bonds, portfolio view, deploy** | Revenue-bond extractor; multi-bond portfolio dashboard; free public deploy (HF Spaces + Neon, reuse the MBS project's deploy pattern). |

**First demonstrable target:** M0 + M1 — "drop in a real GO official statement, get a structured, source-cited profile." That alone is a portfolio-worthy artifact.

---

## 9. Honest risks & how to handle them

- **PDF heterogeneity is the project.** Every issuer's OS is laid out differently; that's *why* this is hard and *why* it's valuable. Don't fight it with rigid templates — use retrieval + constrained LLM extraction and lean on the gold set to measure reality.
- **Scanned older OS** need OCR; newer continuing disclosures are required to be word-searchable PDFs. Budget for the OCR path in M0/M1; include ≥2 scanned docs in the gold set.
- **Provenance or it didn't happen.** A muni tool that confidently states an un-sourced number is worse than useless. The fail-closed rule and provenance tests are the spine of the project.
- **Derived metrics vs. the grounding rule (M3).** Debt-service coverage is usually NOT a stated number in an ACFR — it's computed from pledged revenues and debt service. Resolution: derived metrics are allowed **iff every input has provenance**, and the UI labels them "computed from [inputs]", never presented as disclosed facts. ACFR line items aren't standardized across issuers, so M3 is the hardest milestone: fund-balance trend for GO issuers is the realistic M3 target; coverage ratios may belong in M5.
- **Profiles go stale.** Bonds get refunded, defeased, or called between filings; the tool reflects documents *as of their filing dates*, and a defeasance/call notice is one of the 15c2-12 events the monitor must catch. Say this in the UI.
- **The financial-advice line is real.** Keep everything descriptive. The README should state the informs-not-advises stance explicitly — it's both the ethical posture and a clean interview answer.
- **Coverage humility.** EMMA itself notes no single source has everything. The tool helps a holder *read what's filed*; it is not a credit rating or a guarantee. Say so in the UI.
- **Scope creep via "unique."** Open-ended ≠ unbounded. Each milestone ships something defensible; if you stop after M2 you still have a real, demoable, honest artifact.

---

## 10. Conventions for the coding agent

- **M0 first.** Don't write extraction code until you can reliably get a real document in for a handful of test CUSIPs. The data path is the project's foundation; prove it before building on it.
- Keep `extract/` honest: **every value carries provenance; fail closed to "not disclosed."** No bare values, ever.
- **Holdings-scoped only.** No code that crawls or bulk-fetches the EMMA portal. Ingestion is uploads + low-volume per-CUSIP fetch for the user's own watchlist.
- **Never emit advice.** Descriptive language only; the `test_no_advice` gate must stay green.
- Build the **gold set early** (even 5 docs) so M1 has a target; grow it as you add bond types. Use the §7 protocol: LLM proposes labels with provenance, a human verifies against the cited pages — no expert hand-labeling.
- **Confidence is computed, never trusted from the model** (§4.3): snippet verification + value-in-snippet + cross-run agreement; the model's self-report is an input at most.
- **MyEMMA email format is not an API contract.** Keep `monitor/alerts.py` thin and covered by fixture tests of real sample emails, so a format change breaks a test, not production.
- Dedup everything by SHA-256; cache extractions by document hash.
- The README is the resume artifact: document the schema, the extraction+provenance approach, the eval numbers, and the informs-not-advises stance.
