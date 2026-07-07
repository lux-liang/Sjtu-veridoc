#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib import request


AMOUNT_RE = re.compile(r"(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"(20[0-9]{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12][0-9]|3[01])日?)")
ID_RE = re.compile(r"\b[0-9]{6}(?:19|20)?[0-9]{6,8}[0-9Xx]?\b")
ACCOUNT_RE = re.compile(r"\b[0-9]{12,24}\b")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def render_pdf(path: Path, out_dir: Path, dpi: int, max_pages: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"
    proc = run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", str(max_pages), str(path), str(prefix)])
    if proc.returncode != 0:
        return []
    return sorted(out_dir.glob("page-*.png"))


def tesseract_tsv(image: Path, lang: str) -> tuple[list[dict], str]:
    if not shutil.which("tesseract"):
        return [], "tesseract_unavailable"
    proc = run(["tesseract", str(image), "stdout", "-l", lang, "--psm", "6", "tsv"])
    if proc.returncode != 0:
        return [], f"tesseract_failed:{proc.stderr[:180]}"
    rows = []
    for row in csv.DictReader(proc.stdout.splitlines(), delimiter="\t"):
        text = safe_text(row.get("text", ""))
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1
        if conf < 0:
            continue
        rows.append(
            {
                "page": 0,
                "text": text,
                "conf": round(conf, 2),
                "x": int(float(row.get("left") or 0)),
                "y": int(float(row.get("top") or 0)),
                "w": int(float(row.get("width") or 0)),
                "h": int(float(row.get("height") or 0)),
                "line_num": row.get("line_num", ""),
                "block_num": row.get("block_num", ""),
            }
        )
    return rows, ""


def ocr_document(row: dict, render_root: Path, dpi: int, max_pages: int, lang: str) -> tuple[list[dict], str]:
    src = Path(row["path"])
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        return [], "missing_file"
    image_paths = []
    if row.get("ext", "").lower() == ".pdf":
        doc_dir = render_root / row.get("document_id", src.stem)
        image_paths = render_pdf(src, doc_dir, dpi, max_pages)
        if not image_paths:
            return [], "pdf_render_failed"
    elif row.get("ext", "").lower() in {".jpg", ".jpeg", ".png"}:
        image_paths = [src]
    all_words = []
    errors = []
    for page_idx, image in enumerate(image_paths, start=1):
        words, error = tesseract_tsv(image, lang)
        if error:
            errors.append(error)
        for word in words:
            word["page"] = page_idx
        all_words.extend(words)
    return all_words, "|".join(dict.fromkeys(errors))


def words_to_text(words: list[dict]) -> str:
    ordered = sorted(words, key=lambda item: (item["page"], item["y"], item["x"]))
    lines = []
    current = []
    last_key = None
    for word in ordered:
        key = (word["page"], round(word["y"] / 18))
        if last_key is not None and key != last_key and current:
            lines.append(" ".join(current))
            current = []
        current.append(word["text"])
        last_key = key
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def extract_fields(text: str) -> dict:
    amounts = []
    for value in AMOUNT_RE.findall(text):
        try:
            amounts.append(float(value.replace(",", "")))
        except ValueError:
            pass
    return {
        "amounts": amounts[:30],
        "dates": DATE_RE.findall(text)[:20],
        "id_numbers": ID_RE.findall(text)[:10],
        "account_numbers": ACCOUNT_RE.findall(text)[:10],
    }


def local_ocr_rules(doc_type: str, words: list[dict], text: str, fields: dict, error: str) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    if error:
        score += 8
        reasons.append(error.split(":", 1)[0])
    if not words:
        score += 20
        reasons.append("ocr_text_missing")
        return min(score, 100), reasons
    mean_conf = sum(float(w["conf"]) for w in words) / max(len(words), 1)
    low_conf_ratio = sum(1 for w in words if float(w["conf"]) < 45) / max(len(words), 1)
    if mean_conf < 45:
        score += 18
        reasons.append("ocr_low_mean_confidence")
    if low_conf_ratio > 0.35:
        score += 12
        reasons.append("ocr_many_low_confidence_words")
    if doc_type == "invoice" and len(fields["amounts"]) >= 3:
        a, b, c = fields["amounts"][:3]
        if abs((a + b) - c) > 0.1 and abs((a + c) - b) > 0.1 and abs((b + c) - a) > 0.1:
            score += 25
            reasons.append("ocr_invoice_amount_logic_suspicious")
    if doc_type == "bank_page" and len(fields["amounts"]) < 2:
        score += 8
        reasons.append("ocr_bank_amount_sequence_sparse")
    if doc_type == "contract" and not fields["dates"]:
        score += 8
        reasons.append("ocr_contract_date_missing")
    if doc_type == "credit_report" and not fields["id_numbers"]:
        score += 8
        reasons.append("ocr_credit_id_missing")
    return min(score, 100), reasons


def deepseek_verify(row: dict, ocr_text: str, fields: dict, local_reasons: list[str]) -> tuple[int, list[str], str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return 0, [], ""
    prompt = {
        "document_id": row.get("document_id"),
        "doc_type": row.get("doc_type"),
        "label": row.get("label"),
        "ocr_text": ocr_text[:6000],
        "regex_fields": fields,
        "local_reasons": local_reasons,
        "task": "基于 OCR 文本核验材料字段和业务逻辑。只输出 JSON：risk_score 0-100, reasons 字符串数组, extracted_fields 对象, review_note 中文一句话。不要编造 OCR 中没有的内容。",
    }
    payload = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": "你是材料真实性 OCR 核验助手，只能基于用户提供的 OCR 文本和字段进行规则核验。"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        score = int(float(parsed.get("risk_score") or 0))
        reasons = [safe_text(x) for x in parsed.get("reasons", []) if safe_text(str(x))]
        note = safe_text(parsed.get("review_note", ""))[:500]
        return max(0, min(100, score)), reasons[:12], note
    except Exception as exc:
        return 0, [f"deepseek_verify_failed:{type(exc).__name__}"], ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-words-csv", required=True)
    parser.add_argument("--render-dir", default="outputs/ocr_pages")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--lang", default="chi_sim+eng")
    parser.add_argument("--use-deepseek", action="store_true")
    parser.add_argument("--deepseek-limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    rows = [row for row in read_csv(Path(args.manifest)) if row.get("ext", "").lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    render_root = Path(args.render_dir)
    summaries = []
    word_rows = []
    deepseek_used = 0
    for row in rows:
        words, error = ocr_document(row, render_root, args.dpi, args.max_pages, args.lang)
        text = words_to_text(words)
        fields = extract_fields(text)
        local_score, local_reasons = local_ocr_rules(row.get("doc_type", ""), words, text, fields, error)
        deepseek_score, deepseek_reasons, deepseek_note = 0, [], ""
        should_call = args.use_deepseek and (args.deepseek_limit <= 0 or deepseek_used < args.deepseek_limit)
        if should_call:
            deepseek_score, deepseek_reasons, deepseek_note = deepseek_verify(row, text, fields, local_reasons)
            deepseek_used += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
        mean_conf = round(sum(float(w["conf"]) for w in words) / max(len(words), 1), 2) if words else 0
        score = min(100, max(local_score, deepseek_score))
        reasons = local_reasons + [f"deepseek:{r}" for r in deepseek_reasons]
        summaries.append(
            {
                **row,
                "ocr_word_count": len(words),
                "ocr_mean_confidence": mean_conf,
                "ocr_amount_count": len(fields["amounts"]),
                "ocr_date_count": len(fields["dates"]),
                "ocr_id_count": len(fields["id_numbers"]),
                "ocr_account_count": len(fields["account_numbers"]),
                "ocr_risk_score": score,
                "ocr_risk_reasons": "|".join(dict.fromkeys(reasons)),
                "ocr_text_preview": text[:1000],
                "ocr_fields_json": json.dumps(fields, ensure_ascii=False, sort_keys=True),
                "deepseek_review_note": deepseek_note,
                "ocr_error": error,
            }
        )
        for word in words:
            word_rows.append(
                {
                    "document_id": row.get("document_id", ""),
                    "label": row.get("label", ""),
                    "doc_type": row.get("doc_type", ""),
                    **word,
                }
            )

    fields = list(summaries[0].keys()) if summaries else []
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(summaries)
    word_fields = ["document_id", "label", "doc_type", "page", "text", "conf", "x", "y", "w", "h", "line_num", "block_num"]
    with Path(args.out_words_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=word_fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(word_rows)

    summary = {"count": len(summaries), "deepseek_used": deepseek_used, "by_label": {}, "top_reasons": Counter()}
    for item in summaries:
        bucket = summary["by_label"].setdefault(item.get("label", ""), {"count": 0, "mean_ocr_risk": 0, "textless": 0})
        bucket["count"] += 1
        bucket["mean_ocr_risk"] += int(item["ocr_risk_score"])
        bucket["textless"] += 1 if int(item["ocr_word_count"]) == 0 else 0
        for reason in filter(None, item["ocr_risk_reasons"].split("|")):
            summary["top_reasons"][reason] += 1
    for bucket in summary["by_label"].values():
        bucket["mean_ocr_risk"] = round(bucket["mean_ocr_risk"] / max(bucket["count"], 1), 2)
    summary["top_reasons"] = summary["top_reasons"].most_common(30)
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
