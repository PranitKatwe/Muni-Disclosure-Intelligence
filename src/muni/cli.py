from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

from .config import get_settings
from .ingest.pdf import PageText
from .ingest.uploads import ingest_pdf
from .store.db import make_engine, session_scope
from .store.models import Document, Extraction


def _find_document(session, ref: str) -> Document | None:
    if ref.isdigit():
        doc = session.get(Document, int(ref))
        if doc:
            return doc
    return session.scalar(select(Document).where(Document.sha256.like(f"{ref}%")))


def cmd_ingest(args, settings, engine) -> int:
    with session_scope(engine) as session:
        for path in args.paths:
            path = Path(path)
            if not path.exists():
                print(f"skip (not found): {path}")
                continue
            doc, created = ingest_pdf(session, settings.data_dir, path, doc_type=args.type)
            status = "ingested" if created else "already ingested (dedup by sha256)"
            print(f"[{doc.id}] {doc.filename}: {status} - {doc.page_count} pages"
                  + (" - LOOKS SCANNED, needs OCR before extraction" if doc.is_scanned else ""))
    return 0


def cmd_docs(args, settings, engine) -> int:
    with session_scope(engine) as session:
        docs = session.scalars(select(Document).order_by(Document.id)).all()
        if not docs:
            print("no documents ingested yet - try: muni ingest <path-to-pdf>")
        for d in docs:
            flags = " [scanned]" if d.is_scanned else ""
            print(f"[{d.id}] {d.sha256[:12]}  {d.filename}  ({d.doc_type}, {d.page_count} pages){flags}")
    return 0


def cmd_extract(args, settings, engine) -> int:
    from .extract.go_bond import extract_go_profile
    from .extract.llm import make_extractor

    with session_scope(engine) as session:
        doc = _find_document(session, args.document)
        if doc is None:
            print(f"document not found: {args.document} (see: muni docs)")
            return 1
        if doc.is_scanned:
            print("this document looks scanned (no text layer); run OCR first (e.g. ocrmypdf)")
            return 1
        pages = [PageText(number=p.page_number, text=p.text) for p in doc.pages]

        extractor = make_extractor(settings, provider=args.provider, model=args.model)
        double_run = settings.double_run and not args.single_run
        print(f"extracting with {extractor.model} (double_run={double_run}) ...", file=sys.stderr)
        diagnostics: list[str] = []
        profile = extract_go_profile(
            pages, doc_id=doc.sha256, extractor=extractor, cusip=args.cusip,
            double_run=double_run, diagnostics=diagnostics,
        )
        for line in diagnostics:
            print(f"  [diag] {line}", file=sys.stderr)

        payload = profile.model_dump()
        session.add(Extraction(
            document_id=doc.id,
            cusip=args.cusip,
            model=extractor.model,
            profile_json=json.dumps(payload),
        ))
        print(json.dumps(payload, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="muni",
        description="Muni Disclosure Intelligence — grounded extraction from bond disclosure PDFs. "
                    "Informs, never advises.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest one or more disclosure PDFs (dedup by sha256)")
    p_ingest.add_argument("paths", nargs="+")
    p_ingest.add_argument("--type", default="official_statement",
                          choices=["official_statement", "annual_filing", "event_notice", "disclosure"])
    p_ingest.set_defaults(func=cmd_ingest)

    p_docs = sub.add_parser("docs", help="list ingested documents")
    p_docs.set_defaults(func=cmd_docs)

    p_extract = sub.add_parser("extract", help="extract a grounded BondProfile from a document")
    p_extract.add_argument("document", help="document id or sha256 prefix (see: muni docs)")
    p_extract.add_argument("--cusip", help="your holding's CUSIP, to extract its maturity row")
    p_extract.add_argument("--single-run", action="store_true",
                           help="skip the second verification pass (cheaper, lower confidence)")
    p_extract.add_argument("--model", help="override the extraction model")
    p_extract.add_argument("--provider", choices=["anthropic", "nvidia"],
                           help="LLM provider (default from MUNI_LLM_PROVIDER, anthropic)")
    p_extract.set_defaults(func=cmd_extract)

    args = parser.parse_args(argv)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    return args.func(args, settings, engine)


if __name__ == "__main__":
    raise SystemExit(main())
