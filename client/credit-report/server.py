#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


WEB_ROOT = Path(__file__).resolve().parent
ORIGIN = os.environ.get("VERIDOC_ORIGIN", "http://127.0.0.1:3002").rstrip("/")
RESULTS_PATH = Path(os.environ.get("CREDIT_RULE_RESULTS", str(WEB_ROOT / "data" / "credit_rule_results.json")))
PUBLIC_STATUSES = ("fail", "possible", "manual", "pass")
_cache_lock = threading.Lock()
_cache_mtime = -1.0
_cache_payload: dict | None = None


def public_document_id(source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"CR-{digest}"


def load_results() -> dict:
    global _cache_mtime, _cache_payload
    stat = RESULTS_PATH.stat()
    with _cache_lock:
        if _cache_payload is None or stat.st_mtime != _cache_mtime:
            payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "credit-word-rules-v1":
                raise ValueError("unsupported credit-rule schema")
            _cache_payload = payload
            _cache_mtime = stat.st_mtime
        return _cache_payload


def public_rule(item: dict) -> dict:
    return {
        "rule_id": str(item.get("rule_id") or ""),
        "title": str(item.get("title") or ""),
        "status": str(item.get("status") or "manual"),
        "message": str(item.get("message") or ""),
        "word_level": str(item.get("word_level") or ""),
        "mode": str(item.get("mode") or ""),
    }


def public_row(document: dict) -> dict:
    source_id = str(document.get("source_id") or "")
    document_id = public_document_id(source_id)
    counts = document.get("rule_counts") or {}
    rules = [public_rule(item) for item in document.get("rule_results", [])]
    findings = [item for item in rules if item["status"] in {"fail", "possible", "manual"}]
    return {
        "document_id": document_id,
        "doc_type": "credit_report",
        "report_variant": str(document.get("report_variant") or "unknown"),
        "source_format": str(document.get("source_format") or "unknown"),
        "overall_status": str(document.get("overall_status") or "manual"),
        "rule_counts": {key: int(counts.get(key, 0) or 0) for key in ("fail", "possible", "manual", "pass", "not_applicable")},
        "findings": findings[:8],
        "rule_results": rules,
        "pdf_url": f"/api/pdf/{parse.quote(document_id)}" if document.get("has_pdf") else "",
    }


def documents() -> list[dict]:
    rows = [public_row(item) for item in load_results().get("documents", [])]
    order = {"fail": 0, "possible": 1, "manual": 2, "pass": 3}
    return sorted(rows, key=lambda row: (order.get(row["overall_status"], 9), -row["rule_counts"]["fail"], -row["rule_counts"]["possible"], row["document_id"]))


def source_id_for_public(document_id: str) -> str | None:
    for item in load_results().get("documents", []):
        source_id = str(item.get("source_id") or "")
        if public_document_id(source_id) == document_id and item.get("has_pdf"):
            return source_id
    return None


def dashboard_payload(rows: list[dict]) -> dict:
    status_counts = Counter(row["overall_status"] for row in rows)
    finding_counts = Counter(
        (item["rule_id"], item["title"], item["status"])
        for row in rows for item in row["findings"] if item["status"] in {"fail", "possible"}
    )
    queue = [row for row in rows if row["overall_status"] in {"fail", "possible"}]
    meta = load_results()
    return {
        "total": len(rows),
        "status_counts": {key: status_counts.get(key, 0) for key in PUBLIC_STATUSES},
        "top_findings": [
            {"rule_id": key[0], "label": key[1], "status": key[2], "count": count}
            for key, count in finding_counts.most_common(8)
        ],
        "attention_rows": queue[:12],
        "scope": "credit_report_word_rules_only",
        "schema_version": meta.get("schema_version"),
        "generated_at": meta.get("generated_at"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "VeriDocCreditClient/2.0"

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
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else ""))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        if not head_only:
            with target.open("rb") as stream:
                shutil.copyfileobj(stream, self.wfile)

    def do_HEAD(self) -> None:
        parsed = parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_response(200 if RESULTS_PATH.is_file() else 503)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_static(parsed.path, head_only=True)

    def proxy_pdf(self, document_id: str) -> None:
        source_id = source_id_for_public(document_id)
        if not source_id:
            self.send_error(404)
            return
        headers = {"Range": self.headers["Range"]} if self.headers.get("Range") else {}
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
                payload = load_results()
                self.send_json({"ok": True, "scope": "credit_report_word_rules_only", "documents": payload.get("document_count", 0), "schema_version": payload.get("schema_version")})
                return
            if parsed.path == "/api/dashboard":
                self.send_json(dashboard_payload(documents()))
                return
            if parsed.path == "/api/documents":
                query = parse.parse_qs(parsed.query)
                rows = documents()
                search = (query.get("search") or [""])[0].strip().lower()
                status = (query.get("status") or [""])[0]
                if search:
                    rows = [row for row in rows if search in (row["document_id"] + " " + " ".join(item["title"] + item["message"] for item in row["findings"])).lower()]
                if status in PUBLIC_STATUSES:
                    rows = [row for row in rows if row["overall_status"] == status]
                try:
                    limit = max(1, min(int((query.get("limit") or ["500"])[0]), 2000))
                except ValueError:
                    limit = 500
                summary_rows = [{key: value for key, value in row.items() if key != "rule_results"} for row in rows[:limit]]
                self.send_json({"rows": summary_rows, "count": len(rows), "scope": "credit_report_word_rules_only"})
                return
            if parsed.path == "/api/document":
                document_id = (parse.parse_qs(parsed.query).get("id") or [""])[0]
                row = next((row for row in documents() if row["document_id"] == document_id), None)
                self.send_json({"document": row} if row else {"error": "not_found"}, 200 if row else 404)
                return
            if parsed.path.startswith("/api/pdf/"):
                self.proxy_pdf(parse.unquote(parsed.path.rsplit("/", 1)[-1]))
                return
            self.send_static(parsed.path)
        except FileNotFoundError:
            self.send_json({"error": "credit_rule_results_missing"}, 503)
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "credit_rule_results_invalid"}, 503)
        except (error.URLError, TimeoutError):
            self.send_json({"error": "upstream_unavailable"}, 502)


def main() -> None:
    parser = argparse.ArgumentParser(description="Client-facing Word-rule credit-report dashboard")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3003")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"credit-report Word-rule client listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
