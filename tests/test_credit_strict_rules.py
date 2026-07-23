import gzip
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.analyze_credit_report_strict import (
    SUPPORT_RULE_IDS,
    WORD_RULE_IDS,
    common_rules,
    enterprise_identity_rule,
    enterprise_rules,
    mixed_g1_reliable,
    ocr_text,
    original_pdf_rules,
    personal_rules,
    read_csv,
    report_datetime,
    report_number,
    report_variant,
    rule,
    source_is_original,
    status_counts,
    summarize_status,
    main,
)


class CreditStrictRuleTests(unittest.TestCase):
    def by_id(self, rules):
        return {item.rule_id: item for item in rules}

    def test_csv_reader_tolerates_embedded_nul_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "features.csv"
            path.write_bytes(b'"document_id","value"\n"doc-1","a\x00b"\n')
            self.assertEqual(read_csv(path), [{"document_id": "doc-1", "value": "ab"}])

    def test_report_number_matches_to_second(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04"
        self.assertEqual(report_number(text)[:14], "20250305123504")
        self.assertEqual(report_datetime(text), datetime(2025, 3, 5, 12, 35, 4))
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "pass")

    def test_report_number_second_mismatch_fails(self):
        text = "报告编号：2025030512350573088374\n报告时间：2025-03-05 12:35:04"
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "fail")

    def test_date_only_report_time_never_supports_second_level_failure(self):
        text = "报告编号：2025030512350573088374\n报告时间：2025-03-05"
        result = self.by_id(common_rules(text, True))["G1"]
        self.assertEqual(result.status, "manual")
        self.assertIn("含秒", result.message)

    def test_exact_report_time_skips_an_earlier_date_only_label(self):
        text = "报告时间：2025-03-05\n报告时间：2025-03-05 12:35:04"
        self.assertEqual(report_datetime(text, require_seconds=True), datetime(2025, 3, 5, 12, 35, 4))

    def test_information_report_date_is_not_the_cover_report_time(self):
        text = "报告编号：2025030512350473088374\n信息报告日期：2025-03-06 00:00:00"
        self.assertIsNone(report_datetime(text, require_seconds=True))
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "manual")

    def test_report_number_does_not_borrow_from_later_pages(self):
        text = "报告时间：2025-03-05 12:35:04\n\f附件报告编号：2025030612350473088374"
        self.assertEqual(report_number(text), "")
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "manual")

    def test_glued_pdf_datetime_keeps_seconds(self):
        text = "报告编号：2024112612005163484278\n报告时间：2024-11-2617:00:51"
        self.assertEqual(report_datetime(text), datetime(2024, 11, 26, 17, 0, 51))
        self.assertEqual(self.by_id(common_rules(text, False, True))["G1"].status, "fail")

    def test_future_maturity_date_is_not_failure(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04\n贷款到期日：2028-01-01"
        self.assertEqual(self.by_id(common_rules(text, True))["G2"].status, "pass")

    def test_future_historical_date_fails(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04\n贷款发放日：2025-03-06"
        self.assertEqual(self.by_id(common_rules(text, True))["G2"].status, "fail")

    def test_date_only_report_time_uses_end_of_day_for_date_rules(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05\n贷款发放日：2025-03-05 18:30:00"
        self.assertEqual(self.by_id(common_rules(text, True))["G2"].status, "pass")

    def test_duplicate_future_dates_are_counted_once(self):
        text = "报告时间：2025-03-05 12:35:04\n贷款发放日：2025-03-06\n贷款发放日：2025-03-06"
        result = self.by_id(common_rules(text, True))["G2"]
        self.assertEqual(result.status, "fail")
        self.assertIn("1个", result.message)

    def test_unresolved_table_date_requires_manual_review(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04\n开立日期 到期日 信息报告日期\n2024-01-01 2028-01-01 2025-03-01"
        self.assertEqual(self.by_id(common_rules(text, True))["G2"].status, "manual")

    def test_unrelated_report_word_does_not_make_future_date_historical(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04\n生态环境报告表审批意见 2025-09-09"
        self.assertEqual(self.by_id(common_rules(text, True))["G2"].status, "manual")

    def test_original_pdf_word_baseline(self):
        text = "报告时间：2025-03-05 12:35:04"
        info = {"CreationDate": "Wed, 05 Mar 2025 12:35:05 +0000", "ModDate": "Wed, 05 Mar 2025 12:35:05 +0000", "Producer": "iText 2.1.7 by 1T3XT", "PDF version": "1.4"}
        fonts = "Helvetica Type 1 WinAnsi no no no\nABCDEF+SourceHanSerifCN-Regular CID TrueType Identity-H yes yes yes"
        results = self.by_id(original_pdf_rules(None, text, info, fonts, True))
        self.assertEqual(results["P1A"].status, "pass")
        self.assertEqual(results["P1B"].status, "pass")
        self.assertEqual(results["P1C"].status, "pass")
        self.assertEqual(results["P1D"].status, "pass")
        self.assertEqual(results["P2"].status, "pass")
        self.assertIn("evidence", results["P1A"].as_dict())

    def test_pdf_property_command_failure_requires_manual_review(self):
        results = self.by_id(original_pdf_rules(None, "报告时间：2025-03-05 12:35:04", {}, "", True, False, False))
        self.assertEqual(results["P1A"].status, "manual")
        self.assertEqual(results["P1B"].status, "manual")
        self.assertEqual(results["P1C"].status, "manual")
        self.assertEqual(results["P1D"].status, "manual")
        self.assertEqual(results["P2"].status, "manual")

    def test_pdf_font_set_rejects_extra_or_unembedded_fonts(self):
        text = "报告时间：2025-03-05 12:35:04"
        info = {"CreationDate": "Wed, 05 Mar 2025 12:35:05 +0000", "ModDate": "Wed, 05 Mar 2025 12:35:05 +0000", "Producer": "iText 2.1.7 by 1T3XT", "PDF version": "1.4"}
        extra = "Helvetica Type 1 WinAnsi no no no\nABCDEF+SourceHanSerifCN-Regular CID TrueType Identity-H yes yes yes\nJinbiaoSong CID TrueType Identity-H yes yes yes"
        unembedded = "Helvetica Type 1 WinAnsi no no no\nSourceHanSerifCN-Regular CID TrueType Identity-H no no yes"
        self.assertEqual(self.by_id(original_pdf_rules(None, text, info, extra, True))["P1D"].status, "possible")
        self.assertEqual(self.by_id(original_pdf_rules(None, text, info, unembedded, True))["P1D"].status, "possible")

    def test_pdf_creation_gap_over_one_day_requires_manual_review(self):
        text = "报告时间：2025-03-03 12:35:04"
        info = {"CreationDate": "Wed, 05 Mar 2025 12:35:05 +0000", "ModDate": "Wed, 05 Mar 2025 12:35:05 +0000", "Producer": "iText 2.1.7 by 1T3XT", "PDF version": "1.4"}
        fonts = "Helvetica Type 1 WinAnsi no no no\nABCDEF+SourceHanSerifCN-Regular CID TrueType Identity-H yes yes yes"
        self.assertEqual(self.by_id(original_pdf_rules(None, text, info, fonts, True))["P2"].status, "manual")

    def test_enterprise_identity_normalizes_label_colons(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称
甲方教育有限公司
中征码
1234567890123456
统一社会信用代码
91110101MA1234567X
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "pass")

    def test_enterprise_identity_does_not_borrow_loan_account(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称
甲方教育有限公司
中征码
账户编号
1234567890123456
统一社会信用代码
91110101MA1234567X
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "manual")

    def test_enterprise_identity_ignores_later_counterparty_codes(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f信贷记录明细
企业名称：其他交易对手有限公司
中征码：9999999999999999
统一社会信用代码：92220202MA7654321Y
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "pass")

    def test_enterprise_identity_ignores_identity_type_table(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f主要出资人信息
身份标识类型 身份标识号码
企业名称：其他股东有限公司
中征码：9999999999999999
统一社会信用代码：92220202MA7654321Y
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "pass")

    def test_enterprise_identity_conflict_fails(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称：乙方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "fail")

    def test_enterprise_identity_multiple_values_in_one_section_is_manual(self):
        text = """企业信用报告
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
\f身份标识
企业名称：甲方教育有限公司
中征码：1234567890123456
统一社会信用代码：91110101MA1234567X
统一社会信用代码：92220202MA7654321Y
"""
        self.assertEqual(enterprise_identity_rule(text, True).status, "manual")

    def test_enterprise_variant_uses_query_institution_not_business_text(self):
        bank_report = """企业信用报告
查询机构：中国工商银行股份有限公司某分行
报告时间：2025-01-01T10:00:00
\f信贷记录明细 融资租赁业务
"""
        leasing_report = """企业信用报告
查询机构：甲方融资租赁有限公司
报告时间：2025-01-01T10:00:00
"""
        self.assertEqual(report_variant(bank_report, True), "online_enterprise")
        self.assertEqual(report_variant(leasing_report, True), "leasing_enterprise")

    def test_personal_variant_requires_version_markers(self):
        online = "个人信用报告 信息概要 发生过逾期的贷记卡账户明细如下"
        printed = "个人信用报告 一 个人基本信息 二 信息概要 三 信贷交易信息明细"
        ambiguous = "个人信用报告 姓名 证件号码"
        self.assertEqual(report_variant(online, True), "online_personal")
        self.assertEqual(report_variant(printed, False), "pboc_print_personal")
        self.assertEqual(report_variant(ambiguous, True), "unknown")

    def test_pdf_structure_controls_scan_classification(self):
        self.assertTrue(source_is_original(True, {"pages": "10", "large_image_count": "1"}))
        self.assertFalse(source_is_original(True, {"pages": "10", "large_image_count": "8"}))
        self.assertFalse(source_is_original(False, {"pages": "3", "large_image_count": "0"}))
        self.assertFalse(source_is_original(True, None))
        self.assertFalse(source_is_original(True, {}))

    def test_mixed_native_text_remains_reliable_for_common_rules(self):
        mixed_text = "报告编号：2025030512350573088374\n报告时间：2025-03-05 12:35:04\n" + ("正文" * 280) + "\f" + ("另一页内容" * 160) + "\f"
        ocr_scan_text = (("报告内容" * 100) + "\f") * 3
        font_rows = "\n".join(["Font CID TrueType Identity-H yes yes yes"] * 3)
        self.assertTrue(mixed_g1_reliable(mixed_text, {"pages": "3", "large_image_count": "3", "font_object_count": "43"}, font_rows))
        self.assertFalse(mixed_g1_reliable(ocr_scan_text, {"pages": "3", "large_image_count": "3", "font_object_count": "9"}, "Font Type3 Custom no no no"))

    def test_absent_default_summary_table_requires_manual_review(self):
        result = self.by_id(personal_rules("个人信用报告 个人基本信息", "pboc_print_personal", True))["H2"]
        self.assertEqual(result.status, "manual")
        self.assertEqual(result.word_level, "有造假可能")

    def test_personal_identity_ignores_other_people_and_spouse_fields(self):
        text = """其他人员证件号码：110105198001011234
一 个人基本信息
证件号码：110105199001011234
出生日期：1990-01-01
配偶信息
配偶证件号码：110105198505051234
配偶出生日期：1985-05-05
二 信息概要
"""
        self.assertEqual(self.by_id(personal_rules(text, "pboc_print_personal", True))["H1"].status, "pass")

    def test_spouse_only_identity_does_not_support_subject_failure(self):
        text = """一 个人基本信息
配偶证件号码：110105198505051234
配偶出生日期：1986-05-05
二 信息概要
"""
        self.assertEqual(self.by_id(personal_rules(text, "pboc_print_personal", True))["H1"].status, "manual")

    def test_enterprise_rules_match_four_word_subchecks(self):
        results = self.by_id(enterprise_rules("企业信用报告", "online_enterprise", True))
        self.assertNotIn("C3E", results)
        self.assertEqual({"C3A", "C3B", "C3C", "C3D"}, {key for key in results if key.startswith("C3")})
        self.assertIn("承兑汇票", results["C3C"].expected)
        self.assertEqual(len(WORD_RULE_IDS), 22)

    def test_material_prerequisites_do_not_inflate_word_rule_counts(self):
        numbered = [rule(rule_id, rule_id, "pass", "ok") for rule_id in WORD_RULE_IDS]
        prerequisites = [rule(rule_id, rule_id, "manual", "review") for rule_id in SUPPORT_RULE_IDS]
        overall, counts = summarize_status(numbered + prerequisites)
        self.assertEqual(overall, "pass")
        self.assertEqual(counts["pass"], 22)
        self.assertEqual(counts["manual"], 0)
        self.assertEqual(status_counts(prerequisites)["manual"], len(SUPPORT_RULE_IDS))

    def test_main_writes_private_result_and_restores_umask(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            combined = root / "combined.csv"
            ocr = root / "ocr.csv"
            output = root / "credit_rule_results.json"
            combined.write_text("document_id,doc_type,path,ext\n", encoding="utf-8")
            ocr.write_text("document_id\n", encoding="utf-8")
            previous = os.umask(0o027)
            try:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "analyze_credit_report_strict.py",
                        "--combined-csv",
                        str(combined),
                        "--ocr-csv",
                        str(ocr),
                        "--data-root",
                        str(root),
                        "--output",
                        str(output),
                    ],
                ):
                    main()
                observed_umask = os.umask(0o027)
                self.assertEqual(observed_umask, 0o027)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            finally:
                os.umask(previous)

    def test_full_ocr_text_json_takes_precedence_over_preview(self):
        row = {"ocr_text_preview": "第一页", "ocr_text_json": '"第一页\\n\\f\\n第二页"'}
        self.assertIn("第二页", ocr_text(row))

    def test_private_gzip_ocr_text_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "run" / "doc.txt.gz"
            target.parent.mkdir()
            with gzip.open(target, "wt", encoding="utf-8") as stream:
                stream.write("第一页\n\f\n第二页")
            row = {"ocr_text_preview": "预览", "ocr_text_file": "run/doc.txt.gz"}
            self.assertIn("第二页", ocr_text(row, root))

    def test_private_gzip_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(ocr_text({"ocr_text_file": "../outside.gz", "ocr_text_preview": "安全预览"}, root), "安全预览")


if __name__ == "__main__":
    unittest.main()
