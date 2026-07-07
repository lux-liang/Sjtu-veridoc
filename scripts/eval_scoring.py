#!/usr/bin/env python3
"""Diagnostic + calibration eval for sjtu_material_ai risk scoring.

Reports AUC for:
  - baseline v2 (existing combined_risk_score)
  - rule v3 (sign-corrected evidence sum)          [honest / +marker]
  - logistic calibration (out-of-fold CV)          [honest / +doc_type / +marker]

"honest" = uses only genuine forensic/structural/business features, NOT the
synthetic watermark text and NOT doc_type (which is a construction confounder).
"""
from __future__ import annotations
import csv, json, math, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, average_precision_score

FEAT = Path("outputs/features")

def read_csv(path):
    t = Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(t.splitlines()))

def f(x):
    try: return float(x or 0)
    except (TypeError, ValueError): return 0.0

def reasons(v): return [x for x in (v or "").split("|") if x]

MARKERS = ["synthetic", "training only", "void stamp", "tampered", "edited-",
           "generated for model training", "screenshot simulation", "edit ",
           "作废", "伪造", "篡改", "仅供"]

def build_marker_map():
    """doc_id -> 1 if the rendered/text layer carries an explicit fake watermark."""
    marked = set()
    # 1) from word coordinates (per-word text) -- highest recall
    p = FEAT / "text_word_coordinates.csv"
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
        r = csv.DictReader(t.splitlines())
        for row in r:
            w = (row.get("text") or "").lower()
            if any(m.strip() in w for m in ["synthetic", "edited", "training", "tampered", "void", "specimen", "作废", "伪造", "篡改", "仅供"]):
                marked.add(row.get("document_id"))
    # 2) from extracted fields / explanation
    for row in read_csv(FEAT / "text_business_features.csv"):
        blob = (row.get("extracted_fields_json", "") + " " + row.get("deepseek_explanation", "")).lower()
        if any(m in blob for m in MARKERS):
            marked.add(row.get("document_id"))
    return marked

def load():
    pdf = {r["document_id"]: r for r in read_csv(FEAT / "pdf_object_features.csv")}
    vis = {r["document_id"]: r for r in read_csv(FEAT / "visual_forensics_features.csv")}
    txt = {r["document_id"]: r for r in read_csv(FEAT / "text_business_features.csv")}
    base = {r["document_id"]: r for r in read_csv(FEAT / "combined_risk_features.csv")}
    marker = build_marker_map()
    ids = [i for i in sorted(set(pdf) | set(txt)) if (pdf.get(i, txt.get(i, {})).get("label") in ("fake", "normal"))]
    docs = []
    for i in ids:
        p, v, t = pdf.get(i, {}), vis.get(i, {}), txt.get(i, {})
        label = p.get("label") or t.get("label") or v.get("label")
        if label not in ("fake", "normal"):
            continue
        rs = ([f"object:{x}" for x in reasons(p.get("object_risk_reasons"))]
              + [f"visual:{x}" for x in reasons(v.get("visual_risk_reasons"))]
              + [f"text:{x}" for x in reasons(t.get("business_risk_reasons"))])
        docs.append({
            "id": i, "y": 1 if label == "fake" else 0,
            "doc_type": p.get("doc_type") or t.get("doc_type") or "other",
            "reasons": set(rs),
            "object_score": f(p.get("object_risk_score")),
            "visual_score": f(v.get("visual_risk_score")),
            "business_score": f(t.get("business_risk_score")),
            "ela": f(v.get("ela_score")), "blockvar": f(v.get("block_variance_score")),
            "edge": f(v.get("edge_density")), "redstamp": f(v.get("red_stamp_score")),
            "smask": f(p.get("smask_count")),
            "words": f(t.get("text_word_count")),
            "no_text": 1.0 if f(t.get("text_word_count")) < 5 else 0.0,
            "marker": 1.0 if i in marker else 0.0,
            "base_v2": f(base.get(i, {}).get("combined_risk_score")),
        })
    return docs

# ---- sign-corrected rule v3 (interpretable) ----
POS = {  # reasons empirically pointing to FAKE
    "object:pdf_smask_present": 26,
    "text:bank_account_number_missing": 20,
    "text:invoice_amount_tax_total_mismatch": 22,
    "text:settlement_amount_total_mismatch": 22,
    "text:bank_balance_sequence_broken": 18,
    "text:contract_amount_date_conflict": 18,
    "text:bank_amount_sequence_sparse": 8,
    "object:poppler_font_warning": 6,
}
# everything else (paste boundary, field-missing on scans, missing producer...) -> ignored (non-discriminative/inverted)

