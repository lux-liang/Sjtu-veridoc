#!/usr/bin/env python3
"""Extract explicit forgery/watermark markers from the text layer.

Synthetic negatives in this project carry drawn/text-layer watermarks such as
"SYNTHETIC FAKE - TRAINING ONLY - VOID", "Generated for model training only",
"TAMPERED", "EDITED-nnnnn". This detector surfaces those as an explicit,
separable evidence channel (flagged as marker-based, since real-world fraud
will NOT carry them -- see provenance-confound note in the iteration report).

Output: outputs/features/text_marker_flags.csv
  document_id, label, doc_type, marker_flag, marker_tokens
Reads text_word_coordinates.csv (per-word, highest recall) and
text_business_features.csv (extracted_fields_json + deepseek_explanation).
"""
from __future__ import annotations
import argparse, csv
from collections import Counter, defaultdict
from pathlib import Path

# Watermark-specific tokens (curated to avoid firing on legitimate content).
STRONG_TOKENS = [
    "synthetic", "tampered", "void stamp", "training only",
    "generated for model training", "not a valid official document",
    "screenshot simulation", "specimen",
    "作废", "伪造", "篡改",  # note: "仅供"(仅供参考) removed -- legit on real reports
]
# "edited"/"edit "/"void"/"training" alone are looser; require them combined
# with another signal or a hyphen form used by the generator (EDITED-12345).
HYPHEN_FORMS = ["edited-", "edit-", "tampered-"]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    t = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(t.splitlines()))


def match_tokens(text: str) -> list[str]:
    t = (text or "").lower()
    hits = [tok for tok in STRONG_TOKENS if tok in t]
    hits += [tok for tok in HYPHEN_FORMS if tok in t]
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-dir", default="outputs/features")
    ap.add_argument("--out", default="outputs/features/text_marker_flags.csv")
    args = ap.parse_args()
    fd = Path(args.feature_dir)

    tokens_by_doc: dict[str, Counter] = defaultdict(Counter)
    meta: dict[str, dict] = {}

    # 1) per-word coordinates (aggregate words per doc for phrase matching)
    wc = fd / "text_word_coordinates.csv"
    if wc.exists():
        t = wc.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
        words_by_doc: dict[str, list[str]] = defaultdict(list)
        for row in csv.DictReader(t.splitlines()):
            did = row.get("document_id")
            meta.setdefault(did, {"label": row.get("label", ""), "doc_type": row.get("doc_type", "")})
            words_by_doc[did].append((row.get("text") or ""))
        for did, words in words_by_doc.items():
            blob = " ".join(words)
            for tok in match_tokens(blob):
                tokens_by_doc[did][tok] += 1

    # 2) extracted fields + explanation
    for row in read_csv(fd / "text_business_features.csv"):
        did = row.get("document_id")
        meta.setdefault(did, {"label": row.get("label", ""), "doc_type": row.get("doc_type", "")})
        blob = f"{row.get('extracted_fields_json','')} {row.get('deepseek_explanation','')}"
        for tok in match_tokens(blob):
            tokens_by_doc[did][tok] += 1

    rows = []
    for did in sorted(meta):
        toks = tokens_by_doc.get(did, Counter())
        rows.append({
            "document_id": did,
            "label": meta[did]["label"],
            "doc_type": meta[did]["doc_type"],
            "marker_flag": 1 if toks else 0,
            "marker_tokens": ";".join(sorted(toks)),
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["document_id", "label", "doc_type", "marker_flag", "marker_tokens"])
        w.writeheader(); w.writerows(rows)

    # report coverage / false-positive rate
    cov = {"fake": [0, 0], "normal": [0, 0]}
    for r in rows:
        l = r["label"]
        if l in cov:
            cov[l][1] += 1
            cov[l][0] += r["marker_flag"]
    print(f"wrote {out} ({len(rows)} rows)")
    for l, (hit, tot) in cov.items():
        print(f"  {l}: marker in {hit}/{tot} = {hit/max(1,tot):.1%}")
    fp = [r["document_id"] for r in rows if r["label"] == "normal" and r["marker_flag"]]
    if fp:
        print(f"  normal false-positives ({len(fp)}): {fp[:10]}")


if __name__ == "__main__":
    main()
