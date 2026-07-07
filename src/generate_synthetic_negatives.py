#!/usr/bin/env python3
import argparse
import csv
import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


DOC_TYPES = ["credit_report", "contract", "invoice", "bank_page", "settlement_statement"]
TAMPER_TYPES = [
    "amount_rewrite",
    "date_rewrite",
    "identity_rewrite",
    "stamp_paste",
    "signature_paste",
    "qr_replace",
    "screenshot_to_pdf",
    "local_cover_overlay",
    "font_mismatch",
    "page_splice",
    "logic_conflict",
    "noise_patch",
    "text_layer_mismatch",
]
WATERMARK = "SYNTHETIC FAKE - TRAINING ONLY - VOID"
DETECTION_CATEGORIES = {
    "amount_rewrite": "font_text_or_business_logic",
    "date_rewrite": "font_text_or_business_logic",
    "identity_rewrite": "font_text_or_business_logic",
    "stamp_paste": "seal_overlay",
    "signature_paste": "seal_or_signature_overlay",
    "qr_replace": "visual_overlay_or_business_logic",
    "screenshot_to_pdf": "image_ps_or_pdf_object",
    "local_cover_overlay": "pdf_object_or_text_layer",
    "font_mismatch": "font_text_layer",
    "page_splice": "pdf_object_or_layout_consistency",
    "logic_conflict": "business_logic",
    "noise_patch": "image_ps_noise_consistency",
    "text_layer_mismatch": "text_layer_ocr_mismatch",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_stamp(path: Path, seed: int) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGBA", (360, 360), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    red = (180 + rng.randrange(40), 0, 0, 165)
    draw.ellipse((28, 28, 332, 332), outline=red, width=14)
    draw.ellipse((96, 96, 264, 264), outline=red, width=5)
    draw.text((88, 154), "VOID STAMP", fill=red)
    draw.text((102, 190), "SYNTHETIC", fill=red)
    img = img.rotate(rng.uniform(-8, 8), expand=True).filter(ImageFilter.GaussianBlur(0.25))
    img.save(path)
    return path


def make_signature(path: Path, seed: int) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGBA", (520, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    points = []
    x = 30
    for i in range(16):
        x += rng.randrange(18, 34)
        y = 88 + rng.randrange(-42, 42)
        points.append((x, y))
    draw.line(points, fill=(20, 20, 35, 190), width=5, joint="curve")
    draw.line([(45, 132), (470, 116)], fill=(20, 20, 35, 120), width=2)
    img = img.rotate(rng.uniform(-5, 5), expand=True).filter(ImageFilter.GaussianBlur(0.2))
    img.save(path)
    return path


def make_qr_like(path: Path, seed: int) -> Path:
    rng = random.Random(seed)
    size = 210
    cell = 10
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for px, py in [(0, 0), (size - 70, 0), (0, size - 70)]:
        draw.rectangle((px, py, px + 69, py + 69), fill="black")
        draw.rectangle((px + 10, py + 10, px + 59, py + 59), fill="white")
        draw.rectangle((px + 22, py + 22, px + 47, py + 47), fill="black")
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            if rng.random() < 0.34:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="black")
    draw.rectangle((82, 82, 135, 135), fill="white")
    draw.text((87, 100), "VOID", fill=(180, 0, 0))
    img.save(path)
    return path


def make_noise_patch(path: Path, seed: int) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGB", (340, 96), (246, 246, 244))
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            base = 246 + rng.randrange(-3, 4)
            if 95 < x < 245 and 22 < y < 70:
                base = 236 + rng.randrange(-24, 25)
            pixels[x, y] = (base, base, max(0, base - rng.randrange(0, 5)))
    draw = ImageDraw.Draw(img)
    draw.text((104, 38), f"EDIT {rng.randrange(10000, 99999)}", fill=(0, 0, 0))
    img.save(path, quality=83)
    return path


def make_screenshot_image(path: Path, doc_type: str, seed: int) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGB", (1240, 1754), (248, 248, 246))
    draw = ImageDraw.Draw(img)
    draw.rectangle((60, 60, 1180, 1694), outline=(205, 205, 205), width=3)
    draw.text((90, 90), f"{doc_type.upper()} SCREENSHOT SIMULATION", fill=(35, 35, 35))
    y = 160
    for i in range(18):
        draw.line((90, y, 1150, y), fill=(220, 220, 220), width=2)
        draw.text((100, y + 12), f"field_{i + 1}: VALUE-{rng.randrange(10000, 99999)}", fill=(40, 40, 40))
        y += 72
    draw.rectangle((640, 504, 900, 555), fill=(255, 255, 255))
    draw.text((650, 516), f"TAMPERED {rng.randrange(100000, 999999)}", fill=(0, 0, 0))
    draw.text((240, 840), WATERMARK, fill=(185, 45, 45))
    img.save(path, quality=88)
    return path


