#!/usr/bin/env python3
"""OCR and entity reconciliation for localized seal candidates.

Input is the visual-forensics CSV produced with ``--seal-crop-dir``.  The
script unwraps circular text into a rectangular strip, runs local Tesseract
when available, and compares recognized seal text with document entities.
All reasons are review/context signals; they do not prove forgery by themselves.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


ENTITY_KEYS = {
    "party_a", "party_b", "buyer", "seller", "payer", "payee", "name",
    "company", "organization", "issuer", "bank_name", "creditor", "debtor",
}

SEAL_TEXT_TOKENS = (
    "公章", "合同专用章", "财务专用章", "发票专用章", "业务专用章",
    "有限公司", "股份有限公司", "company", "corporation", "limited",
)


def as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_bbox(value: str) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(item) for item in str(value or "").split(","))
    except ValueError:
        return None
    if len(values) != 4:
        return None
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def candidate_zone(bbox_value: str) -> str:
    bbox = parse_bbox(bbox_value)
    if not bbox:
        return "none"
    center_y = (bbox[1] + bbox[3]) / 2.0
    if center_y < 0.25:
        return "top"
    if center_y >= 0.68:
        return "bottom"
    return "middle"


def image_dhash(path: Path, hash_size: int = 8) -> str:
    """Return a dependency-free perceptual hash for repeated emblem detection."""
    try:
        with Image.open(path) as image:
            gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
            gray = gray.resize((hash_size + 1, hash_size))
            array = np.asarray(gray, dtype=np.int16)
    except (OSError, ValueError):
        return ""
    bits = array[:, 1:] > array[:, :-1]
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def classify_candidate(row: dict, duplicate_count: int = 1) -> tuple[str, float, list[str], bool]:
    """Return seal/logo/unknown, confidence, reasons and whether OCR is useful."""
    if not row.get("seal_candidate_bbox_norm"):
        return "none", 1.0, ["candidate_missing"], False

    bbox = parse_bbox(row.get("seal_candidate_bbox_norm", ""))
    area_ratio = as_float(row.get("seal_candidate_area_ratio"))
    if not area_ratio and bbox:
        area_ratio = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    ring = as_float(row.get("seal_candidate_ring_density"))
    center = as_float(row.get("seal_candidate_center_density"))
    ink = as_float(row.get("seal_candidate_ink_ratio"))
    saturation = as_float(row.get("seal_candidate_mean_saturation"))
    semantic = as_float(row.get("seal_candidate_semantic_score"))
    initial = str(row.get("seal_candidate_class") or "unknown")
    dense_square = str(row.get("seal_candidate_dense_square") or "0") == "1" or (
        ink >= 0.285 and ring >= 0.28 and center >= 0.22 and saturation <= 0.10
    )
    center_ring_ratio = center / max(ring, 1e-6)
    strong_outer_ring = (
        ring >= 0.085
        and center_ring_ratio <= 0.45
        and area_ratio >= 0.0035
        and ink <= 0.22
    )
    repeated_emblem = (
        duplicate_count >= 3
        and 0.003 <= area_ratio <= 0.008
        and (center_ring_ratio >= 1.08 or ring < 0.06)
        and not strong_outer_ring
        and not dense_square
    )
    repeated_layout_fragment = duplicate_count >= 3 and area_ratio >= 0.012 and not strong_outer_ring
    zone = candidate_zone(row.get("seal_candidate_bbox_norm", ""))
    header_emblem = (
        row.get("doc_type") in {"credit_report", "bank_page"}
        and zone == "top"
        and area_ratio <= 0.03
        and saturation >= 0.12
        and not dense_square
    )

    if dense_square:
        return "unknown", 0.96, ["dense_square_pattern", "ocr_not_recommended"], False
    if repeated_emblem:
        confidence = min(0.97, 0.68 + 0.04 * duplicate_count)
        return "logo", confidence, ["repeated_compact_emblem", f"duplicate_hash:{duplicate_count}"], False
    if header_emblem:
        return "logo", 0.78, ["header_emblem_context"], False
    if repeated_layout_fragment:
        confidence = min(0.96, 0.66 + 0.03 * duplicate_count)
        return "unknown", confidence, ["repeated_layout_fragment", f"duplicate_hash:{duplicate_count}"], False
    if initial == "seal":
        return "seal", max(0.62, semantic), [str(row.get("seal_candidate_class_reason") or "annular_stamp_geometry")], True

    recommended = str(row.get("seal_candidate_ocr_recommended") or "0") == "1" or semantic >= 0.42
    reasons = [str(row.get("seal_candidate_class_reason") or "insufficient_stamp_geometry")]
    return "unknown", max(0.35, semantic), reasons, recommended


def refine_candidate_class(candidate_class: str, confidence: float, reasons: list[str], ocr_text: str, ocr_confidence: float, row: dict) -> tuple[str, float, list[str]]:
    """Promote an unknown annular candidate only when OCR supplies seal semantics."""
    if candidate_class != "unknown" or ocr_confidence < 45:
        return candidate_class, confidence, reasons
    normalized = normalize_text(ocr_text)
    has_seal_text = any(normalize_text(token) in normalized for token in SEAL_TEXT_TOKENS)
    if has_seal_text and as_float(row.get("seal_candidate_ring_density")) >= 0.065:
        return "seal", max(confidence, 0.74), list(dict.fromkeys(reasons + ["seal_text_semantics"]))
    return candidate_class, confidence, reasons


def position_assessment(doc_type: str, candidate_class: str, bbox_value: str) -> tuple[str, str]:
    zone = candidate_zone(bbox_value)
    if candidate_class != "seal":
        return zone, "not_applicable"
    signature_types = {"contract", "invoice", "settlement_statement"}
    if doc_type in signature_types and zone == "bottom":
        return zone, "expected_signature_zone"
    if doc_type in signature_types and zone == "top":
        return zone, "unusual_header_zone_review"
    return zone, "context_unknown"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def normalize_text(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def polar_unwrap(image: Image.Image, radial_samples: int = 170, angular_samples: int = 900) -> Image.Image:
    """Unwrap an annular band so circular seal text becomes approximately flat."""
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    array = np.asarray(gray, dtype=np.uint8)
    h, w = array.shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    radius_max = max(2.0, min(w, h) * 0.47)
    radii = np.linspace(radius_max * 0.30, radius_max, radial_samples, dtype=np.float32)[:, None]
    angles = np.linspace(-math.pi, math.pi, angular_samples, endpoint=False, dtype=np.float32)[None, :]
    xs = np.clip(np.rint(cx + radii * np.cos(angles)).astype(np.int32), 0, w - 1)
    ys = np.clip(np.rint(cy + radii * np.sin(angles)).astype(np.int32), 0, h - 1)
    unwrapped = array[ys, xs]
    result = Image.fromarray(unwrapped)
    return ImageOps.autocontrast(result, cutoff=1).filter(ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=3))


def tesseract_languages() -> tuple[str, str]:
    if not shutil.which("tesseract"):
        return "", "tesseract_not_found"
    proc = run(["tesseract", "--list-langs"])
    if proc.returncode != 0:
        return "", "tesseract_language_query_failed"
    available = {line.strip() for line in proc.stdout.splitlines() if line.strip() and "List of" not in line}
    selected = [lang for lang in ("chi_sim", "eng") if lang in available]
    if not selected and available:
        selected = [sorted(available)[0]]
    return "+".join(selected), "" if selected else "tesseract_no_languages"


def tesseract_tsv(image_path: Path, language: str, psm: int) -> tuple[str, float, str]:
    cmd = ["tesseract", str(image_path), "stdout", "--psm", str(psm), "tsv"]
    if language:
        cmd.extend(["-l", language])
    proc = run(cmd)
    if proc.returncode != 0:
        return "", 0.0, f"tesseract_failed:{proc.stderr[:120]}"
    reader = csv.DictReader(proc.stdout.splitlines(), delimiter="\t")
    words = []
    confidences = []
    for row in reader:
        word = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            confidence = -1
        if word:
            words.append(word)
        if confidence >= 0 and word:
            confidences.append(confidence)
    return " ".join(words), sum(confidences) / max(len(confidences), 1), ""


def document_entities(raw_json: str) -> list[str]:
    try:
        payload = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return []
    entities = []

    def visit(value, key=""):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and (key in ENTITY_KEYS or any(token in key for token in ENTITY_KEYS)):
            cleaned = value.strip()
            if 2 <= len(normalize_text(cleaned)) <= 80:
                entities.append(cleaned)

    visit(payload)
    return list(dict.fromkeys(entities))


def partial_similarity(left: str, right: str) -> float:
    left, right = normalize_text(left), normalize_text(right)
    if not left or not right:
        return 0.0
    if min(len(left), len(right)) < 4:
        return 0.0
    if left in right or right in left:
        return 1.0
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(long) <= len(short) + 3:
        return SequenceMatcher(None, short, long).ratio()
    window = len(short)
    best = 0.0
    for start in range(0, len(long) - window + 1):
        best = max(best, SequenceMatcher(None, short, long[start: start + window]).ratio())
    return best


def best_entity_match(ocr_text: str, entities: list[str]) -> tuple[str, float]:
    best_entity = ""
    best_score = 0.0
    for entity in entities:
        score = partial_similarity(entity, ocr_text)
        if score > best_score:
            best_entity, best_score = entity, score
    return best_entity, best_score


def ocr_candidate(path: Path, language: str, polar_dir: Path | None, stem: str) -> tuple[str, float, str, str]:
    with Image.open(path) as image:
        base = ImageOps.autocontrast(image.convert("L"), cutoff=1)
        polar = polar_unwrap(base)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        base_path = tmp_root / "base.png"
        polar_path = tmp_root / "polar.png"
        mirror_path = tmp_root / "polar_mirror.png"
        base.save(base_path)
        polar.save(polar_path)
        ImageOps.mirror(polar).save(mirror_path)
        attempts = [
            tesseract_tsv(base_path, language, 11),
            tesseract_tsv(polar_path, language, 7),
            tesseract_tsv(mirror_path, language, 7),
        ]
    texts = []
    confidences = []
    errors = []
    for text, confidence, error in attempts:
        if text and normalize_text(text) not in {normalize_text(item) for item in texts}:
            texts.append(text)
        if text:
            confidences.append(confidence)
        if error:
            errors.append(error)
    saved_polar = ""
    if polar_dir:
        polar_dir.mkdir(parents=True, exist_ok=True)
        output = polar_dir / f"{stem}_seal_polar.png"
        polar.save(output)
        saved_polar = str(output)
    return " | ".join(texts), sum(confidences) / max(len(confidences), 1), ";".join(dict.fromkeys(errors)), saved_polar


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-csv", required=True)
    parser.add_argument("--text-csv", default="")
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--polar-dir", default="")
    args = parser.parse_args()

    visual_rows = read_csv(Path(args.visual_csv))
    text_rows = {row.get("document_id"): row for row in read_csv(Path(args.text_csv))} if args.text_csv else {}
    language, setup_error = tesseract_languages()
    polar_dir = Path(args.polar_dir) if args.polar_dir else None
    candidate_hashes = {}
    for row in visual_rows:
        crop_value = row.get("seal_candidate_crop_path") or row.get("seal_candidate_ocr_path") or ""
        if crop_value and row.get("seal_candidate_bbox_norm"):
            candidate_hashes[row.get("document_id", "")] = image_dhash(Path(crop_value))
    hash_counts = Counter(value for value in candidate_hashes.values() if value)
    records = []
    for row in visual_rows:
        document_id = row.get("document_id", "")
        crop_value = row.get("seal_candidate_ocr_path") or ""
        crop_path = Path(crop_value) if crop_value else None
        ocr_text = ""
        confidence = 0.0
        error = setup_error
        saved_polar = ""
        candidate_hash = candidate_hashes.get(document_id, "")
        duplicate_count = hash_counts.get(candidate_hash, 0) if candidate_hash else 0
        candidate_class, class_confidence, class_reasons, should_ocr = classify_candidate(row, duplicate_count)
        if not row.get("seal_candidate_bbox_norm"):
            error = "seal_candidate_missing"
        elif not should_ocr:
            error = "seal_ocr_skipped:" + class_reasons[0]
        elif crop_path is None or not crop_path.is_file():
            error = "seal_ocr_crop_missing"
        elif not setup_error:
            ocr_text, confidence, error, saved_polar = ocr_candidate(crop_path, language, polar_dir, document_id)
        candidate_class, class_confidence, class_reasons = refine_candidate_class(
            candidate_class, class_confidence, class_reasons, ocr_text, confidence, row
        )
        zone, position = position_assessment(row.get("doc_type", ""), candidate_class, row.get("seal_candidate_bbox_norm", ""))
        entities = document_entities(text_rows.get(document_id, {}).get("extracted_fields_json", ""))
        matched_entity, similarity = best_entity_match(ocr_text, entities)
        reasons = []
        if candidate_class == "seal":
            reasons.append("seal_candidate_likely_seal")
        elif candidate_class == "logo":
            reasons.append("seal_candidate_likely_logo")
        elif candidate_class == "unknown" and row.get("seal_candidate_bbox_norm"):
            reasons.append("seal_candidate_unknown_review")
        if position == "expected_signature_zone":
            reasons.append("seal_position_expected_context")
        elif position == "unusual_header_zone_review":
            reasons.append("seal_position_unusual_review")
        if candidate_class == "seal" and len(normalize_text(ocr_text)) >= 4 and entities and matched_entity and confidence >= 35 and similarity >= 0.62:
            reasons.append("seal_ocr_entity_match_context")
        elif candidate_class == "seal" and len(normalize_text(ocr_text)) >= 4 and entities and matched_entity and confidence >= 45 and similarity < 0.30:
            reasons.append("seal_ocr_entity_mismatch_review")
        if candidate_class == "seal" and ocr_text and confidence < 35:
            reasons.append("seal_ocr_low_confidence_review")
        records.append({
            "document_id": document_id,
            "label": row.get("label", ""),
            "doc_type": row.get("doc_type", ""),
            "seal_candidate_best_score": row.get("seal_candidate_best_score", ""),
            "seal_candidate_bbox_norm": row.get("seal_candidate_bbox_norm", ""),
            "seal_candidate_is_monochrome": row.get("seal_candidate_is_monochrome", ""),
            "seal_candidate_semantic_score": row.get("seal_candidate_semantic_score", ""),
            "seal_candidate_class": candidate_class,
            "seal_candidate_class_confidence": round(class_confidence, 6),
            "seal_candidate_class_reasons": "|".join(class_reasons),
            "seal_candidate_duplicate_count": duplicate_count,
            "seal_candidate_perceptual_hash": candidate_hash,
            "seal_candidate_zone": zone,
            "seal_position_assessment": position,
            "seal_ocr_triggered": int(should_ocr and not setup_error and crop_path is not None and crop_path.is_file()),
            "seal_ocr_text": ocr_text[:1200],
            "seal_ocr_mean_confidence": round(confidence, 4),
            "seal_document_entities_json": json.dumps(entities, ensure_ascii=False),
            "seal_entity_best_match": matched_entity,
            "seal_entity_similarity": round(similarity, 6),
            "seal_ocr_risk_reasons": "|".join(reasons),
            "seal_polar_image_path": saved_polar,
            "seal_ocr_error": error,
        })

    fields = list(records[0].keys()) if records else []
    output = Path(args.out_csv)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(records)
    summary = {
        "count": len(records),
        "candidate_count": sum(bool(record["seal_candidate_bbox_norm"]) for record in records),
        "class_counts": dict(Counter(record["seal_candidate_class"] for record in records)),
        "ocr_triggered_count": sum(record["seal_ocr_triggered"] for record in records),
        "ocr_skipped_count": sum(record["seal_ocr_error"].startswith("seal_ocr_skipped:") for record in records),
        "ocr_success_count": sum(bool(record["seal_ocr_text"]) for record in records),
        "entity_match_count": sum("seal_ocr_entity_match_context" in record["seal_ocr_risk_reasons"] for record in records),
        "entity_mismatch_review_count": sum("seal_ocr_entity_mismatch_review" in record["seal_ocr_risk_reasons"] for record in records),
        "setup_error": setup_error,
    }
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
