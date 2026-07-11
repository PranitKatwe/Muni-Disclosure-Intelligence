from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass
class PageText:
    number: int  # 1-based, matches what a human sees in a PDF viewer
    text: str


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pages(path: Path | str) -> list[PageText]:
    with pymupdf.open(path) as doc:
        return [PageText(number=i + 1, text=page.get_text("text")) for i, page in enumerate(doc)]


def looks_scanned(pages: list[PageText], min_avg_chars: int = 100) -> bool:
    """Heuristic: a scanned (image-only) PDF yields almost no extractable text.

    Such documents need the OCR path (ocrmypdf/Tesseract) before extraction.
    """
    if not pages:
        return True
    avg = sum(len(p.text.strip()) for p in pages) / len(pages)
    return avg < min_avg_chars
