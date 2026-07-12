"""Generate candidate gold-set labels from a stored extraction (DESIGN.md §7).

LLM-assisted labeling: the pipeline proposes value+page+snippet, a human then
verifies each one by opening the PDF at the cited page. Verification, not
expert labeling. Usage:

    python scripts/propose_labels.py <doc id or sha prefix> [--out FILE]

Then, for each field in the YAML: check the snippet on the cited page, fix the
value/page if wrong, and set `verified: true`. A field whose fact is genuinely
absent from the document should have `value: null`. Only verified fields count
in scripts/eval.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from muni.config import get_settings  # noqa: E402
from muni.store.db import make_engine, session_scope  # noqa: E402
from muni.store.models import Document, Extraction  # noqa: E402


def flatten_profile(profile: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for scope in ("issue", "holding"):
        section = profile.get(scope)
        if not section:
            continue
        for name, field in section.items():
            if not isinstance(field, dict):  # skip list fields (covenants) for now
                continue
            prov = field.get("provenance") or {}
            out[f"{scope}.{name}"] = {
                "value": field.get("value"),
                "page": prov.get("page"),
                "snippet": prov.get("snippet"),
                "verified": False,
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", help="document id or sha256 prefix")
    parser.add_argument("--out", help="output YAML path (default: tests/gold/labels/<name>.yaml)")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    with session_scope(engine) as session:
        doc = None
        if args.document.isdigit():
            doc = session.get(Document, int(args.document))
        if doc is None:
            doc = session.scalar(
                select(Document).where(Document.sha256.like(f"{args.document}%"))
            )
        if doc is None:
            print(f"document not found: {args.document}")
            return 1
        extraction = session.scalar(
            select(Extraction)
            .where(Extraction.document_id == doc.id)
            .order_by(Extraction.id.desc())
        )
        if extraction is None:
            print(f"no extraction stored for {doc.filename}; run `muni extract` first")
            return 1

        filename = doc.filename
        labels = {
            "document": filename,
            "sha256": doc.sha256,
            "cusip": extraction.cusip,
            "generated_from": f"extraction {extraction.id} ({extraction.model})",
            "instructions": (
                "For each field: open the PDF at 'page', confirm the snippet supports the "
                "value, correct anything wrong, then set verified: true. If the fact is "
                "genuinely absent from the document, value must be null."
            ),
            "fields": flatten_profile(json.loads(extraction.profile_json)),
        }

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parents[1]
        / "tests" / "gold" / "labels" / f"{Path(filename).stem}.yaml"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(labels, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {out} ({len(labels['fields'])} candidate fields, all unverified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
