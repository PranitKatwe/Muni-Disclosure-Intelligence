"""Canonical value comparison for evals (DESIGN.md section 7).

Without normalization the precision metric measures string formatting, not
extraction quality: "5.000%" and "5.00%" are the same coupon, "Dec. 1, 2025"
and "December 1, 2025" the same date.
"""

from __future__ import annotations

import re

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _as_percent_or_number(value: str) -> tuple[float, bool] | None:
    """Returns (number, is_percent) or None."""
    s = _norm_text(value).replace("$", "").replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(%|percent)?", s)
    if not m:
        return None
    return float(m.group(1)), m.group(2) is not None


def _as_date(value: str) -> tuple[int | None, int, int] | None:
    """Returns (year_or_None, month, day) or None."""
    s = _norm_text(value).replace(".", "").replace(",", "")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?", s)
    if m and m.group(1)[:3] in _MONTHS:
        year = int(m.group(3)) if m.group(3) else None
        return year, _MONTHS[m.group(1)[:3]], int(m.group(2))
    return None


def values_equivalent(a: str | float | None, b: str | float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None

    na, nb = _as_percent_or_number(str(a)), _as_percent_or_number(str(b))
    if na is not None and nb is not None:
        return na == nb

    da, db = _as_date(str(a)), _as_date(str(b))
    if da is not None and db is not None:
        if da[0] is None or db[0] is None:  # a fiscal-year-end like "December 31"
            return da[1:] == db[1:]
        return da == db

    return _norm_text(str(a)) == _norm_text(str(b))
