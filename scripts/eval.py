"""Gold-set evaluation (DESIGN.md §7): compares the latest stored extraction of
each labeled document against its verified labels.

    python scripts/eval.py [--labels tests/gold/labels] [--include-unverified]

A field counts as correct only if the value matches (after normalization) AND
the provenance page matches the label. Categories:

  correct        label value + extracted value match, same page
  wrong_value    both non-null but values differ
  wrong_page     value matches but cited page differs
  miss           label has a value, extraction says "not disclosed"
  FABRICATION    label says absent (null), extraction produced a value  <- worst
  correct_null   label null, extraction null (correct refusal)

Only `verified: true` labels count, unless --include-unverified (provisional).
Exit code 1 if any fabrication among verified labels.
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
from muni.extract.normalize import values_equivalent  # noqa: E402
from muni.store.db import make_engine, session_scope  # noqa: E402
from muni.store.models import Document, Extraction  # noqa: E402


def flatten(profile: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for scope in ("issue", "holding"):
        section = profile.get(scope)
        if not section:
            continue
        for name, field in section.items():
            if isinstance(field, dict):
                prov = field.get("provenance") or {}
                out[f"{scope}.{name}"] = {"value": field.get("value"), "page": prov.get("page")}
    return out


def judge(label: dict, got: dict | None) -> str:
    label_value, got_value = label.get("value"), (got or {}).get("value")
    if label_value is None:
        return "correct_null" if got_value is None else "FABRICATION"
    if got_value is None:
        return "miss"
    if not values_equivalent(label_value, got_value):
        return "wrong_value"
    if label.get("page") is not None and (got or {}).get("page") != label.get("page"):
        return "wrong_page"
    return "correct"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default=None)
    parser.add_argument("--include-unverified", action="store_true",
                        help="also score unverified candidate labels (provisional numbers)")
    args = parser.parse_args()

    labels_dir = Path(args.labels) if args.labels else (
        Path(__file__).resolve().parents[1] / "tests" / "gold" / "labels"
    )
    label_files = sorted(labels_dir.glob("*.yaml"))
    if not label_files:
        print(f"no label files in {labels_dir}; run scripts/propose_labels.py first")
        return 1

    settings = get_settings()
    engine = make_engine(settings.database_url)

    totals: dict[str, int] = {}
    skipped_unverified = 0
    fabrications: list[str] = []

    with session_scope(engine) as session:
        for path in label_files:
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))
            doc = session.scalar(select(Document).where(Document.sha256 == spec["sha256"]))
            if doc is None:
                print(f"{path.name}: document not in database, skipping")
                continue
            extraction = session.scalar(
                select(Extraction).where(Extraction.document_id == doc.id)
                .order_by(Extraction.id.desc())
            )
            if extraction is None:
                print(f"{path.name}: no extraction stored, skipping")
                continue
            got_fields = flatten(json.loads(extraction.profile_json))

            print(f"\n== {spec['document']} (extraction {extraction.id}, {extraction.model}) ==")
            for name, label in spec["fields"].items():
                if not label.get("verified") and not args.include_unverified:
                    skipped_unverified += 1
                    continue
                verdict = judge(label, got_fields.get(name))
                totals[verdict] = totals.get(verdict, 0) + 1
                if verdict == "FABRICATION":
                    fabrications.append(f"{spec['document']}:{name}")
                marker = "ok " if verdict in ("correct", "correct_null") else "!! "
                print(f"  {marker}{verdict:<13} {name}: label={label.get('value')!r} "
                      f"p{label.get('page')} vs got={ (got_fields.get(name) or {}).get('value')!r} "
                      f"p{(got_fields.get(name) or {}).get('page')}")

    n_scored = sum(totals.values())
    correct = totals.get("correct", 0) + totals.get("correct_null", 0)
    positives = totals.get("correct", 0) + totals.get("wrong_value", 0) \
        + totals.get("wrong_page", 0) + totals.get("FABRICATION", 0)
    labeled_present = totals.get("correct", 0) + totals.get("wrong_value", 0) \
        + totals.get("wrong_page", 0) + totals.get("miss", 0)

    print("\n==== summary ====")
    for k in ("correct", "correct_null", "wrong_page", "wrong_value", "miss", "FABRICATION"):
        if totals.get(k):
            print(f"  {k}: {totals[k]}")
    if skipped_unverified:
        print(f"  (skipped {skipped_unverified} unverified labels; "
              f"use --include-unverified for provisional numbers)")
    if n_scored:
        print(f"  accuracy (incl. correct refusals): {correct}/{n_scored} = {correct/n_scored:.2f}")
    if positives:
        print(f"  precision (of emitted values):     "
              f"{totals.get('correct', 0)}/{positives} = {totals.get('correct', 0)/positives:.2f}")
    if labeled_present:
        print(f"  recall (of facts in the doc):      "
              f"{totals.get('correct', 0)}/{labeled_present} = "
              f"{totals.get('correct', 0)/labeled_present:.2f}")
    if fabrications:
        print(f"\n  FABRICATIONS (must be zero): {fabrications}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
