"""Batch-run extraction + candidate-label generation for every ingested
document that has no stored extraction yet.

    python scripts/batch_extract.py [--single-run]

Skips the demo doc, scanned docs, and anything already extracted. Each result
is stored in the extractions table and mirrored to tests/gold/labels/<doc>.yaml
as unverified candidate labels (see scripts/propose_labels.py for the
verification workflow).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from muni.config import get_settings  # noqa: E402
from muni.extract.go_bond import extract_go_profile  # noqa: E402
from muni.extract.llm import make_extractor  # noqa: E402
from muni.ingest.pdf import PageText  # noqa: E402
from muni.store.db import make_engine, session_scope  # noqa: E402
from muni.store.models import Document, Extraction  # noqa: E402

LABELS_DIR = Path(__file__).resolve().parents[1] / "tests" / "gold" / "labels"


def flatten_profile(profile: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for scope in ("issue", "holding"):
        section = profile.get(scope)
        if not section:
            continue
        for name, field in section.items():
            if not isinstance(field, dict):
                continue
            prov = field.get("provenance") or {}
            out[f"{scope}.{name}"] = {
                "value": field.get("value"),
                "page": prov.get("page"),
                "snippet": prov.get("snippet"),
                "verified": False,
            }
    return out


def count_found(profile: dict) -> tuple[int, int]:
    found = total = 0
    for scope in ("issue", "holding"):
        for field in (profile.get(scope) or {}).values():
            if isinstance(field, dict):
                total += 1
                found += field.get("value") is not None
    return found, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--single-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    extractor = make_extractor(settings)
    double_run = settings.double_run and not args.single_run

    with session_scope(engine) as session:
        docs = session.scalars(select(Document).order_by(Document.id)).all()
        todo = []
        for doc in docs:
            if doc.filename == "demo_os.pdf" or doc.is_scanned:
                continue
            has = session.scalar(
                select(Extraction.id).where(Extraction.document_id == doc.id).limit(1)
            )
            if has is None:
                todo.append(doc.id)
        print(f"model={extractor.model} double_run={double_run} queue={len(todo)} docs",
              flush=True)

    ok = failed = 0
    for doc_id in todo:
        started = time.monotonic()
        try:
            with session_scope(engine) as session:
                doc = session.get(Document, doc_id)
                filename, sha = doc.filename, doc.sha256
                pages = [PageText(number=p.page_number, text=p.text) for p in doc.pages]
                diagnostics: list[str] = []
                profile = extract_go_profile(
                    pages, doc_id=sha, extractor=extractor,
                    double_run=double_run, diagnostics=diagnostics,
                )
                payload = profile.model_dump()
                session.add(Extraction(
                    document_id=doc.id, cusip=None, model=extractor.model,
                    profile_json=json.dumps(payload),
                ))

            labels = {
                "document": filename,
                "sha256": sha,
                "cusip": None,
                "generated_from": f"batch ({extractor.model})",
                "instructions": (
                    "For each field: open the PDF at 'page', confirm the snippet supports "
                    "the value, correct anything wrong, then set verified: true. If the "
                    "fact is genuinely absent from the document, value must be null."
                ),
                "fields": flatten_profile(payload),
            }
            LABELS_DIR.mkdir(parents=True, exist_ok=True)
            out = LABELS_DIR / f"{Path(filename).stem}.yaml"
            out.write_text(
                yaml.safe_dump(labels, sort_keys=False, allow_unicode=True), encoding="utf-8"
            )

            found, total = count_found(payload)
            elapsed = time.monotonic() - started
            rejects = sum("discarded" in d for d in diagnostics)
            reanchors = sum("re-anchored" in d for d in diagnostics)
            print(f"[ok] {filename}: {found}/{total} fields, {reanchors} re-anchored, "
                  f"{rejects} discarded, {elapsed:.0f}s", flush=True)
            ok += 1
        except Exception as err:  # keep the batch going; report at the end
            elapsed = time.monotonic() - started
            print(f"[FAIL] doc {doc_id}: {type(err).__name__}: {err} ({elapsed:.0f}s)",
                  flush=True)
            failed += 1

    print(f"done: {ok} ok, {failed} failed", flush=True)
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
