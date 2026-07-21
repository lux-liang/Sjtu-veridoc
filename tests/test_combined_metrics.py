import unittest

from src.build_combined_risk import labeled_binary_evaluation, score_v3


class LabeledEvaluationTests(unittest.TestCase):
    def test_confusion_matrix_and_marker_audit(self):
        records = [
            {"label": "fake", "combined_risk_score": 72, "marker_free_risk_score": 0, "combined_risk_reasons": "marker:explicit", "marker_flag": 1},
            {"label": "fake", "combined_risk_score": 26, "marker_free_risk_score": 26, "combined_risk_reasons": "object:pdf_smask_present"},
            {"label": "fake", "combined_risk_score": 10, "marker_free_risk_score": 10, "combined_risk_reasons": ""},
            {"label": "normal", "combined_risk_score": 0, "marker_free_risk_score": 0, "combined_risk_reasons": ""},
            {"label": "normal", "combined_risk_score": 30, "marker_free_risk_score": 30, "combined_risk_reasons": ""},
            {"label": "uploaded", "combined_risk_score": 99, "combined_risk_reasons": ""},
        ]
        result = labeled_binary_evaluation(records, threshold=25)
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["confusion_matrix"], {"tp": 2, "fp": 1, "tn": 1, "fn": 1})
        self.assertEqual(result["marker_driven_fake_count"], 1)
        self.assertEqual(result["unmarked_fake_count"], 2)
        self.assertAlmostEqual(result["precision"], 2 / 3, places=6)
        self.assertAlmostEqual(result["recall"], 2 / 3, places=6)
        self.assertAlmostEqual(result["f1"], 2 / 3, places=6)
        self.assertAlmostEqual(result["accuracy"], 3 / 5, places=6)
        self.assertEqual(result["full_set"]["confusion_matrix"], result["confusion_matrix"])
        self.assertEqual(result["marker_free_audit"]["confusion_matrix"], {"tp": 1, "fp": 1, "tn": 1, "fn": 2})
        self.assertAlmostEqual(result["marker_free_audit"]["recall"], 1 / 3, places=6)

    def test_seal_localization_and_ocr_reviews_are_neutral(self):
        reasons = [
            "visual:seal_monochrome_candidate",
            "visual:seal_ocr_entity_mismatch_review",
        ]
        score, confidence, retained = score_v3(reasons)
        self.assertEqual(score, 0)
        self.assertEqual(confidence, "none")
        self.assertEqual(retained, reasons)


if __name__ == "__main__":
    unittest.main()
