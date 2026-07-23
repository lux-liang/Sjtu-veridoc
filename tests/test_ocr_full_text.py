import csv
import gzip
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import analyze_ocr_deepseek as ocr_module
from src.analyze_ocr_deepseek import (
    extract_fields,
    ocr_document,
    redact_sensitive_text,
    words_to_text,
)


class OcrFullTextTests(unittest.TestCase):
    def test_words_to_text_preserves_page_boundaries(self):
        words = [
            {"page": 2, "x": 30, "y": 20, "text": "第二页"},
            {"page": 1, "x": 60, "y": 20, "text": "报告"},
            {"page": 1, "x": 10, "y": 20, "text": "征信"},
        ]
        self.assertEqual(words_to_text(words), "征信 报告\n\f\n第二页")

    def test_words_to_text_preserves_blank_page_slots(self):
        words = [
            {"page": 1, "x": 10, "y": 20, "text": "第一页"},
            {"page": 3, "x": 10, "y": 20, "text": "第三页"},
        ]
        self.assertEqual(words_to_text(words, 3), "第一页\n\f\n\n\f\n第三页")

    def test_tesseract_line_numbers_drive_reconstruction(self):
        words = [
            {"page": 1, "block_num": "1", "par_num": "1", "line_num": "2", "x": 10, "y": 60, "text": "第二行"},
            {"page": 1, "block_num": "1", "par_num": "1", "line_num": "1", "x": 60, "y": 20, "text": "报告"},
            {"page": 1, "block_num": "1", "par_num": "1", "line_num": "1", "x": 10, "y": 20, "text": "征信"},
        ]
        self.assertEqual(words_to_text(words), "征信 报告\n第二行")

    def test_words_to_text_keeps_separate_lines(self):
        words = [
            {"page": 1, "x": 10, "y": 18, "text": "第一行"},
            {"page": 1, "x": 10, "y": 54, "text": "第二行"},
        ]
        self.assertEqual(words_to_text(words), "第一行\n第二行")

    def test_credit_id_pattern_accepts_standard_18_digit_id(self):
        fields = extract_fields("证件号码 11010519900101123X")
        self.assertEqual(fields["id_numbers"], ["11010519900101123X"])

    def test_sensitive_preview_redaction(self):
        redacted = redact_sensitive_text("证件 11010519900101123X 账号 6222021234567890123")
        self.assertNotIn("11010519900101123X", redacted)
        self.assertNotIn("6222021234567890123", redacted)

    def test_failed_tesseract_page_is_not_marked_successful(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "page.png"
            image.write_bytes(b"not-an-image")
            row = {"path": str(image), "ext": ".png", "document_id": "sample"}
            with patch("src.analyze_ocr_deepseek.tesseract_tsv", return_value=([], "tesseract_failed:test")):
                _, error, rendered, successful, requested, total = ocr_document(row, Path(temp_dir) / "render", 180, 1, "chi_sim+eng")
            self.assertIn("tesseract_failed", error)
            self.assertEqual((rendered, successful, requested, total), (1, 0, 1, 1))

    def test_main_writes_private_outputs_without_summary_pii_and_restores_umask(self):
        def current_umask():
            value = os.umask(0)
            os.umask(value)
            return value

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_csv = root / "ocr_features.csv"
            out_json = root / "ocr_summary.json"
            out_words = root / "ocr_words.csv.gz"
            row = {
                "document_id": "sample-document",
                "label": "normal",
                "doc_type": "credit_report",
                "ext": ".pdf",
                "path": str(root / "sample.pdf"),
            }
            words = [
                {
                    "page": 1,
                    "text": "证件号码",
                    "conf": 99.0,
                    "x": 10,
                    "y": 20,
                    "w": 30,
                    "h": 10,
                    "line_num": "1",
                    "block_num": "1",
                    "par_num": "1",
                },
                {
                    "page": 1,
                    "text": "11010519900101123X",
                    "conf": 99.0,
                    "x": 50,
                    "y": 20,
                    "w": 100,
                    "h": 10,
                    "line_num": "1",
                    "block_num": "1",
                    "par_num": "1",
                },
                {
                    "page": 2,
                    "text": "账号6222021234567890123",
                    "conf": 98.0,
                    "x": 10,
                    "y": 20,
                    "w": 120,
                    "h": 10,
                    "line_num": "1",
                    "block_num": "1",
                    "par_num": "1",
                },
            ]
            argv = [
                "analyze_ocr_deepseek.py",
                "--manifest",
                str(root / "manifest.csv"),
                "--out-csv",
                str(out_csv),
                "--out-json",
                str(out_json),
                "--out-words-csv",
                str(out_words),
                "--credit-max-pages",
                "0",
            ]
            before_umask = current_umask()
            with (
                patch.object(sys, "argv", argv),
                patch.object(ocr_module, "read_csv", return_value=[row]),
                patch.object(
                    ocr_module,
                    "ocr_document",
                    return_value=(words, "", 2, 2, 2, 2),
                ),
                patch.object(
                    ocr_module.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(free=19 * 1024**3),
                ),
                redirect_stdout(io.StringIO()),
            ):
                ocr_module.main()
            self.assertEqual(current_umask(), before_umask)

            with out_csv.open("r", encoding="utf-8", newline="") as stream:
                summaries = list(csv.DictReader(stream))
            self.assertEqual(len(summaries), 1)
            summary_row = summaries[0]
            self.assertEqual(summary_row["ocr_requested_complete"], "1")
            self.assertEqual(summary_row["ocr_complete"], "1")
            self.assertNotIn("ocr_text_json", summary_row)
            self.assertNotIn("ocr_fields_json", summary_row)
            serialized_row = json.dumps(summary_row, ensure_ascii=False)
            self.assertNotIn("11010519900101123X", serialized_row)
            self.assertNotIn("6222021234567890123", serialized_row)

            text_path = root / "ocr_features_text" / summary_row["ocr_text_file"]
            self.assertEqual(text_path.stat().st_mode & 0o777, 0o600)
            with gzip.open(text_path, "rt", encoding="utf-8") as stream:
                private_text = stream.read()
            self.assertIn("11010519900101123X", private_text)
            self.assertIn("6222021234567890123", private_text)

            with gzip.open(out_words, "rt", encoding="utf-8", newline="") as stream:
                word_rows = list(csv.DictReader(stream))
            self.assertEqual(len(word_rows), 3)
            self.assertEqual(word_rows[0]["run_id"], summary_row["ocr_run_id"])
            self.assertEqual(word_rows[0]["par_num"], "1")

            summary_json = out_json.read_text(encoding="utf-8")
            self.assertNotIn("11010519900101123X", summary_json)
            self.assertNotIn("6222021234567890123", summary_json)

    def test_parallel_workers_publish_all_rows_in_manifest_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out_csv = root / "parallel.csv"
            out_json = root / "parallel.json"
            out_words = root / "parallel_words.csv.gz"
            rows = [
                {
                    "document_id": "first",
                    "label": "normal",
                    "doc_type": "credit_report",
                    "ext": ".pdf",
                    "path": str(root / "first.pdf"),
                },
                {
                    "document_id": "second",
                    "label": "normal",
                    "doc_type": "credit_report",
                    "ext": ".pdf",
                    "path": str(root / "second.pdf"),
                },
            ]

            def fake_ocr(row, *_args, **_kwargs):
                if row["document_id"] == "first":
                    time.sleep(0.03)
                word = {
                    "page": 1,
                    "text": row["document_id"],
                    "conf": 99.0,
                    "x": 10,
                    "y": 20,
                    "w": 30,
                    "h": 10,
                    "line_num": "1",
                    "block_num": "1",
                    "par_num": "1",
                }
                return [word], "", 1, 1, 1, 1

            argv = [
                "analyze_ocr_deepseek.py",
                "--manifest",
                str(root / "manifest.csv"),
                "--out-csv",
                str(out_csv),
                "--out-json",
                str(out_json),
                "--out-words-csv",
                str(out_words),
                "--workers",
                "2",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(ocr_module, "read_csv", return_value=rows),
                patch.object(ocr_module, "ocr_document", side_effect=fake_ocr),
                patch.object(
                    ocr_module.shutil,
                    "disk_usage",
                    return_value=ocr_module.shutil._ntuple_diskusage(
                        20 * 1024**3,
                        1 * 1024**3,
                        19 * 1024**3,
                    ),
                ),
                redirect_stdout(io.StringIO()),
            ):
                ocr_module.main()

            with out_csv.open("r", encoding="utf-8", newline="") as stream:
                summaries = list(csv.DictReader(stream))
            self.assertEqual(
                [item["document_id"] for item in summaries], ["first", "second"]
            )
            self.assertTrue(all(item["ocr_complete"] == "1" for item in summaries))
            with gzip.open(out_words, "rt", encoding="utf-8", newline="") as stream:
                words = list(csv.DictReader(stream))
            self.assertEqual({item["document_id"] for item in words}, {"first", "second"})
            self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["count"], 2)


if __name__ == "__main__":
    unittest.main()
