#!/usr/bin/env python3
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

STRONG_REASON_WEIGHTS = {
    "object:pdf_smask_present": 26,
    "object:full_page_image_with_local_overlays": 24,
    "object:dense_pdf_image_overlays": 20,
    "object:embedded_script_or_file": 18,
    "object:poppler_font_warning": 10,
    "object:high_font_object_count": 8,
    "object:incremental_update": 8,
    "visual:local_noise_block_inconsistency": 12,
    "visual:dense_edge_or_paste_boundary": 10,
    "visual:ela_high_error_region": 16,
    "visual:jpeg_block_artifact_inconsistency": 14,
    "visual:seal_hard_edge_overlay": 16,
    "text:invoice_amount_tax_total_mismatch": 22,
    "text:settlement_amount_total_mismatch": 22,
    "text:bank_balance_sequence_broken": 18,
    "text:contract_amount_date_conflict": 18,
    "text:duplicated_text_lines": 12,
    "text:text_layer_repetition": 10,
}

WEAK_REASON_WEIGHTS = {
    "object:missing_creator_producer": 4,
    "text:pdf_text_layer_missing_or_unreadable": 4,
    "text:very_sparse_text_layer": 4,
    "text:credit_report_id_missing": 3,
    "text:credit_report_date_missing": 3,
    "text:bank_account_number_missing": 3,
    "text:contract_date_missing": 3,
    "text:contract_party_field_missing": 3,
    "text:invoice_number_missing": 3,
    "text:bank_amount_sequence_sparse": 4,
    "visual:red_stamp_like_region": 3,
    "visual:seal_red_connected_component": 4,
    "visual:seal_flat_color_overlay": 4,
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def as_int(value) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def reasons(value: str) -> list[str]:
    return [item for item in (value or "").split("|") if item]


def cap_sum(values: list[int], cap: int) -> int:
    return min(cap, sum(values))


def is_scanner_like_pdf(pdf: dict) -> bool:
    producer = f"{pdf.get('creator', '')} {pdf.get('producer', '')}".lower()
    scanner_keywords = ["intsig", "docucentre", "quartz", "qqbrowser", "scanner", "scan", "camscanner"]
    pages = max(1, as_int(pdf.get("pages")))
    large_images = as_int(pdf.get("large_image_count"))
    font_objects = as_int(pdf.get("font_object_count"))
    has_scanner_producer = any(item in producer for item in scanner_keywords)
    image_backed = large_images >= max(1, pages // 2) and font_objects == 0
    return has_scanner_producer and image_backed


def normalize_reasons_for_context(pdf: dict, all_reasons: list[str]) -> list[str]:
    if not is_scanner_like_pdf(pdf):
        return all_reasons
    downgraded = []
    scanner_visual_review = {
        "visual:local_noise_block_inconsistency",
        "visual:dense_edge_or_paste_boundary",
        "visual:seal_red_connected_component",
        "visual:seal_flat_color_overlay",
        "visual:red_stamp_like_region",
    }
    for reason in all_reasons:
        if reason == "object:full_page_image_with_local_overlays":
            downgraded.append("object:scanner_image_layer_review")
        elif reason == "object:embedded_script_or_file" and as_int(pdf.get("javascript_count")) <= as_int(pdf.get("pages")):
            downgraded.append("object:scanner_annotation_or_app_metadata")
        elif reason in scanner_visual_review:
            downgraded.append(reason.replace("visual:", "visual:scanner_"))
        else:
            downgraded.append(reason)
    return downgraded


def score_v2(pdf: dict, object_score: int, visual_score: int, business_score: int, all_reasons: list[str]) -> tuple[int, str, list[str]]:
    all_reasons = normalize_reasons_for_context(pdf, all_reasons)
    unique_reasons = list(dict.fromkeys(all_reasons))
    strong_hits = [r for r in unique_reasons if r in STRONG_REASON_WEIGHTS]
    weak_hits = [r for r in unique_reasons if r in WEAK_REASON_WEIGHTS]
    structural_hits = [r for r in strong_hits if r.startswith("object:")]
    corroborating_visual_reasons = {
        "visual:seal_hard_edge_overlay",
        "visual:ela_high_error_region",
        "visual:jpeg_block_artifact_inconsistency",
    }
    visual_hits = [r for r in strong_hits if r in corroborating_visual_reasons]
    logic_hits = [r for r in strong_hits if r.startswith("text:")]
    corroborating_strong_reasons = {
        "object:pdf_smask_present",
        "object:dense_pdf_image_overlays",
        "object:embedded_script_or_file",
        "object:full_page_image_with_local_overlays",
        "visual:seal_hard_edge_overlay",
        "visual:ela_high_error_region",
        "visual:jpeg_block_artifact_inconsistency",
        "text:invoice_amount_tax_total_mismatch",
        "text:settlement_amount_total_mismatch",
        "text:bank_balance_sequence_broken",
        "text:contract_amount_date_conflict",
    }
    corroborating_strong_hits = [r for r in strong_hits if r in corroborating_strong_reasons]

    strong_score = cap_sum([STRONG_REASON_WEIGHTS[r] for r in strong_hits], 78)
    weak_score = cap_sum([WEAK_REASON_WEIGHTS[r] for r in weak_hits], 12)

    calibrated_component = int(round(
        object_score * 0.28 +
        visual_score * 0.38 +
        business_score * 0.18
    ))
    combined = max(strong_score, calibrated_component) + weak_score

    evidence_domains = sum(1 for hits in [structural_hits, visual_hits, logic_hits] if hits)
    if evidence_domains >= 2:
        combined += 12
        unique_reasons.append("cross:v2_strong_multi_domain_agreement")
    elif corroborating_strong_hits and weak_hits:
        combined += 5
        unique_reasons.append("cross:v2_strong_weak_corroboration")

    # A very high legacy object score should remain visible, but it should not
    # dominate when it only comes from weak metadata/text-layer symptoms.
    if object_score >= 80 and structural_hits and not is_scanner_like_pdf(pdf):
        combined = max(combined, 62)
    if visual_score >= 45 and visual_hits:
        combined = max(combined, 55)
    if business_score >= 45 and logic_hits:
        combined = max(combined, 50)

    combined = min(100, max(0, combined))
    if strong_hits:
        confidence = "high" if evidence_domains >= 2 or combined >= 60 else "medium"
    elif weak_hits:
        confidence = "low"
    else:
        confidence = "none"
    return combined, confidence, unique_reasons


def ocr_marker_reasons(text: str) -> list[str]:
    normalized = (text or "").lower()
    markers = []
    marker_map = {
        "synthetic": "ocr:training_synthetic_marker",
        "training": "ocr:training_synthetic_marker",
        "generated for model training": "ocr:training_synthetic_marker",
        "edited": "ocr:edited_marker",
        "void": "ocr:void_marker",
        "fake": "ocr:fake_marker",
        "作废": "ocr:void_marker",
        "伪造": "ocr:fake_marker",
        "篡改": "ocr:edited_marker",
    }
    for needle, reason in marker_map.items():
        if needle in normalized:
            markers.append(reason)
    return list(dict.fromkeys(markers))


def apply_ocr_evidence(combined: int, confidence: str, all_reasons: list[str], ocr: dict) -> tuple[int, str, list[str]]:
    ocr_score = as_int(ocr.get("ocr_risk_score"))
    ocr_reasons = [f"ocr:{item}" for item in reasons(ocr.get("ocr_risk_reasons", ""))]
    marker_reasons = ocr_marker_reasons(ocr.get("ocr_text_preview", ""))
    all_reasons = list(dict.fromkeys(all_reasons + ocr_reasons + marker_reasons))

    if marker_reasons:
        combined = max(combined, 72 if any(r in marker_reasons for r in ["ocr:edited_marker", "ocr:void_marker", "ocr:fake_marker"]) else 58)
        confidence = "high"
    elif ocr_score >= 35 and combined >= 18:
        combined = max(combined, min(55, int(round((combined + ocr_score) / 2 + 8))))
        confidence = "medium" if confidence in {"none", "low"} else confidence
    elif ocr_score >= 25 and confidence == "none":
        # Low-confidence OCR alone is a review signal, not a fraud score.
        combined = max(combined, 12)
        confidence = "low"
    return min(100, combined), confidence, all_reasons


# ---------------------------------------------------------------------------
# v3: sign-corrected evidence scoring
# ---------------------------------------------------------------------------
# Empirically (see ITERATION_REPORT), v2 treated several reasons as "strong"
# that actually fire mostly on genuine SCANNED normals (paste-boundary,
# full-page-image overlays, missing creator/producer, credit-report field
# missing). That inverted the ranking (AUC 0.18: normals scored riskier than
# fakes). v3 scores ONLY reasons whose empirical direction points at fraud,
# neutralises provenance/scan artifacts (kept as context tags, weight 0), and
# adds an explicit, separable text-marker channel. Fully interpretable.

V3_FAKE_WEIGHTS = {
    # PDF structure — genuine manipulation red flags
    "object:pdf_smask_present": 26,
    "object:embedded_script_or_file": 10,   # JS/embedded file: real red flag even on scans -> review
    # visual forensics — recompression / splice (rarely fires on this dataset)
    "visual:seal_hard_edge_overlay": 14,
    "visual:ela_high_error_region": 14,
    "visual:jpeg_block_artifact_inconsistency": 12,
    # business-logic conflicts — strongest genuine (non-provenance) fraud signal
    "text:invoice_amount_tax_total_mismatch": 24,
    "text:settlement_amount_total_mismatch": 24,
    "text:bank_balance_sequence_broken": 20,
    "text:contract_amount_date_conflict": 20,
    "text:bank_account_number_missing": 16,
    "text:bank_amount_sequence_sparse": 8,
    "text:duplicated_text_lines": 8,
    "object:poppler_font_warning": 6,
}

# Reasons empirically shown to be scan/provenance artifacts (fire on normals).
# Scored 0 but retained as context tags so reviewers still see them.
V3_NEUTRAL = {
    "object:full_page_image_with_local_overlays",
    "object:dense_pdf_image_overlays",
    "object:missing_creator_producer",
    "object:high_font_object_count",
    "object:incremental_update_trace",
    "visual:dense_edge_or_paste_boundary",
    "visual:local_noise_block_inconsistency",
    "visual:red_stamp_like_region",
    "text:pdf_text_layer_missing_or_unreadable",
    "text:very_sparse_text_layer",
    "text:text_layer_repetition",
    "text:credit_report_id_missing",
    "text:credit_report_date_missing",
    "text:contract_date_missing",
    "text:contract_party_field_missing",
    "text:invoice_number_missing",
}


def score_v3(all_reasons: list[str], marker_flag: bool = False, marker_tokens: str = "") -> tuple[int, str, list[str]]:
    unique = list(dict.fromkeys(all_reasons))
    hits = [r for r in unique if r in V3_FAKE_WEIGHTS]
    evidence = min(84, sum(V3_FAKE_WEIGHTS[r] for r in hits))
    out_reasons = list(unique)
    domains = {r.split(":", 1)[0] for r in hits}
    if len(domains) >= 2:
        evidence = min(90, evidence + 10)
        out_reasons.append("cross:v3_multi_domain_agreement")
    if marker_flag:
        evidence = max(evidence, 72)
        out_reasons.append("marker:explicit_forgery_text" + (f":{marker_tokens}" if marker_tokens else ""))
    combined = min(100, max(0, evidence))
    if marker_flag or len(domains) >= 2 or combined >= 60:
        confidence = "high"
    elif hits:
        confidence = "medium"
    else:
        confidence = "none"
    return combined, confidence, out_reasons


def load_marker_flags(path: str) -> dict[str, dict]:
    if not path:
        return {}
    return {r["document_id"]: r for r in read_csv(Path(path))}


def apply_qwen_evidence(combined: int, confidence: str, all_reasons: list[str], qwen: dict) -> tuple[int, str, list[str]]:
    """Fold Qwen-VL visual-forensics verdict in as a corroborating evidence channel.

    Catches GROSS/visible tampering (misaligned pasted seal, mismatched-font
    edited number, splice) that pixel statistics miss. Flagged 'qwen:' so it is
    separable/auditable. NOTE: requires sending the page image to an external
    API -- only run on data cleared for external transfer (see docs).
    """
    q = as_int(qwen.get("qwen_fx_risk_score"))
    tags = [t for t in (qwen.get("qwen_fx_reason_tags") or "").split("|") if t]
    if q >= 60:
        combined = max(combined, min(90, q))
        all_reasons.append("qwen:visual_tamper_high")
        confidence = "high"
    elif q >= 35:
        combined = max(combined, 40)
        all_reasons.append("qwen:visual_tamper_medium")
        confidence = "medium" if confidence in {"none", "low"} else confidence
    for t in tags[:5]:
        all_reasons.append(f"qwen:{t}")
    return min(100, combined), confidence, list(dict.fromkeys(all_reasons))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-csv", required=True)
    parser.add_argument("--visual-csv", required=True)
    parser.add_argument("--text-csv", required=True)
    parser.add_argument("--ocr-csv", default="")
    parser.add_argument("--marker-csv", default="outputs/features/text_marker_flags.csv",
                        help="text_marker_flags.csv from extract_text_markers.py (v3 only)")
    parser.add_argument("--scoring-version", choices=["v2", "v3"], default="v3",
                        help="v2=legacy evidence-calibrated (kept for rollback), v3=sign-corrected (default)")
    parser.add_argument("--qwen-csv", default="",
                        help="optional Qwen-VL visual-forensics output (analyze_qwen_forensics.py); external-API, off by default")
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args()

    pdf_rows = {row["document_id"]: row for row in read_csv(Path(args.pdf_csv))}
    visual_rows = {row["document_id"]: row for row in read_csv(Path(args.visual_csv))}
    text_rows = {row["document_id"]: row for row in read_csv(Path(args.text_csv))}
    ocr_rows = {row["document_id"]: row for row in read_csv(Path(args.ocr_csv))} if args.ocr_csv else {}
    marker_rows = load_marker_flags(args.marker_csv) if args.scoring_version == "v3" else {}
    qwen_rows = {r["document_id"]: r for r in read_csv(Path(args.qwen_csv))} if args.qwen_csv else {}
    ids = sorted(set(pdf_rows) | set(visual_rows) | set(text_rows) | set(ocr_rows))
    records = []
    for doc_id in ids:
        pdf = pdf_rows.get(doc_id, {})
        visual = visual_rows.get(doc_id, {})
        text = text_rows.get(doc_id, {})
        ocr = ocr_rows.get(doc_id, {})
        object_score = as_int(pdf.get("object_risk_score"))
        visual_score = as_int(visual.get("visual_risk_score"))
        business_score = as_int(text.get("business_risk_score"))
        all_reasons = []
        all_reasons += [f"object:{item}" for item in reasons(pdf.get("object_risk_reasons", ""))]
        all_reasons += [f"visual:{item}" for item in reasons(visual.get("visual_risk_reasons", ""))]
        all_reasons += [f"text:{item}" for item in reasons(text.get("business_risk_reasons", ""))]
        if args.scoring_version == "v3":
            marker = marker_rows.get(doc_id, {})
            marker_flag = str(marker.get("marker_flag", "0")) == "1"
            combined, risk_confidence, all_reasons = score_v3(all_reasons, marker_flag, marker.get("marker_tokens", ""))
            if ocr:
                combined, risk_confidence, all_reasons = apply_ocr_evidence(combined, risk_confidence, all_reasons, ocr)
            if qwen_rows:
                qwen = qwen_rows.get(doc_id, {})
                if qwen:
                    combined, risk_confidence, all_reasons = apply_qwen_evidence(combined, risk_confidence, all_reasons, qwen)
        else:
            combined, risk_confidence, all_reasons = score_v2(pdf, object_score, visual_score, business_score, all_reasons)
            if ocr:
                combined, risk_confidence, all_reasons = apply_ocr_evidence(combined, risk_confidence, all_reasons, ocr)
        record = {
            "document_id": doc_id,
            "label": pdf.get("label") or text.get("label") or visual.get("label") or "",
            "doc_type": pdf.get("doc_type") or text.get("doc_type") or visual.get("doc_type") or "",
            "object_risk_score": object_score,
            "visual_risk_score": visual_score,
            "business_risk_score": business_score,
            "combined_risk_score": combined,
            "risk_confidence": risk_confidence,
            "scoring_version": "v3_sign_corrected" if args.scoring_version == "v3" else "v2_evidence_calibrated",
            "combined_risk_reasons": "|".join(dict.fromkeys(all_reasons)),
            "text_word_count": text.get("text_word_count", ""),
            "field_amount_count": text.get("field_amount_count", ""),
            "field_date_count": text.get("field_date_count", ""),
            "field_id_count": text.get("field_id_count", ""),
            "field_account_count": text.get("field_account_count", ""),
            "field_invoice_count": text.get("field_invoice_count", ""),
            "extracted_fields_json": text.get("extracted_fields_json", ""),
            "path": pdf.get("path") or text.get("path") or visual.get("path") or "",
        }
        records.append(record)

    fields = [
        "document_id",
        "label",
        "doc_type",
        "object_risk_score",
        "visual_risk_score",
        "business_risk_score",
        "combined_risk_score",
        "risk_confidence",
        "scoring_version",
        "combined_risk_reasons",
        "text_word_count",
        "field_amount_count",
        "field_date_count",
        "field_id_count",
        "field_account_count",
        "field_invoice_count",
        "extracted_fields_json",
        "path",
    ]
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        writer.writeheader()
        writer.writerows(records)

    by_label = {}
    reason_counts = Counter()
    for record in records:
        bucket = by_label.setdefault(record["label"], {"count": 0, "mean_combined_risk": 0, "high": 0, "medium": 0, "low": 0, "clean": 0})
        score = int(record["combined_risk_score"])
        bucket["count"] += 1
        bucket["mean_combined_risk"] += score
        if score >= 60:
            bucket["high"] += 1
        elif score >= 25:
            bucket["medium"] += 1
        elif score > 0:
            bucket["low"] += 1
        else:
            bucket["clean"] += 1
        for reason in reasons(record["combined_risk_reasons"]):
            reason_counts[reason] += 1
    for bucket in by_label.values():
        bucket["mean_combined_risk"] = round(bucket["mean_combined_risk"] / max(bucket["count"], 1), 2)
    summary = {"count": len(records), "by_label": by_label, "top_reasons": reason_counts.most_common(30)}
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
