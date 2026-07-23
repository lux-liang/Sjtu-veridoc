import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "client" / "credit-report" / "server.py"
SPEC = importlib.util.spec_from_file_location("credit_client_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(server)


class CreditClientServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_pdf_setting = server.ALLOW_PDF_PREVIEW
        server.ALLOW_PDF_PREVIEW = False
        server.RESULTS_PATH = Path(self.temp.name) / "results.json"
        server._cache_payload = None
        server._cache_mtime = -1.0
        payload = {
            "schema_version": "credit-word-rules-v1",
            "generated_at": "2026-07-23T00:00:00+00:00",
            "word_rule_count": 22,
            "synthetic_excluded": True,
            "document_count": 4,
            "documents": [
                {
                    "source_id": "fake_001",
                    "source_format": "original_electronic",
                    "report_variant": "online_personal",
                    "overall_status": "fail",
                    "rule_counts": {
                        "fail": 1,
                        "possible": 0,
                        "manual": 1,
                        "pass": 2,
                        "not_applicable": 3,
                    },
                    "rule_results": [
                        {
                            "rule_id": "G1",
                            "title": "编号时间",
                            "status": "fail",
                            "message": "OCR读取值与Word规则不一致",
                            "word_level": "提示造假",
                            "mode": "automatic",
                            "evidence": {
                                "expected": "报告编号时间应与报告时间一致",
                                "observed": "编号时间与报告时间不一致",
                                "source_hint": "报告首页",
                                "review_action": "请核对首页的编号和报告时间。",
                                "debug_trace": "不得下发",
                            },
                        },
                        {
                            "rule_id": "G3",
                            "title": "水印",
                            "status": "manual",
                            "message": "需模板确认",
                            "word_level": "提示造假",
                            "mode": "visual_template",
                        },
                    ],
                    "has_pdf": True,
                },
                {
                    "source_id": "possible_002",
                    "source_format": "original_electronic",
                    "report_variant": "leasing_enterprise",
                    "overall_status": "possible",
                    "rule_counts": {
                        "fail": 0,
                        "possible": 1,
                        "manual": 10,
                        "pass": 1,
                        "not_applicable": 7,
                    },
                    "rule_results": [
                        {
                            "rule_id": rule_id,
                            "title": f"规则{rule_id}",
                            "status": "manual",
                            "message": "需结合原件确认",
                            "word_level": "提示造假",
                            "mode": "table_parser",
                        }
                        for rule_id in (
                            "G2",
                            "G3",
                            "G4",
                            "C1",
                            "C2",
                            "C3A",
                            "C3B",
                            "C3C",
                            "C3D",
                            "C3E",
                        )
                    ]
                    + [
                        {
                            "rule_id": "P1A",
                            "title": "文件时间属性",
                            "status": "possible",
                            "message": "创建时间与修改时间不一致",
                            "word_level": "有造假可能",
                            "mode": "automatic",
                        },
                        {
                            "rule_id": "P1C",
                            "title": "文件版本",
                            "status": "pass",
                            "message": "符合规则要求",
                            "word_level": "有造假可能",
                            "mode": "automatic",
                        },
                    ],
                    "has_pdf": True,
                },
                {
                    "source_id": "clear_003",
                    "source_format": "original_electronic",
                    "report_variant": "pboc_print_personal",
                    "overall_status": "manual",
                    "rule_counts": {
                        "fail": 0,
                        "possible": 0,
                        "manual": 1,
                        "pass": 2,
                        "not_applicable": 4,
                    },
                    "rule_results": [
                        {
                            "rule_id": "G3",
                            "title": "水印",
                            "status": "manual",
                            "message": "需结合原件确认",
                            "word_level": "提示造假",
                            "mode": "visual_template",
                        },
                        {
                            "rule_id": "H1",
                            "title": "身份信息",
                            "status": "pass",
                            "message": "信息一致",
                            "word_level": "提示造假",
                            "mode": "automatic",
                        },
                    ],
                    "has_pdf": True,
                },
                {
                    "source_id": "unknown_004",
                    "source_format": "scanned_or_image",
                    "report_variant": "unknown",
                    "overall_status": "manual",
                    "rule_counts": {
                        "fail": 0,
                        "possible": 0,
                        "manual": 2,
                        "pass": 0,
                        "not_applicable": 6,
                    },
                    "rule_results": [
                        {
                            "rule_id": "G3",
                            "title": "水印",
                            "status": "manual",
                            "message": "需结合原件确认",
                            "word_level": "提示造假",
                            "mode": "visual_template",
                        },
                        {
                            "rule_id": "G4",
                            "title": "版式",
                            "status": "manual",
                            "message": "需结合原件确认",
                            "word_level": "提示造假",
                            "mode": "layout_model",
                        },
                        {
                            "rule_id": "A1",
                            "title": "补充原始电子版",
                            "status": "manual",
                            "message": "当前为扫描件，请补充原始电子版",
                            "word_level": "材料前提",
                            "mode": "manual",
                        },
                    ],
                    "has_pdf": True,
                },
            ],
        }
        for document in payload["documents"]:
            source_rules = document["rule_results"]
            numbered = {
                item["rule_id"]: item
                for item in source_rules
                if item["rule_id"] in server.WORD_RULE_IDS
            }
            support = [
                item for item in source_rules if item["rule_id"] in server.SUPPORT_RULE_IDS
            ]
            completed = []
            for rule_id in sorted(server.WORD_RULE_IDS):
                completed.append(numbered.get(rule_id, {
                    "rule_id": rule_id,
                    "title": f"规则{rule_id}",
                    "status": "not_applicable",
                    "message": "不适用于当前报告",
                    "word_level": "提示造假",
                    "mode": "automatic",
                }))
            document["rule_results"] = completed + support
            counts = {key: 0 for key in server.RULE_STATUSES}
            for item in completed:
                counts[item["status"]] += 1
            document["rule_counts"] = counts
            document["analysis_complete"] = True
        server.RESULTS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        server.ALLOW_PDF_PREVIEW = self.previous_pdf_setting
        self.temp.cleanup()

    def test_public_rows_hide_source_and_internal_fields(self):
        row = next(item for item in server.documents() if item["risk_conclusion"] == "fail")
        serialized = json.dumps(row, ensure_ascii=False)
        self.assertTrue(row["document_id"].startswith("CR-"))
        self.assertNotIn("fake", row["document_id"].lower())
        self.assertNotIn("source_id", row)
        self.assertNotIn("overall_status", row)
        self.assertNotIn("pdf_url", row)
        self.assertNotIn('"mode"', serialized)
        self.assertNotIn("word_level", serialized)
        self.assertNotIn("OCR", serialized.upper())
        self.assertNotIn("WORD", serialized.upper())
        evidence = next(
            item["evidence"] for item in row["rule_results"] if item["rule_id"] == "G1"
        )
        self.assertEqual(
            set(evidence), {"expected", "observed", "source_hint", "review_action"}
        )
        self.assertEqual(evidence["source_hint"], "报告首页")
        self.assertNotIn("debug_trace", serialized)
        self.assertNotIn("analysis_notices", serialized)
        self.assertNotIn("analysis_notice_count", serialized)
        self.assertEqual(row["risk_conclusion"], "fail")
        self.assertTrue(row["manual_review_required"])
        self.assertEqual(row["material_requirements"], [])

    def test_conclusion_is_separate_from_manual_review(self):
        rows = server.documents()
        conclusions = {row["risk_conclusion"] for row in rows}
        self.assertEqual(
            conclusions,
            {"fail", "possible", "no_automatic_anomaly", "undetermined"},
        )
        self.assertTrue(all(row["manual_review_required"] for row in rows))

    def test_primary_finding_uses_status_priority(self):
        possible = next(
            row for row in server.documents() if row["risk_conclusion"] == "possible"
        )
        self.assertEqual(possible["primary_finding"]["rule_id"], "P1A")
        self.assertEqual(possible["primary_finding"]["status"], "possible")

    def test_dashboard_has_decoupled_counts_and_slim_attention_rows(self):
        payload = server.dashboard_payload(server.documents())
        self.assertEqual(payload["scope"], "credit_report_only")
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["risk_conclusion_counts"]["fail"], 1)
        self.assertEqual(payload["risk_conclusion_counts"]["possible"], 1)
        self.assertEqual(payload["risk_conclusion_counts"]["no_automatic_anomaly"], 1)
        self.assertEqual(payload["risk_conclusion_counts"]["undetermined"], 1)
        self.assertEqual(payload["manual_review_required_count"], 4)
        self.assertEqual(payload["rule_count"], 22)
        self.assertEqual(payload["rule_status_counts"]["fail"], 1)
        self.assertEqual(payload["rule_status_counts"]["possible"], 1)
        self.assertEqual(payload["rule_status_counts"]["manual"], 13)
        self.assertEqual(payload["rule_status_counts"]["pass"], 2)
        self.assertEqual(len(payload["attention_rows"]), 2)
        self.assertTrue(
            all("rule_results" not in row for row in payload["attention_rows"])
        )
        self.assertNotIn("schema_version", payload)
        self.assertNotIn("synthetic_excluded", payload)

    def test_complete_applicable_rule_search_and_filters(self):
        rows = server.documents()
        c3d_rows = server.filter_documents(rows, {"search": ["C3D"]})
        self.assertEqual(len(c3d_rows), 1)
        self.assertEqual(c3d_rows[0]["report_variant"], "leasing_enterprise")
        self.assertEqual(
            len(server.filter_documents(rows, {"conclusion": ["possible"]})), 1
        )
        self.assertEqual(
            len(
                server.filter_documents(
                    rows, {"source_format": ["scanned_or_image"]}
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                server.filter_documents(
                    rows, {"report_variant": ["pboc_print_personal"]}
                )
            ),
            1,
        )
        self.assertEqual(
            len(server.filter_documents(rows, {"review": ["required"]})), 4
        )
        self.assertEqual(
            len(server.filter_documents(rows, {"review": ["not_required"]})), 0
        )

    def test_pagination_returns_only_summary_rows(self):
        payload = server.paginate_rows(server.documents(), page=2, page_size=2)
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["total_pages"], 2)
        self.assertEqual(len(payload["rows"]), 2)
        self.assertTrue(all("rule_results" not in row for row in payload["rows"]))
        self.assertTrue(all("material_requirements" not in row for row in payload["rows"]))

    def test_material_requirements_are_separate_from_the_22_rules(self):
        row = next(
            item for item in server.documents() if item["report_variant"] == "unknown"
        )
        self.assertEqual(len(row["rule_results"]), 22)
        self.assertEqual(len(row["material_requirements"]), 1)
        self.assertNotIn("rule_id", row["material_requirements"][0])
        self.assertEqual(row["material_requirements"][0]["title"], "补充原始电子版")
        self.assertEqual(sum(row["rule_status_counts"].values()), 22)

    def test_public_text_redacts_accidental_identity_values(self):
        text = (
            "身份证110105199001011234 旧证110105900101123 "
            "统一代码91310000MA1K123456 手机13800138000 "
            "账号6222021234567890123 邮箱test@example.com "
            "报告编号时间20260723143059"
        )
        sanitized = server.sanitize_public_text(text)
        self.assertNotIn("110105199001011234", sanitized)
        self.assertNotIn("110105900101123", sanitized)
        self.assertNotIn("91310000MA1K123456", sanitized)
        self.assertNotIn("13800138000", sanitized)
        self.assertNotIn("6222021234567890123", sanitized)
        self.assertNotIn("test@example.com", sanitized)
        self.assertIn("20260723143059", sanitized)
        self.assertIn("已隐藏", sanitized)

    def test_pdf_preview_is_opt_in(self):
        source_document = server.load_results()["documents"][0]
        public_id = server.public_document_id(source_document["source_id"])
        self.assertNotIn("pdf_url", server.public_row(source_document))
        server.ALLOW_PDF_PREVIEW = True
        self.assertEqual(
            server.public_row(source_document)["pdf_url"], f"/api/pdf/{public_id}"
        )

    def test_security_headers_are_private_and_not_indexable(self):
        self.assertIn("private", server.SECURITY_HEADERS["Cache-Control"])
        self.assertIn("no-store", server.SECURITY_HEADERS["Cache-Control"])
        self.assertIn("noindex", server.SECURITY_HEADERS["X-Robots-Tag"])
        self.assertEqual(server.SECURITY_HEADERS["X-Frame-Options"], "DENY")


if __name__ == "__main__":
    unittest.main()
