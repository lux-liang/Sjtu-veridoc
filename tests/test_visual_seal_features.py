import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import src.analyze_visual_forensics as visual_module
from src.analyze_visual_forensics import aggregate_page_results, color_agnostic_seal_features, save_seal_candidate_crops


def document_with_stamp(color=(75, 75, 75), top=900):
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    for y in range(120, 720, 70):
        draw.line((100, y, 800, y), fill=(45, 45, 45), width=3)
    draw.ellipse((600, top, 820, top + 220), outline=color, width=10)
    draw.ellipse((625, top + 25, 795, top + 195), outline=color, width=4)
    draw.text((665, top + 90), "SEAL", fill=color)
    return image


class ColorAgnosticSealTests(unittest.TestCase):
    def test_monochrome_stamp_is_localized(self):
        features = color_agnostic_seal_features(document_with_stamp())
        self.assertGreaterEqual(features["seal_candidate_count"], 1)
        self.assertGreater(features["seal_candidate_best_score"], 0.7)
        self.assertEqual(features["seal_candidate_is_monochrome"], 1)
        self.assertEqual(features["seal_candidate_class"], "seal")
        self.assertGreater(features["seal_candidate_ring_uniformity"], 0)

    def test_red_stamp_is_not_monochrome(self):
        features = color_agnostic_seal_features(document_with_stamp((190, 25, 35)))
        self.assertEqual(features["seal_candidate_class"], "seal")
        self.assertEqual(features["seal_candidate_is_monochrome"], 0)
        self.assertGreater(features["seal_candidate_mean_saturation"], 0.5)

    def test_header_round_logo_is_not_called_a_seal(self):
        image = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((790, 45, 920, 175), outline=(45, 85, 150), width=7)
        draw.ellipse((812, 67, 898, 153), outline=(45, 85, 150), width=3)
        draw.text((825, 105), "LOGO", fill=(45, 85, 150))
        features = color_agnostic_seal_features(image)
        self.assertGreaterEqual(features["seal_candidate_count"], 1)
        self.assertNotEqual(features["seal_candidate_class"], "seal")
        self.assertEqual(features["seal_candidate_class_reason"], "header_emblem_pattern")
        self.assertEqual(features["seal_candidate_ocr_recommended"], 0)

    def test_dense_qr_like_square_skips_ocr(self):
        image = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(image)
        left, top, cell = 620, 900, 12
        for row in range(17):
            for col in range(17):
                if (row * 7 + col * 5 + row * col) % 9 < 4:
                    draw.rectangle((left + col * cell, top + row * cell, left + (col + 1) * cell, top + (row + 1) * cell), fill="black")
        features = color_agnostic_seal_features(image)
        self.assertGreaterEqual(features["seal_candidate_count"], 1)
        self.assertNotEqual(features["seal_candidate_class"], "seal")
        self.assertEqual(features["seal_candidate_ocr_recommended"], 0)

    def test_text_lines_do_not_form_candidate(self):
        image = Image.new("RGB", (1000, 1400), "white")
        draw = ImageDraw.Draw(image)
        for y in range(120, 1100, 55):
            draw.line((100, y, 800, y), fill=(45, 45, 45), width=3)
        self.assertEqual(color_agnostic_seal_features(image)["seal_candidate_count"], 0)

    def test_dependency_free_component_fallback(self):
        previous = visual_module.ndimage
        visual_module.ndimage = None
        try:
            features = color_agnostic_seal_features(document_with_stamp())
        finally:
            visual_module.ndimage = previous
        self.assertGreaterEqual(features["seal_candidate_count"], 1)

    def test_candidate_crops_are_saved(self):
        image = document_with_stamp()
        features = color_agnostic_seal_features(image)
        with tempfile.TemporaryDirectory() as tmp:
            context, ocr = save_seal_candidate_crops(image, features["seal_candidate_bbox_norm"], Path(tmp), "sample")
            self.assertTrue(Path(context).exists())
            self.assertTrue(Path(ocr).exists())
            with Image.open(ocr) as ocr_image:
                self.assertEqual(ocr_image.mode, "L")
                self.assertGreaterEqual(max(ocr_image.size), 640)

    def test_multi_page_aggregation_prefers_semantic_candidate(self):
        page_one = {
            "visual_risk_score": 20,
            "visual_risk_reasons": "high_ela_recompression_error",
            "ela_score": 8,
            "seal_candidate_best_score": 0.96,
            "seal_candidate_semantic_score": 0.30,
            "seal_candidate_bbox_norm": "0.1,0.1,0.2,0.2",
        }
        page_two = {
            "visual_risk_score": 0,
            "visual_risk_reasons": "seal_candidate_likely_seal",
            "ela_score": 1,
            "seal_candidate_best_score": 0.82,
            "seal_candidate_semantic_score": 0.76,
            "seal_candidate_bbox_norm": "0.1,0.2,0.3,0.4",
        }
        result, seal_path = aggregate_page_results([
            (1, Path("page-1.png"), page_one),
            (2, Path("page-2.png"), page_two),
        ])
        self.assertEqual(result["visual_risk_score"], 20)
        self.assertEqual(result["seal_candidate_page"], 2)
        self.assertEqual(seal_path, Path("page-2.png"))


if __name__ == "__main__":
    unittest.main()
