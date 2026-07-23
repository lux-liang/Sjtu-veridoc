#!/usr/bin/env python3
"""Install the isolated client-facing credit-report page into a live web root."""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def deploy(web_root: Path, source: Path, dry_run: bool = False) -> Path:
    target = web_root / "credit-report.html"
    content = source.read_text(encoding="utf-8")
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return target
    if dry_run:
        return target
    if target.exists():
        backup = web_root / f".backup_credit_report_{datetime.now():%Y%m%d_%H%M%S}.html"
        shutil.copy2(target, backup)
    target.write_text(content, encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("client/credit-report/index.html"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target = deploy(args.web_root.resolve(), args.source.resolve(), args.dry_run)
    print(f"{'would install' if args.dry_run else 'installed'} {target}")
