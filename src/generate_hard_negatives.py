#!/usr/bin/env python3
"""Provenance-matched hard-negative generator.

Takes REAL scanned normal documents and produces, for each source, a matched
PAIR that differs only by tampering:

  hnctrl_XXXX  (label=normal)  source image -> same render/save pipeline, untouched
  hntamp_XXXX  (label=fake)    source image -> one realistic tamper -> same save

Because both members of a pair pass through the identical render->save pipeline
and share the same scan provenance, any feature separating them reflects the
TAMPER itself, not "synthetic-vs-scanned" provenance (the confound that let the
old synthetic negatives be classified at AUC ~1.0 via watermark/provenance).

Tampers (image space, NO watermark text):
  copy_move        clone a patch to another location (copy-move forgery)
  splice           paste a patch from a DIFFERENT scanned doc (noise/ELA break)
  digit_edit       inpaint a digit-like glyph and retype a new number
  recompress_patch locally recompress a region at low JPEG quality (ELA break)

Output: data/hard_negatives/*.pdf  +  outputs/hard_neg_manifest.csv
"""
from __future__ import annotations
import argparse, csv, hashlib, json, subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def read_csv(path: Path) -> list[dict]:
    t = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(t.splitlines()))


def render_bgr(path: Path, doc_id: str, dpi: int, tmp: Path):
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png"}:
        return cv2.imread(str(path))
    if ext == ".pdf":
        prefix = tmp / doc_id
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(path), str(prefix)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pages = sorted(tmp.glob(f"{doc_id}-*.png"))
        return cv2.imread(str(pages[0])) if pages else None
    return None


