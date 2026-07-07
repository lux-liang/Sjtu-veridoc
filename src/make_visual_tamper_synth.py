#!/usr/bin/env python3
"""Synthetic VISUAL-tamper pairs (NO real PII) to test VLM forensics safely.

Base = reportlab invoice (fake numbers). For each source, a matched pair:
  vctrl_*  clean render
  vtamp_*  same render + a VISIBLE tamper:
             - a crooked pasted red 'seal' (flat color, hard edge)
             - one number inpainted and retyped in a mismatched font/color
Both saved as PDF via the same pipeline. Fully synthetic -> safe to send to any
external API. Tests whether a multimodal LLM can SEE visual tampering.
"""
from __future__ import annotations
import argparse, csv, random
from pathlib import Path
import numpy as np, cv2, subprocess
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from PIL import Image

W, H = A4

def make_invoice(pdf, rng):
    amount = rng.randrange(1000, 90000); tax = round(amount*0.13); total = amount+tax
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.setFont("Helvetica-Bold", 16); c.drawString(60, H-70, "VALUE ADDED TAX INVOICE")
    c.setFont("Helvetica", 12)
    c.drawString(60, H-120, f"Invoice No: INV{rng.randrange(10000000,99999999)}")
    c.drawString(60, H-150, f"Buyer: Company {rng.randrange(100,999)} Co., Ltd.")
    c.drawString(60, H-200, f"Amount: {amount}.00")
    c.drawString(60, H-225, f"Tax: {tax}.00")
    c.drawString(60, H-250, f"Total: {total}.00")
    c.drawString(60, H-300, f"Date: 2026-0{rng.randrange(1,9)}-1{rng.randrange(0,9)}")
    c.drawString(60, H-360, "Authorized signature: ____________________")
    c.showPage(); c.save()

def render(pdf, did, tmp, dpi=150):
    pre = tmp/did
    subprocess.run(["pdftoppm","-r",str(dpi),"-png","-f","1","-l","1",str(pdf),str(pre)],
                   stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    pg = sorted(tmp.glob(f"{did}-*.png"))
    return cv2.imread(str(pg[0])) if pg else None

def visual_tamper(img, rng):
    h, w = img.shape[:2]
    # 1) crooked flat-red pasted 'seal' (hard edge, uniform color)
    cx, cy = rng.randint(int(w*0.55), int(w*0.8)), rng.randint(int(h*0.55), int(h*0.8))
    overlay = img.copy()
    cv2.ellipse(overlay, (cx, cy), (70, 70), rng.randint(-25,25), 0, 360, (40,40,190), 4)
    cv2.putText(overlay, "SEAL", (cx-45, cy+8), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40,40,190), 2, cv2.LINE_AA)
    img = cv2.addWeighted(overlay, 0.9, img, 0.1, 0)
    # 2) edit a number: cover the Total value and retype bigger, off-color, off-baseline
    y0, y1, x0, x1 = int(h*0.36), int(h*0.40), int(w*0.16), int(w*0.42)
    img[y0:y1, x0:x1] = 255
    cv2.putText(img, str(rng.randrange(10000,999999))+".00", (x0, y1-2),
                cv2.FONT_HERSHEY_TRIPLEX, 0.9, (60,60,60), 2, cv2.LINE_AA)
    return img

def save_pdf(img, out, dpi=150):
    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(str(out), "PDF", resolution=float(dpi))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/visual_tamper_synth")
    ap.add_argument("--out-manifest", default="outputs/visual_tamper_manifest.csv")
    ap.add_argument("--pairs", type=int, default=15)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    tmp = out/"_r"; tmp.mkdir(exist_ok=True)
    recs = []
    for i in range(args.pairs):
        base_pdf = tmp/f"base_{i}.pdf"; make_invoice(base_pdf, random.Random(rng.randrange(1<<30)))
        img = render(base_pdf, f"base_{i}", tmp)
        if img is None: continue
        for tag, tam in [("vctrl","normal"), ("vtamp","fake")]:
            did = f"{tag}_{i:04d}"; pdf = out/f"{did}.pdf"
            save_pdf(visual_tamper(img.copy(), rng) if tag=="vtamp" else img.copy(), pdf)
            recs.append({"document_id":did,"label":tam,"doc_type":"invoice","ext":".pdf",
                         "size_bytes":pdf.stat().st_size,"sha256":"","path":str(pdf)})
    for p in tmp.glob("*"): p.unlink()
    tmp.rmdir()
    std=["document_id","label","doc_type","ext","size_bytes","sha256","path"]
    with open(args.out_manifest,"w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=std,quoting=csv.QUOTE_ALL,escapechar="\\");w.writeheader();w.writerows(recs)
    print(f"wrote {len(recs)} docs -> {args.out_manifest}")

if __name__=="__main__":
    main()
