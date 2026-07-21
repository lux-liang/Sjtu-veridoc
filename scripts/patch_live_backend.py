#!/usr/bin/env python3
"""Patch the deployment-only app.py with evaluation and seal feature support."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


BACKEND_MARKER = "VERIDOC_BACKEND_ENHANCEMENT_20260717"
EVAL_SCOPE_MARKER = "VERIDOC_DASHBOARD_EVALUATION_SCOPE_20260717"
DUAL_SCOPE_MARKER = "VERIDOC_DUAL_SCOPE_EVALUATION_20260718"
SEAL_SEMANTIC_MARKER = "VERIDOC_SEAL_SEMANTIC_FIELDS_20260718"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise ValueError(f"cannot find backend anchor: {label}")
    return text.replace(old, new, 1)


def patch_evaluation_scope(text: str) -> str:
    if DUAL_SCOPE_MARKER in text:
        return text
    helper = '''# VERIDOC_DASHBOARD_EVALUATION_SCOPE_20260717
# VERIDOC_DUAL_SCOPE_EVALUATION_20260718
def dashboard_binary_metrics(rows: list[dict], threshold: int, score_field: str, scope: str) -> dict:
    labeled = [row for row in rows if row.get("label") in {"fake", "normal"}]
    tp = fp = tn = fn = 0
    for row in labeled:
        actual_fake = row.get("label") == "fake"
        predicted_fake = int(row.get(score_field) or 0) >= threshold
        if actual_fake and predicted_fake:
            tp += 1
        elif not actual_fake and predicted_fake:
            fp += 1
        elif actual_fake:
            fn += 1
        else:
            tn += 1
    divide = lambda numerator, denominator: numerator / denominator if denominator else 0.0
    precision = divide(tp, tp + fp)
    recall = divide(tp, tp + fn)
    fake_count = tp + fn
    return {
        "scope": scope,
        "score_field": score_field,
        "decision_rule": f"{score_field} >= {threshold}",
        "threshold": threshold,
        "positive_label": "fake",
        "sample_count": len(labeled),
        "class_counts": {"fake": fake_count, "normal": tn + fp},
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": round(divide(tp + tn, len(labeled)), 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(divide(2 * precision * recall, precision + recall), 6),
    }


def dashboard_has_explicit_marker(row: dict) -> bool:
    if int(row.get("marker_flag") or 0) == 1:
        return True
    reason_text = str(row.get("combined_risk_reasons") or "")
    return "marker:" in reason_text or any(token in reason_text for token in [
        "ocr:training_synthetic_marker", "ocr:edited_marker", "ocr:void_marker", "ocr:fake_marker",
    ])


def labeled_dashboard_evaluation(rows: list[dict], threshold: int = 25) -> dict:
    labeled = [row for row in rows if row.get("label") in {"fake", "normal"}]
    full = dashboard_binary_metrics(rows, threshold, "combined_risk_score", "dashboard_full_labeled_set")
    marker_score_field = "marker_free_risk_score" if any("marker_free_risk_score" in row for row in labeled) else "combined_risk_score"
    audit = dashboard_binary_metrics(rows, threshold, marker_score_field, "dashboard_marker_free_counterfactual")
    marker_fake = sum(row.get("label") == "fake" and dashboard_has_explicit_marker(row) for row in labeled)
    marker_normal = sum(row.get("label") == "normal" and dashboard_has_explicit_marker(row) for row in labeled)
    result = dict(full)
    result.update({
        "full_set": full,
        "marker_free_audit": audit,
        "marker_driven_fake_count": marker_fake,
        "marker_normal_count": marker_normal,
        "unmarked_fake_count": full["class_counts"]["fake"] - marker_fake,
        "generalization_warning": "Full-set metrics include explicit markers; marker-free metrics suppress that score channel.",
    })
    return result


'''
    if EVAL_SCOPE_MARKER in text:
        start = text.index("# " + EVAL_SCOPE_MARKER)
        end = text.index("def build_dashboard() -> dict:\n", start)
        return text[:start] + helper + text[end:]
    text = replace_once(text, "def build_dashboard() -> dict:\n", helper + "def build_dashboard() -> dict:\n", "dashboard evaluation helper")
    text = replace_once(
        text,
        '    combined_summary = load_json(COMBINED_RISK_JSON, {})\n\n    return {\n',
        '    combined_summary = load_json(COMBINED_RISK_JSON, {})\n'
        '    labeled_evaluation = labeled_dashboard_evaluation(rows)\n\n'
        '    return {\n',
        "dashboard-scoped evaluation",
    )
    text = replace_once(
        text,
        '        "labeled_evaluation": combined_summary.get("labeled_evaluation", {}),\n',
        '        "labeled_evaluation": labeled_evaluation,\n',
        "dashboard evaluation field",
    )
    text = replace_once(
        text,
        '            payload = load_json(COMBINED_RISK_JSON, {}).get("labeled_evaluation", {})\n'
        '            self.send_json(payload)\n',
        '            self.send_json(labeled_dashboard_evaluation(read_feature_rows()))\n',
        "evaluation endpoint scope",
    )
    return text


def patch_semantic_fields(text: str) -> str:
    if SEAL_SEMANTIC_MARKER in text:
        return text
    text = replace_once(
        text,
        '        "qwen_seal_count",\n    }\n',
        '        "qwen_seal_count",\n'
        '        "marker_free_risk_score", "marker_flag",\n'
        '        "seal_candidate_ocr_recommended", "seal_candidate_dense_square",\n'
        '        "seal_candidate_duplicate_count", "seal_ocr_triggered",\n'
        '    }\n',
        "new numeric fields",
    )
    text = replace_once(
        text,
        '            "seal_candidate_crop_path", "seal_candidate_ocr_path",\n',
        '            "seal_candidate_crop_path", "seal_candidate_ocr_path",\n'
        '            # VERIDOC_SEAL_SEMANTIC_FIELDS_20260718\n'
        '            "seal_candidate_ring_uniformity", "seal_candidate_semantic_score",\n'
        '            "seal_candidate_class", "seal_candidate_class_reason",\n'
        '            "seal_candidate_ocr_recommended", "seal_candidate_pixel_aspect",\n'
        '            "seal_candidate_area_ratio", "seal_candidate_center_ring_ratio",\n'
        '            "seal_candidate_dense_square",\n',
        "visual semantic fields",
    )
    text = replace_once(
        text,
        '            "seal_ocr_error",\n        ]:\n',
        '            "seal_ocr_error",\n'
        '            "seal_candidate_class", "seal_candidate_class_confidence",\n'
        '            "seal_candidate_class_reasons", "seal_candidate_duplicate_count",\n'
        '            "seal_candidate_zone", "seal_position_assessment", "seal_ocr_triggered",\n'
        '        ]:\n',
        "seal OCR semantic fields",
    )
    text = replace_once(
        text,
        '            "qwen_seal_candidates",\n        ]:\n',
        '            "qwen_seal_candidates",\n'
        '            "marker_free_risk_score", "marker_free_risk_confidence",\n'
        '            "marker_flag", "marker_tokens",\n'
        '            "seal_candidate_class", "seal_candidate_class_confidence",\n'
        '            "seal_candidate_class_reasons", "seal_candidate_duplicate_count",\n'
        '            "seal_candidate_zone", "seal_position_assessment", "seal_ocr_triggered",\n'
        '        ]:\n',
        "combined dual-scope and semantic fields",
    )
    return text


def patch_backend(text: str) -> str:
    if BACKEND_MARKER in text:
        return patch_semantic_fields(patch_evaluation_scope(text))

    text = replace_once(
        text,
        'QWEN_OCR_SUMMARY_JSON = DATA_ROOT / "features" / "qwen_ocr_summary.json"\n',
        'QWEN_OCR_SUMMARY_JSON = DATA_ROOT / "features" / "qwen_ocr_summary.json"\n'
        '# VERIDOC_BACKEND_ENHANCEMENT_20260717\n'
        'VISUAL_CSV = DATA_ROOT / "features" / "visual_forensics_features.csv"\n'
        'SEAL_OCR_CSV = DATA_ROOT / "features" / "seal_ocr_features.csv"\n',
        "feature constants",
    )
    text = replace_once(
        text,
        '    qwen_by_id = {row.get("document_id"): row for row in read_csv_rows(QWEN_OCR_CSV)}\n',
        '    qwen_by_id = {row.get("document_id"): row for row in read_csv_rows(QWEN_OCR_CSV)}\n'
        '    visual_by_id = {row.get("document_id"): row for row in read_csv_rows(VISUAL_CSV)}\n'
        '    seal_ocr_by_id = {row.get("document_id"): row for row in read_csv_rows(SEAL_OCR_CSV)}\n',
        "feature maps",
    )
    text = replace_once(
        text,
        '        "qwen_risk_score",\n',
        '        "qwen_risk_score",\n'
        '        "visual_page_count_analyzed",\n'
        '        "seal_candidate_count",\n'
        '        "seal_candidate_page",\n'
        '        "seal_candidate_is_monochrome",\n'
        '        "qwen_seal_count",\n',
        "numeric seal fields",
    )
    text = replace_once(
        text,
        '        qwen = qwen_by_id.get(row.get("document_id"), {})\n',
        '        qwen = qwen_by_id.get(row.get("document_id"), {})\n'
        '        visual = visual_by_id.get(row.get("document_id"), {})\n'
        '        seal_ocr = seal_ocr_by_id.get(row.get("document_id"), {})\n'
        '        for key in [\n'
        '            "visual_page_count_analyzed", "seal_candidate_count", "seal_candidate_best_score",\n'
        '            "seal_candidate_bbox_norm", "seal_candidate_is_monochrome", "seal_candidate_page",\n'
        '            "seal_candidate_ring_density", "seal_candidate_angular_coverage",\n'
        '            "seal_candidate_halftone_score", "seal_candidate_mean_saturation",\n'
        '            "seal_candidate_crop_path", "seal_candidate_ocr_path",\n'
        '        ]:\n'
        '            if key in visual:\n'
        '                row[key] = visual.get(key, "")\n'
        '        for key in [\n'
        '            "seal_ocr_text", "seal_ocr_mean_confidence", "seal_entity_best_match",\n'
        '            "seal_entity_similarity", "seal_ocr_risk_reasons", "seal_polar_image_path",\n'
        '            "seal_ocr_error",\n'
        '        ]:\n'
        '            if key in seal_ocr:\n'
        '                row[key] = seal_ocr.get(key, "")\n',
        "row feature merge",
    )
    text = replace_once(
        text,
        '            "extracted_fields_json",\n',
        '            "extracted_fields_json",\n'
        '            "seal_ocr_text",\n'
        '            "seal_entity_best_match",\n'
        '            "seal_entity_similarity",\n'
        '            "seal_ocr_error",\n'
        '            "qwen_seal_count",\n'
        '            "qwen_seal_candidates",\n',
        "combined seal fields",
    )
    text = replace_once(
        text,
        '    training = load_training_history()\n\n    return {\n',
        '    training = load_training_history()\n'
        '    combined_summary = load_json(COMBINED_RISK_JSON, {})\n\n'
        '    return {\n',
        "dashboard combined summary",
    )
    text = replace_once(
        text,
        '        "combined_summary": load_json(COMBINED_RISK_JSON, {}),\n',
        '        "labeled_evaluation": combined_summary.get("labeled_evaluation", {}),\n'
        '        "combined_summary": combined_summary,\n',
        "dashboard evaluation payload",
    )
    text = replace_once(
        text,
        '        if parsed.path == "/api/documents":\n',
        '        if parsed.path == "/api/evaluation":\n'
        '            payload = load_json(COMBINED_RISK_JSON, {}).get("labeled_evaluation", {})\n'
        '            self.send_json(payload)\n'
        '            return\n'
        '        if parsed.path == "/api/documents":\n',
        "evaluation endpoint",
    )
    text = replace_once(
        text,
        '            min_risk = int((query.get("min_risk") or ["0"])[0] or 0)\n',
        '            min_risk = int((query.get("min_risk") or ["0"])[0] or 0)\n'
        '            try:\n'
        '                limit = max(1, min(int((query.get("limit") or ["500"])[0] or 500), 2000))\n'
        '            except ValueError:\n'
        '                limit = 500\n',
        "document limit query",
    )
    text = replace_once(
        text,
        '            self.send_json({"rows": [row_public(row) for row in rows[:500]], "count": len(rows)})\n',
        '            self.send_json({"rows": [row_public(row) for row in rows[:limit]], "count": len(rows), "limit": limit})\n',
        "document result limit",
    )
    return patch_semantic_fields(patch_evaluation_scope(text))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    app_path = args.root.resolve() / "app.py"
    original = app_path.read_text(encoding="utf-8")
    patched = patch_backend(original)
    if patched == original:
        print("backend already patched")
        return
    if args.dry_run:
        print(f"would patch {app_path}")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.root.resolve() / f".backup_backend_{stamp}" / "app.py"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app_path, backup)
    app_path.write_text(patched, encoding="utf-8")
    print(f"patched {app_path}; backup={backup}")


if __name__ == "__main__":
    main()
