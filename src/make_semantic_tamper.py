#!/usr/bin/env python3
"""Semantic-tamper benchmark: matched pairs whose ONLY difference is a number
that breaks a business-logic invariant. No watermark, same digital pipeline.

  invoice:   control Total = Amount+Tax ; tamper Total = Amount+Tax+delta
  bank_page: control balance_i = balance_{i-1}+flow_i ; tamper breaks one row
  contract:  control has consistent sign/date ; tamper: total <> sum(items)

This isolates SEMANTIC fraud (the axis that actually matters for 虚假材料),
so business-logic detectors can be measured with a fair matched control.
"""
from __future__ import annotations
import argparse, csv, random
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

W, H = A4

def draw_invoice(c, rng, tamper):
    amount = rng.randrange(1000, 90000)
    tax = round(amount * 0.13)
    total = amount + tax
    if tamper:
        total += rng.choice([-1, 1]) * rng.randrange(50, 5000)
    c.setFont("Helvetica-Bold", 16); c.drawString(60, H-70, "VALUE ADDED TAX INVOICE")
    c.setFont("Helvetica", 12)
    c.drawString(60, H-120, f"Invoice No: INV{rng.randrange(10000000,99999999)}")
    c.drawString(60, H-150, f"Buyer: Company {rng.randrange(100,999)} Co., Ltd.")
    c.drawString(60, H-200, f"Amount: {amount}.00")
    c.drawString(60, H-225, f"Tax: {tax}.00")
    c.drawString(60, H-250, f"Total: {total}.00")
    c.drawString(60, H-300, f"Date: 2026-0{rng.randrange(1,9)}-1{rng.randrange(0,9)}")
    return "invoice"

def draw_bank(c, rng, tamper):
    c.setFont("Helvetica-Bold", 16); c.drawString(60, H-70, "BANK TRANSACTION STATEMENT")
    c.setFont("Helvetica", 11)
    c.drawString(60, H-110, f"Account: {rng.randrange(6210000000000000,6229999999999999)}")
    bal = rng.randrange(20000, 80000)
    y = H-150
    rows = []
    for i in range(6):
        flow = rng.choice([-1, 1]) * rng.randrange(500, 9000)
        bal += flow
        rows.append((f"2026-06-{i+10}", flow, bal))
    if tamper:
        i = rng.randrange(1, 6)
        d, flw, b = rows[i]
        rows[i] = (d, flw, b + rng.choice([-1, 1]) * rng.randrange(300, 4000))  # break continuity
    c.drawString(60, y, "Date        Flow      Balance"); y -= 22
    for d, flw, b in rows:
        c.drawString(60, y, f"{d}   {flw:+d}   Balance: {b}"); y -= 20
    return "bank_page"

DRAW = {"invoice": draw_invoice, "bank_page": draw_bank}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/semantic_bench")
    ap.add_argument("--out-manifest", default="outputs/semantic_manifest.csv")
    ap.add_argument("--pairs-per-type", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    recs = []
    idx = 0
    for dtype in ["invoice", "bank_page"]:
        for _ in range(args.pairs_per_type):
            seed_pair = rng.randrange(1 << 30)
            for tamper, tag in [(False, "semctrl"), (True, "semtamp")]:
                r2 = random.Random(seed_pair)  # SAME content, only tamper differs
                did = f"{tag}_{dtype}_{idx:04d}"
                pdf = out / f"{did}.pdf"
                c = canvas.Canvas(str(pdf), pagesize=A4)
                DRAW[dtype](c, r2, tamper)
                c.showPage(); c.save()
                recs.append({"document_id": did, "label": "fake" if tamper else "normal",
                             "doc_type": dtype, "ext": ".pdf", "size_bytes": pdf.stat().st_size,
                             "sha256": "", "path": str(pdf),
                             "tamper_type": ("math_break" if tamper else "none")})
            idx += 1
    std = ["document_id", "label", "doc_type", "ext", "size_bytes", "sha256", "path"]
    with Path(args.out_manifest).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=std, quoting=csv.QUOTE_ALL, escapechar="\\"); w.writeheader()
        for r in recs: w.writerow({k: r[k] for k in std})
    with Path(args.out_manifest.replace(".csv", "_labels.csv")).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["document_id", "label", "doc_type", "tamper_type"]); w.writeheader()
        for r in recs: w.writerow({k: r[k] for k in ["document_id", "label", "doc_type", "tamper_type"]})
    print(f"wrote {len(recs)} docs -> {args.out_manifest}")

if __name__ == "__main__":
    main()
