#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib import request


AMOUNT_RE = re.compile(r"(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"(20[0-9]{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12][0-9]|3[01])日?)")
ID_RE = re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)")
ACCOUNT_RE = re.compile(r"(?<!\d)[0-9]{12,24}(?!\d)")
SOCIAL_CREDIT_RE = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])", re.I)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
MAX_OCR_PAGES = 200


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 124, "", f"{type(exc).__name__}")


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    csv.field_size_limit(16 * 1024 * 1024)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        clean_lines = (line.replace("\x00", "") for line in stream)
        return list(csv.DictReader(clean_lines))


def safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def redact_sensitive_text(value: str) -> str:
    value = ID_RE.sub("[证件号码已隐藏]", value or "")
    value = SOCIAL_CREDIT_RE.sub("[统一社会信用代码已隐藏]", value)
    value = ACCOUNT_RE.sub("[长数字字段已隐藏]", value)
    value = PHONE_RE.sub("[联系电话已隐藏]", value)
    value = EMAIL_RE.sub("[电子邮箱已隐藏]", value)
    value = re.sub(
        r"((?:姓名|企业名称|单位名称|地址|住址|通讯地址)\s*[:：]?\s*)[^\s|，,；;]{2,80}",
        r"\1[字段值已隐藏]",
        value,
    )
    return value


def write_private_gzip(root: Path, relative_name: str, value: str) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    target = root / relative_name
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as stream:
        stream.write(value)
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def pdf_page_count(path: Path) -> int:
    proc = run(["pdfinfo", str(path)], timeout=30)
    if proc.returncode != 0:
        return 0
    match = re.search(r"^Pages:\s*(\d+)\s*$", proc.stdout, re.M)
    return int(match.group(1)) if match else 0


def render_pdf_page(path: Path, out_dir: Path, dpi: int, page: int) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"page-{page:04d}"
    proc = run([
        "pdftoppm",
        "-r",
        str(dpi),
        "-png",
        "-singlefile",
        "-f",
        str(page),
        "-l",
        str(page),
        str(path),
        str(prefix),
    ], timeout=180)
    if proc.returncode != 0:
        prefix.with_suffix(".png").unlink(missing_ok=True)
        return None
    image = prefix.with_suffix(".png")
    return image if image.is_file() else None


def tesseract_tsv(image: Path, lang: str) -> tuple[list[dict], str]:
    if not shutil.which("tesseract"):
        return [], "tesseract_unavailable"
    proc = run(["tesseract", str(image), "stdout", "-l", lang, "--psm", "6", "tsv"], timeout=240)
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
                "par_num": row.get("par_num", ""),
            }
        )
    return rows, ""


def ocr_document(
    row: dict,
    render_root: Path,
    dpi: int,
    max_pages: int,
    lang: str,
    keep_rendered_pages: bool = False,
) -> tuple[list[dict], str, int, int, int, int]:
    src = Path(row["path"])
    if not src.is_absolute():
        src = Path.cwd() / src
    if not src.exists():
        return [], "missing_file", 0, 0, 0, 0
    all_words = []
    errors = []
    rendered_pages = 0
    successful_pages = 0
    if row.get("ext", "").lower() == ".pdf":
        document_ref = hashlib.sha256(str(row.get("document_id") or src).encode("utf-8")).hexdigest()[:20]
        doc_dir = render_root / document_ref
        total_pages = pdf_page_count(src)
        if total_pages <= 0:
            return [], "pdf_page_count_failed", 0, 0, 0, 0
        requested_pages = total_pages if max_pages <= 0 else min(total_pages, max_pages)
        if requested_pages > MAX_OCR_PAGES:
            requested_pages = MAX_OCR_PAGES
            errors.append("ocr_page_limit_capped")
        for page_idx in range(1, requested_pages + 1):
            image = render_pdf_page(src, doc_dir, dpi, page_idx)
            if not image:
                errors.append(f"pdf_render_failed_page_{page_idx}")
                continue
            rendered_pages += 1
            try:
                words, error = tesseract_tsv(image, lang)
                if error:
                    errors.append(error)
                else:
                    successful_pages += 1
                for word in words:
                    word["page"] = page_idx
                all_words.extend(words)
            finally:
                if not keep_rendered_pages:
                    image.unlink(missing_ok=True)
        if not keep_rendered_pages:
            try:
                doc_dir.rmdir()
            except OSError:
                pass
    elif row.get("ext", "").lower() in {".jpg", ".jpeg", ".png"}:
        total_pages = 1
        requested_pages = 1
        words, error = tesseract_tsv(src, lang)
        rendered_pages = 1
        if error:
            errors.append(error)
        else:
            successful_pages = 1
        for word in words:
            word["page"] = 1
        all_words.extend(words)
    else:
        return [], "unsupported_file_type", 0, 0, 0, 0
    return (
        all_words,
        "|".join(dict.fromkeys(errors)),
        rendered_pages,
        successful_pages,
        requested_pages,
        total_pages,
    )


