#!/usr/bin/env python3
from __future__ import annotations
import argparse
import csv
import json
import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def read_csv_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:120]


def render_pdf_first_page(path: Path, out_dir: Path, dpi: int) -> Path | None:
    prefix = out_dir / safe_name(path.stem)
    proc = run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(path), str(prefix)])
    pages = sorted(out_dir.glob(f"{prefix.name}-*.png"))
    if proc.returncode != 0 or not pages:
        return None
    return pages[0]


def ela_score(image: Image.Image, quality: int = 85) -> float:
    image = normalized_image(image, 900).convert("RGB")
    with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
        image.save(f.name, "JPEG", quality=quality)
        recompressed = Image.open(f.name).convert("RGB")
        diff = ImageChops.difference(image, recompressed)
        stat = ImageStat.Stat(diff)
    return round(sum(stat.mean) / 3.0, 4)


def block_variance_score(image: Image.Image, block: int = 64) -> float:
    gray = normalized_image(image, 900).convert("L")
    w, h = gray.size
    values = []
    for y in range(0, h - block, block):
        for x in range(0, w - block, block):
            crop = gray.crop((x, y, x + block, y + block))
            values.append(ImageStat.Stat(crop).stddev[0])
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return round(math.sqrt(var), 4)


def edge_density(image: Image.Image) -> float:
    gray = normalized_image(image, 700).convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return round(stat.mean[0], 4)


def red_stamp_score(image: Image.Image) -> float:
    rgb = normalized_image(image, 700).convert("RGB")
    pixels = rgb.load()
    w, h = rgb.size
    red_like = 0
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if r > 120 and r > g * 1.45 and r > b * 1.45:
                red_like += 1
    return round(red_like / max(w * h, 1), 6)


def seal_overlay_features(image: Image.Image) -> dict:
    """Estimate whether red seal pixels look like a pasted overlay."""
    rgb = normalized_image(image, 700).convert("RGB")
    gray_edges = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    pixels = rgb.load()
    edge_pixels = gray_edges.load()
    w, h = rgb.size
    red_mask = bytearray(w * h)
    red_points = []
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if r > 120 and r > g * 1.45 and r > b * 1.45:
                red_mask[y * w + x] = 1
                red_points.append((x, y, r, g, b))

    if not red_points:
        return {
            "red_component_count": 0,
            "max_red_component_ratio": 0.0,
            "red_component_edge_contrast": 0.0,
            "red_component_color_std": 0.0,
        }

    visited = bytearray(w * h)
    component_sizes = []
    component_edge_values = []
    component_colors = []
    for y0 in range(h):
        for x0 in range(w):
            idx0 = y0 * w + x0
            if not red_mask[idx0] or visited[idx0]:
                continue
            stack = [(x0, y0)]
            visited[idx0] = 1
            xs = []
            colors = []
            edge_values = []
            while stack:
                x, y = stack.pop()
                xs.append((x, y))
                colors.append(pixels[x, y])
                edge_values.append(edge_pixels[x, y])
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= w or ny >= h:
                        continue
                    idx = ny * w + nx
                    if red_mask[idx] and not visited[idx]:
                        visited[idx] = 1
                        stack.append((nx, ny))
            if len(xs) >= 20:
                component_sizes.append(len(xs))
                component_edge_values.extend(edge_values)
                component_colors.extend(colors)

    if not component_sizes:
        return {
            "red_component_count": 0,
            "max_red_component_ratio": 0.0,
            "red_component_edge_contrast": 0.0,
            "red_component_color_std": 0.0,
        }

    max_component_ratio = max(component_sizes) / max(w * h, 1)
    edge_contrast = sum(component_edge_values) / max(len(component_edge_values), 1)
    means = [sum(channel) / len(component_colors) for channel in zip(*component_colors)]
    color_var = 0.0
    for color in component_colors:
        color_var += sum((color[i] - means[i]) ** 2 for i in range(3)) / 3.0
    color_std = math.sqrt(color_var / max(len(component_colors), 1))
    return {
        "red_component_count": len(component_sizes),
        "max_red_component_ratio": round(max_component_ratio, 6),
        "red_component_edge_contrast": round(edge_contrast, 4),
        "red_component_color_std": round(color_std, 4),
    }


def normalized_image(image: Image.Image, max_side: int) -> Image.Image:
    image = image.convert("RGB")
    w, h = image.size
    scale = min(max_side / max(w, h), 1.0)
    if scale < 1.0:
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return image


