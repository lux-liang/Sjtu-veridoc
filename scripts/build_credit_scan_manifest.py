#!/usr/bin/env python3
"""Build a private OCR manifest for scanned credit reports only."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(16 * 1024 * 1024)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        return list(csv.DictReader(stream))


def scanned_credit_ids(strict_results: Path) -> set[str]:
    payload = json.loads(strict_results.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "credit-word-rules-v1":
        raise ValueError("unsupported strict-result schema")
    if payload.get("synthetic_excluded") is not True:
        raise ValueError("strict results must exclude synthetic documents")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError("invalid strict-result document list")
    identifiers = [
        str(item.get("source_id") or "")
        for item in documents
        if item.get("source_format") == "scanned_or_image"
    ]
    if not identifiers or any(not item for item in identifiers):
        raise ValueError("no valid scanned credit-report identifiers found")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate scanned credit-report identifiers")
    return set(identifiers)


def build_manifest(
    combined_csv: Path,
    strict_results: Path,
    data_root: Path,
    output: Path,
) -> int:
    wanted = scanned_credit_ids(strict_results)
    rows_by_id: dict[str, dict[str, str]] = {}
    for row in read_csv(combined_csv):
        document_id = str(row.get("document_id") or "")
        if document_id not in wanted or row.get("doc_type") != "credit_report":
            continue
        if document_id in rows_by_id:
            raise ValueError(f"duplicate combined row for {document_id}")
        source = Path(str(row.get("path") or ""))
        resolved = source if source.is_absolute() else data_root / source
        resolved = resolved.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"credit-report source missing for {document_id}")
        extension = resolved.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported credit-report source type for {document_id}")
        rows_by_id[document_id] = {
            "document_id": document_id,
            "label": str(row.get("label") or ""),
            "doc_type": "credit_report",
            "ext": extension,
            "path": str(resolved),
        }

    missing = sorted(wanted - rows_by_id.keys())
    if missing:
        raise ValueError(f"combined features missing {len(missing)} scanned credit reports")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    fieldnames = ["document_id", "label", "doc_type", "ext", "path"]
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            writer.writerows(rows_by_id[key] for key in sorted(rows_by_id))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return len(rows_by_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined-csv", type=Path, required=True)
    parser.add_argument("--strict-results", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = build_manifest(
        args.combined_csv.resolve(),
        args.strict_results.resolve(),
        args.data_root.resolve(),
        args.output.resolve(),
    )
    print(f"wrote {count} scanned credit reports to {args.output}")


if __name__ == "__main__":
    main()