def words_to_text(words: list[dict], page_count: int | None = None) -> str:
    grouped: dict[int, dict[tuple, list[dict]]] = {}
    for word in words:
        page = int(word.get("page") or 1)
        block = str(word.get("block_num") or "")
        paragraph = str(word.get("par_num") or "")
        line = str(word.get("line_num") or "")
        if line:
            key = ("tsv", block, paragraph, line)
        else:
            key = ("geometry", round(float(word.get("y") or 0) / 18))
        grouped.setdefault(page, {}).setdefault(key, []).append(word)

    if page_count is None:
        page_count = max(grouped, default=0)
    pages = []
    for page in range(1, max(0, page_count) + 1):
        line_groups = list(grouped.get(page, {}).values())
        line_groups.sort(
            key=lambda items: (
                min(float(item.get("y") or 0) for item in items),
                min(float(item.get("x") or 0) for item in items),
            )
        )
        lines = []
        for items in line_groups:
            ordered = sorted(items, key=lambda item: float(item.get("x") or 0))
            lines.append(" ".join(str(item.get("text") or "") for item in ordered).strip())
        pages.append("\n".join(line for line in lines if line))
    return "\n\f\n".join(pages)


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
        reasons.append("ocr_credit_identity_unresolved")
    return min(score, 100), reasons


