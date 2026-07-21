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

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover - production fallback when scipy is absent
    ndimage = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def read_csv_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


def safe_name(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)[:120]


def render_pdf_pages(path: Path, out_dir: Path, dpi: int, max_pages: int, prefix_name: str = "") -> list[Path]:
    prefix = out_dir / safe_name(prefix_name or path.stem)
    proc = run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", str(max(1, max_pages)), str(path), str(prefix)])
    pages = sorted(out_dir.glob(f"{prefix.name}-*.png"))
    if proc.returncode != 0 or not pages:
        return []
    return pages[:max_pages]


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


def _component_boxes(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Return (x0, y0, x1, y1, pixels) for 8-connected components."""
    if ndimage is not None:
        labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=np.uint8))
        if count == 0:
            return []
        sizes = np.bincount(labels.ravel())
        boxes = []
        for label_id, item in enumerate(ndimage.find_objects(labels), start=1):
            if item is None:
                continue
            ys, xs = item
            boxes.append((xs.start, ys.start, xs.stop, ys.stop, int(sizes[label_id])))
        return boxes

    # Dependency-free fallback. The normalized page is at most 900 px on its
    # longest side, so a byte mask + iterative flood fill remains bounded.
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=np.uint8)
    boxes = []
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or visited[y0, x0]:
                continue
            stack = [(x0, y0)]
            visited[y0, x0] = 1
            x_min = x_max = x0
            y_min = y_max = y0
            size = 0
            while stack:
                x, y = stack.pop()
                size += 1
                x_min, x_max = min(x_min, x), max(x_max, x)
                y_min, y_max = min(y_min, y), max(y_max, y)
                for ny in range(max(0, y - 1), min(h, y + 2)):
                    for nx in range(max(0, x - 1), min(w, x + 2)):
                        if mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = 1
                            stack.append((nx, ny))
            boxes.append((x_min, y_min, x_max + 1, y_max + 1, size))
    return boxes


def _radial_seal_features(mask: np.ndarray) -> tuple[float, float, float, float]:
    """Return annulus density, angular coverage, center density and uniformity."""
    h, w = mask.shape
    if h < 8 or w < 8:
        return 0.0, 0.0, 0.0, 0.0
    yy, xx = np.indices((h, w), dtype=np.float32)
    nx = (xx - (w - 1) / 2.0) / max(w / 2.0, 1.0)
    ny = (yy - (h - 1) / 2.0) / max(h / 2.0, 1.0)
    radius = np.sqrt(nx * nx + ny * ny)
    annulus = (radius >= 0.62) & (radius <= 1.02)
    center = radius <= 0.52
    ring_density = float(mask[annulus].mean()) if annulus.any() else 0.0
    center_density = float(mask[center].mean()) if center.any() else 0.0

    angle = (np.arctan2(ny, nx) + math.pi) / (2 * math.pi)
    occupied = 0
    sector_densities = []
    sectors = 24
    for sector in range(sectors):
        sector_mask = annulus & (angle >= sector / sectors) & (angle < (sector + 1) / sectors)
        density = float(mask[sector_mask].mean()) if sector_mask.any() else 0.0
        sector_densities.append(density)
        if density >= 0.018:
            occupied += 1
    active = [density for density in sector_densities if density >= 0.018]
    if len(active) >= 4:
        mean = sum(active) / len(active)
        variance = sum((density - mean) ** 2 for density in active) / len(active)
        ring_uniformity = 1.0 / (1.0 + math.sqrt(variance) / max(mean, 1e-6))
    else:
        ring_uniformity = 0.0
    return ring_density, occupied / sectors, center_density, ring_uniformity


def candidate_semantics(candidate: dict, page_width: int, page_height: int) -> dict:
    """Classify obvious stamp geometry without treating the result as fraud evidence.

    This deliberately has an ``unknown`` outcome. Dense QR-like squares and
    tiny text fragments are retained for audit but should not outrank a clear
    annular stamp or automatically trigger OCR.
    """
    x0, y0, x1, y1 = candidate["bbox"]
    bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)
    pixel_aspect = bw / bh
    page_area = max(page_width * page_height, 1)
    area_ratio = bw * bh / page_area
    short_fraction = max(bw, bh) / max(min(page_width, page_height), 1)
    ring_density = float(candidate["ring_density"])
    center_density = float(candidate["center_density"])
    angular_coverage = float(candidate["angular_coverage"])
    ring_uniformity = float(candidate["ring_uniformity"])
    ink_ratio = float(candidate["ink_ratio"])
    saturation = float(candidate["mean_saturation"])
    center_ring_ratio = center_density / max(ring_density, 1e-6)

    near_square = 0.62 <= pixel_aspect <= 1.62
    color_near_square = 0.45 <= pixel_aspect <= 1.80
    dense_square = (
        0.72 <= pixel_aspect <= 1.38
        and ink_ratio >= 0.285
        and ring_density >= 0.28
        and center_density >= 0.22
        and saturation <= 0.10
    )
    tiny_fragment = area_ratio < 0.0024 or short_fraction < 0.045
    outer_ring = (
        near_square
        and ring_density >= 0.085
        and angular_coverage >= 0.70
        and ring_uniformity >= 0.68
        and center_ring_ratio <= 0.45
        and ink_ratio <= 0.22
        and area_ratio >= 0.0035
    )
    color_stamp = (
        color_near_square
        and saturation >= 0.14
        and ring_density >= 0.035
        and angular_coverage >= 0.50
        and ink_ratio <= 0.28
        and area_ratio >= 0.0022
    )
    monochrome_stamp = (
        near_square
        and saturation <= 0.12
        and ring_density >= 0.085
        and angular_coverage >= 0.78
        and ring_uniformity >= 0.42
        and 0.0032 <= area_ratio <= 0.055
        and center_ring_ratio <= 0.40
        and ink_ratio <= 0.22
    )

    shape_score = max(0.0, 1.0 - abs(math.log(max(pixel_aspect, 1e-6))) / math.log(2.0))
    outer_ring_score = max(0.0, min(1.0, 1.25 - center_ring_ratio))
    semantic_score = (
        0.20 * shape_score
        + 0.23 * angular_coverage
        + 0.20 * ring_uniformity
        + 0.22 * outer_ring_score
        + 0.15 * min(1.0, ring_density / 0.13)
    )
    if dense_square:
        semantic_score -= 0.42
    if tiny_fragment:
        semantic_score -= 0.20
    semantic_score = max(0.0, min(1.0, semantic_score))

    if dense_square:
        candidate_class = "unknown"
        class_reason = "dense_square_pattern"
        ocr_recommended = 0
    elif not near_square:
        candidate_class = "unknown"
        class_reason = "elongated_component"
        ocr_recommended = 0
    elif tiny_fragment and not color_stamp:
        candidate_class = "unknown"
        class_reason = "tiny_fragment"
        ocr_recommended = 0
    elif outer_ring or color_stamp or monochrome_stamp:
        candidate_class = "seal"
        class_reason = "annular_stamp_geometry"
        ocr_recommended = 1
    else:
        candidate_class = "unknown"
        class_reason = "insufficient_stamp_geometry"
        ocr_recommended = int(semantic_score >= 0.42)

    return {
        "semantic_score": semantic_score,
        "candidate_class": candidate_class,
        "class_reason": class_reason,
        "ocr_recommended": ocr_recommended,
        "pixel_aspect": pixel_aspect,
        "area_ratio": area_ratio,
        "center_ring_ratio": center_ring_ratio,
        "dense_square": int(dense_square),
    }


def color_agnostic_seal_features(image: Image.Image) -> dict:
    """Locate stamp-like regions without relying on red color.

    The detector groups locally dark ink, then scores near-square components by
    elliptical annulus occupancy.  Candidate presence is intentionally a
    *review/localization* signal, not proof of forgery.  Saturation separates
    red/color seals from grayscale/photocopied candidates for downstream OCR.
    """
    rgb = normalized_image(image, 900).convert("RGB")
    gray_image = rgb.convert("L")
    gray = np.asarray(gray_image, dtype=np.float32)
    background = np.asarray(gray_image.filter(ImageFilter.GaussianBlur(radius=8.0)), dtype=np.float32)
    ink_strength = np.clip(background - gray, 0, 255)
    positive = ink_strength[ink_strength > 2]
    adaptive = float(np.percentile(positive, 58)) if positive.size else 0.0
    ink_threshold = max(8.0, min(30.0, adaptive))
    ink_mask = ink_strength >= ink_threshold

    # Remove long table rules and form lines before component grouping. A seal
    # often overlaps a table/signature line; without suppression that line
    # merges the circular ink into a page-wide rectangular component.
    candidate_ink = ink_mask.copy()
    long_rows = np.flatnonzero(candidate_ink.mean(axis=1) >= 0.22)
    long_cols = np.flatnonzero(candidate_ink.mean(axis=0) >= 0.22)
    for row in long_rows:
        candidate_ink[max(0, row - 2): min(candidate_ink.shape[0], row + 3), :] = False
    for col in long_cols:
        candidate_ink[:, max(0, col - 2): min(candidate_ink.shape[1], col + 3)] = False

    # Close and slightly dilate so the outer ring and internal seal text form a
    # single candidate, while ordinary text lines remain wide rectangles.
    grouped = Image.fromarray(candidate_ink.astype(np.uint8) * 255)
    grouped = grouped.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(5))
    grouped_mask = np.asarray(grouped) > 0

    h, w = ink_mask.shape
    page_area = max(h * w, 1)
    page_short = max(1, min(h, w))
    edge_array = np.asarray(gray_image.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    hsv = np.asarray(rgb.convert("HSV"), dtype=np.float32)
    high_frequency = np.abs(gray - np.asarray(gray_image.filter(ImageFilter.GaussianBlur(radius=0.9)), dtype=np.float32))
    candidates = []
    for x0, y0, x1, y1, component_pixels in _component_boxes(grouped_mask):
        bw, bh = x1 - x0, y1 - y0
        if bw < page_short * 0.035 or bh < page_short * 0.035:
            continue
        if bw > page_short * 0.34 or bh > page_short * 0.34:
            continue
        aspect = bw / max(bh, 1)
        # Photocopy dropout or a crossed form line can elongate the grouped
        # component even when the underlying stamp is circular. Radial
        # occupancy below remains the stronger shape gate.
        if not 0.45 <= aspect <= 2.22:
            continue
        bbox_ratio = (bw * bh) / page_area
        grouped_fill = component_pixels / max(bw * bh, 1)
        if not 0.0008 <= bbox_ratio <= 0.085 or not 0.035 <= grouped_fill <= 0.92:
            continue

        pad_x = max(2, int(round(bw * 0.08)))
        pad_y = max(2, int(round(bh * 0.08)))
        ax0, ay0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
        ax1, ay1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
        local_mask = ink_mask[ay0:ay1, ax0:ax1]
        ink_ratio = float(local_mask.mean())
        if not 0.018 <= ink_ratio <= 0.58:
            continue
        ring_density, angular_coverage, center_density, ring_uniformity = _radial_seal_features(local_mask)
        if ring_density < 0.018 or angular_coverage < 0.28:
            continue

        local_strength = ink_strength[ay0:ay1, ax0:ax1]
        local_edges = edge_array[ay0:ay1, ax0:ax1]
        local_gray = gray[ay0:ay1, ax0:ax1]
        local_sat = hsv[ay0:ay1, ax0:ax1, 1]
        local_hf = high_frequency[ay0:ay1, ax0:ax1]
        ink_values = local_mask
        mean_contrast = float(local_strength[ink_values].mean()) if ink_values.any() else 0.0
        edge_contrast = float(local_edges[ink_values].mean()) if ink_values.any() else 0.0
        texture_std = float(local_gray[ink_values].std()) if ink_values.any() else 0.0
        mean_saturation = float(local_sat[ink_values].mean() / 255.0) if ink_values.any() else 0.0
        midtone_ratio = float(((local_gray > 35) & (local_gray < 220) & local_mask).sum() / max(local_mask.sum(), 1))
        hf_mean = float(local_hf[ink_values].mean()) if ink_values.any() else 0.0
        halftone_score = min(1.0, midtone_ratio * min(1.0, hf_mean / 12.0))

        shape_score = max(0.0, 1.0 - abs(math.log(max(aspect, 1e-6))) / math.log(2.0))
        ring_score = min(1.0, ring_density / 0.11)
        contrast_score = min(1.0, mean_contrast / 38.0)
        size_fraction = max(bw, bh) / page_short
        size_score = max(0.0, 1.0 - abs(size_fraction - 0.13) / 0.13)
        candidate_score = (
            0.24 * shape_score
            + 0.34 * angular_coverage
            + 0.22 * ring_score
            + 0.12 * contrast_score
            + 0.08 * size_score
        )
        if candidate_score < 0.43:
            continue
        candidates.append({
            "score": candidate_score,
            "bbox": (ax0, ay0, ax1, ay1),
            "ink_ratio": ink_ratio,
            "ring_density": ring_density,
            "angular_coverage": angular_coverage,
            "ring_uniformity": ring_uniformity,
            "center_density": center_density,
            "edge_contrast": edge_contrast,
            "texture_std": texture_std,
            "halftone_score": halftone_score,
            "mean_saturation": mean_saturation,
        })

    for candidate in candidates:
        candidate.update(candidate_semantics(candidate, w, h))

    if not candidates:
        return {
            "seal_candidate_count": 0,
            "seal_candidate_best_score": 0.0,
            "seal_candidate_bbox_norm": "",
            "seal_candidate_ink_ratio": 0.0,
            "seal_candidate_ring_density": 0.0,
            "seal_candidate_angular_coverage": 0.0,
            "seal_candidate_ring_uniformity": 0.0,
            "seal_candidate_center_density": 0.0,
            "seal_candidate_edge_contrast": 0.0,
            "seal_candidate_texture_std": 0.0,
            "seal_candidate_halftone_score": 0.0,
            "seal_candidate_mean_saturation": 0.0,
            "seal_candidate_is_monochrome": 0,
            "seal_candidate_semantic_score": 0.0,
            "seal_candidate_class": "none",
            "seal_candidate_class_reason": "candidate_missing",
            "seal_candidate_ocr_recommended": 0,
            "seal_candidate_pixel_aspect": 0.0,
            "seal_candidate_area_ratio": 0.0,
            "seal_candidate_center_ring_ratio": 0.0,
            "seal_candidate_dense_square": 0,
        }

    candidates.sort(key=lambda item: (item["semantic_score"], item["score"]), reverse=True)
    best = candidates[0]
    x0, y0, x1, y1 = best["bbox"]
    bbox_norm = ",".join(f"{value:.6f}" for value in (x0 / w, y0 / h, x1 / w, y1 / h))
    return {
        "seal_candidate_count": len(candidates),
        "seal_candidate_best_score": round(best["score"], 6),
        "seal_candidate_bbox_norm": bbox_norm,
        "seal_candidate_ink_ratio": round(best["ink_ratio"], 6),
        "seal_candidate_ring_density": round(best["ring_density"], 6),
        "seal_candidate_angular_coverage": round(best["angular_coverage"], 6),
        "seal_candidate_ring_uniformity": round(best["ring_uniformity"], 6),
        "seal_candidate_center_density": round(best["center_density"], 6),
        "seal_candidate_edge_contrast": round(best["edge_contrast"], 4),
        "seal_candidate_texture_std": round(best["texture_std"], 4),
        "seal_candidate_halftone_score": round(best["halftone_score"], 6),
        "seal_candidate_mean_saturation": round(best["mean_saturation"], 6),
        "seal_candidate_is_monochrome": int(best["mean_saturation"] <= 0.12),
        "seal_candidate_semantic_score": round(best["semantic_score"], 6),
        "seal_candidate_class": best["candidate_class"],
        "seal_candidate_class_reason": best["class_reason"],
        "seal_candidate_ocr_recommended": best["ocr_recommended"],
        "seal_candidate_pixel_aspect": round(best["pixel_aspect"], 6),
        "seal_candidate_area_ratio": round(best["area_ratio"], 6),
        "seal_candidate_center_ring_ratio": round(best["center_ring_ratio"], 6),
        "seal_candidate_dense_square": best["dense_square"],
    }


def save_seal_candidate_crops(image: Image.Image, bbox_norm: str, crop_dir: Path, stem: str) -> tuple[str, str]:
    """Save a context crop and a contrast-normalized OCR preparation crop."""
    if not bbox_norm:
        return "", ""
    values = [float(value) for value in bbox_norm.split(",")]
    if len(values) != 4:
        return "", ""
    image = image.convert("RGB")
    w, h = image.size
    x0, y0, x1, y1 = values
    left, top, right, bottom = x0 * w, y0 * h, x1 * w, y1 * h
    pad = 0.20 * max(right - left, bottom - top)
    box = (
        max(0, int(left - pad)),
        max(0, int(top - pad)),
        min(w, int(math.ceil(right + pad))),
        min(h, int(math.ceil(bottom + pad))),
    )
    crop = image.crop(box)
    crop_dir.mkdir(parents=True, exist_ok=True)
    context_path = crop_dir / f"{safe_name(stem)}_seal_context.png"
    ocr_path = crop_dir / f"{safe_name(stem)}_seal_ocr.png"
    crop.save(context_path)
    gray = ImageOps.autocontrast(crop.convert("L"), cutoff=1).filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=3))
    scale = max(1.0, 640 / max(gray.size))
    if scale > 1.0:
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
    gray.save(ocr_path)
    return str(context_path), str(ocr_path)


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
    seal_candidate = color_agnostic_seal_features(image)
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
    if seal_candidate["seal_candidate_best_score"] >= 0.52:
        reasons.append("seal_color_agnostic_candidate")
    if seal_candidate["seal_candidate_class"] == "seal":
        reasons.append("seal_candidate_likely_seal")
    elif seal_candidate["seal_candidate_dense_square"]:
        reasons.append("seal_candidate_dense_square_nonseal")
    elif seal_candidate["seal_candidate_count"]:
        reasons.append("seal_candidate_unknown_review")
    if seal_candidate["seal_candidate_is_monochrome"] and seal_candidate["seal_candidate_best_score"] >= 0.52:
        reasons.append("seal_monochrome_candidate")
    if (
        seal_candidate["seal_candidate_best_score"] >= 0.58
        and seal_candidate["seal_candidate_edge_contrast"] >= 20.0
        and seal_candidate["seal_candidate_texture_std"] <= 45.0
    ):
        reasons.append("seal_candidate_hard_edge_review")
    return {
        "visual_width": w,
        "visual_height": h,
        "ela_score": ela,
        "block_variance_score": block_var,
        "edge_density": edge,
        "red_stamp_score": red,
        **seal,
        **seal_candidate,
        "visual_risk_score": min(score, 100),
        "visual_risk_reasons": "|".join(reasons),
    }


def aggregate_page_results(page_results: list[tuple[int, Path, dict]]) -> tuple[dict, Path | None]:
    """Aggregate page features while keeping the best seal page for cropping."""
    if not page_results:
        return {}, None
    _, _, risk_best = max(page_results, key=lambda item: item[2].get("visual_risk_score", 0))
    result = dict(risk_best)
    result["visual_page_count_analyzed"] = len(page_results)
    result["visual_risk_score"] = max(item[2].get("visual_risk_score", 0) for item in page_results)
    merged_reasons = []
    for _, _, features in page_results:
        merged_reasons.extend(reason for reason in features.get("visual_risk_reasons", "").split("|") if reason)
    result["visual_risk_reasons"] = "|".join(dict.fromkeys(merged_reasons))
    for key in (
        "ela_score", "block_variance_score", "edge_density", "red_stamp_score",
        "red_component_count", "max_red_component_ratio", "red_component_edge_contrast",
        "red_component_color_std",
    ):
        result[key] = max(float(item[2].get(key, 0) or 0) for item in page_results)

    seal_page, seal_path, seal_best = max(
        page_results,
        key=lambda item: (
            item[2].get("seal_candidate_semantic_score", 0),
            item[2].get("seal_candidate_best_score", 0),
        ),
    )
    for key, value in seal_best.items():
        if key.startswith("seal_candidate_"):
            result[key] = value
    result["seal_candidate_page"] = seal_page if seal_best.get("seal_candidate_best_score", 0) > 0 else 0
    return result, seal_path if result["seal_candidate_page"] else None


def analyze_row(
    row: dict,
    render_dir: Path,
    dpi: int,
    seal_crop_dir: Path | None = None,
    max_pages: int = 1,
) -> dict:
    src = Path(row["path"])
    if not src.exists():
        return {**row, "visual_error": "missing_file"}
    temp_root = render_dir / "tmp_visual_pages"
    temp_root.mkdir(parents=True, exist_ok=True)
    image_paths = []
    if row["ext"].lower() == ".pdf":
        image_paths = render_pdf_pages(src, temp_root, dpi, max_pages, row.get("document_id", ""))
    elif row["ext"].lower() in {".jpg", ".jpeg", ".png"}:
        image_paths = [src]
    if not image_paths:
        return {**row, "visual_error": "render_failed"}
    try:
        page_results = [(index, path, analyze_image(path)) for index, path in enumerate(image_paths, start=1)]
        result, seal_image_path = aggregate_page_results(page_results)
        context_path = ""
        ocr_path = ""
        if seal_crop_dir and result.get("seal_candidate_bbox_norm") and seal_image_path:
            with Image.open(seal_image_path) as rendered:
                context_path, ocr_path = save_seal_candidate_crops(
                    rendered,
                    result["seal_candidate_bbox_norm"],
                    seal_crop_dir,
                    row.get("document_id") or src.stem,
                )
        return {
            **row,
            **result,
            "seal_candidate_crop_path": context_path,
            "seal_candidate_ocr_path": ocr_path,
            "visual_error": "",
        }
    except Exception as exc:
        return {**row, "visual_error": str(exc)[:200]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--render-dir", default="outputs/visual_forensics")
    parser.add_argument("--seal-crop-dir", default="", help="optional directory for best seal context/OCR crops")
    parser.add_argument("--max-pages", type=int, default=1, help="analyze the first N PDF pages")
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
    seal_crop_dir = Path(args.seal_crop_dir) if args.seal_crop_dir else None
    records = [analyze_row(row, render_dir, args.dpi, seal_crop_dir, args.max_pages) for row in rows]
    shutil.rmtree(render_dir / "tmp_visual_pages", ignore_errors=True)

    fields = list(rows[0].keys()) + [
        "visual_width",
        "visual_height",
        "visual_page_count_analyzed",
        "ela_score",
        "block_variance_score",
        "edge_density",
        "red_stamp_score",
        "red_component_count",
        "max_red_component_ratio",
        "red_component_edge_contrast",
        "red_component_color_std",
        "seal_candidate_count",
        "seal_candidate_best_score",
        "seal_candidate_bbox_norm",
        "seal_candidate_ink_ratio",
        "seal_candidate_ring_density",
        "seal_candidate_angular_coverage",
        "seal_candidate_ring_uniformity",
        "seal_candidate_center_density",
        "seal_candidate_edge_contrast",
        "seal_candidate_texture_std",
        "seal_candidate_halftone_score",
        "seal_candidate_mean_saturation",
        "seal_candidate_is_monochrome",
        "seal_candidate_semantic_score",
        "seal_candidate_class",
        "seal_candidate_class_reason",
        "seal_candidate_ocr_recommended",
        "seal_candidate_pixel_aspect",
        "seal_candidate_area_ratio",
        "seal_candidate_center_ring_ratio",
        "seal_candidate_dense_square",
        "seal_candidate_page",
        "seal_candidate_crop_path",
        "seal_candidate_ocr_path",
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
