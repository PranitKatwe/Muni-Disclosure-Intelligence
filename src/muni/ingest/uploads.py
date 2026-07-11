from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..store.models import Document, Page
from .pdf import extract_pages, looks_scanned, sha256_file


def ingest_pdf(
    session: Session,
    data_dir: Path,
    path: Path | str,
    doc_type: str = "disclosure",
) -> tuple[Document, bool]:
    """Ingest a user-uploaded PDF. Returns (document, created).

    Dedup is by SHA-256 of file content: re-uploading the same document is a
    no-op and returns the existing record.
    """
    path = Path(path)
    digest = sha256_file(path)

    existing = session.scalar(select(Document).where(Document.sha256 == digest))
    if existing is not None:
        return existing, False

    pages = extract_pages(path)

    docs_dir = Path(data_dir) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    stored = docs_dir / f"{digest}.pdf"
    if not stored.exists():
        shutil.copy2(path, stored)

    doc = Document(
        sha256=digest,
        filename=path.name,
        stored_path=str(stored),
        doc_type=doc_type,
        page_count=len(pages),
        is_scanned=looks_scanned(pages),
    )
    doc.pages = [Page(page_number=p.number, text=p.text) for p in pages]
    session.add(doc)
    session.flush()
    return doc, True