def deepseek_verify(row: dict, ocr_text: str, fields: dict, local_reasons: list[str]) -> tuple[int, list[str], str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return 0, [], ""
    prompt = {
        "doc_type": row.get("doc_type"),
        "ocr_text": redact_sensitive_text(ocr_text)[:6000],
        "field_counts": {
            "amounts": len(fields.get("amounts", [])),
            "dates": len(fields.get("dates", [])),
            "id_numbers": len(fields.get("id_numbers", [])),
            "account_numbers": len(fields.get("account_numbers", [])),
        },
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
        reasons = [redact_sensitive_text(safe_text(x)) for x in parsed.get("reasons", []) if safe_text(str(x))]
        note = redact_sensitive_text(safe_text(parsed.get("review_note", "")))[:500]
        return max(0, min(100, score)), reasons[:12], note
    except Exception as exc:
        return 0, [f"deepseek_verify_failed:{type(exc).__name__}"], ""


def _main_impl() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-words-csv", required=True)
    parser.add_argument("--out-text-dir")
    parser.add_argument("--render-dir", default="outputs/ocr_pages")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument(
        "--credit-max-pages",
        type=int,
        default=None,
        help="credit-report page limit; inherits --max-pages when omitted, 0 processes all pages",
    )
    parser.add_argument("--lang", default="chi_sim+eng")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel local OCR documents; 1-4, cloud review requires 1",
    )
    parser.add_argument("--keep-rendered-pages", action="store_true")
    parser.add_argument("--use-deepseek", action="store_true")
    parser.add_argument(
        "--confirm-external-data-transfer",
        action="store_true",
        help="required with --use-deepseek to acknowledge document text leaves the server",
    )
    parser.add_argument("--deepseek-limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    if args.use_deepseek and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        parser.error("--use-deepseek requires DEEPSEEK_API_KEY")
    if args.use_deepseek and not args.confirm_external_data_transfer:
        parser.error("--use-deepseek requires --confirm-external-data-transfer")
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be between 1 and 4")
    if args.use_deepseek and args.workers != 1:
        parser.error("--use-deepseek requires --workers 1")

    rows = [row for row in read_csv(Path(args.manifest)) if row.get("ext", "").lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    document_ids = [str(row.get("document_id") or "").strip() for row in rows]
    if any(not document_id for document_id in document_ids):
        parser.error("OCR manifest contains a missing document_id")
    if len(document_ids) != len(set(document_ids)):
        parser.error("OCR manifest contains duplicate document_id values")
    render_root = Path(args.render_dir)
    if args.keep_rendered_pages:
        render_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(render_root).free < 5 * 1024**3:
            parser.error("--keep-rendered-pages requires at least 5 GiB free space")
    out_csv_path = Path(args.out_csv)
    out_json_path = Path(args.out_json)
    text_root = Path(args.out_text_dir) if args.out_text_dir else out_csv_path.with_name(out_csv_path.stem + "_text")
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}"
    indexed_summaries: list[tuple[int, dict]] = []
    deepseek_used = 0
    word_fields = ["run_id", "document_id", "label", "doc_type", "page", "text", "conf", "x", "y", "w", "h", "line_num", "block_num", "par_num"]
    words_path = Path(args.out_words_csv)
    words_path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(words_path.parent).free < 1024**3:
        parser.error("OCR outputs require at least 1 GiB free space")
    words_temporary = words_path.with_suffix(words_path.suffix + ".tmp")
    words_temporary.unlink(missing_ok=True)

    def process_row(row_index: int, row: dict) -> dict:
        nonlocal deepseek_used
        if shutil.disk_usage(words_path.parent).free < 512 * 1024**2:
            raise RuntimeError("ocr_disk_space_low")
        page_limit = (
            args.credit_max_pages
            if row.get("doc_type") == "credit_report"
            and args.credit_max_pages is not None
            else args.max_pages
        )
        document_ref = hashlib.sha256(
            str(row.get("document_id") or row_index).encode("utf-8")
        ).hexdigest()[:20]
        try:
            (
                words,
                error,
                rendered_pages,
                successful_pages,
                requested_pages,
                total_pages,
            ) = ocr_document(
                row,
                render_root,
                args.dpi,
                page_limit,
                args.lang,
                args.keep_rendered_pages,
            )
        except Exception as exc:
            words = []
            error = f"ocr_document_failed:{type(exc).__name__}"
            rendered_pages = successful_pages = requested_pages = total_pages = 0
        text = words_to_text(words, requested_pages)
        relative_text_file = f"{run_id}/{document_ref}.txt.gz"
        write_private_gzip(text_root, relative_text_file, text)
        extracted = extract_fields(text)
        local_score, local_reasons = local_ocr_rules(
            row.get("doc_type", ""), words, text, extracted, error
        )
        deepseek_score, deepseek_reasons, deepseek_note = 0, [], ""
        should_call = args.use_deepseek and (
            args.deepseek_limit <= 0 or deepseek_used < args.deepseek_limit
        )
        if should_call:
            deepseek_score, deepseek_reasons, deepseek_note = deepseek_verify(
                row, text, extracted, local_reasons
            )
            deepseek_used += 1
            if args.sleep > 0:
                time.sleep(args.sleep)
        mean_conf = (
            round(
                sum(float(word["conf"]) for word in words) / max(len(words), 1),
                2,
            )
            if words
            else 0
        )
        score = min(100, max(local_score, deepseek_score))
        reasons = local_reasons + [f"deepseek:{item}" for item in deepseek_reasons]
        requested_complete = bool(
            requested_pages > 0
            and successful_pages == requested_pages
            and not error
        )
        summary_row = {
            **row,
            "ocr_run_id": run_id,
            "ocr_rendered_pages": rendered_pages,
            "ocr_page_count": successful_pages,
            "ocr_requested_pages": requested_pages,
            "ocr_total_pages": total_pages,
            "ocr_requested_complete": int(requested_complete),
            "ocr_complete": int(
                requested_complete and requested_pages == total_pages
            ),
            "ocr_word_count": len(words),
            "ocr_mean_confidence": mean_conf,
            "ocr_amount_count": len(extracted["amounts"]),
            "ocr_date_count": len(extracted["dates"]),
            "ocr_id_count": len(extracted["id_numbers"]),
            "ocr_account_count": len(extracted["account_numbers"]),
            "ocr_risk_score": score,
            "ocr_risk_reasons": "|".join(dict.fromkeys(reasons)),
            "ocr_text_preview": safe_text(redact_sensitive_text(text))[:1000],
            "ocr_text_file": relative_text_file,
            "ocr_field_counts_json": json.dumps(
                {
                    "amount_count": len(extracted["amounts"]),
                    "date_count": len(extracted["dates"]),
                    "id_count": len(extracted["id_numbers"]),
                    "account_count": len(extracted["account_numbers"]),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "deepseek_review_note": deepseek_note,
            "ocr_error": error,
        }
        return {
            "row_index": row_index,
            "row": row,
            "document_ref": document_ref,
            "words": words,
            "summary": summary_row,
            "successful_pages": successful_pages,
            "requested_pages": requested_pages,
            "total_pages": total_pages,
            "error": error,
        }

    try:
        word_stream_context = (
            gzip.open(words_temporary, "wt", encoding="utf-8", newline="", compresslevel=6)
            if words_path.suffix.lower() == ".gz"
            else words_temporary.open("w", encoding="utf-8", newline="")
        )
        with word_stream_context as word_stream:
            word_writer = csv.DictWriter(word_stream, fieldnames=word_fields, quoting=csv.QUOTE_ALL, escapechar="\\")
            word_writer.writeheader()

            completed_count = 0

            def write_result(result: dict) -> None:
                nonlocal completed_count
                completed_count += 1
                row = result["row"]
                words = result["words"]
                indexed_summaries.append((result["row_index"], result["summary"]))
                for word in words:
                    word_writer.writerow(
                        {
                            "run_id": run_id,
                            "document_id": row.get("document_id", ""),
                            "label": row.get("label", ""),
                            "doc_type": row.get("doc_type", ""),
                            **word,
                        }
                    )
                word_stream.flush()
                print(
                    f"[{completed_count}/{len(rows)}] {result['document_ref']}: "
                    f"pages={result['successful_pages']}/{result['requested_pages']}/{result['total_pages']} "
                    f"words={len(words)} error={result['error'] or '-'}",
                    flush=True,
                )

            if args.workers == 1:
                for row_index, row in enumerate(rows, 1):
                    write_result(process_row(row_index, row))
            else:
                row_iterator = iter(enumerate(rows, 1))
                with ThreadPoolExecutor(
                    max_workers=args.workers,
                    thread_name_prefix="credit-ocr",
                ) as executor:
                    pending = {}
                    for _ in range(min(args.workers, len(rows))):
                        row_index, row = next(row_iterator)
                        future = executor.submit(process_row, row_index, row)
                        pending[future] = row_index
                    while pending:
                        done, _ = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            pending.pop(future)
                            write_result(future.result())
                            try:
                                row_index, row = next(row_iterator)
                            except StopIteration:
                                continue
                            next_future = executor.submit(process_row, row_index, row)
                            pending[next_future] = row_index
            word_stream.flush()
            try:
                os.fsync(word_stream.fileno())
            except (AttributeError, OSError):
                pass
        os.chmod(words_temporary, 0o600)
    except Exception:
        words_temporary.unlink(missing_ok=True)
        shutil.rmtree(text_root / run_id, ignore_errors=True)
        raise

    summaries = [
        summary for _, summary in sorted(indexed_summaries, key=lambda item: item[0])
    ]
    fields = list(summaries[0].keys()) if summaries else []
    out_csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_temporary = out_csv_path.with_suffix(out_csv_path.suffix + ".tmp")
    json_temporary = out_json_path.with_suffix(out_json_path.suffix + ".tmp")
    try:
        with csv_temporary.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
            writer.writeheader()
            writer.writerows(summaries)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(csv_temporary, 0o600)
        summary = {"count": len(summaries), "run_id": run_id, "deepseek_used": deepseek_used, "by_label": {}, "top_reasons": Counter()}
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
        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        json_temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(json_temporary, 0o600)
        csv_temporary.replace(out_csv_path)
        json_temporary.replace(out_json_path)
        words_temporary.replace(words_path)
    except Exception:
        csv_temporary.unlink(missing_ok=True)
        json_temporary.unlink(missing_ok=True)
        words_temporary.unlink(missing_ok=True)
        shutil.rmtree(text_root / run_id, ignore_errors=True)
        raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    previous_umask = os.umask(0o077)
    try:
        _main_impl()
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
