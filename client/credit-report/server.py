#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


WEB_ROOT = Path(__file__).resolve().parent
ORIGIN = os.environ.get("VERIDOC_ORIGIN", "http://127.0.0.1:3002").rstrip("/")
RESULTS_PATH = Path(os.environ.get("CREDIT_RULE_RESULTS", str(WEB_ROOT / "data" / "credit_rule_results.json")))
ALLOW_PDF_PREVIEW = os.environ.get("ALLOW_PDF_PREVIEW", "0").strip() == "1"

RULE_STATUSES = ("fail", "possible", "manual", "pass", "not_applicable")
RISK_CONCLUSIONS = ("fail", "possible", "no_automatic_anomaly", "undetermined")
PUBLIC_SCOPE = "credit_report_only"
WORD_RULE_IDS = {
    "G1", "G2", "G3", "G4",
    "P1A", "P1B", "P1C", "P1D", "P2",
    "O1",
    "H1", "H2", "H3", "H4", "H5", "H6",
    "C1", "C2", "C3A", "C3B", "C3C", "C3D",
}
SUPPORT_RULE_IDS = {"A0", "A1", "A2", "A3"}
ID_NUMBER_RE = re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SOCIAL_CREDIT_RE = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])", re.I)
LONG_ACCOUNT_RE = re.compile(r"(?<!\d)\d{16,32}(?!\d)")
LABELED_PRIVATE_VALUE_RE = re.compile(
    r"((?:姓名|企业名称|单位名称|证件号码|身份证号码|统一社会信用代码|"
    r"中征码|账号|账户|银行卡号|卡号|手机号|联系电话|电子邮箱|地址|住址)"
    r")(?:\s*[:：]\s*|\s+)[^\s|，,；;]{2,80}",
    re.I,
)
SECURITY_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; object-src 'none'; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}

_cache_lock = threading.Lock()
_cache_mtime = -1.0
_cache_payload: dict | None = None


