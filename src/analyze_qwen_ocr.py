#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib import request


AMOUNT_RE = re.compile(r"(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"(20[0-9]{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12][0-9]|3[01])日?)")
ID_RE = re.compile(r"\b[0-9]{17}[0-9Xx]\b|\b[0-9]{15}\b")
ACCOUNT_RE = re.compile(r"\b[0-9]{12,24}\b")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def read_csv(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def render_first_page(path: Path, out_dir: Path, dpi: int) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "page"
    proc = run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(path), str(prefix)])
    pages = sorted(out_dir.glob("page-*.png"))
    if proc.returncode != 0 or not pages:
        return None
    return pages[0]


def encode_image(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def parse_fields(text: str) -> dict:
    amounts = []
    for value in AMOUNT_RE.findall(text or ""):
        try:
            amounts.append(float(value.replace(",", "")))
        except ValueError:
            pass
    return {
        "amounts": amounts[:30],
        "dates": DATE_RE.findall(text or "")[:20],
        "id_numbers": ID_RE.findall(text or "")[:10],
        "account_numbers": ACCOUNT_RE.findall(text or "")[:10],
    }


def parse_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {
        "ocr_text": text,
        "fields": {},
        "risk_score": 0,
        "reasons": ["qwen_returned_non_json_text"],
        "review_note": text[:800],
    }


def qwen_vision_ocr(image_path: Path, doc_type: str, model: str, timeout: int, max_tokens: int) -> dict:
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
    if not api_key:
        raise RuntimeError("missing DASHSCOPE_API_KEY or QWEN_API_KEY")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "你是材料真实性 OCR 核验助手。请直接识别图片中的文字并抽取字段。"
                            "只输出 JSON，字段为：ocr_text, fields, risk_score, reasons, review_note。"
                            f"文档类型：{doc_type}。risk_score 为 0-100，reasons 为字符串数组。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": encode_image(image_path)}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    req = request.Request(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return parse_json_object(content)


def risk_level(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 25:
        return "medium"
    if score > 0:
        return "low"
    return "clean"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--render-dir", default="data/qwen_ocr_pages")
    parser.add_argument("--model", default="qwen-vl-max")
    parser.add_argument("--dpi", type=int, default=160)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=3000)
    args = parser.parse_args()

    rows = [row for row in read_csv(Path(args.manifest)) if row.get("ext", "").lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    if args.limit > 0:
        rows = rows[: args.limit]
    records = []
    render_dir = Path(args.render_dir)
    for row in rows:
        src = Path(row["path"])
        if not src.is_absolute():
            src = Path.cwd() / src
        image = None
        error = ""
        if row.get("ext", "").lower() == ".pdf":
            image = render_first_page(src, render_dir / row["document_id"], args.dpi) if src.exists() else None
            if not image:
                error = "pdf_render_failed_or_missing"
        else:
            image = src if src.exists() else None
            if not image:
                error = "image_missing"
        result = {}
        if image and not error:
            try:
                result = qwen_vision_ocr(image, row.get("doc_type", ""), args.model, args.timeout, args.max_tokens)
            except Exception as exc:
                error = f"qwen_ocr_failed:{type(exc).__name__}:{str(exc)[:180]}"
        ocr_text = str(result.get("ocr_text") or "")
        fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
        regex_fields = parse_fields(ocr_text)
        score = int(float(result.get("risk_score") or 0)) if result else 0
        reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
        records.append(
            {
                **row,
                "qwen_model": args.model,
                "qwen_ocr_text": ocr_text[:3000],
                "qwen_fields_json": json.dumps(fields or regex_fields, ensure_ascii=False, sort_keys=True),
                "qwen_regex_fields_json": json.dumps(regex_fields, ensure_ascii=False, sort_keys=True),
                "qwen_risk_score": max(0, min(100, score)),
                "qwen_risk_level": risk_level(score),
                "qwen_risk_reasons": "|".join(str(item)[:120] for item in reasons),
                "qwen_review_note": str(result.get("review_note") or "")[:800],
                "qwen_error": error,
            }
        )
        if args.sleep > 0:
            time.sleep(args.sleep)

    fields = list(records[0].keys()) if records else []
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(records)
    summary = {"count": len(records), "errors": Counter(), "by_label": {}, "top_reasons": Counter()}
    for record in records:
        if record["qwen_error"]:
            summary["errors"][record["qwen_error"].split(":", 2)[0]] += 1
        bucket = summary["by_label"].setdefault(record.get("label", ""), {"count": 0, "mean_qwen_risk": 0})
        bucket["count"] += 1
        bucket["mean_qwen_risk"] += int(record["qwen_risk_score"])
        for reason in filter(None, record["qwen_risk_reasons"].split("|")):
            summary["top_reasons"][reason] += 1
    for bucket in summary["by_label"].values():
        bucket["mean_qwen_risk"] = round(bucket["mean_qwen_risk"] / max(bucket["count"], 1), 2)
    summary["errors"] = dict(summary["errors"])
    summary["top_reasons"] = summary["top_reasons"].most_common(30)
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
