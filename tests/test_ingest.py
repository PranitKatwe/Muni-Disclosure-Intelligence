from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from muni.ingest.uploads import ingest_pdf
from muni.store.db import make_engine, session_scope
from muni.store.models import Document, Page


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "os.pdf"
    filler = (
        "The Bonds are being issued pursuant to the Bond Ordinance adopted by the City "
        "Council. Interest on the Bonds is payable semiannually on June 1 and December 1. "
        "The Bonds are subject to optional redemption as described herein.\n"
    ) * 3  # real OS pages are text-dense; keep the fixture above the scanned-PDF heuristic
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        "OFFICIAL STATEMENT\nCity of Springfield General Obligation Bonds\n" + filler,
    )
    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        "SECURITY FOR THE BONDS\nFull faith and credit of the City.\n" + filler,
    )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def engine(tmp_path: Path):
    return make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


def test_ingest_stores_pages_with_numbers(engine, sample_pdf, tmp_path):
    with session_scope(engine) as session:
        doc, created = ingest_pdf(session, tmp_path / "data", sample_pdf)
        assert created
        assert doc.page_count == 2
        assert doc.is_scanned is False  # text-based PDF, no OCR needed
        texts = {p.page_number: p.text for p in doc.pages}
        assert "OFFICIAL STATEMENT" in texts[1]
        assert "SECURITY FOR THE BONDS" in texts[2]
        assert Path(doc.stored_path).exists()


def test_reingest_is_deduped_by_sha256(engine, sample_pdf, tmp_path):
    with session_scope(engine) as session:
        _, created1 = ingest_pdf(session, tmp_path / "data", sample_pdf)
        assert created1
    with session_scope(engine) as session:
        _, created2 = ingest_pdf(session, tmp_path / "data", sample_pdf)
        assert not created2
        assert len(session.scalars(select(Document)).all()) == 1
        assert len(session.scalars(select(Page)).all()) == 2
