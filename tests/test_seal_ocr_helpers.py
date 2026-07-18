import unittest

from PIL import Image, ImageDraw

from src.analyze_seal_ocr import (
    best_entity_match,
    candidate_zone,
    classify_candidate,
    document_entities,
    normalize_text,
    partial_similarity,
    polar_unwrap,
    position_assessment,
)


class SealOcrHelperTests(unittest.TestCase):
    def test_polar_unwrap_shape(self):
        image = Image.new("L", (300, 300), "white")
        ImageDraw.Draw(image).ellipse((35, 35, 265, 265), outline=40, width=8)
        self.assertEqual(polar_unwrap(image, radial_samples=80, angular_samples=360).size, (360, 80))

    def test_document_entities_uses_business_fields(self):
        entities = document_entities('{"party_a":"上海示例科技有限公司","amounts":[1,2],"buyer":"测试银行"}')
        self.assertEqual(entities, ["上海示例科技有限公司", "测试银行"])

    def test_partial_entity_match_handles_noise(self):
        self.assertGreater(partial_similarity("上海示例科技有限公司", "印章上海示例科技有限公同"), 0.75)
        entity, score = best_entity_match("中国测试银行股份有限公司", ["无关公司", "中国测试银行股份有限公司"])
        self.assertEqual(entity, "中国测试银行股份有限公司")
        self.assertEqual(score, 1.0)
        self.assertEqual(normalize_text(" A-上海（测试）123 "), "a上海测试123")

    def test_repeated_compact_emblem_is_logo(self):
        row = {
            "seal_candidate_bbox_norm": "0.07,0.05,0.14,0.10",
            "seal_candidate_area_ratio": "0.0035",
            "seal_candidate_ring_density": "0.20",
            "seal_candidate_center_density": "0.50",
            "seal_candidate_ink_ratio": "0.25",
            "seal_candidate_mean_saturation": "0.64",
            "seal_candidate_semantic_score": "0.61",
            "seal_candidate_class": "unknown",
        }
        candidate_class, confidence, reasons, should_ocr = classify_candidate(row, duplicate_count=6)
        self.assertEqual(candidate_class, "logo")
        self.assertGreater(confidence, 0.8)
        self.assertIn("repeated_compact_emblem", reasons)
        self.assertFalse(should_ocr)

    def test_repeated_large_watermark_is_unknown(self):
        row = {
            "seal_candidate_bbox_norm": "0.10,0.57,0.28,0.67",
            "seal_candidate_area_ratio": "0.0175",
            "seal_candidate_ring_density": "0.12",
            "seal_candidate_center_density": "0.15",
            "seal_candidate_ink_ratio": "0.11",
            "seal_candidate_mean_saturation": "0.07",
            "seal_candidate_semantic_score": "0.62",
            "seal_candidate_class": "unknown",
        }
        candidate_class, _, reasons, should_ocr = classify_candidate(row, duplicate_count=20)
        self.assertEqual(candidate_class, "unknown")
        self.assertIn("repeated_layout_fragment", reasons)
        self.assertFalse(should_ocr)

    def test_dense_square_is_unknown_and_skips_ocr(self):
        row = {
            "seal_candidate_bbox_norm": "0.58,0.66,0.81,0.82",
            "seal_candidate_area_ratio": "0.035",
            "seal_candidate_ring_density": "0.37",
            "seal_candidate_center_density": "0.31",
            "seal_candidate_ink_ratio": "0.32",
            "seal_candidate_mean_saturation": "0.002",
            "seal_candidate_class": "unknown",
        }
        candidate_class, _, reasons, should_ocr = classify_candidate(row)
        self.assertEqual(candidate_class, "unknown")
        self.assertIn("dense_square_pattern", reasons)
        self.assertFalse(should_ocr)

    def test_position_semantics_are_business_scoped(self):
        self.assertEqual(candidate_zone("0.5,0.72,0.7,0.90"), "bottom")
        self.assertEqual(position_assessment("contract", "seal", "0.5,0.72,0.7,0.90"), ("bottom", "expected_signature_zone"))
        self.assertEqual(position_assessment("contract", "logo", "0.1,0.02,0.2,0.12"), ("top", "not_applicable"))


if __name__ == "__main__":
    unittest.main()