def draw_watermark(c: canvas.Canvas, width: float, height: float) -> None:
    c.saveState()
    c.setFillColor(colors.Color(0.75, 0.05, 0.05, alpha=0.16))
    c.setFont("Helvetica-Bold", 34)
    c.translate(width / 2, height / 2)
    c.rotate(32)
    c.drawCentredString(0, 0, WATERMARK)
    c.restoreState()


def draw_header(c: canvas.Canvas, doc_type: str, index: int, width: float, height: float) -> None:
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(42, height - 52, f"Synthetic {doc_type.replace('_', ' ').title()} #{index:04d}")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.darkred)
    c.drawString(42, height - 70, "Generated for model training only. Not a valid official document.")
    c.setStrokeColor(colors.lightgrey)
    c.line(42, height - 82, width - 42, height - 82)


def draw_table(c: canvas.Canvas, rows: list[tuple[str, str]], x: float, y: float, width: float) -> None:
    c.setFont("Helvetica", 10)
    row_h = 28
    for idx, (key, value) in enumerate(rows):
        top = y - idx * row_h
        c.setStrokeColor(colors.lightgrey)
        c.rect(x, top - row_h, width, row_h, stroke=1, fill=0)
        c.setFillColor(colors.grey)
        c.drawString(x + 8, top - 18, key)
        c.setFillColor(colors.black)
        c.drawString(x + 170, top - 18, value)


def rows_for_doc(doc_type: str, rng: random.Random) -> list[tuple[str, str]]:
    common = {
        "credit_report": [
            ("Name", "ZHANG TEST"),
            ("ID No.", f"110000{rng.randrange(19700101, 20050101)}0000"),
            ("Loan Balance", f"{rng.randrange(10000, 900000)}.00"),
            ("Overdue Count", str(rng.randrange(0, 8))),
            ("Query Date", f"2026-0{rng.randrange(1, 7)}-{rng.randrange(10, 28)}"),
        ],
        "contract": [
            ("Party A", "Shanghai Demo Company Ltd."),
            ("Party B", "Training Sample Co., Ltd."),
            ("Contract Amount", f"{rng.randrange(50000, 900000)}.00"),
            ("Effective Date", f"2026-0{rng.randrange(1, 7)}-{rng.randrange(10, 28)}"),
            ("Contract No.", f"HT-SYN-{rng.randrange(100000, 999999)}"),
        ],
        "invoice": [
            ("Buyer", "Training Buyer Co., Ltd."),
            ("Tax ID", f"91310000{rng.randrange(10000000, 99999999)}"),
            ("Amount", f"{rng.randrange(1000, 80000)}.00"),
            ("Tax", f"{rng.randrange(80, 9000)}.00"),
            ("Invoice No.", f"INV-SYN-{rng.randrange(100000, 999999)}"),
        ],
        "bank_page": [
            ("Account Name", "Training Account"),
            ("Account No.", f"6222 **** **** {rng.randrange(1000, 9999)}"),
            ("Balance", f"{rng.randrange(10000, 500000)}.00"),
            ("Transaction Amount", f"{rng.randrange(100, 90000)}.00"),
            ("Transaction Date", f"2026-0{rng.randrange(1, 7)}-{rng.randrange(10, 28)}"),
        ],
        "settlement_statement": [
            ("Payer", "Synthetic Payer Ltd."),
            ("Payee", "Synthetic Payee Ltd."),
            ("Settlement Amount", f"{rng.randrange(10000, 700000)}.00"),
            ("Bank Ref", f"BNK-SYN-{rng.randrange(100000, 999999)}"),
            ("Settlement Date", f"2026-0{rng.randrange(1, 7)}-{rng.randrange(10, 28)}"),
        ],
    }
    return common[doc_type]


