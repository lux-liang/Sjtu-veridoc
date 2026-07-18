#!/usr/bin/env bash
set -euo pipefail

ROOT="${VERIDOC_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FEATURE_DIR="$ROOT/outputs/features"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1,2,3}"

python3 "$ROOT/src/prepare_dataset.py" \
  --normal-zip "$ROOT/data/raw/正常材料包.zip" \
  --fake-zip "$ROOT/data/raw/虚假材料包.zip" \
  --prepared-dir "$ROOT/data/prepared" \
  --manifest "$ROOT/outputs/manifest.csv"

python3 "$ROOT/src/extract_pdf_features.py" \
  --manifest "$ROOT/outputs/manifest.csv" \
  --out-csv "$ROOT/outputs/features/pdf_object_features.csv" \
  --out-json "$ROOT/outputs/features/pdf_object_summary.json"

python3 "$ROOT/src/analyze_text_business_rules.py" \
  --manifest "$ROOT/outputs/manifest.csv" \
  --out-csv "$ROOT/outputs/features/text_business_features.csv" \
  --out-json "$ROOT/outputs/features/text_business_summary.json" \
  --out-words-csv "$ROOT/outputs/features/text_word_coordinates.csv" \
  --max-pages 3

python3 "$ROOT/src/analyze_visual_forensics.py" \
  --manifest "$ROOT/outputs/manifest.csv" \
  --out-csv "$ROOT/outputs/features/visual_forensics_features.csv" \
  --out-json "$ROOT/outputs/features/visual_forensics_summary.json" \
  --render-dir "$ROOT/outputs/visual_forensics" \
  --seal-crop-dir "$ROOT/outputs/seal_candidates" \
  --dpi 110 \
  --max-pages 3 \
  --max-normal-per-type 20

python3 "$ROOT/src/analyze_seal_ocr.py" \
  --visual-csv "$ROOT/outputs/features/visual_forensics_features.csv" \
  --text-csv "$ROOT/outputs/features/text_business_features.csv" \
  --out-csv "$ROOT/outputs/features/seal_ocr_features.csv" \
  --out-json "$ROOT/outputs/features/seal_ocr_summary.json" \
  --polar-dir "$ROOT/outputs/seal_candidates/polar"

python3 "$ROOT/src/extract_text_markers.py" \
  --feature-dir "$FEATURE_DIR" \
  --out "$FEATURE_DIR/text_marker_flags.csv"

python3 "$ROOT/scripts/audit_feature_alignment.py" \
  --base-csv "$FEATURE_DIR/pdf_object_features.csv" \
  --feature "text=$FEATURE_DIR/text_business_features.csv" \
  --feature "visual=$FEATURE_DIR/visual_forensics_features.csv" \
  --feature "seal=$FEATURE_DIR/seal_ocr_features.csv" \
  --require-complete text \
  --out-json "$FEATURE_DIR/feature_alignment_pre_fusion.json"

python3 "$ROOT/src/train_object_classifier.py" \
  --features-csv "$ROOT/outputs/features/pdf_object_features.csv" \
  --out-dir "$ROOT/outputs/models/object_classifier" \
  --epochs 120 \
  --lr 0.08 \
  --l2 0.01 \
  --val-ratio 0.25 \
  --seed 42

OCR_ARGS=()
if [[ -f "$FEATURE_DIR/ocr_deepseek_features.csv" ]]; then
  OCR_ARGS=(--ocr-csv "$FEATURE_DIR/ocr_deepseek_features.csv")
fi

python3 "$ROOT/src/build_combined_risk.py" \
  --pdf-csv "$ROOT/outputs/features/pdf_object_features.csv" \
  --visual-csv "$ROOT/outputs/features/visual_forensics_features.csv" \
  --text-csv "$ROOT/outputs/features/text_business_features.csv" \
  --marker-csv "$ROOT/outputs/features/text_marker_flags.csv" \
  --seal-ocr-csv "$ROOT/outputs/features/seal_ocr_features.csv" \
  "${OCR_ARGS[@]}" \
  --out-csv "$ROOT/outputs/features/combined_risk_features.csv" \
  --out-json "$ROOT/outputs/features/combined_risk_summary.json"

python3 "$ROOT/scripts/audit_feature_alignment.py" \
  --base-csv "$FEATURE_DIR/pdf_object_features.csv" \
  --feature "combined=$FEATURE_DIR/combined_risk_features.csv" \
  --require-complete combined \
  --out-json "$FEATURE_DIR/feature_alignment_post_fusion.json"

python3 "$ROOT/src/render_pages.py" \
  --manifest "$ROOT/outputs/manifest.csv" \
  --render-dir "$ROOT/outputs/renders" \
  --render-manifest "$ROOT/outputs/render_manifest.csv" \
  --dpi 144 \
  --max-pages 4 \
  --workers 16

python3 "$ROOT/src/train_doc_classifier.py" \
  --render-manifest "$ROOT/outputs/render_manifest.csv" \
  --model-dir "$ROOT/outputs/models/doc_classifier" \
  --epochs 20 \
  --batch-size 64 \
  --image-size 224 \
  --lr 0.0003 \
  --num-workers 8 \
  --val-ratio 0.2
