#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path


DOC_TYPE_KEYWORDS = {
    "credit_report": ["征信", "信用报告"],
    "invoice": ["发票"],
    "contract": ["合同", "购销"],
    "settlement_statement": ["结算单"],
    "receipt": ["回单"],
    "bank_page": ["网银"],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def decode_zip_name(name: str) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return name.encode("cp437").decode(encoding)
        except UnicodeError:
            continue
    for encoding in ("utf-8", "gbk"):
        decoded = name.encode("cp437", errors="ignore").decode(encoding, errors="ignore")
        if any(keyword in decoded for keywords in DOC_TYPE_KEYWORDS.values() for keyword in keywords):
            return decoded
    return name


def unzip_once(zip_path: Path, target_dir: Path) -> None:
    marker = target_dir / ".extracted"
    if marker.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        counter = 0
        index_rows = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            original_name = decode_zip_name(info.filename)
            source_name = Path(original_name).name
            if source_name.startswith("._") or source_name == ".DS_Store":
                continue
            ext = Path(source_name).suffix.lower()
            if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
                continue
            counter += 1
            doc_type = infer_doc_type(Path(original_name))
            short_name = f"{doc_type}_{counter:06d}{ext}"
            doc_dir = target_dir / doc_type
            doc_dir.mkdir(parents=True, exist_ok=True)
            target = doc_dir / short_name
            with zf.open(info, "r") as src, target.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
            index_rows.append(
                {
                    "stored_path": str(target),
                    "zip_member": original_name,
                    "zip_member_raw": info.filename,
                }
            )
    with (target_dir / "zip_index.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["stored_path", "zip_member", "zip_member_raw"])
        writer.writeheader()
        writer.writerows(index_rows)
    marker.write_text(zip_path.name, encoding="utf-8")


def clean_name(path: Path) -> bool:
    return not path.name.startswith("._") and path.name != ".DS_Store"


def infer_doc_type(path: Path) -> str:
    text = str(path)
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return doc_type
    return "other"


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and clean_name(path):
            yield path


def build_manifest(prepared_dir: Path, manifest_path: Path) -> None:
    rows = []
    for label in ["normal", "fake"]:
        label_root = prepared_dir / label
        for path in iter_files(label_root):
            ext = path.suffix.lower()
            if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
                continue
            doc_type = path.parent.name if path.parent != label_root else infer_doc_type(path)
            rows.append(
                {
                    "document_id": f"{label}_{len(rows):06d}",
                    "label": label,
                    "doc_type": doc_type,
                    "ext": ext,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "path": str(path),
                }
            )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["document_id", "label", "doc_type", "ext", "size_bytes", "sha256", "path"],
        )
        writer.writeheader()
        writer.writerows(rows)
    summary = {}
    for row in rows:
        key = (row["label"], row["doc_type"], row["ext"])
        summary[key] = summary.get(key, 0) + 1
    print(json.dumps({str(k): v for k, v in sorted(summary.items())}, ensure_ascii=False, indent=2))
    print(f"manifest={manifest_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--normal-zip", required=True)
    parser.add_argument("--fake-zip", required=True)
    parser.add_argument("--prepared-dir", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    prepared_dir = Path(args.prepared_dir)
    unzip_once(Path(args.normal_zip), prepared_dir / "normal")
    unzip_once(Path(args.fake_zip), prepared_dir / "fake")
    build_manifest(prepared_dir, Path(args.manifest))


if __name__ == "__main__":
    main()
