import unittest
from datetime import datetime

from src.analyze_credit_report_strict import common_rules, enterprise_identity_rule, mixed_g1_reliable, original_pdf_rules, report_datetime, report_number, report_variant, source_is_original


class CreditStrictRuleTests(unittest.TestCase):
    def by_id(self, rules):
        return {item.rule_id: item for item in rules}

    def test_report_number_matches_to_second(self):
        text = "报告编号：2025030512350473088374\n报告时间：2025-03-05 12:35:04"
        self.assertEqual(report_number(text)[:14], "20250305123504")
        self.assertEqual(report_datetime(text), datetime(2025, 3, 5, 12, 35, 4))
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "pass")

    def test_report_number_second_mismatch_fails(self):
        text = "报告编号：2025030512350573088374\n报告时间：2025-03-05 12:35:04"
        self.assertEqual(self.by_id(common_rules(text, True))["G1"].status, "fail")

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

    def test_mixed_native_text_remains_reliable_for_common_rules(self):
        mixed_text = "报告编号：2025030512350573088374\n报告时间：2025-03-05 12:35:04\n" + ("正文" * 280) + "\f" + ("另一页内容" * 160) + "\f"
        ocr_scan_text = (("报告内容" * 100) + "\f") * 3
        font_rows = "\n".join(["Font CID TrueType Identity-H yes yes yes"] * 3)
        self.assertTrue(mixed_g1_reliable(mixed_text, {"pages": "3", "large_image_count": "3", "font_object_count": "43"}, font_rows))
        self.assertFalse(mixed_g1_reliable(ocr_scan_text, {"pages": "3", "large_image_count": "3", "font_object_count": "9"}, "Font Type3 Custom no no no"))


if __name__ == "__main__":
    unittest.main()
