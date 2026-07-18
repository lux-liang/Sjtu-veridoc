#!/usr/bin/env python3
"""Audit document identity alignment across VeriDoc feature CSV files.

Feature fusion is keyed by ``document_id``.  Reusing an ID for a different
file silently joins unrelated evidence and invalidates evaluation metrics.
This command fails closed on duplicate IDs, IDs outside the base manifest,
and path/label/doc-type mismatches.  Partial feature tables are allowed unless
their name is listed with ``--require-complete``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path


IDENTITY_FIELDS = ("label", "doc_type")


def read_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=PATH")
    name, raw_path = value.split("=", 1)
    name = name.strip()
    if not name or not raw_path.strip():
        raise argparse.ArgumentTypeError("expected non-empty NAME=PATH")
    return name, Path(raw_path)


def indexed_rows(rows: list[dict]) -> tuple[dict[str, dict], list[str]]:
    counts = Counter(str(row.get("document_id") or "") for row in rows)
    duplicates = sorted(document_id for document_id, count in counts.items() if document_id and count > 1)
    return {str(row.get("document_id") or ""): row for row in rows if row.get("document_id")}, duplicates


def same_path(left: str, right: str) -> bool:
    """Compare paths by basename because deployment roots legitimately differ."""
    return os.path.basename(str(left or "")) == os.path.basename(str(right or ""))


def audit_feature(
    base: dict[str, dict],
    name: str,
    path: Path,
    require_complete: bool = False,
) -> tuple[dict, list[str]]:
    rows = read_rows(path)
    indexed, duplicates = indexed_rows(rows)
    base_ids = set(base)
    feature_ids = set(indexed)
    outside = sorted(feature_ids - base_ids)
    missing = sorted(base_ids - feature_ids)
    path_mismatches = []
    field_mismatches: dict[str, list[str]] = {field: [] for field in IDENTITY_FIELDS}

    for document_id in sorted(base_ids & feature_ids):
        base_row = base[document_id]
        row = indexed[document_id]
        if base_row.get("path") and row.get("path") and not same_path(base_row["path"], row["path"]):
            path_mismatches.append(document_id)
        for field in IDENTITY_FIELDS:
            if base_row.get(field) and row.get(field) and base_row[field] != row[field]:
                field_mismatches[field].append(document_id)

    errors = []
    if duplicates:
        errors.append(f"{name}: duplicate document_id count={len(duplicates)}")
    if outside:
        errors.append(f"{name}: IDs outside base count={len(outside)}")
    if path_mismatches:
        errors.append(f"{name}: path mismatches count={len(path_mismatches)}")
    for field, values in field_mismatches.items():
        if values:
            errors.append(f"{name}: {field} mismatches count={len(values)}")
    if require_complete and missing:
        errors.append(f"{name}: missing base IDs count={len(missing)}")

    report = {
        "name": name,
        "path": str(path),
        "row_count": len(rows),
        "unique_id_count": len(indexed),
        "common_id_count": len(base_ids & feature_ids),
        "duplicate_ids": duplicates[:20],
        "outside_base_ids": outside[:20],
        "missing_base_count": len(missing),
        "path_mismatch_ids": path_mismatches[:20],
        "field_mismatch_ids": {field: values[:20] for field, values in field_mismatches.items()},
        "require_complete": require_complete,
        "ok": not errors,
    }
    return report, errors


def run_audit(base_path: Path, features: list[tuple[str, Path]], require_complete: set[str]) -> dict:
    base_rows = read_rows(base_path)
    base, base_duplicates = indexed_rows(base_rows)
    errors = []
    if base_duplicates:
        errors.append(f"base: duplicate document_id count={len(base_duplicates)}")
    reports = []
    for name, path in features:
        report, feature_errors = audit_feature(base, name, path, name in require_complete)
        reports.append(report)
        errors.extend(feature_errors)
    return {
        "base": {
            "path": str(base_path),
            "row_count": len(base_rows),
            "unique_id_count": len(base),
            "duplicate_ids": base_duplicates[:20],
        },
        "features": reports,
        "errors": errors,
        "ok": not errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-csv", type=Path, required=True)
    parser.add_argument("--feature", type=parse_named_path, action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--require-complete", action="append", default=[], metavar="NAME")
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()
    if not args.feature:
        parser.error("at least one --feature NAME=PATH is required")

    result = run_audit(args.base_csv, args.feature, set(args.require_complete))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(rendered, encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
