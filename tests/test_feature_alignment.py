import csv
import tempfile
import unittest
from pathlib import Path

from scripts.audit_feature_alignment import run_audit


FIELDS = ["document_id", "label", "doc_type", "path"]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class FeatureAlignmentTests(unittest.TestCase):
    def test_allows_partial_aligned_feature_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.csv"
            visual = root / "visual.csv"
            rows = [
                {"document_id": "a", "label": "fake", "doc_type": "contract", "path": "/base/a.pdf"},
                {"document_id": "b", "label": "normal", "doc_type": "invoice", "path": "/base/b.pdf"},
            ]
            write_csv(base, rows)
            write_csv(visual, [{**rows[0], "path": "/deployed/a.pdf"}])
            result = run_audit(base, [("visual", visual)], set())
            self.assertTrue(result["ok"])
            self.assertEqual(result["features"][0]["missing_base_count"], 1)

    def test_rejects_path_identity_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.csv"
            visual = root / "visual.csv"
            write_csv(base, [{"document_id": "a", "label": "fake", "doc_type": "contract", "path": "/base/a.pdf"}])
            write_csv(visual, [{"document_id": "a", "label": "fake", "doc_type": "contract", "path": "/old/other.pdf"}])
            result = run_audit(base, [("visual", visual)], set())
            self.assertFalse(result["ok"])
            self.assertIn("a", result["features"][0]["path_mismatch_ids"])

    def test_rejects_outside_duplicate_and_incomplete_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.csv"
            feature = root / "feature.csv"
            write_csv(base, [
                {"document_id": "a", "label": "fake", "doc_type": "contract", "path": "a.pdf"},
                {"document_id": "b", "label": "normal", "doc_type": "invoice", "path": "b.pdf"},
            ])
            write_csv(feature, [
                {"document_id": "a", "label": "fake", "doc_type": "contract", "path": "a.pdf"},
                {"document_id": "a", "label": "fake", "doc_type": "contract", "path": "a.pdf"},
                {"document_id": "x", "label": "fake", "doc_type": "contract", "path": "x.pdf"},
            ])
            result = run_audit(base, [("combined", feature)], {"combined"})
            self.assertFalse(result["ok"])
            report = result["features"][0]
            self.assertEqual(report["duplicate_ids"], ["a"])
            self.assertEqual(report["outside_base_ids"], ["x"])
            self.assertEqual(report["missing_base_count"], 1)


if __name__ == "__main__":
    unittest.main()
