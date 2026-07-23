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
        server.RESULTS_PATH = Path(self.temp.name) / "results.json"
        server._cache_payload = None
        server._cache_mtime = -1.0
        payload = {
            "schema_version": "credit-word-rules-v1",
            "generated_at": "2026-07-23T00:00:00+00:00",
            "synthetic_excluded": True,
            "document_count": 1,
            "documents": [{
                "source_id": "fake_001",
                "source_format": "original_electronic",
                "report_variant": "online_personal",
                "overall_status": "fail",
                "rule_counts": {"fail": 1, "possible": 0, "manual": 1, "pass": 2, "not_applicable": 3},
                "rule_results": [
                    {"rule_id": "G1", "title": "编号时间", "status": "fail", "message": "不一致", "word_level": "提示造假", "mode": "automatic"},
                    {"rule_id": "G3", "title": "水印", "status": "manual", "message": "需模板", "word_level": "提示造假", "mode": "visual_template"},
                ],
                "has_pdf": True,
            }],
        }
        server.RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_public_rows_hide_source_identity(self):
        row = server.documents()[0]
        self.assertTrue(row["document_id"].startswith("CR-"))
        self.assertNotIn("fake", row["document_id"].lower())
        self.assertNotIn("source_id", row)
        self.assertNotIn("risk_score", row)
        self.assertEqual(row["overall_status"], "fail")

    def test_dashboard_uses_rule_statuses(self):
        payload = server.dashboard_payload(server.documents())
        self.assertEqual(payload["scope"], "credit_report_word_rules_only")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["status_counts"]["fail"], 1)
        self.assertNotIn("synthetic_excluded", payload)


if __name__ == "__main__":
    unittest.main()
