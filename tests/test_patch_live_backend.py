import unittest

from scripts.patch_live_backend import (
    BACKEND_MARKER,
    BUSINESS_ACCEPTANCE_MARKER,
    DUAL_SCOPE_MARKER,
    EVAL_SCOPE_MARKER,
    SEAL_SEMANTIC_MARKER,
    patch_backend,
)


FIXTURE = '''QWEN_OCR_SUMMARY_JSON = DATA_ROOT / "features" / "qwen_ocr_summary.json"
def read_feature_rows():
    qwen_by_id = {row.get("document_id"): row for row in read_csv_rows(QWEN_OCR_CSV)}
    numeric_fields = {
        "qwen_risk_score",
    }
    for row in rows:
        qwen = qwen_by_id.get(row.get("document_id"), {})
        for key in [
            "extracted_fields_json",
        ]:
            pass
def build_dashboard() -> dict:
    training = load_training_history()

    return {
        "combined_summary": load_json(COMBINED_RISK_JSON, {}),
    }
class Handler:
    def do_GET(self):
        if parsed.path == "/api/documents":
            query = parse_qs(parsed.query)
            min_risk = int((query.get("min_risk") or ["0"])[0] or 0)
            self.send_json({"rows": [row_public(row) for row in rows[:500]], "count": len(rows)})
'''


class BackendPatchTests(unittest.TestCase):
    def test_patch_is_idempotent_and_adds_dual_scope_contract(self):
        once = patch_backend(FIXTURE)
        twice = patch_backend(once)
        self.assertEqual(once, twice)
        self.assertIn(BACKEND_MARKER, once)
        self.assertIn(EVAL_SCOPE_MARKER, once)
        self.assertIn(DUAL_SCOPE_MARKER, once)
        self.assertIn(SEAL_SEMANTIC_MARKER, once)
        self.assertIn(BUSINESS_ACCEPTANCE_MARKER, once)
        self.assertIn('parsed.path == "/api/evaluation"', once)
        self.assertIn('"marker_free_audit": audit', once)
        self.assertIn('"marker_free_risk_score"', once)
        self.assertIn('"business_acceptance": business_acceptance', once)
        self.assertIn('"similar_image"', once)
        self.assertIn('rows[:limit]', once)


if __name__ == "__main__":
    unittest.main()
