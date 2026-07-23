import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_credit_scan_manifest import build_manifest


class CreditScanManifestTests(unittest.TestCase):
    def test_builds_private_manifest_for_scanned_credit_reports_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scanned = root / "scan.pdf"
            original = root / "original.pdf"
            scanned.write_bytes(b"scan")
            original.write_bytes(b"original")
            combined = root / "combined.csv"
            with combined.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["document_id", "label", "doc_type", "path"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "document_id": "scan-1",
                            "label": "normal",
                            "doc_type": "credit_report",
                            "path": scanned.name,
                        },
                        {
                            "document_id": "original-1",
                            "label": "normal",
                            "doc_type": "credit_report",
                            "path": original.name,
                        },
                    ]
                )
            strict = root / "strict.json"
            strict.write_text(
                json.dumps(
                    {
                        "schema_version": "credit-word-rules-v1",
                        "synthetic_excluded": True,
                        "documents": [
                            {
                                "source_id": "scan-1",
                                "source_format": "scanned_or_image",
                            },
                            {
                                "source_id": "original-1",
                                "source_format": "original_electronic",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "scan_manifest.csv"

            self.assertEqual(build_manifest(combined, strict, root, output), 1)
            with output.open("r", encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["document_id"] for row in rows], ["scan-1"])
            self.assertEqual(rows[0]["ext"], ".pdf")
            self.assertEqual(rows[0]["path"], str(scanned.resolve()))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
