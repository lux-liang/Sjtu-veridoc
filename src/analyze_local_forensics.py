#!/usr/bin/env python3
"""Localized image-forensics detector (catches LOCAL tampering the page-level
aggregate features miss).

Three localized signals, each keeps a full-resolution local peak instead of a
page-wide average:

  local ELA        JPEG re-compression residual, blockwise; a local paste/edit
                   spikes a few blocks -> ratio p99/median >> 1.
  local noise      high-pass residual (img - median), blockwise std; a spliced
                   or recompressed patch has inconsistent local noise energy.
  copy-move        ORB keypoint self-matching with OFFSET-CONSISTENCY voting:
                   a cloned region yields many matches sharing one (dx,dy)
                   offset -- distinguishes true copy-move from repeated glyphs.

Outputs raw features + a combined local_risk_score. Designed to be evaluated on
the provenance-matched hard-negative benchmark.
"""
from __future__ import annotations
import argparse, csv, json, subprocess
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


def read_csv(path: Path) -> list[dict]:
    t = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(t.splitlines()))


def render_gray(path: Path, doc_id: str, dpi: int, tmp: Path, max_side: int):
    ext = path.suffix.lower()
    img = None
    if ext in {".jpg", ".jpeg", ".png"}:
        img = cv2.imread(str(path))
    elif ext == ".pdf":
        prefix = tmp / doc_id
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(path), str(prefix)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pages = sorted(tmp.glob(f"{doc_id}-*.png"))
        if pages:
            img = cv2.imread(str(pages[0]))
    if img is None:
        return None
    h, w = img.shape[:2]
    s = min(max_side / max(h, w), 1.0)
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)))
    return img


def local_ela(bgr, block=16, q=90):
    ok, enc = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
    re = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    resid = cv2.absdiff(bgr, re).sum(axis=2).astype(np.float32)
    h, w = resid.shape
    bh, bw = h // block, w // block
    if bh < 2 or bw < 2:
        return {"ela_p99_ratio": 0.0, "ela_hotspot_frac": 0.0, "ela_max": 0.0}
    cropped = resid[:bh * block, :bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    med = float(np.median(cropped)) + 1e-3
    p99 = float(np.percentile(cropped, 99))
    mean, std = float(cropped.mean()), float(cropped.std()) + 1e-3
    hotspot = float((cropped > mean + 4 * std).mean())
    return {"ela_p99_ratio": round(p99 / med, 4), "ela_hotspot_frac": round(hotspot, 5),
            "ela_max": round(float(cropped.max()), 3)}


def local_noise(bgr, block=32):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    resid = gray - cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)
    h, w = resid.shape
    bh, bw = h // block, w // block
    if bh < 2 or bw < 2:
        return {"noise_peak_ratio": 0.0, "noise_hotspot_frac": 0.0}
    stds = resid[:bh * block, :bw * block].reshape(bh, block, bw, block).std(axis=(1, 3))
    med = float(np.median(stds)) + 1e-3
    p99 = float(np.percentile(stds, 99))
    mean, sd = float(stds.mean()), float(stds.std()) + 1e-3
    return {"noise_peak_ratio": round(p99 / med, 4),
            "noise_hotspot_frac": round(float((stds > mean + 4 * sd).mean()), 5)}


def copy_move(bgr, min_spatial=32, desc_thresh=32, offset_grid=8):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=2500)
    kp, des = orb.detectAndCompute(gray, None)
    if des is None or len(kp) < 20:
        return {"copymove_offset_votes": 0, "copymove_match_count": 0}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = bf.knnMatch(des, des, k=3)
    offsets = Counter()
    count = 0
    for m_list in matches:
        for m in m_list:
            if m.queryIdx == m.trainIdx:
                continue
            if m.distance > desc_thresh:
                continue
            p1 = kp[m.queryIdx].pt
            p2 = kp[m.trainIdx].pt
            dx, dy = p1[0] - p2[0], p1[1] - p2[1]
            if (dx * dx + dy * dy) ** 0.5 < min_spatial:
                continue
            count += 1
            offsets[(round(dx / offset_grid), round(dy / offset_grid))] += 1
            break
    # a true clone => one dominant offset; ignore the symmetric duplicate sign
    merged = Counter()
    for (gx, gy), c in offsets.items():
        merged[(abs(gx), abs(gy))] += c
    top = merged.most_common(1)[0][1] if merged else 0
    return {"copymove_offset_votes": int(top), "copymove_match_count": int(count)}


def local_risk(feat):
    score = 0.0
    reasons = []
    if feat["ela_p99_ratio"] >= 6.0:
        score += 22; reasons.append("local_ela_hotspot")
    if feat["noise_peak_ratio"] >= 3.5:
        score += 18; reasons.append("local_noise_inconsistency")
    if feat["copymove_offset_votes"] >= 8:
        score += 30; reasons.append("copy_move_cloned_region")
    return min(100, score), reasons


def analyze(row, tmp, dpi, max_side):
    src = Path(row["path"])
    if not src.exists():
        return {**row, "local_error": "missing_file"}
    img = render_gray(src, row["document_id"], dpi, tmp, max_side)
    if img is None:
        return {**row, "local_error": "render_failed"}
    feat = {}
    feat.update(local_ela(img))
    feat.update(local_noise(img))
    feat.update(copy_move(img))
    score, reasons = local_risk(feat)
    return {**row, **feat, "local_risk_score": score, "local_risk_reasons": "|".join(reasons), "local_error": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--max-side", type=int, default=1400)
    args = ap.parse_args()
    rows = [r for r in read_csv(Path(args.manifest)) if r.get("ext", "").lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    tmp = Path(args.out_csv).parent / "_localtmp"
    tmp.mkdir(parents=True, exist_ok=True)
    recs = [analyze(r, tmp, args.dpi, args.max_side) for r in rows]
    for p in tmp.glob("*.png"):
        p.unlink()
    tmp.rmdir()
    cols = list(recs[0].keys())
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL, escapechar="\\")
        w.writeheader(); w.writerows(recs)
    print(f"wrote {len(recs)} rows -> {args.out_csv}")


if __name__ == "__main__":
    main()