def rule_v3(d, use_marker):
    s = sum(w for r, w in POS.items() if r in d["reasons"])
    if use_marker and d["marker"]:
        s = max(s, 72)
    # amount mismatch corroborated by any structural
    return float(min(100, s))

def auc(y, s):
    return roc_auc_score(y, s) if len(set(y)) > 1 else float("nan")

def logreg_oof(docs, feat_fn, seed=42, folds=5):
    X = np.array([feat_fn(d) for d in docs], dtype=float)
    y = np.array([d["y"] for d in docs])
    oof = np.zeros(len(y))
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    for tr, te in skf.split(X, y):
        sc = StandardScaler().fit(X[tr])
        m = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)
        m.fit(sc.transform(X[tr]), y[tr])
        oof[te] = m.predict_proba(sc.transform(X[te]))[:, 1]
    return oof, y

def main():
    docs = load()
    y = [d["y"] for d in docs]
    print(f"docs={len(docs)}  fake={sum(y)}  normal={len(y)-sum(y)}")
    print(f"marker coverage: fake={sum(d['marker'] for d in docs if d['y'])/max(1,sum(y)):.1%}  "
          f"normal={sum(d['marker'] for d in docs if not d['y'])/max(1,len(y)-sum(y)):.1%}\n")

    # reason vocabulary for one-hot (exclude nothing structural; marker/doc_type handled separately)
    vocab = sorted({r for d in docs for r in d["reasons"]})
    numeric = ["object_score", "visual_score", "business_score", "ela", "blockvar",
               "edge", "redstamp", "smask", "no_text"]
    def logw(d): return math.log1p(d["words"])
    doctypes = sorted({d["doc_type"] for d in docs})

    def feat_honest(d):
        return ([d[k] for k in numeric] + [logw(d)]
                + [1.0 if r in d["reasons"] else 0.0 for r in vocab])

    # provenance proxies to EXCLUDE for a controlled "real fraud-artifact" signal
    PROV_NUM = {"no_text"}  # word-count / text-layer presence = scanned-vs-digital
    PROV_REASONS = {
        "text:credit_report_id_missing", "text:credit_report_date_missing",
        "text:pdf_text_layer_missing_or_unreadable", "text:very_sparse_text_layer",
        "text:text_layer_repetition", "object:missing_creator_producer",
        "text:contract_date_missing", "text:contract_party_field_missing",
        "text:invoice_number_missing",
    }
    art_num = [k for k in numeric if k not in PROV_NUM]
    art_vocab = [r for r in vocab if r not in PROV_REASONS]
    def feat_artifact(d):  # genuine forensic/logic artifacts only, no provenance, no word count
        return ([d[k] for k in art_num]
                + [1.0 if r in d["reasons"] else 0.0 for r in art_vocab])
    def feat_artifact_mark(d):
        return feat_artifact(d) + [d["marker"]]
    def feat_doctype(d):
        return feat_honest(d) + [1.0 if d["doc_type"] == t else 0.0 for t in doctypes]
    def feat_full(d):
        return feat_doctype(d) + [d["marker"]]

    print("=== AUC (higher=better; 0.5=random) ===")
    print(f"{'baseline v2 (current prod)':42s} AUC={auc(y,[d['base_v2'] for d in docs]):.3f}")
    print(f"{'rule v3  (honest, no marker)':42s} AUC={auc(y,[rule_v3(d,False) for d in docs]):.3f}")
    print(f"{'rule v3  (+marker)':42s} AUC={auc(y,[rule_v3(d,True) for d in docs]):.3f}")
    for name, fn in [("logreg ALL feats (no marker)", feat_honest),
                     ("logreg +doc_type", feat_doctype),
                     ("logreg +marker (full)", feat_full),
                     (">> logreg ARTIFACT-only (prov-ctrl)", feat_artifact),
                     (">> logreg ARTIFACT + marker", feat_artifact_mark)]:
        oof, yy = logreg_oof(docs, fn)
        print(f"{name:42s} AUC={auc(yy,oof):.3f}  PR-AUC={average_precision_score(yy,oof):.3f}")

    # honest logreg feature weights (fit on all data, for interpretability)
    X = np.array([feat_honest(d) for d in docs]); yv = np.array(y)
    sc = StandardScaler().fit(X); m = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5)
    m.fit(sc.transform(X), yv)
    names = numeric + ["log_words"] + vocab
    w = m.coef_[0]
    order = np.argsort(-np.abs(w))
    print("\n=== honest logreg top weights (sign = fake direction) ===")
    for idx in order[:14]:
        print(f"  {names[idx]:44s} {w[idx]:+.2f}")

if __name__ == "__main__":
    main()
