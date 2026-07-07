#!/usr/bin/env python3
from __future__ import annotations
import argparse
import csv
import json
import sys
from pathlib import Path

from src.detectors.integrated_detector import IntegratedDetector


VALID_DETECTORS = {"pdf_structure", "image_forensics", "seal_overlay", "ocr_text", "business_logic"}


def parse_detectors(value: str) -> set[str] | None:
    if not value or value == "all":
        return None
    selected = {item.strip() for item in value.split(",") if item.strip()}
    unknown = selected - VALID_DETECTORS
    if unknown:
        raise SystemExit(f"unknown detector(s): {', '.join(sorted(unknown))}")
    return selected


def print_table(results: list[dict]) -> None:
    headers = ["document_id", "label", "doc_type", "score", "level", "top_reasons"]
    widths = {key: len(key) for key in headers}
    rows = []
    for result in results:
        row = {
            "document_id": result.get("document_id", ""),
            "label": result.get("label", ""),
            "doc_type": result.get("doc_type", ""),
            "score": f"{float(result.get('score', 0)):.1f}",
            "level": result.get("level", ""),
            "top_reasons": ", ".join(result.get("reasons", [])[:3]),
        }
        rows.append(row)
        for key, value in row.items():
            widths[key] = min(54, max(widths[key], len(str(value))))
    line = " | ".join(key.ljust(widths[key]) for key in headers)
    sep = "-+-".join("-" * widths[key] for key in headers)
    print(line)
    print(sep)
    for row in rows:
        print(" | ".join(str(row[key])[: widths[key]].ljust(widths[key]) for key in headers))


def load_manifest_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as f:
        return [row["document_id"] for row in csv.DictReader(f) if row.get("document_id")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run integrated material authenticity detection.")
    parser.add_argument("--project-root", default=".", help="Project root containing src/ and outputs/")
    parser.add_argument("--document-id", action="append", help="Detect an existing document_id from outputs/features")
    parser.add_argument("--batch-manifest", help="CSV manifest with document_id column")
    parser.add_argument("--file", help="Detect a single local PDF/image path")
    parser.add_argument("--doc-type", default="other", help="Document type for --file")
    parser.add_argument("--label", default="unknown", help="Label metadata for --file")
    parser.add_argument("--detectors", default="all", help="Comma-separated detector names or all")
    parser.add_argument("--format", choices=["json", "table"], default="json")
    parser.add_argument("--out", help="Write JSON output to file")
    args = parser.parse_args()

    selected = parse_detectors(args.detectors)
    detector = IntegratedDetector(args.project_root)
    results = []
    if args.file:
        results.append(detector.detect_path(args.file, doc_type=args.doc_type, label=args.label, detectors=selected))
    ids = []
    if args.document_id:
        ids.extend(args.document_id)
    if args.batch_manifest:
        ids.extend(load_manifest_ids(Path(args.batch_manifest)))
    for document_id in ids:
        results.append(detector.detect_document_id(document_id, detectors=selected))
    if not results:
        raise SystemExit("provide --file, --document-id, or --batch-manifest")

    payload = {"count": len(results), "results": results}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "table":
        print_table(results)
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()
