#!/usr/bin/env python3
import argparse
import csv
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def safe_name(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_"}:
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)[:160]


def render_pdf(path: Path, out_dir: Path, dpi: int, max_pages: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("page-*.png"))
    if existing:
        return existing
    prefix = out_dir / "page"
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", str(max_pages), str(path), str(prefix)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return sorted(out_dir.glob("page-*.png"))


def copy_image(path: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg" if path.suffix.lower() in {".jpg", ".jpeg"} else path.suffix.lower()
    target = out_dir / f"page-1{suffix}"
    if not target.exists():
        shutil.copy2(path, target)
    return [target]


def process_row(row: dict, render_root: Path, dpi: int, max_pages: int) -> list[dict]:
    src = Path(row["path"])
    doc_dir = render_root / row["doc_type"] / row["label"] / safe_name(row["document_id"])
    if row["ext"] == ".pdf":
        pages = render_pdf(src, doc_dir, dpi, max_pages)
    else:
        pages = copy_image(src, doc_dir)
    return [
        {
            "document_id": row["document_id"],
            "label": row["label"],
            "doc_type": row["doc_type"],
            "page_index": index,
            "image_path": str(page),
            "source_path": row["path"],
        }
        for index, page in enumerate(pages, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--render-dir", required=True)
    parser.add_argument("--render-manifest", required=True)
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    render_root = Path(args.render_dir)
    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    rendered = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_row, row, render_root, args.dpi, args.max_pages) for row in rows]
        for future in as_completed(futures):
            rendered.extend(future.result())

    Path(args.render_manifest).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.render_manifest).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["document_id", "label", "doc_type", "page_index", "image_path", "source_path"],
        )
        writer.writeheader()
        writer.writerows(rendered)
    print(f"rendered_pages={len(rendered)}")
    print(f"render_manifest={args.render_manifest}")


if __name__ == "__main__":
    main()
