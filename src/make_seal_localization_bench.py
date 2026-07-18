#!/usr/bin/env python3
"""Generate a zero-PII benchmark for color-agnostic seal localization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


SEAL_TYPES = ("red", "gray", "photocopy", "faint")
NEGATIVE_TYPES = ("no_seal", "round_logo")


def page_background(rng: random.Random, width: int = 1000, height: int = 1400) -> Image.Image:
    noise_rng = np.random.default_rng(rng.randrange(1 << 32))
    base = noise_rng.normal(248, 2.5, size=(height, width, 1))
    tint = np.array([1.0, 0.996, 0.98], dtype=np.float32).reshape(1, 1, 3)
    array = np.clip(base * tint, 0, 255).astype(np.uint8)
    image = Image.fromarray(array)
    draw = ImageDraw.Draw(image)
    draw.text((70, 55), "SYNTHETIC BUSINESS DOCUMENT", fill=(35, 45, 60))
    draw.text((70, 90), f"Reference: DEMO-{rng.randrange(100000, 999999)}", fill=(65, 70, 80))
    for row in range(9):
        y = 190 + row * 72
        length = rng.randint(540, 820)
        draw.line((80, y, 80 + length, y), fill=(105, 110, 118), width=2)
        draw.line((80, y + 22, 80 + int(length * 0.68), y + 22), fill=(175, 178, 184), width=1)
    draw.rectangle((75, 900, 925, 1160), outline=(120, 125, 132), width=2)
    for x in (250, 480, 700):
        draw.line((x, 900, x, 1160), fill=(145, 150, 158), width=1)
    for y in (965, 1030, 1095):
        draw.line((75, y, 925, y), fill=(145, 150, 158), width=1)
    draw.text((90, 1215), "Authorized signature:", fill=(70, 75, 82))
    draw.line((270, 1240, 600, 1240), fill=(65, 70, 78), width=2)
    return image


def draw_round_logo(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    draw.ellipse((790, 45, 920, 175), outline=(45, 85, 150), width=7)
    draw.ellipse((812, 67, 898, 153), outline=(45, 85, 150), width=3)
    draw.text((825, 105), "LOGO", fill=(45, 85, 150))


def seal_layer(size: int, seal_type: str, rng: random.Random) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    if seal_type == "red":
        color = (185, 28, 38, 220)
    elif seal_type == "faint":
        color = (145, 145, 145, 145)
    else:
        color = (68, 68, 68, 205)
    margin = max(8, size // 18)
    draw.ellipse((margin, margin, size - margin, size - margin), outline=color, width=max(4, size // 30))
    draw.ellipse((margin * 2, margin * 2, size - margin * 2, size - margin * 2), outline=color, width=max(2, size // 60))
    draw.text((int(size * 0.27), int(size * 0.45)), "SEAL", fill=color)
    draw.line((int(size * 0.32), int(size * 0.62), int(size * 0.68), int(size * 0.62)), fill=color, width=max(2, size // 70))
    if seal_type == "photocopy":
        layer = layer.filter(ImageFilter.GaussianBlur(radius=0.65))
        array = np.asarray(layer).copy()
        alpha = array[:, :, 3]
        noise_rng = np.random.default_rng(rng.randrange(1 << 32))
        ink = alpha > 20
        dropout = noise_rng.random(alpha.shape) < 0.12
        alpha[ink & dropout] = 0
        speckle = (noise_rng.random(alpha.shape) < 0.015) & (alpha == 0)
        alpha[speckle] = noise_rng.integers(25, 90, size=int(speckle.sum()), dtype=np.uint8)
        array[:, :, 3] = alpha
        layer = Image.fromarray(array)
    return layer


def add_seal(image: Image.Image, seal_type: str, rng: random.Random) -> tuple[float, float, float, float]:
    size = rng.randint(170, 235)
    x0 = rng.randint(610, 850 - size)
    y0 = rng.randint(1120 - size, 1240 - size)
    layer = seal_layer(size, seal_type, rng)
    angle = rng.uniform(-12, 12)
    rotated = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    px = x0 - (rotated.width - size) // 2
    py = y0 - (rotated.height - size) // 2
    image.alpha_composite(rotated, (px, py))
    return x0 / image.width, y0 / image.height, (x0 + size) / image.width, (y0 + size) / image.height


def make_sample(seal_type: str, rng: random.Random) -> tuple[Image.Image, str]:
    image = page_background(rng).convert("RGBA")
    bbox = ""
    if seal_type in SEAL_TYPES:
        bbox = ",".join(f"{value:.6f}" for value in add_seal(image, seal_type, rng))
    elif seal_type == "round_logo":
        rgb = image.convert("RGB")
        draw_round_logo(rgb)
        image = rgb.convert("RGBA")
    return image.convert("RGB"), bbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="data/seal_localization_bench")
    parser.add_argument("--out-manifest", default="outputs/seal_localization_manifest.csv")
    parser.add_argument("--per-type", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = Path(args.out_manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    rows = []
    for seal_type in (*NEGATIVE_TYPES, *SEAL_TYPES):
        for index in range(args.per_type):
            document_id = f"sealbench_{seal_type}_{index:04d}"
            image, bbox = make_sample(seal_type, random.Random(rng.randrange(1 << 32)))
            path = out_dir / f"{document_id}.png"
            image.save(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append({
                "document_id": document_id,
                "label": "seal" if seal_type in SEAL_TYPES else "no_seal",
                "seal_type": seal_type,
                "has_seal": int(seal_type in SEAL_TYPES),
                "expected_bbox_norm": bbox,
                "ext": ".png",
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "path": str(path),
            })
    fields = ["document_id", "label", "seal_type", "has_seal", "expected_bbox_norm", "ext", "size_bytes", "sha256", "path"]
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} synthetic zero-PII samples -> {manifest}")


if __name__ == "__main__":
    main()
