#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/luxliang/sjtu_material_ai"
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
  --dpi 110 \
  --max-normal-per-type 20

python3 "$ROOT/src/train_object_classifier.py" \
  --features-csv "$ROOT/outputs/features/pdf_object_features.csv" \
  --out-dir "$ROOT/outputs/models/object_classifier" \
  --epochs 120 \
  --lr 0.08 \
  --l2 0.01 \
  --val-ratio 0.25 \
  --seed 42

python3 "$ROOT/src/build_combined_risk.py" \
  --pdf-csv "$ROOT/outputs/features/pdf_object_features.csv" \
  --visual-csv "$ROOT/outputs/features/visual_forensics_features.csv" \
  --text-csv "$ROOT/outputs/features/text_business_features.csv" \
  --out-csv "$ROOT/outputs/features/combined_risk_features.csv" \
  --out-json "$ROOT/outputs/features/combined_risk_summary.json"

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