# ---- tamper operations (each: (img, rng, pool) -> (img, desc) or None) ----
def op_copy_move(img, rng, pool):
    h, w = img.shape[:2]
    ph, pw = h // 12, w // 4
    if ph < 8 or pw < 8:
        return None
    sy, sx = rng.randint(h // 4, h * 3 // 4 - ph), rng.randint(w // 8, w * 3 // 4 - pw)
    patch = img[sy:sy + ph, sx:sx + pw].copy()
    dy, dx = rng.randint(h // 8, h * 3 // 4 - ph), rng.randint(w // 8, w * 3 // 4 - pw)
    img[dy:dy + ph, dx:dx + pw] = patch
    return img, f"copy_move:{sy},{sx}->{dy},{dx}"


def op_splice(img, rng, pool):
    if not pool:
        return None
    other = pool[rng.randint(0, len(pool) - 1)]
    h, w = img.shape[:2]
    oh, ow = other.shape[:2]
    ph, pw = min(h // 8, oh // 2), min(w // 3, ow // 2)
    if ph < 8 or pw < 8:
        return None
    sy, sx = rng.randint(0, oh - ph), rng.randint(0, ow - pw)
    patch = other[sy:sy + ph, sx:sx + pw]
    dy, dx = rng.randint(h // 8, h * 3 // 4 - ph), rng.randint(w // 8, w * 3 // 4 - pw)
    img[dy:dy + ph, dx:dx + pw] = patch
    return img, "splice_foreign_patch"


def op_digit_edit(img, rng, pool):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(th, 8)
    h, w = img.shape[:2]
    cands = [i for i in range(1, n)
             if 8 < stats[i, cv2.CC_STAT_HEIGHT] < h // 20 and 5 < stats[i, cv2.CC_STAT_WIDTH] < w // 8]
    if not cands:
        return None
    i = cands[rng.randint(0, len(cands) - 1)]
    x, y, ww, hh = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                    stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
    pad = 4
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + ww + pad), min(h, y + hh + pad)
    mask = np.zeros((h, w), np.uint8)
    mask[y0:y1, x0:x1] = 255
    img = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
    newnum = str(rng.randint(10, 99999))
    cv2.putText(img, newnum, (x0, y1 - 2), cv2.FONT_HERSHEY_SIMPLEX,
                max(0.4, hh / 30.0), (25, 25, 25), 1, cv2.LINE_AA)
    return img, f"digit_inpaint_retype:{newnum}"


def op_recompress_patch(img, rng, pool):
    h, w = img.shape[:2]
    ph, pw = h // 6, w // 3
    if ph < 8 or pw < 8:
        return None
    y, x = rng.randint(0, h - ph), rng.randint(0, w - pw)
    ok, enc = cv2.imencode(".jpg", img[y:y + ph, x:x + pw], [cv2.IMWRITE_JPEG_QUALITY, 35])
    if ok:
        img[y:y + ph, x:x + pw] = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img, "local_recompress_q35"


OPS = [op_copy_move, op_splice, op_digit_edit, op_recompress_patch]


def save_pdf(img_bgr, out_pdf: Path, dpi: int):
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    Image.fromarray(rgb).save(str(out_pdf), "PDF", resolution=float(dpi))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="outputs/manifest.csv")
    ap.add_argument("--out-dir", default="data/hard_negatives")
    ap.add_argument("--out-manifest", default="outputs/hard_neg_manifest.csv")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--pairs", type=int, default=80, help="number of source normals to tamper")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import random
    rng = random.Random(args.seed)

    normals = [r for r in read_csv(Path(args.manifest)) if r.get("label") == "normal"]
    rng.shuffle(normals)
    normals = normals[: args.pairs]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_render"
    tmp.mkdir(exist_ok=True)

    # a small pool of foreign images for splice
    pool = []
    for r in normals[: min(12, len(normals))]:
        img = render_bgr(Path(r["path"]), r["document_id"] + "_pool", args.dpi, tmp)
        if img is not None:
            pool.append(img)

    records = []
    op_counts = {}
    idx = 0
    for r in normals:
        src = Path(r["path"])
        if not src.exists():
            continue
        base = render_bgr(src, r["document_id"], args.dpi, tmp)
        if base is None or base.size == 0:
            continue
        doc_type = r.get("doc_type", "other")
        # control (untouched, same pipeline)
        ctrl_id = f"hnctrl_{idx:04d}"
        ctrl_pdf = out_dir / f"{ctrl_id}.pdf"
        save_pdf(base.copy(), ctrl_pdf, args.dpi)
        records.append({"document_id": ctrl_id, "label": "normal", "doc_type": doc_type,
                        "ext": ".pdf", "size_bytes": ctrl_pdf.stat().st_size, "sha256": sha256(ctrl_pdf),
                        "path": str(ctrl_pdf), "source_id": r["document_id"], "tamper_op": "none"})
        # tampered (try ops until one applies)
        op_order = OPS[:]
        rng.shuffle(op_order)
        tampered = None
        desc = ""
        for op in op_order:
            res = op(base.copy(), rng, pool)
            if res is not None:
                tampered, desc = res
                op_name = op.__name__.replace("op_", "")
                break
        if tampered is None:
            continue
        tamp_id = f"hntamp_{idx:04d}"
        tamp_pdf = out_dir / f"{tamp_id}.pdf"
        save_pdf(tampered, tamp_pdf, args.dpi)
        records.append({"document_id": tamp_id, "label": "fake", "doc_type": doc_type,
                        "ext": ".pdf", "size_bytes": tamp_pdf.stat().st_size, "sha256": sha256(tamp_pdf),
                        "path": str(tamp_pdf), "source_id": r["document_id"], "tamper_op": op_name})
        op_counts[op_name] = op_counts.get(op_name, 0) + 1
        idx += 1

    # cleanup render temp
    for p in tmp.glob("*.png"):
        p.unlink()
    tmp.rmdir()

    fields = ["document_id", "label", "doc_type", "ext", "size_bytes", "sha256", "path", "source_id", "tamper_op"]
    outm = Path(args.out_manifest)
    outm.parent.mkdir(parents=True, exist_ok=True)
    with outm.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_ALL, escapechar="\\")
        wr.writeheader()
        wr.writerows(records)
    print(f"wrote {len(records)} docs ({idx} pairs) -> {outm}")
    print("tamper op distribution:", json.dumps(op_counts))


if __name__ == "__main__":
    main()
