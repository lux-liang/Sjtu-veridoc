#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib import request


AMOUNT_RE = re.compile(r"(?<![A-Za-z])(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"\b(20[0-9]{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12][0-9]|3[01]))\b")
ID_RE = re.compile(r"\b[0-9]{6}(?:19|20)?[0-9]{6,8}[0-9Xx]?\b")
ACCOUNT_RE = re.compile(r"\b[0-9]{12,24}\b")
TAX_ID_RE = re.compile(r"\b[0-9A-Z]{15,20}\b")
INVOICE_NO_RE = re.compile(r"\b(?:INV|FP|FAPIAO|INVOICE)[-_A-Z0-9]*[0-9]{4,}\b", re.I)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def parse_amount(text: str) -> float | None:
    match = AMOUNT_RE.search(text.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


NUM_AFTER = r"([+-]?[0-9][0-9,]*(?:\.[0-9]{1,2})?)"


def labeled_amount(lines: list[dict], labels: list[str]) -> float | None:
    """Amount for a label ONLY when a number follows it on the same line.

    Avoids matching a label token embedded in a title/heading (e.g. the 'TAX'
    inside 'VALUE ADDED TAX INVOICE'), which the naive substring matcher hit.
    """
    for line in lines:
        text = line["text"]
        for label in labels:
            m = re.search(re.escape(label) + r"\s*[:：]?\s*(?:CNY|RMB|¥)?\s*" + NUM_AFTER, text, re.I)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
    return None


def balance_rows(lines: list[dict]) -> list[tuple[float, float]]:
    """Parse (flow, balance) pairs from bank-statement lines for continuity check."""
    rows = []
    for line in lines:
        bm = re.search(r"(?:Balance|余额)\s*[:：]?\s*" + NUM_AFTER, line["text"], re.I)
        if not bm:
            continue
        try:
            balance = float(bm.group(1).replace(",", ""))
        except ValueError:
            continue
        # signed flow, but NOT the '-06' inside a date like 2026-06-10
        fm = re.search(r"(?<![0-9])([+-][0-9][0-9,]*(?:\.[0-9]{1,2})?)(?![0-9])", line["text"])
        flow = None
        if fm:
            try:
                flow = float(fm.group(1).replace(",", ""))
            except ValueError:
                flow = None
        rows.append((flow, balance))
    return rows


def extract_words_pdf(path: Path, max_pages: int) -> tuple[list[dict], str]:
    if not shutil.which("pdftotext"):
        return [], "pdftotext_unavailable"
    with tempfile.TemporaryDirectory() as td:
        xml_path = Path(td) / "bbox.xml"
        proc = run(["pdftotext", "-f", "1", "-l", str(max_pages), "-bbox", str(path), str(xml_path)])
        if proc.returncode != 0 or not xml_path.exists():
            return [], f"pdftotext_failed:{proc.stderr[:180]}"
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError as exc:
            return [], f"bbox_parse_failed:{exc}"
    words = []
    page_index = 0
    for page in root.iterfind(".//{*}page"):
        page_index += 1
        for word in page.iterfind("{*}word"):
            text = safe_text("".join(word.itertext()))
            if not text:
                continue
            words.append(
                {
                    "page": page_index,
                    "text": text,
                    "x_min": float(word.attrib.get("xMin", 0)),
                    "y_min": float(word.attrib.get("yMin", 0)),
                    "x_max": float(word.attrib.get("xMax", 0)),
                    "y_max": float(word.attrib.get("yMax", 0)),
                }
            )
    return words, ""


def line_key(word: dict) -> tuple[int, int]:
    return int(word["page"]), round(float(word["y_min"]) / 6)


def words_to_lines(words: list[dict]) -> list[dict]:
    buckets = {}
    for word in words:
        buckets.setdefault(line_key(word), []).append(word)
    lines = []
    for (page, _), line_words in sorted(buckets.items()):
        line_words = sorted(line_words, key=lambda item: item["x_min"])
        text = safe_text(" ".join(item["text"] for item in line_words))
        if not text:
            continue
        lines.append(
            {
                "page": page,
                "text": text,
                "x_min": min(item["x_min"] for item in line_words),
                "y_min": min(item["y_min"] for item in line_words),
                "x_max": max(item["x_max"] for item in line_words),
                "y_max": max(item["y_max"] for item in line_words),
            }
        )
    return lines


def nearby_value(lines: list[dict], labels: list[str]) -> str:
    for line in lines:
        lower = line["text"].lower()
        for label in labels:
            if label.lower() in lower:
                parts = re.split(re.escape(label), line["text"], flags=re.I, maxsplit=1)
                if len(parts) > 1 and safe_text(parts[1].lstrip(":：- ")):
                    return safe_text(parts[1].lstrip(":：- "))
                return line["text"]
    return ""


def extract_fields(doc_type: str, lines: list[dict]) -> dict:
    full_text = "\n".join(line["text"] for line in lines)
    amounts = [float(item.replace(",", "")) for item in AMOUNT_RE.findall(full_text)[:30]]
    dates = DATE_RE.findall(full_text)[:20]
    ids = ID_RE.findall(full_text)[:10]
    accounts = ACCOUNT_RE.findall(full_text)[:10]
    fields = {
        "amounts": amounts,
        "dates": dates,
        "id_numbers": ids,
        "account_numbers": accounts,
        "tax_ids": TAX_ID_RE.findall(full_text)[:10],
        "invoice_numbers": INVOICE_NO_RE.findall(full_text)[:10],
        "name": nearby_value(lines, ["Name", "姓名"]),
        "buyer": nearby_value(lines, ["Buyer", "购买方"]),
        "party_a": nearby_value(lines, ["Party A", "甲方"]),
        "party_b": nearby_value(lines, ["Party B", "乙方"]),
        "amount_line": nearby_value(lines, ["Amount", "Contract Amount", "Settlement Amount", "金额"]),
        "tax_line": nearby_value(lines, ["Tax", "税额"]),
        "total_line": nearby_value(lines, ["Total", "价税合计", "合计"]),
        "balance_line": nearby_value(lines, ["Balance", "余额"]),
    }
    if doc_type == "invoice":
        # robust labeled extraction (number must follow the label on the line)
        fields["amount"] = labeled_amount(lines, ["Amount", "金额", "Amount excl", "价款"])
        fields["tax"] = labeled_amount(lines, ["Tax", "税额", "税金"])
        fields["total"] = labeled_amount(lines, ["Total", "价税合计", "合计", "Amount incl"])
    if doc_type == "bank_page":
        fields["balance_sequence"] = balance_rows(lines)
    if doc_type in {"contract", "settlement_statement", "bank_page", "credit_report"}:
        fields["primary_amount"] = labeled_amount(lines, ["Amount", "金额", "Contract Amount", "Settlement Amount"]) or (amounts[0] if amounts else None)
    return fields


def duplicated_text_ratio(lines: list[dict]) -> float:
    normalized = [safe_text(line["text"]).lower() for line in lines if len(safe_text(line["text"])) >= 6]
    if not normalized:
        return 0.0
    counts = Counter(normalized)
    duplicate_count = sum(count for count in counts.values() if count > 1)
    return round(duplicate_count / len(normalized), 4)


def business_rules(row: dict, words: list[dict], lines: list[dict], fields: dict, error: str) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    doc_type = row.get("doc_type", "")
    ext = row.get("ext", "").lower()
    if error:
        score += 8
        reasons.append(error.split(":", 1)[0])
    if ext == ".pdf" and not words:
        score += 20
        reasons.append("pdf_text_layer_missing_or_unreadable")
    if words:
        word_count = len(words)
        unique_words = len({item["text"].lower() for item in words})
        if word_count < 20:
            score += 12
            reasons.append("very_sparse_text_layer")
        if word_count >= 20 and unique_words / max(word_count, 1) < 0.28:
            score += 10
            reasons.append("text_layer_repetition")
    if duplicated_text_ratio(lines) >= 0.18:
        score += 8
        reasons.append("duplicated_text_lines")
    if doc_type == "invoice":
        amount, tax, total = fields.get("amount"), fields.get("tax"), fields.get("total")
        if amount is not None and tax is not None and total is not None and abs((amount + tax) - total) > 0.05:
            score += 35
            reasons.append("invoice_amount_tax_total_mismatch")
        if not fields.get("invoice_numbers"):
            score += 8
            reasons.append("invoice_number_missing")
    if doc_type == "contract":
        if not fields.get("party_a") or not fields.get("party_b"):
            score += 8
            reasons.append("contract_party_field_missing")
        if not fields.get("dates"):
            score += 8
            reasons.append("contract_date_missing")
    if doc_type == "bank_page":
        if not fields.get("account_numbers"):
            score += 8
            reasons.append("bank_account_number_missing")
        if len(fields.get("amounts", [])) < 2:
            score += 8
            reasons.append("bank_amount_sequence_sparse")
        seq = fields.get("balance_sequence") or []
        broken = sum(1 for i in range(1, len(seq))
                     if seq[i][0] is not None and abs((seq[i - 1][1] + seq[i][0]) - seq[i][1]) > 0.05)
        if len(seq) >= 3 and broken >= 1:
            score += 30
            reasons.append("bank_balance_sequence_broken")
    if doc_type == "credit_report":
        if not fields.get("id_numbers"):
            score += 8
            reasons.append("credit_report_id_missing")
        if not fields.get("dates"):
            score += 8
            reasons.append("credit_report_date_missing")
    if doc_type == "settlement_statement":
        if not fields.get("account_numbers") and not fields.get("amounts"):
            score += 10
            reasons.append("settlement_core_fields_missing")
    return min(score, 100), reasons


def deepseek_explain(row: dict, fields: dict, reasons: list[str]) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key or not reasons:
        return ""
    payload = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {
                "role": "system",
                "content": "你是材料真实性风控辅助解释器。只基于给定字段和规则原因，输出一句简短中文复核建议，不要编造结论。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "document_id": row.get("document_id"),
                        "doc_type": row.get("doc_type"),
                        "fields": fields,
                        "rule_reasons": reasons,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 160,
    }
    req = request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return safe_text(data["choices"][0]["message"]["content"])[:400]
    except Exception as exc:
        return f"deepseek_explain_failed:{type(exc).__name__}"


def analyze_row(row: dict, max_pages: int, use_deepseek: bool) -> tuple[dict, list[dict]]:
    path = Path(row["path"])
    words, error = ([], "missing_file") if not path.exists() else ([], "")
    if path.exists() and row.get("ext", "").lower() == ".pdf":
        words, error = extract_words_pdf(path, max_pages)
    lines = words_to_lines(words)
    fields = extract_fields(row.get("doc_type", ""), lines)
    score, reasons = business_rules(row, words, lines, fields, error)
    explanation = deepseek_explain(row, fields, reasons) if use_deepseek else ""
    summary = {
        **row,
        "text_word_count": len(words),
        "text_line_count": len(lines),
        "text_unique_word_count": len({item["text"].lower() for item in words}),
        "text_duplicate_line_ratio": duplicated_text_ratio(lines),
        "field_amount_count": len(fields.get("amounts", [])),
        "field_date_count": len(fields.get("dates", [])),
        "field_id_count": len(fields.get("id_numbers", [])),
        "field_account_count": len(fields.get("account_numbers", [])),
        "field_invoice_count": len(fields.get("invoice_numbers", [])),
        "business_risk_score": score,
        "business_risk_reasons": "|".join(reasons),
        "extracted_fields_json": json.dumps(fields, ensure_ascii=False, sort_keys=True),
        "deepseek_explanation": explanation,
        "text_error": error,
    }
    word_rows = [
        {
            "document_id": row.get("document_id", ""),
            "label": row.get("label", ""),
            "doc_type": row.get("doc_type", ""),
            **word,
        }
        for word in words
    ]
    return summary, word_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-words-csv", required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--use-deepseek", action="store_true")
    args = parser.parse_args()

    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row.get("ext", "").lower() == ".pdf"]

    summaries = []
    word_rows = []
    for row in rows:
        summary, words = analyze_row(row, args.max_pages, args.use_deepseek)
        summaries.append(summary)
        word_rows.extend(words)

    fields = list(summaries[0].keys()) if summaries else []
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(summaries)

    word_fields = ["document_id", "label", "doc_type", "page", "text", "x_min", "y_min", "x_max", "y_max"]
    with Path(args.out_words_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=word_fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(word_rows)

    summary = {}
    for record in summaries:
        key = (record["label"], record["doc_type"])
        bucket = summary.setdefault(str(key), {"count": 0, "business_risk_sum": 0, "textless": 0, "reason_counts": Counter()})
        bucket["count"] += 1
        bucket["business_risk_sum"] += int(record["business_risk_score"])
        bucket["textless"] += 1 if int(record["text_word_count"]) == 0 else 0
        for reason in filter(None, record["business_risk_reasons"].split("|")):
            bucket["reason_counts"][reason] += 1
    for bucket in summary.values():
        bucket["mean_business_risk_score"] = round(bucket["business_risk_sum"] / max(bucket["count"], 1), 2)
        bucket["reason_counts"] = dict(bucket["reason_counts"].most_common())
        del bucket["business_risk_sum"]
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