def public_document_id(source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"CR-{digest}"


def validate_document_payload(document: dict) -> None:
    if document.get("analysis_complete") is not True:
        raise ValueError("incomplete document result")
    rules = document.get("rule_results")
    if not isinstance(rules, list) or not all(isinstance(item, dict) for item in rules):
        raise ValueError("invalid rule results")
    ids = [str(item.get("rule_id") or "") for item in rules]
    numbered = Counter(item for item in ids if item in WORD_RULE_IDS)
    support = Counter(item for item in ids if item in SUPPORT_RULE_IDS)
    unknown = set(ids) - WORD_RULE_IDS - SUPPORT_RULE_IDS
    if unknown or set(numbered) != WORD_RULE_IDS or any(value != 1 for value in numbered.values()):
        raise ValueError("invalid numbered rule set")
    if any(value != 1 for value in support.values()):
        raise ValueError("duplicate material requirement")
    expected_counts = Counter(
        str(item.get("status") or "manual")
        for item in rules
        if str(item.get("rule_id") or "") in WORD_RULE_IDS
    )
    supplied_counts = document.get("rule_counts") or {}
    if any(
        int(supplied_counts.get(key, 0) or 0) != expected_counts.get(key, 0)
        for key in RULE_STATUSES
    ):
        raise ValueError("rule count mismatch")


def load_results() -> dict:
    global _cache_mtime, _cache_payload
    stat = RESULTS_PATH.stat()
    with _cache_lock:
        if _cache_payload is None or stat.st_mtime != _cache_mtime:
            payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "credit-word-rules-v1":
                raise ValueError("unsupported result format")
            if int(payload.get("word_rule_count", 0) or 0) != len(WORD_RULE_IDS):
                raise ValueError("unexpected credit rule count")
            if payload.get("synthetic_excluded") is not True:
                raise ValueError("client results must exclude synthetic documents")
            if not isinstance(payload.get("documents"), list):
                raise ValueError("invalid document list")
            if int(payload.get("document_count", -1) or 0) != len(payload["documents"]):
                raise ValueError("document count mismatch")
            for document in payload["documents"]:
                if not isinstance(document, dict):
                    raise ValueError("invalid document result")
                validate_document_payload(document)
            _cache_payload = payload
            _cache_mtime = stat.st_mtime
        return _cache_payload


def sanitize_public_text(value: object) -> str:
    text = str(value or "")
    replacements = (
        ("Word图示基线", "规则基准"),
        ("Word基线", "规则基准"),
        ("Word要求", "核验规则要求"),
        ("Word规则", "核验规则"),
        ("OCR需人工确认", "需结合原件人工确认"),
        ("OCR需确认", "需结合原件确认"),
        ("OCR", "原件信息"),
        ("未被完整稳定提取", "未能完整确认"),
        ("未被稳定识别", "未能稳定确认"),
        ("无法稳定解析", "无法稳定确认"),
        ("被稳定识别", "已稳定确认"),
        ("提取", "确认"),
    )
    for source, target in replacements:
        text = re.sub(re.escape(source), target, text, flags=re.IGNORECASE)
    text = ID_NUMBER_RE.sub("[证件号码已隐藏]", text)
    text = SOCIAL_CREDIT_RE.sub("[统一社会信用代码已隐藏]", text)
    text = LONG_ACCOUNT_RE.sub("[账号已隐藏]", text)
    text = PHONE_RE.sub("[联系电话已隐藏]", text)
    text = EMAIL_RE.sub("[电子邮箱已隐藏]", text)
    text = LABELED_PRIVATE_VALUE_RE.sub(r"\1：[字段值已隐藏]", text)
    return text


def review_action(status: str) -> str:
    return {
        "fail": "请结合报告原件复核该项明确差异。",
        "possible": "请核对报告原件及相关属性后进一步确认。",
        "manual": "请结合报告原件或补充材料完成确认。",
        "pass": "该项已完成核验，未发现规则偏离。",
        "not_applicable": "该规则不适用于当前报告类型。",
    }.get(status, "请结合报告原件进一步确认。")


def public_evidence(item: dict, status: str) -> dict:
    source = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    return {
        "expected": sanitize_public_text(source.get("expected")),
        "observed": sanitize_public_text(source.get("observed")),
        "source_hint": sanitize_public_text(source.get("source_hint")),
        "review_action": sanitize_public_text(source.get("review_action"))
        or review_action(status),
    }


def public_rule(item: dict) -> dict:
    status = str(item.get("status") or "manual")
    if status not in RULE_STATUSES:
        status = "manual"
    return {
        "rule_id": sanitize_public_text(item.get("rule_id")),
        "title": sanitize_public_text(item.get("title")),
        "status": status,
        "message": sanitize_public_text(item.get("message")),
        "rule_level": sanitize_public_text(item.get("word_level")),
        "evidence": public_evidence(item, status),
    }


def public_material_requirement(item: dict) -> dict:
    public = public_rule(item)
    return {
        "title": public["title"],
        "status": public["status"],
        "message": public["message"],
        "rule_level": public["rule_level"],
        "evidence": public["evidence"],
    }


def normalized_rule_counts(document: dict, rules: list[dict]) -> dict:
    counted = Counter(item["status"] for item in rules)
    return {key: counted.get(key, 0) for key in RULE_STATUSES}


def risk_conclusion(counts: dict) -> str:
    if counts.get("fail", 0) > 0:
        return "fail"
    if counts.get("possible", 0) > 0:
        return "possible"
    if counts.get("pass", 0) > 0:
        return "no_automatic_anomaly"
    return "undetermined"


def primary_finding(rules: list[dict]) -> dict | None:
    priority = {"fail": 0, "possible": 1, "manual": 2}
    candidates = [item for item in rules if item["status"] in priority]
    if not candidates:
        return None
    return min(candidates, key=lambda item: priority[item["status"]])


def public_row(document: dict) -> dict:
    source_id = str(document.get("source_id") or "")
    document_id = public_document_id(source_id)
    source_rules = document.get("rule_results", [])
    rules = [
        public_rule(item)
        for item in source_rules
        if str(item.get("rule_id") or "") in WORD_RULE_IDS
    ]
    material_requirements = [
        public_material_requirement(item)
        for item in source_rules
        if str(item.get("rule_id") or "") in SUPPORT_RULE_IDS
    ]
    counts = normalized_rule_counts(document, rules)
    review_required = (
        counts["manual"] > 0
        or any(item["status"] == "manual" for item in material_requirements)
    )
    row = {
        "document_id": document_id,
        "report_variant": str(document.get("report_variant") or "unknown"),
        "source_format": str(document.get("source_format") or "unknown"),
        "risk_conclusion": risk_conclusion(counts),
        "manual_review_required": review_required,
        "rule_status_counts": counts,
        "material_requirement_count": len(material_requirements),
        "primary_finding": primary_finding(rules),
        "rule_results": rules,
        "material_requirements": material_requirements,
    }
    if ALLOW_PDF_PREVIEW and document.get("has_pdf"):
        row["pdf_url"] = f"/api/pdf/{parse.quote(document_id)}"
    return row


def summary_row(row: dict) -> dict:
    detail_fields = {"rule_results", "material_requirements"}
    return {key: value for key, value in row.items() if key not in detail_fields}


def documents() -> list[dict]:
    rows = [public_row(item) for item in load_results().get("documents", [])]
    order = {"fail": 0, "possible": 1, "undetermined": 2, "no_automatic_anomaly": 3}
    return sorted(
        rows,
        key=lambda row: (
            order.get(row["risk_conclusion"], 9),
            -row["rule_status_counts"]["fail"],
            -row["rule_status_counts"]["possible"],
            row["document_id"],
        ),
    )


def source_id_for_public(document_id: str) -> str | None:
    for item in load_results().get("documents", []):
        source_id = str(item.get("source_id") or "")
        if public_document_id(source_id) == document_id and item.get("has_pdf"):
            return source_id
    return None


def dashboard_payload(rows: list[dict]) -> dict:
    conclusion_counts = Counter(row["risk_conclusion"] for row in rows)
    rule_counts = Counter()
    finding_counts = Counter()
    source_formats = Counter()
    report_variants = Counter()
    for row in rows:
        rule_counts.update(row["rule_status_counts"])
        source_formats[row["source_format"]] += 1
        report_variants[row["report_variant"]] += 1
        for item in row["rule_results"]:
            if item["status"] in {"fail", "possible"}:
                finding_counts[(item["rule_id"], item["title"], item["status"])] += 1

    attention = [
        summary_row(row)
        for row in rows
        if row["risk_conclusion"] in {"fail", "possible"}
    ][:12]
    return {
        "total": len(rows),
        "risk_conclusion_counts": {
            key: conclusion_counts.get(key, 0) for key in RISK_CONCLUSIONS
        },
        "manual_review_required_count": sum(row["manual_review_required"] for row in rows),
        "rule_status_counts": {key: rule_counts.get(key, 0) for key in RULE_STATUSES},
        "material_counts": {
            "source_formats": dict(source_formats),
            "report_variants": dict(report_variants),
        },
        "top_findings": [
            {"rule_id": key[0], "label": key[1], "status": key[2], "count": count}
            for key, count in finding_counts.most_common(8)
        ],
        "attention_rows": attention,
        "rule_count": int(load_results().get("word_rule_count", 0) or 0),
        "generated_at": load_results().get("generated_at"),
        "scope": PUBLIC_SCOPE,
    }


def searchable_text(row: dict) -> str:
    applicable_rules = [
        item for item in row["rule_results"] if item["status"] != "not_applicable"
    ]
    values = [row["document_id"], row["report_variant"], row["source_format"]]
    for item in applicable_rules:
        values.extend(
            [item["rule_id"], item["title"], item["message"], item["rule_level"]]
            + list(item["evidence"].values())
        )
    return " ".join(values).lower()


def filter_documents(rows: list[dict], query: dict[str, list[str]]) -> list[dict]:
    search = (query.get("search") or [""])[0].strip().lower()
    conclusion = (query.get("conclusion") or [""])[0]
    source_format = (query.get("source_format") or [""])[0]
    report_variant = (query.get("report_variant") or [""])[0]
    review = (query.get("review") or [""])[0]

    if search:
        rows = [row for row in rows if search in searchable_text(row)]
    if conclusion in RISK_CONCLUSIONS:
        rows = [row for row in rows if row["risk_conclusion"] == conclusion]
    if source_format:
        rows = [row for row in rows if row["source_format"] == source_format]
    if report_variant:
        rows = [row for row in rows if row["report_variant"] == report_variant]
    if review == "required":
        rows = [row for row in rows if row["manual_review_required"]]
    elif review == "not_required":
        rows = [row for row in rows if not row["manual_review_required"]]
    return rows


def parse_positive_int(value: str, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def paginate_rows(rows: list[dict], page: int, page_size: int) -> dict:
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(max(1, page), total_pages)
    start = (current_page - 1) * page_size
    return {
        "rows": [summary_row(row) for row in rows[start:start + page_size]],
        "total": total,
        "page": current_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "scope": PUBLIC_SCOPE,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VeriDocClient"
    sys_version = ""

    def version_string(self) -> str:
        return self.server_version

    def end_headers(self) -> None:
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        print(f'{self.address_string()} - {fmt % args}', flush=True)

    def send_json(self, payload: dict, status: int = 200, head_only: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def static_target(self, path: str) -> Path | None:
        target = WEB_ROOT / ("index.html" if path in {"", "/"} else path.lstrip("/"))
        try:
            target.resolve().relative_to(WEB_ROOT.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None

    def send_static(self, path: str, head_only: bool = False) -> None:
        target = self.static_target(path)
        if not target:
            self.send_json({"error": "not_found"}, 404, head_only=head_only)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        if not head_only:
            with target.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)

    def do_HEAD(self) -> None:
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/health":
            try:
                payload = load_results()
                self.send_json(
                    {
                        "ok": True,
                        "scope": PUBLIC_SCOPE,
                        "documents": int(payload.get("document_count", 0) or 0),
                    },
                    head_only=True,
                )
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                self.send_json({"ok": False}, 503, head_only=True)
            return
        self.send_static(parsed.path, head_only=True)

    def proxy_pdf(self, document_id: str) -> None:
        if not ALLOW_PDF_PREVIEW:
            self.send_json({"error": "not_available"}, 404)
            return
        source_id = source_id_for_public(document_id)
        if not source_id:
            self.send_json({"error": "not_found"}, 404)
            return
        headers = {"Range": self.headers["Range"]} if self.headers.get("Range") else {}
        upstream = request.Request(
            f"{ORIGIN}/api/pdf/{parse.quote(source_id)}", headers=headers
        )
        try:
            with request.urlopen(upstream, timeout=30) as response:
                self.send_response(response.status)
                for key in (
                    "Content-Type",
                    "Content-Length",
                    "Content-Range",
                    "Accept-Ranges",
                    "Content-Disposition",
                ):
                    if response.headers.get(key):
                        self.send_header(key, response.headers[key])
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
        except error.HTTPError as exc:
            self.send_json({"error": "upstream_error"}, exc.code)
        except (error.URLError, TimeoutError):
            self.send_json({"error": "upstream_unavailable"}, 502)

    def do_GET(self) -> None:
        parsed = parse.urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                payload = load_results()
                self.send_json(
                    {
                        "ok": True,
                        "scope": PUBLIC_SCOPE,
                        "documents": int(payload.get("document_count", 0) or 0),
                        "generated_at": payload.get("generated_at"),
                    }
                )
                return
            if parsed.path == "/api/dashboard":
                self.send_json(dashboard_payload(documents()))
                return
            if parsed.path == "/api/documents":
                query = parse.parse_qs(parsed.query)
                rows = filter_documents(documents(), query)
                page = parse_positive_int((query.get("page") or ["1"])[0], 1, 100000)
                page_size = parse_positive_int(
                    (query.get("page_size") or ["50"])[0], 50, 100
                )
                self.send_json(paginate_rows(rows, page, page_size))
                return
            if parsed.path == "/api/document":
                document_id = (parse.parse_qs(parsed.query).get("id") or [""])[0]
                row = next(
                    (row for row in documents() if row["document_id"] == document_id),
                    None,
                )
                self.send_json(
                    {"document": row} if row else {"error": "not_found"},
                    200 if row else 404,
                )
                return
            if parsed.path.startswith("/api/pdf/"):
                self.proxy_pdf(parse.unquote(parsed.path.rsplit("/", 1)[-1]))
                return
            self.send_static(parsed.path)
        except FileNotFoundError:
            self.send_json({"error": "results_missing"}, 503)
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "results_invalid"}, 503)
        except (error.URLError, TimeoutError):
            self.send_json({"error": "upstream_unavailable"}, 502)


def main() -> None:
    parser = argparse.ArgumentParser(description="Client-facing credit-report dashboard")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3003")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"credit-report client listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
