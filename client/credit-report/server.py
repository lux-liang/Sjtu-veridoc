#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


WEB_ROOT = Path(__file__).resolve().parent
ORIGIN = os.environ.get("VERIDOC_ORIGIN", "http://127.0.0.1:3002").rstrip("/")

EVIDENCE_RULES = [
    (re.compile(r"credit_report_number_date_mismatch|report.*date.*mismatch", re.I), "报告编号与报告时间不一致"),
    (re.compile(r"future_date|report_after_pdf_creation", re.I), "报告时间或日期逻辑需要复核"),
    (re.compile(r"credit_report_id_missing|ocr_credit_id_missing|birth_date", re.I), "身份信息完整性需要复核"),
    (re.compile(r"credit_report_date_missing", re.I), "报告时间信息需要复核"),
    (re.compile(r"producer|creator|pdf_version|incremental|embedded_script|xref|smask", re.I), "PDF 文档属性或结构需要复核"),
    (re.compile(r"font|text_layer|sparse_text|duplicated_text|repetition", re.I), "字体、文本层或版式一致性需要复核"),
    (re.compile(r"watermark|seal", re.I), "水印或页面标识一致性需要复核"),
    (re.compile(r"noise|edge|overlay|paste|image", re.I), "页面局部内容一致性需要复核"),
    (re.compile(r"account|overdue|default|liability|balance", re.I), "账户、违约或负债信息需要复核"),
    (re.compile(r"company|enterprise|social_credit|public_record", re.I), "法人主体或公共记录需要复核"),
]


def origin_json(path: str) -> dict:
    with request.urlopen(ORIGIN + path, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def risk_score(row: dict) -> int:
    value = row.get("combined_risk_score", row.get("object_risk_score", 0))
    try:
        return max(0, min(100, int(round(float(value or 0)))))
    except (TypeError, ValueError):
        return 0


def risk_status(score: int) -> str:
    if score >= 25:
        return "priority"
    if score >= 15:
        return "review"
    return "routine"


def public_evidence(raw_reasons: str) -> list[str]:
    results: list[str] = []
    for raw in filter(None, re.split(r"[|,]", raw_reasons or "")):
        if "marker" in raw.lower():
            continue
        label = next((label for pattern, label in EVIDENCE_RULES if pattern.search(raw)), "材料信息需要人工复核")
        if label not in results:
            results.append(label)
    return results[:5]


def public_document_id(document_id: str) -> str:
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"CR-{digest}"


def sanitize_row(row: dict) -> dict:
    score = risk_score(row)
    reasons = row.get("combined_risk_reasons") or row.get("object_risk_reasons") or ""
    source_id = str(row.get("document_id") or "")
    document_id = public_document_id(source_id)
    return {
        "document_id": document_id,
        "doc_type": "credit_report",
        "risk_score": score,
        "status": risk_status(score),
        "evidence": public_evidence(str(reasons)),
        "pdf_url": f"/api/pdf/{parse.quote(document_id)}" if row.get("pdf_url") else "",
    }


def raw_credit_rows() -> list[dict]:
    payload = origin_json("/api/documents?doc_type=credit_report&limit=2000")
    return [row for row in payload.get("rows", []) if row.get("doc_type") == "credit_report"]


def credit_rows() -> list[dict]:
    rows = [sanitize_row(row) for row in raw_credit_rows()]
    return sorted(rows, key=lambda row: (-row["risk_score"], row["document_id"]))


def dashboard_payload(rows: list[dict]) -> dict:
    status_counts = Counter(row["status"] for row in rows)
    evidence_counts = Counter(item for row in rows for item in row["evidence"])
    return {
        "total": len(rows),
        "status_counts": {key: status_counts.get(key, 0) for key in ("priority", "review", "routine")},
        "top_evidence": [{"label": label, "count": count} for label, count in evidence_counts.most_common(6)],
        "priority_rows": [row for row in rows if row["status"] == "priority"][:12],
        "scope": "credit_report_only",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "VeriDocCreditClient/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f'{self.address_string()} - {fmt % args}', flush=True)

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_static(self, path: str) -> None:
        target = WEB_ROOT / ("index.html" if path in {"", "/"} else path.lstrip("/"))
        try:
            target.resolve().relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else ""))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def do_HEAD(self) -> None:
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        target = WEB_ROOT / ("index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/"))
        try:
            target.resolve().relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else ""))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()

    def proxy_pdf(self, document_id: str) -> None:
        source_row = next(
            (
                row for row in raw_credit_rows()
                if public_document_id(str(row.get("document_id") or "")) == document_id and row.get("pdf_url")
            ),
            None,
        )
        if not source_row:
            self.send_error(404)
            return
        headers = {}
        if self.headers.get("Range"):
            headers["Range"] = self.headers["Range"]
        source_id = str(source_row.get("document_id") or "")
        upstream = request.Request(f"{ORIGIN}/api/pdf/{parse.quote(source_id)}", headers=headers)
        try:
            with request.urlopen(upstream, timeout=30) as response:
                self.send_response(response.status)
                for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition"):
                    if response.headers.get(key):
                        self.send_header(key, response.headers[key])
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
        except error.HTTPError as exc:
            self.send_error(exc.code)
        except error.URLError:
            self.send_error(502)

    def do_GET(self) -> None:
        parsed = parse.urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self.send_json({"ok": True, "scope": "credit_report_only"})
                return
            if parsed.path == "/api/dashboard":
                self.send_json(dashboard_payload(credit_rows()))
                return
            if parsed.path == "/api/documents":
                query = parse.parse_qs(parsed.query)
                rows = credit_rows()
                search = (query.get("search") or [""])[0].strip().lower()
                status = (query.get("status") or [""])[0]
                if search:
                    rows = [row for row in rows if search in (row["document_id"] + " " + " ".join(row["evidence"])).lower()]
                if status in {"priority", "review", "routine"}:
                    rows = [row for row in rows if row["status"] == status]
                try:
                    limit = max(1, min(int((query.get("limit") or ["500"])[0]), 2000))
                except ValueError:
                    limit = 500
                self.send_json({"rows": rows[:limit], "count": len(rows), "scope": "credit_report_only"})
                return
            if parsed.path == "/api/document":
                document_id = (parse.parse_qs(parsed.query).get("id") or [""])[0]
                row = next((row for row in credit_rows() if row["document_id"] == document_id), None)
                self.send_json({"document": row} if row else {"error": "not_found"}, 200 if row else 404)
                return
            if parsed.path.startswith("/api/pdf/"):
                self.proxy_pdf(parse.unquote(parsed.path.rsplit("/", 1)[-1]))
                return
            self.send_static(parsed.path)
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            self.send_json({"error": "upstream_unavailable"}, 502)


def main() -> None:
    parser = argparse.ArgumentParser(description="Client-facing credit-report-only VeriDoc dashboard")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3003")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"credit-report client listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
