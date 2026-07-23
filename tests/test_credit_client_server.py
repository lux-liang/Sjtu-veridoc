import importlib.util
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "client" / "credit-report" / "server.py"
SPEC = importlib.util.spec_from_file_location("credit_client_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(server)


class CreditClientServerTests(unittest.TestCase):
    def test_sanitize_never_exposes_internal_label_or_path(self):
        row = server.sanitize_row(
            {
                "document_id": "fake_001",
                "doc_type": "credit_report",
                "label": "fake",
                "path": "/private/report.pdf",
                "combined_risk_score": "42",
                "combined_risk_reasons": "marker:explicit_forgery_text:training|text:credit_report_id_missing",
                "pdf_url": "/api/pdf/fake_001",
            }
        )
        self.assertEqual(row["status"], "priority")
        self.assertNotIn("label", row)
        self.assertNotIn("path", row)
        self.assertNotIn("fake", row["document_id"].lower())
        self.assertTrue(row["document_id"].startswith("CR-"))
        self.assertNotIn("training", " ".join(row["evidence"]))
        self.assertEqual(row["doc_type"], "credit_report")

    def test_dashboard_is_credit_only_summary(self):
        rows = [
            {"document_id": "a", "status": "priority", "evidence": ["PDF 文档属性或结构需要复核"]},
            {"document_id": "b", "status": "routine", "evidence": []},
        ]
        payload = server.dashboard_payload(rows)
        self.assertEqual(payload["scope"], "credit_report_only")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["status_counts"]["priority"], 1)


if __name__ == "__main__":
    unittest.main()