def analyze_image(path: Path) -> dict:
    image = Image.open(path)
    w, h = image.size
    ela = ela_score(image)
    block_var = block_variance_score(image)
    edge = edge_density(image)
    red = red_stamp_score(image)
    seal = seal_overlay_features(image)
    score = 0
    reasons = []
    if ela >= 7.0:
        score += 20
        reasons.append("high_ela_recompression_error")
    if block_var >= 22.0:
        score += 15
        reasons.append("local_noise_block_inconsistency")
    if edge >= 18.0:
        score += 10
        reasons.append("dense_edge_or_paste_boundary")
    if red >= 0.012:
        score += 10
        reasons.append("red_stamp_like_region")
    if seal["max_red_component_ratio"] >= 0.0015 and seal["red_component_count"] >= 1:
        score += 8
        reasons.append("seal_red_connected_component")
    if seal["red_component_edge_contrast"] >= 22.0 and seal["max_red_component_ratio"] >= 0.001:
        score += 12
        reasons.append("seal_hard_edge_overlay")
    if 0.001 <= seal["max_red_component_ratio"] <= 0.08 and 0 < seal["red_component_color_std"] <= 28.0:
        score += 8
        reasons.append("seal_flat_color_overlay")
    return {
        "visual_width": w,
        "visual_height": h,
        "ela_score": ela,
        "block_variance_score": block_var,
        "edge_density": edge,
        "red_stamp_score": red,
        **seal,
        "visual_risk_score": min(score, 100),
        "visual_risk_reasons": "|".join(reasons),
    }


def analyze_row(row: dict, render_dir: Path, dpi: int) -> dict:
    src = Path(row["path"])
    if not src.exists():
        return {**row, "visual_error": "missing_file"}
    temp_root = render_dir / "tmp_visual_pages"
    temp_root.mkdir(parents=True, exist_ok=True)
    image_path = None
    if row["ext"].lower() == ".pdf":
        image_path = render_pdf_first_page(src, temp_root, dpi)
    elif row["ext"].lower() in {".jpg", ".jpeg", ".png"}:
        image_path = src
    if not image_path:
        return {**row, "visual_error": "render_failed"}
    try:
        return {**row, **analyze_image(image_path), "visual_error": ""}
    except Exception as exc:
        return {**row, "visual_error": str(exc)[:200]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--render-dir", default="outputs/visual_forensics")
    parser.add_argument("--dpi", type=int, default=110)
    parser.add_argument("--max-normal-per-type", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows = [row for row in read_csv_rows(Path(args.manifest)) if row["ext"].lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    if args.max_normal_per_type > 0:
        rng = random.Random(args.seed)
        kept = []
        normal_by_type = {}
        for row in rows:
            if row.get("label") != "normal" or row["ext"].lower() in {".jpg", ".jpeg", ".png"}:
                kept.append(row)
            else:
                normal_by_type.setdefault(row.get("doc_type", "other"), []).append(row)
        for bucket in normal_by_type.values():
            rng.shuffle(bucket)
            kept.extend(bucket[: args.max_normal_per_type])
        rows = kept
    render_dir = Path(args.render_dir)
    render_dir.mkdir(parents=True, exist_ok=True)
    records = [analyze_row(row, render_dir, args.dpi) for row in rows]
    shutil.rmtree(render_dir / "tmp_visual_pages", ignore_errors=True)

    fields = list(rows[0].keys()) + [
        "visual_width",
        "visual_height",
        "ela_score",
        "block_variance_score",
        "edge_density",
        "red_stamp_score",
        "red_component_count",
        "max_red_component_ratio",
        "red_component_edge_contrast",
        "red_component_color_std",
        "visual_risk_score",
        "visual_risk_reasons",
        "visual_error",
    ]
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    summary = {}
    for record in records:
        key = (record.get("label", ""), record.get("doc_type", ""))
        bucket = summary.setdefault(str(key), {"count": 0, "visual_risk_sum": 0, "errors": 0})
        bucket["count"] += 1
        bucket["visual_risk_sum"] += int(record.get("visual_risk_score") or 0)
        bucket["errors"] += 1 if record.get("visual_error") else 0
    for bucket in summary.values():
        bucket["mean_visual_risk_score"] = round(bucket["visual_risk_sum"] / max(bucket["count"], 1), 2)
        del bucket["visual_risk_sum"]
    Path(args.out_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
