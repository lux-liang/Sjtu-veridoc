#!/usr/bin/env python3
"""Evaluate color-agnostic seal localization on a manifest."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyze_visual_forensics import color_agnostic_seal_features


def parse_box(value: str) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(item) for item in (value or "").split(","))
    except ValueError:
        return None
    return values if len(values) == 4 else None


def iou(left, right) -> float:
    if not left or not right:
        return 0.0
    ax0, ay0, ax1, ay1 = left
    bx0, by0, bx1, by1 = right
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    union = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0) + max(0.0, bx1 - bx0) * max(0.0, by1 - by0) - intersection
    return intersection / union if union else 0.0


def metrics(rows: list[dict], threshold: float) -> dict:
    tp = fp = tn = fn = 0
    class_tp = class_fp = class_tn = class_fn = 0
    ious = []
    class_ious = []
    by_type = defaultdict(lambda: {"count": 0, "detected": 0, "iou_sum": 0.0})
    class_by_type = defaultdict(lambda: {"count": 0, "detected": 0, "iou_sum": 0.0})
    for row in rows:
        with Image.open(row["path"]) as image:
            features = color_agnostic_seal_features(image)
        predicted = features["seal_candidate_best_score"] >= threshold
        class_predicted = features.get("seal_candidate_class") == "seal"
        actual = str(row.get("has_seal", "0")) == "1"
        if actual and predicted:
            tp += 1
        elif actual:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
        if actual and class_predicted:
            class_tp += 1
        elif actual:
            class_fn += 1
        elif class_predicted:
            class_fp += 1
        else:
            class_tn += 1
        overlap = iou(parse_box(row.get("expected_bbox_norm", "")), parse_box(features["seal_candidate_bbox_norm"])) if actual and predicted else 0.0
        class_overlap = iou(parse_box(row.get("expected_bbox_norm", "")), parse_box(features["seal_candidate_bbox_norm"])) if actual and class_predicted else 0.0
        if actual and predicted:
            ious.append(overlap)
        if actual and class_predicted:
            class_ious.append(class_overlap)
        bucket = by_type[row.get("seal_type", "unknown")]
        bucket["count"] += 1
        bucket["detected"] += int(predicted)
        bucket["iou_sum"] += overlap
        class_bucket = class_by_type[row.get("seal_type", "unknown")]
        class_bucket["count"] += 1
        class_bucket["detected"] += int(class_predicted)
        class_bucket["iou_sum"] += class_overlap

    divide = lambda a, b: a / b if b else 0.0
    precision = divide(tp, tp + fp)
    recall = divide(tp, tp + fn)
    class_precision = divide(class_tp, class_tp + class_fp)
    class_recall = divide(class_tp, class_tp + class_fn)
    result = {
        "threshold": threshold,
        "sample_count": len(rows),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(divide(2 * precision * recall, precision + recall), 6),
        "mean_iou_detected": round(sum(ious) / max(len(ious), 1), 6),
        "iou_at_50_recall": round(sum(value >= 0.5 for value in ious) / max(tp + fn, 1), 6),
        "by_type": {},
        "seal_classification": {
            "rule": "seal_candidate_class == seal",
            "confusion_matrix": {"tp": class_tp, "fp": class_fp, "tn": class_tn, "fn": class_fn},
            "precision": round(class_precision, 6),
            "recall": round(class_recall, 6),
            "f1": round(divide(2 * class_precision * class_recall, class_precision + class_recall), 6),
            "mean_iou_detected": round(sum(class_ious) / max(len(class_ious), 1), 6),
            "by_type": {},
        },
    }
    for name, bucket in sorted(by_type.items()):
        result["by_type"][name] = {
            "count": bucket["count"],
            "detection_rate": round(bucket["detected"] / max(bucket["count"], 1), 6),
            "mean_iou": round(bucket["iou_sum"] / max(bucket["detected"], 1), 6),
        }
    for name, bucket in sorted(class_by_type.items()):
        result["seal_classification"]["by_type"][name] = {
            "count": bucket["count"],
            "detection_rate": round(bucket["detected"] / max(bucket["count"], 1), 6),
            "mean_iou": round(bucket["iou_sum"] / max(bucket["detected"], 1), 6),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="outputs/seal_localization_manifest.csv")
    parser.add_argument("--threshold", type=float, default=0.52)
    parser.add_argument("--out-json", default="outputs/seal_localization_metrics.json")
    args = parser.parse_args()
    with Path(args.manifest).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = metrics(rows, args.threshold)
    output = Path(args.out_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
