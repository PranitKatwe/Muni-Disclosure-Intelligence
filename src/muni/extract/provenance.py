"""Mechanical snippet verification and confidence scoring (DESIGN.md section 4.3).

The LLM's self-reported confidence is only an input and can never RAISE the
score above what these checks support. If the snippet cannot be found on the
cited page, the field fails closed to "not disclosed".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 85.0  # normalized partial-ratio floor for OCR'd pages


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class SnippetMatch:
    kind: str  # "exact" | "fuzzy" | "none"
    score: float  # 0-100


def verify_snippet(snippet: str | None, page_text: str | None) -> SnippetMatch:
    if not snippet or not page_text:
        return SnippetMatch("none", 0.0)
    s, p = normalize(snippet), normalize(page_text)
    if s and s in p:
        return SnippetMatch("exact", 100.0)
    score = float(fuzz.partial_ratio(s, p))
    if score >= FUZZY_THRESHOLD:
        return SnippetMatch("fuzzy", score)
    return SnippetMatch("none", score)


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            out.add(round(float(m.replace(",", "")), 6))
        except ValueError:
            continue
    return out


def value_in_snippet(value: str | float | None, snippet: str | None) -> bool:
    """The parsed value must actually appear in (or follow from) the snippet."""
    if value is None or not snippet:
        return False
    v, s = normalize(str(value)), normalize(snippet)
    if v and v in s:
        return True
    vn = _numbers(v)
    return bool(vn) and vn <= _numbers(s)


def compute_confidence(
    match: SnippetMatch,
    value_ok: bool,
    cross_run_agree: bool,
    self_report: float | None = None,  # logged by callers; never raises the score
) -> float:
    if match.kind == "none":
        return 0.0
    base = 0.5 if match.kind == "exact" else 0.5 * (match.score / 100.0)
    score = base + (0.3 if value_ok else 0.0) + (0.2 if cross_run_agree else 0.0)
    return round(min(score, 1.0), 3)