def apply_tamper_visuals(c: canvas.Canvas, tamper_type: str, work_dir: Path, doc_type: str, seed: int, width: float, height: float) -> None:
    rng = random.Random(seed)
    if tamper_type in {"amount_rewrite", "date_rewrite", "identity_rewrite", "local_cover_overlay", "text_layer_mismatch"}:
        for _ in range(4 if tamper_type == "local_cover_overlay" else 2):
            x = rng.randrange(210, 390)
            y = rng.randrange(420, 650)
            c.setFillColor(colors.white)
            c.rect(x, y, 180, 20, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x + 4, y + 6, f"EDITED-{rng.randrange(10000, 99999)}")
        if tamper_type == "text_layer_mismatch":
            c.setFillColor(colors.Color(1, 1, 1, alpha=0.01))
            c.setFont("Helvetica", 6)
            c.drawString(58, 150, f"HIDDEN_ORIGINAL_VALUE_{rng.randrange(100000, 999999)}")
    if tamper_type == "stamp_paste":
        stamp = make_stamp(work_dir / f"stamp_{seed}.png", seed)
        c.drawImage(ImageReader(str(stamp)), 300, 180, width=170, height=170, mask="auto")
    if tamper_type == "signature_paste":
        signature = make_signature(work_dir / f"signature_{seed}.png", seed)
        c.drawImage(ImageReader(str(signature)), 250, 180, width=240, height=84, mask="auto")
    if tamper_type == "qr_replace":
        qr = make_qr_like(work_dir / f"qr_{seed}.png", seed)
        c.drawImage(ImageReader(str(qr)), 360, 160, width=110, height=110)
    if tamper_type == "screenshot_to_pdf":
        screenshot = make_screenshot_image(work_dir / f"screenshot_{seed}.jpg", doc_type, seed)
        c.drawImage(ImageReader(str(screenshot)), 42, 96, width=width - 84, height=height - 160)
    if tamper_type == "font_mismatch":
        c.setFillColor(colors.white)
        c.rect(226, 518, 210, 20, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont("Courier-Bold", 10)
        c.drawString(230, 524, f"FONT-{rng.randrange(10000, 99999)}")
        c.setFont("Times-Italic", 9)
        c.drawString(230, 494, f"mixed baseline {rng.randrange(1000, 9999)}")
    if tamper_type == "page_splice":
        c.setStrokeColor(colors.Color(0.2, 0.2, 0.2, alpha=0.45))
        c.setDash(4, 2)
        c.rect(48, 116, width - 96, 260, stroke=1, fill=0)
        c.setDash()
        c.setFillColor(colors.Color(0.94, 0.94, 0.9, alpha=0.75))
        c.rect(58, 126, width - 116, 235, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        for i in range(6):
            c.drawString(74, 330 - i * 30, f"Spliced section line {i + 1}: source-page-{rng.randrange(10, 99)}")
    if tamper_type == "logic_conflict":
        c.setFillColor(colors.white)
        c.rect(226, 492, 250, 78, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(230, 548, "Amount: 10000.00")
        c.drawString(230, 522, "Tax: 1300.00")
        c.drawString(230, 496, "Total: 19800.00")
    if tamper_type == "noise_patch":
        patch = make_noise_patch(work_dir / f"noise_patch_{seed}.jpg", seed)
        c.drawImage(ImageReader(str(patch)), 214, 492, width=210, height=58)


def generate_pdf(path: Path, doc_type: str, tamper_type: str, index: int, work_dir: Path, seed: int) -> None:
    rng = random.Random(seed)
    width, height = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    draw_header(c, doc_type, index, width, height)
    if tamper_type != "screenshot_to_pdf":
        draw_table(c, rows_for_doc(doc_type, rng), 58, height - 130, width - 116)
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.grey)
        c.drawString(58, 330, f"Tamper simulation: {tamper_type}")
    apply_tamper_visuals(c, tamper_type, work_dir, doc_type, seed, width, height)
    draw_watermark(c, width, height)
    if tamper_type == "page_splice":
        c.showPage()
        draw_header(c, doc_type, index, width, height)
        draw_table(c, rows_for_doc(doc_type, random.Random(seed + 1000)), 58, height - 130, width - 116)
        c.setFillColor(colors.white)
        c.rect(220, 520, 230, 24, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(226, 528, f"INSERTED PAGE VALUE {rng.randrange(10000, 99999)}")
        draw_watermark(c, width, height)
    c.showPage()
    c.save()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--per-type", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--out-dir", default="data/prepared/fake")
    parser.add_argument("--manifest", default="outputs/synthetic_negative_manifest.csv")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    out_root = root / args.out_dir
    work_dir = root / "outputs" / "synthetic_negative_assets"
    manifest_path = root / args.manifest
    work_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    counter = 0
    for doc_type in DOC_TYPES:
        doc_dir = out_root / doc_type
        doc_dir.mkdir(parents=True, exist_ok=True)
        for i in range(args.per_type):
            tamper_type = TAMPER_TYPES[i % len(TAMPER_TYPES)]
            counter += 1
            document_id = f"synthetic_fake_{counter:06d}"
            path = doc_dir / f"{document_id}_{tamper_type}.pdf"
            generate_pdf(path, doc_type, tamper_type, counter, work_dir, args.seed + counter)
            rows.append(
                {
                    "document_id": document_id,
                    "label": "fake",
                    "doc_type": doc_type,
                    "ext": ".pdf",
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "path": str(path),
                    "synthetic": "yes",
                    "tamper_type": tamper_type,
                    "detection_category": DETECTION_CATEGORIES[tamper_type],
                    "safety_watermark": WATERMARK,
                }
            )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "document_id",
            "label",
            "doc_type",
            "ext",
            "size_bytes",
            "sha256",
            "path",
            "synthetic",
            "tamper_type",
            "detection_category",
            "safety_watermark",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"generated={len(rows)}")
    print(f"out_dir={out_root}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
