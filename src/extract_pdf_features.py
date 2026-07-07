#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
from pathlib import Path


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def parse_pdfinfo(path: Path) -> dict:
    proc = run(["pdfinfo", str(path)])
    data = {"pdfinfo_rc": proc.returncode, "pdfinfo_stderr": proc.stderr.strip()}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


def parse_pdfimages(path: Path) -> tuple[list[dict], str]:
    proc = run(["pdfimages", "-list", str(path)])
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit() and parts[1].isdigit():
            try:
                rows.append(
                    {
                        "page": int(parts[0]),
                        "num": int(parts[1]),
                        "type": parts[2],
                        "width": int(parts[3]),
                        "height": int(parts[4]),
                    }
                )
            except ValueError:
                continue
    return rows, proc.stderr.strip()


def inspect_pdf_bytes(path: Path) -> dict:
    data = path.read_bytes()
    text_head = data[:300_000]
    creator_missing = 0 if re.search(rb"/Creator\s*\(", text_head) else 1
    producer_missing = 0 if re.search(rb"/Producer\s*\(", text_head) else 1
    eof_count = data.count(b"%%EOF")
    startxref_count = data.count(b"startxref")
    return {
        "pdf_object_count": len(re.findall(rb"\n\s*\d+\s+\d+\s+obj\b", data)),
        "stream_count": data.count(b"stream"),
        "xref_count": data.count(b"xref"),
        "eof_count": eof_count,
        "startxref_count": startxref_count,
        "font_object_count": len(re.findall(rb"/Font\b", data)),
        "javascript_count": len(re.findall(rb"/JavaScript\b|/JS\b", data)),
        "embedded_file_count": len(re.findall(rb"/EmbeddedFile\b|/Filespec\b", data)),
        "acroform_count": len(re.findall(rb"/AcroForm\b|/XFA\b", data)),
        "annotation_count": len(re.findall(rb"/Annot\b", data)),
        "creator_missing": creator_missing,
        "producer_missing": producer_missing,
        "incremental_update_count": max(eof_count, startxref_count),
    }


def risk_score(record: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    if record["smask_count"] >= 1:
        score += 25
        reasons.append("pdf_smask_present")
    if record["font_warning_count"] >= 1:
        score += 20
        reasons.append("poppler_font_warning")
    if record["large_image_count"] >= 1 and record["small_overlay_count"] >= 3:
        score += 15
        reasons.append("full_page_image_with_local_overlays")
    if record["image_count"] >= max(record["pages"], 1) * 3 and record["small_overlay_count"] >= 3:
        score += 15
        reasons.append("dense_pdf_image_overlays")
    if record["creator_missing"] and record["producer_missing"]:
        score += 10
        reasons.append("missing_creator_producer")
    if record["incremental_update_count"] > 1:
        score += 15
        reasons.append("incremental_update_trace")
    if record["javascript_count"] or record["embedded_file_count"]:
        score += 20
        reasons.append("embedded_script_or_file")
    if record["font_object_count"] > 80:
        score += 10
        reasons.append("high_font_object_count")
    if record["acroform_count"]:
        score += 8
        reasons.append("interactive_form_or_xfa")
    return min(score, 100), reasons


def analyze_pdf(row: dict) -> dict:
    path = Path(row["path"])
    info = parse_pdfinfo(path)
    images, image_stderr = parse_pdfimages(path)
    byte_features = inspect_pdf_bytes(path)
    record = {
        **row,
        "pages": int(info.get("Pages", "0") or 0),
        "creator": info.get("Creator", ""),
        "producer": info.get("Producer", ""),
        "pdf_version": info.get("PDF version", ""),
        "page_size": info.get("Page size", ""),
        "custom_metadata": info.get("Custom Metadata", ""),
        "optimized": info.get("Optimized", ""),
        "image_count": sum(1 for item in images if item["type"] == "image"),
        "smask_count": sum(1 for item in images if item["type"] == "smask"),
        "large_image_count": sum(
            1 for item in images if item["type"] == "image" and item["width"] * item["height"] >= 1_000_000
        ),
        "small_overlay_count": sum(
            1 for item in images if item["type"] == "image" and item["width"] * item["height"] < 300_000
        ),
        "font_warning_count": image_stderr.count("Syntax Warning"),
        "pdfimages_stderr": image_stderr[:500],
        **byte_features,
    }
    score, reasons = risk_score(record)
    record["object_risk_score"] = score
    record["object_risk_reasons"] = "|".join(reasons)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    pdf_rows = [row for row in rows if row["ext"].lower() == ".pdf"]
    records = [analyze_pdf(row) for row in pdf_rows]

    fields = [
        "document_id",
        "label",
        "doc_type",
        "ext",
        "size_bytes",
        "sha256",
        "pages",
        "creator",
        "producer",
        "pdf_version",
        "page_size",
        "custom_metadata",
        "optimized",
        "image_count",
        "smask_count",
        "large_image_count",
        "small_overlay_count",
        "font_warning_count",
        "pdf_object_count",
        "stream_count",
        "xref_count",
        "eof_count",
        "startxref_count",
        "font_object_count",
        "javascript_count",
        "embedded_file_count",
        "acroform_count",
        "annotation_count",
        "creator_missing",
        "producer_missing",
        "incremental_update_count",
        "object_risk_score",
        "object_risk_reasons",
        "pdfimages_stderr",
        "path",
    ]
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(records)

    summary = {}
    for record in records:
        key = (record["label"], record["doc_type"])
        bucket = summary.setdefault(str(key), {"count": 0, "smask": 0, "font_warning": 0, "risk_score_sum": 0})
        bucket["count"] += 1
        bucket["smask"] += int(record["smask_count"])
        bucket["font_warning"] += int(record["font_warning_count"])
        bucket["risk_score_sum"] += int(record["object_risk_score"])
    for bucket in summary.values():
        bucket["mean_object_risk_score"] = round(bucket["risk_score_sum"] / max(bucket["count"], 1), 2)
        del bucket["risk_score_sum"]
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
