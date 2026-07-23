import unittest

from src.detectors.business_logic_detector import BusinessLogicDetector


class CreditReportRuleTests(unittest.TestCase):
    def setUp(self):
        self.detector = BusinessLogicDetector()

    def reasons(self, text, fields=None):
        return {item["rule"] for item in self.detector.detect("credit_report", text, fields)["findings"]}

    def test_report_number_date_mismatch(self):
        reasons = self.reasons("报告编号：20260722093000ABC 报告时间：2026-07-21")
        self.assertIn("credit_report_number_date_mismatch", reasons)

    def test_future_date(self):
        reasons = self.reasons("报告编号：20260722093000ABC 报告时间：2026-07-22 还款日期：2026-07-23")
        self.assertIn("credit_report_future_date", reasons)

    def test_id_and_birth_date(self):
        reasons = self.reasons("身份证号码：110105199001011234 出生日期：1991-01-01")
        self.assertIn("credit_report_id_birth_date_mismatch", reasons)

    def test_pdf_creation_date(self):
        reasons = self.reasons("报告时间：2026-07-23", {"pdf_creation_date": "2026-07-22"})
        self.assertIn("credit_report_report_after_pdf_creation", reasons)


if __name__ == "__main__":
    unittest.main()
