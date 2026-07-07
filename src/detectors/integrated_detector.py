#!/usr/bin/env python3
from __future__ import annotations
import csv
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .business_logic_detector import BusinessLogicDetector, risk_level


DEFAULT_WEIGHTS = {
    "pdf_structure": 0.32,
    "image_forensics": 0.18,
    "seal_overlay": 0.12,
    "ocr_text": 0.18,
    "business_logic": 0.20,
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def split_reasons(value: str) -> list[str]:
    return [item for item in (value or "").split("|") if item]


class IntegratedDetector:
    """Unified risk pipeline for PDF structure, image forensics, seal, OCR, and business logic."""

    def __init__(self, project_root: str | Path = ".", weights: dict[str, float] | None = None):
        self.root = Path(project_root)
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.business_detector = BusinessLogicDetector()
        feature_dir = self.root / "outputs/features"
        if not (feature_dir / "pdf_object_features.csv").exists():
            feature_dir = self.root / "data/features"
        self.feature_dir = feature_dir
        self.pdf_rows = {row.get("document_id"): row for row in read_csv(feature_dir / "pdf_object_features.csv")}
        self.visual_rows = {row.get("document_id"): row for row in read_csv(feature_dir / "visual_forensics_features.csv")}
        self.text_rows = {row.get("document_id"): row for row in read_csv(feature_dir / "text_business_features.csv")}
        self.ocr_rows = {row.get("document_id"): row for row in read_csv(feature_dir / "ocr_deepseek_features.csv")}
        self.manifest_rows = read_csv(self.root / "outputs/manifest.csv") or read_csv(self.root / "data/manifest.csv")

    def detect_document_id(self, document_id: str, detectors: set[str] | None = None) -> dict[str, Any]:
        pdf = self.pdf_rows.get(document_id, {})
        visual = self.visual_rows.get(document_id, {})
        text = self.text_rows.get(document_id, {})
        ocr = self.ocr_rows.get(document_id, {})
        if not pdf and not text and not ocr:
            raise KeyError(f"document_id not found: {document_id}")
        doc_type = pdf.get("doc_type") or text.get("doc_type") or ocr.get("doc_type") or "other"
        ocr_text = ocr.get("ocr_text_preview") or ""
        fields = {}
        for raw in [text.get("extracted_fields_json"), ocr.get("ocr_fields_json")]:
            if raw:
                try:
                    fields.update(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        business = self.business_detector.detect(doc_type, ocr_text, fields)
        components = self._components(pdf, visual, text, ocr, business, detectors)
        return self._compose(document_id, pdf or text or ocr, components)

    def detect_path(self, path: str | Path, doc_type: str = "other", label: str = "unknown", detectors: set[str] | None = None) -> dict[str, Any]:
        path = Path(path)
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.csv"
            row = {
                "document_id": path.stem,
                "label": label,
                "doc_type": doc_type,
                "ext": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
                "sha256": "",
                "path": str(path),
            }
            with manifest.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader()
                writer.writerow(row)
            pdf = self._run_pdf_features(manifest, Path(td)) if path.suffix.lower() == ".pdf" else {}
            visual = self._run_visual_features(manifest, Path(td))
            text = {}
            ocr = {}
            business = self.business_detector.detect(doc_type, "", {})
            components = self._components(pdf, visual, text, ocr, business, detectors)
            return self._compose(path.stem, {**row, **pdf}, components)

    def _components(self, pdf: dict, visual: dict, text: dict, ocr: dict, business: dict, detectors: set[str] | None) -> dict[str, dict]:
        enabled = detectors or {"pdf_structure", "image_forensics", "seal_overlay", "ocr_text", "business_logic"}
        components = {}
        if "pdf_structure" in enabled:
            components["pdf_structure"] = {
                "score": as_float(pdf.get("object_risk_score")),
                "level": risk_level(as_float(pdf.get("object_risk_score"))),
                "reasons": split_reasons(pdf.get("object_risk_reasons", "")),
            }
        if "image_forensics" in enabled:
            components["image_forensics"] = {
                "score": as_float(visual.get("visual_risk_score")),
                "level": risk_level(as_float(visual.get("visual_risk_score"))),
                "reasons": split_reasons(visual.get("visual_risk_reasons", "")),
            }
        if "seal_overlay" in enabled:
            seal_score = 0.0
            seal_reasons = []
            if as_float(pdf.get("smask_count")) > 0:
                seal_score += 18
                seal_reasons.append("smask_alpha_overlay")
            if "red_stamp_like_region" in split_reasons(visual.get("visual_risk_reasons", "")):
                seal_score += 12
                seal_reasons.append("red_stamp_like_region")
            components["seal_overlay"] = {"score": min(100, seal_score), "level": risk_level(seal_score), "reasons": seal_reasons}
        if "ocr_text" in enabled:
            ocr_score = max(as_float(ocr.get("ocr_risk_score")), as_float(text.get("business_risk_score")))
            ocr_reasons = split_reasons(ocr.get("ocr_risk_reasons", "")) + split_reasons(text.get("business_risk_reasons", ""))
            components["ocr_text"] = {"score": ocr_score, "level": risk_level(ocr_score), "reasons": ocr_reasons}
        if "business_logic" in enabled:
            components["business_logic"] = business
        return components

    def _compose(self, document_id: str, row: dict, components: dict[str, dict]) -> dict[str, Any]:
        weighted = 0.0
        total_weight = 0.0
        reasons = []
        for name, result in components.items():
            weight = self.weights.get(name, 0)
            score = as_float(result.get("score"))
            weighted += score * weight
            total_weight += weight
            for reason in result.get("reasons") or []:
                if isinstance(reason, dict):
                    reason = reason.get("rule") or reason.get("message") or str(reason)
                reasons.append(f"{name}:{reason}")
        score = weighted / total_weight if total_weight else 0.0
        # Preserve very strong individual detector evidence.
        max_component = max([as_float(item.get("score")) for item in components.values()] or [0])
        score = max(score, max_component * 0.72 if max_component >= 60 else score)
        score = round(min(100.0, score), 2)
        return {
            "document_id": document_id,
            "label": row.get("label", ""),
            "doc_type": row.get("doc_type", ""),
            "path": row.get("path", ""),
            "score": score,
            "level": risk_level(score),
            "components": components,
            "reasons": list(dict.fromkeys(reasons))[:40],
        }

    def _run_pdf_features(self, manifest: Path, tmp: Path) -> dict:
        script = self.root / "src/extract_pdf_features.py"
        if not script.exists():
            return {}
        out_csv = tmp / "pdf.csv"
        out_json = tmp / "pdf.json"
        subprocess.run(["python3", str(script), "--manifest", str(manifest), "--out-csv", str(out_csv), "--out-json", str(out_json)], cwd=str(self.root), check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        rows = read_csv(out_csv)
        return rows[0] if rows else {}

    def _run_visual_features(self, manifest: Path, tmp: Path) -> dict:
        script = self.root / "src/analyze_visual_forensics.py"
        if not script.exists():
            return {}
        out_csv = tmp / "visual.csv"
        out_json = tmp / "visual.json"
        subprocess.run(["python3", str(script), "--manifest", str(manifest), "--out-csv", str(out_csv), "--out-json", str(out_json), "--render-dir", str(tmp / "render"), "--dpi", "110"], cwd=str(self.root), check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        rows = read_csv(out_csv)
        return rows[0] if rows else {}
