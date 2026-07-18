import unittest

from src.analyze_qwen_forensics import normalize_seal_candidates


class QwenSealCandidateTests(unittest.TestCase):
    def test_invalid_boxes_are_removed_and_values_are_clamped(self):
        result = normalize_seal_candidates([
            {"bbox": [0.1, 0.2, 0.4, 0.6], "color": "gray", "text": "测试章", "pasted_suspicion": 120},
            {"bbox": [0.8, 0.2, 0.3, 0.6], "color": "red"},
            {"bbox": [0, 0, 2, 3], "color": "red"},
            {"bbox": "bad"},
        ])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["bbox"], [0.1, 0.2, 0.4, 0.6])
        self.assertEqual(result[0]["pasted_suspicion"], 100)
        self.assertEqual(result[0]["text"], "测试章")


if __name__ == "__main__":
    unittest.main()
