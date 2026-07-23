#!/usr/bin/env python3
"""Evaluate credit-report PDFs against the rules in 征信报告造假识别(1).docx.

The output intentionally contains rule states and redacted evidence, not a
synthetic risk score.  A rule is only marked FAIL when both inputs are
machine-readable with high confidence.  Missing OCR, templates, or external
registry data is MANUAL rather than an accusation of forgery.
"""
from __future__ import annotations

import argparse
import csv
import email.utils
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


STATUS = {"pass", "fail", "possible", "manual", "not_applicable"}
WORD_RULE_IDS = (
    "G1", "G2", "G3", "G4",
    "P1A", "P1B", "P1C", "P1D", "P2",
    "O1",
    "H1", "H2", "H3", "H4", "H5", "H6",
    "C1", "C2", "C3A", "C3B", "C3C", "C3D",
)
SUPPORT_RULE_IDS = {"A0", "A1", "A2", "A3"}
FORWARD_DATE_WORDS = ("到期", "截止", "有效期", "营业期限", "还款计划", "期限", "终止")
HISTORICAL_DATE_WORDS = ("开户", "发放", "结清", "关闭", "更新", "报告", "还款", "立案", "成立", "发生", "登记日期")
FORWARD_DATE_FIELD_RE = re.compile(r"(?:到期|截止|有效期|有效截止|营业期限|许可截止|还款计划|终止)(?:日期|日|时间|期)?[:：]?$", re.I)
HISTORICAL_DATE_FIELD_RE = re.compile(r"(?:开户|开立|发放|结清|关闭|更新|信息报告|还款发生|最近一次还款|立案|成立|登记)(?:日期|日|时间)?[:：]?$", re.I)
DATE_RE = re.compile(r"((?:19|20)\d{2})[-/.年](0?[1-9]|1[0-2])[-/.月](3[01]|[12]\d|0?[1-9])(?:日)?(?:[T\s]*([01]?\d|2[0-3])[:：]([0-5]\d)(?:[:：]([0-5]\d))?)?")
REPORT_NUMBER_RE = re.compile(r"(?:报告编号|报告号码|NO\.?)(?:[:：]|：)?\s*([0-9OIl\s]{14,28})", re.I)
ID_RE = re.compile(r"(?<!\d)(\d{17}[\dXx]|\d{15})(?!\d)")
SOCIAL_CREDIT_RE = re.compile(r"(?<![0-9A-Z])([0-9A-Z]{18})(?![0-9A-Z])")
ZHONGZHENG_RE = re.compile(r"(?<!\d)(\d{16})(?!\d)")


@dataclass
class Rule:
    rule_id: str
    title: str
    status: str
    message: str
    word_level: str = "提示造假"
    mode: str = "automatic"
    expected: str = ""
    observed: str = ""
    source_hint: str = ""
    review_action: str = ""

    def as_dict(self) -> dict:
        if self.status not in STATUS:
            raise ValueError(f"invalid rule status: {self.status}")
        payload = {
            "rule_id": self.rule_id,
            "title": self.title,
            "status": self.status,
            "message": self.message,
            "word_level": self.word_level,
            "mode": self.mode,
        }
        evidence = {
            "expected": self.expected,
            "observed": self.observed,
            "source_hint": self.source_hint,
            "review_action": self.review_action,
        }
        if any(evidence.values()):
            payload["evidence"] = evidence
        return payload


def run(command: list[str], timeout: int = 90) -> tuple[str, bool]:
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    return (process.stdout, True) if process.returncode == 0 else ("", False)


def read_csv(path: Path) -> list[dict]:
    csv.field_size_limit(16 * 1024 * 1024)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        clean_lines = (line.replace("\x00", "") for line in stream)
        return list(csv.DictReader(clean_lines))


def ocr_text(row: dict, text_root: Path | None = None) -> str:
    """Read full OCR text when available, with preview compatibility."""
    relative_file = str(row.get("ocr_text_file") or "").strip()
    if relative_file and text_root:
        root = text_root.resolve()
        target = (root / relative_file).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            target = Path()
        if target.is_file():
            try:
                with gzip.open(target, "rt", encoding="utf-8", errors="replace") as stream:
                    return stream.read()
            except (OSError, EOFError):
                pass
    encoded = row.get("ocr_text_json", "")
    if encoded:
        try:
            value = json.loads(encoded)
            if isinstance(value, str):
                return value
        except (TypeError, json.JSONDecodeError):
            pass
    return str(row.get("ocr_text_preview") or "")


def normalize_text(value: str) -> str:
    value = value.replace("：", ":").replace("／", "/").replace("．", ".")
    return re.sub(r"[ \t]+", " ", value)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


def parse_date_match(match: re.Match) -> datetime | None:
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4) or 0), int(match.group(5) or 0), int(match.group(6) or 0),
        )
    except ValueError:
        return None


def parse_pdfinfo_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        return parsed.replace(tzinfo=None) if parsed else None
    except (TypeError, ValueError, OverflowError):
        pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern)
        except ValueError:
            continue
    return None


def pdf_info(path: Path) -> tuple[dict[str, str], bool]:
    info: dict[str, str] = {}
    output, readable = run(["pdfinfo", str(path)])
    for line in output.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info, readable


def pdf_fonts(path: Path) -> tuple[str, bool]:
    return run(["pdffonts", str(path)])


def pdf_text(path: Path) -> tuple[str, bool]:
    with tempfile.TemporaryDirectory(prefix="credit_text_") as temp_dir:
        target = Path(temp_dir) / "report.txt"
        try:
            process = subprocess.run(
                ["pdftotext", "-layout", str(path), str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "", False
        if process.returncode != 0 or not target.exists():
            return "", False
        return target.read_text(encoding="utf-8", errors="replace"), True


def report_datetime(text: str, require_seconds: bool = False) -> datetime | None:
    normalized = normalize_text(text)
    pages = [page for page in re.split(r"\f+", normalized) if page.strip()]
    cover = pages[0] if pages else normalized[:20000]
    field_boundary = r"(?<![A-Za-z\u3400-\u9fff])"
    patterns = [
        re.compile(field_boundary + r"报告\s*时\s*间\s*:?\s*" + DATE_RE.pattern),
        re.compile(field_boundary + r"报告\s*日\s*期\s*:?\s*" + DATE_RE.pattern),
    ]
    for pattern in patterns:
        for match in pattern.finditer(cover):
            if require_seconds and not all(match.group(index) is not None for index in (4, 5, 6)):
                continue
            return parse_date_match(match)
    return None


def report_number(text: str) -> str:
    normalized = normalize_text(text)
    pages = [page for page in re.split(r"\f+", normalized) if page.strip()]
    cover = pages[0] if pages else normalized[:20000]
    match = REPORT_NUMBER_RE.search(cover)
    if not match:
        # OCR often inserts spaces between the four Chinese characters.
        match = re.search(r"报\s*告\s*编\s*号\s*:?\s*([0-9OIl\s]{14,28})", cover, re.I)
    if not match:
        return ""
    value = re.sub(r"\s+", "", match.group(1)).translate(str.maketrans({"O": "0", "I": "1", "l": "1"}))
    return re.match(r"\d+", value).group(0) if re.match(r"\d+", value) else ""


def date_occurrences(text: str) -> list[tuple[datetime, str]]:
    output: list[tuple[datetime, str]] = []
    lines = normalize_text(text).splitlines()
    for line in lines:
        for match in DATE_RE.finditer(line):
            parsed = parse_date_match(match)
            if parsed:
                output.append((parsed, line.strip()[:160]))
    return output


def date_field_kind(line: str, match_start: int) -> str:
    """Classify a date only when an adjacent field label establishes meaning.

    PDF table extraction often places several unrelated columns on one line.
    A keyword elsewhere on that line is not enough evidence to call a date a
    historical fact or a maturity date.
    """
    prefix = compact_text(line[max(0, match_start - 36):match_start])
    if FORWARD_DATE_FIELD_RE.search(prefix):
        return "forward"
    if HISTORICAL_DATE_FIELD_RE.search(prefix):
        return "historical"
    return "unknown"


def next_value(lines: list[str], label: str) -> list[str]:
    values: list[str] = []
    matcher = re.compile(label)
    for index, line in enumerate(lines):
        match = matcher.search(line)
        if not match:
            continue
        tail = line[match.end():].strip(" :：|\t")
        if tail:
            values.append(tail)
            continue
        for following in lines[index + 1:index + 4]:
            candidate = following.strip(" :：|\t")
            if candidate:
                values.append(candidate)
                break
    return values


def cover_query_institution(text: str) -> str:
    pages = [page for page in re.split(r"\f+", text) if page.strip()]
    cover = compact_text(pages[0] if pages else text[:8000])
    match = re.search(r"查询机构:?(.{2,100}?)(?=报告时间|报告日期|第\d+页|$)", cover)
    return match.group(1) if match else ""


def report_variant(text: str, embedded: bool) -> str:
    compact = compact_text(text)
    enterprise_score = sum(token in compact for token in ("企业信用报告", "企业名称", "中征码", "统一社会信用代码"))
    personal_score = sum(token in compact for token in ("个人信用报告", "证件号码", "信用卡", "个人基本信息"))
    if enterprise_score > personal_score:
        if not embedded:
            return "scanned_enterprise"
        if "租赁" in cover_query_institution(text):
            return "leasing_enterprise"
        return "online_enterprise"
    if personal_score:
        online_markers = ("自主查询版", "账户明细如下", "发生过逾期的贷记卡", "从未逾期过的贷记卡")
        print_markers = ("个人基本信息", "信贷交易信息明细", "信贷交易违约信息概要", "还款状态")
        if any(token in compact for token in online_markers):
            return "online_personal" if embedded else "scanned_online_personal"
        if any(token in compact for token in print_markers):
            return "pboc_print_personal"
        return "unknown"
    return "unknown"


def source_is_original(has_native_text: bool, object_features: dict | None = None) -> bool:
    """Separate electronic/mixed PDFs from page-image scans using structure."""
    if not has_native_text or object_features is None:
        return False
    features = object_features
    try:
        pages = max(0, int(float(features.get("pages") or 0)))
        large_images = max(0, int(float(features.get("large_image_count") or 0)))
    except (TypeError, ValueError):
        pages = large_images = 0
    if pages <= 0:
        return False
    if pages and large_images >= math.ceil(pages * 0.8):
        return False
    return True


def mixed_g1_reliable(text: str, object_features: dict | None, fonts: str) -> bool:
    """Allow only dense mixed-PDF native text to support a G1 contradiction."""
    features = object_features or {}
    try:
        pages = max(0, int(float(features.get("pages") or 0)))
        large_images = max(0, int(float(features.get("large_image_count") or 0)))
        font_objects = max(0, int(float(features.get("font_object_count") or 0)))
    except (TypeError, ValueError):
        return False
    if not pages:
        return False
    image_backed = large_images >= math.ceil(pages * 0.8)
    if not image_backed or font_objects < max(12, 3 * pages):
        return False
    embedded_unicode_fonts = sum(bool(re.search(r"\byes\s+yes\s+yes\b", line, re.I)) for line in fonts.splitlines())
    if embedded_unicode_fonts < 3:
        return False
    page_parts = re.split(r"\f", text)[:pages]
    nonempty = [compact_text(page) for page in page_parts if len(compact_text(page)) >= 100]
    if not nonempty or len(set(nonempty)) / len(nonempty) <= 0.5:
        return False
    strict_number = re.compile(r"(?:报告编号|报告号码|NO\.?)\s*[:：]?\s*([0-9 ]{14,28})", re.I)
    strict_time = re.compile(r"报告\s*(?:时间|日期)\s*[:：]?\s*((?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}(?:日)?[T\s]*(?:[01]?\d|2[0-3])[:：][0-5]\d[:：][0-5]\d)")
    return any(
        len(compact_text(page)) >= 500 and strict_number.search(normalize_text(page)) and strict_time.search(normalize_text(page))
        for page in page_parts
    )


def rule(
    rule_id: str,
    title: str,
    status: str,
    message: str,
    word_level: str = "提示造假",
    mode: str = "automatic",
    *,
    expected: str = "",
    observed: str = "",
    source_hint: str = "",
    review_action: str = "",
) -> Rule:
    return Rule(
        rule_id,
        title,
        status,
        message,
        word_level,
        mode,
        expected,
        observed,
        source_hint,
        review_action,
    )


def common_rules(text: str, embedded: bool, g1_reliable: bool | None = None) -> list[Rule]:
    rules: list[Rule] = []
    number = report_number(text)
    report_time = report_datetime(text)
    report_time_exact = report_datetime(text, require_seconds=True)
    reliable = embedded
    g1_reliable = reliable if g1_reliable is None else g1_reliable
    if number and len(number) >= 14 and report_time_exact:
        expected_number_time = report_time_exact.strftime("%Y%m%d%H%M%S")
        evidence = {
            "expected": f"编号前14位应为 {expected_number_time}",
            "observed": f"编号前14位为 {number[:14]}；报告时间为 {report_time_exact:%Y-%m-%d %H:%M:%S}",
            "source_hint": "报告首页的报告编号与报告时间",
            "review_action": "对照报告首页原文确认编号和时间",
        }
        if number[:14] == expected_number_time:
            rules.append(rule("G1", "报告编号与报告时间一致", "pass", "报告编号前14位与报告时间精确到秒一致", **evidence))
        else:
            status = "fail" if g1_reliable else "manual"
            rules.append(rule(
                "G1",
                "报告编号与报告时间一致",
                status,
                "编号时间与报告时间不一致" if g1_reliable else "读取结果显示编号时间可能不一致，需查看原件",
                mode="automatic" if g1_reliable else "ocr_review",
                **evidence,
            ))
    else:
        missing = "报告编号" if not number or len(number) < 14 else "含秒的完整报告时间"
        rules.append(rule(
            "G1",
            "报告编号与报告时间一致",
            "manual",
            f"{missing}未被稳定识别，需查看原件",
            mode="ocr_review",
            expected="报告编号前14位与报告时间（精确到秒）一致",
            observed=f"缺少可稳定比对的{missing}",
            source_hint="报告首页的报告编号与报告时间",
            review_action="查看报告首页并补录完整编号与时间",
        ))

    if not report_time:
        rules.append(rule(
            "G2",
            "报告内日期不得晚于报告时间",
            "manual",
            "报告日期未被稳定识别，无法执行日期逻辑校验",
            mode="manual",
            expected="历史事实日期不得晚于报告日期",
            observed="报告日期缺失或无法稳定读取",
            source_hint="报告首页及全文日期字段",
            review_action="补录报告日期后重新核对全文历史日期",
        ))
    else:
        comparison_time = report_time_exact or report_time.replace(hour=23, minute=59, second=59)
        historical_later: set[str] = set()
        unknown_later: set[str] = set()
        forward_later: set[str] = set()
        for line in normalize_text(text).splitlines():
            for match in DATE_RE.finditer(line):
                value = parse_date_match(match)
                if not value or value <= comparison_time:
                    continue
                kind = date_field_kind(line, match.start())
                marker = value.isoformat(timespec="seconds")
                if kind == "forward":
                    forward_later.add(marker)
                elif kind == "historical":
                    historical_later.add(marker)
                else:
                    unknown_later.add(marker)
        if historical_later and reliable:
            rules.append(rule(
                "G2",
                "报告内日期不得晚于报告时间",
                "fail",
                f"发现{len(historical_later)}个历史事实日期晚于报告时间",
                expected="历史事实日期不得晚于报告时间",
                observed=f"发现 {len(historical_later)} 个不符合时间先后关系的历史日期",
                source_hint="报告全文日期字段",
                review_action="定位对应历史日期并与报告时间逐项复核",
            ))
        elif historical_later or unknown_later:
            unresolved_count = len(historical_later | unknown_later)
            rules.append(rule(
                "G2",
                "报告内日期不得晚于报告时间",
                "manual",
                f"发现{unresolved_count}个晚于报告时间的日期，字段含义需结合原件确认",
                mode="manual",
                expected="历史事实日期不得晚于报告时间；到期、截止等前瞻日期不计异常",
                observed=f"发现 {unresolved_count} 个日期需确认字段含义",
                source_hint="报告全文日期字段",
                review_action="区分历史事实日期与到期、截止类前瞻日期",
            ))
        else:
            suffix = f"；另有{len(forward_later)}个到期或截止类前瞻日期不计异常" if forward_later else ""
            rules.append(rule(
                "G2",
                "报告内日期不得晚于报告时间",
                "pass",
                "未发现历史事实日期晚于报告时间" + suffix,
                expected="历史事实日期不得晚于报告时间",
                observed="未发现明确违反时间先后关系的历史日期",
                source_hint="报告全文日期字段",
            ))

    rules.append(rule(
        "G3",
        "水印位置与完整性一致",
        "manual",
        "需逐页对照同渠道、同版本报告确认水印位置和完整性",
        mode="visual_template",
        expected="同一份报告每页水印位置一致且水印完整",
        observed="当前材料缺少可直接定性的同版本逐页对照结果",
        source_hint="报告每一页的征信中心水印",
        review_action="逐页核对水印位置、方向和完整性",
    ))
    rules.append(rule(
        "G4",
        "字体、字号与行间距符合对应版本",
        "manual",
        "需对照同渠道、同版本报告确认版式一致性",
        mode="layout_model",
        expected="字体、字号和行间距与对应报告版本一致",
        observed="当前材料缺少同版本版式对照结论",
        source_hint="报告全文版式",
        review_action="使用同渠道、同版本基准样本进行版式核对",
    ))
    return rules


def original_pdf_rules(
    path: Path | None,
    text: str,
    info: dict[str, str],
    fonts: str,
    embedded: bool,
    metadata_readable: bool = True,
    fonts_readable: bool = True,
) -> list[Rule]:
    titles = {
        "P1A": "PDF创建时间与修改时间一致",
        "P1B": "PDF制作者符合原始版要求",
        "P1C": "PDF版本为1.4",
        "P1D": "PDF字体资源符合原始版要求",
        "P2": "报告时间不晚于PDF创建时间",
    }
    if not embedded:
        return [rule(
            key,
            title,
            "not_applicable",
            "扫描或图片版不适用原始电子PDF属性规则",
            "有造假可能" if key.startswith("P1") else "提示造假",
            expected="仅对直接下载或导出的原始电子版报告核验",
            observed="当前材料为扫描或图片版",
            source_hint="文件形态与PDF文档属性",
        ) for key, title in titles.items()]
    creation = parse_pdfinfo_date(info.get("CreationDate", ""))
    modified = parse_pdfinfo_date(info.get("ModDate", ""))
    rules: list[Rule] = []
    if not metadata_readable:
        for key in ("P1A", "P1B", "P1C"):
            rules.append(rule(
                key,
                titles[key],
                "manual",
                "PDF文档属性未能稳定读取",
                "有造假可能",
                "manual",
                expected="读取原始电子版PDF文档属性并与规则要求对照",
                observed="文档属性读取失败",
                source_hint="PDF文档属性",
                review_action="使用PDF阅读器查看文档属性后人工核对",
            ))
    else:
        same_timestamp = bool(creation and modified and creation == modified)
        rules.append(rule(
            "P1A",
            titles["P1A"],
            "pass" if same_timestamp else "possible",
            "创建时间与修改时间一致" if same_timestamp else "创建时间与修改时间不一致或属性缺失",
            "有造假可能",
            expected="创建时间与修改时间一致",
            observed=f"创建时间：{creation.isoformat(sep=' ') if creation else '未提供'}；修改时间：{modified.isoformat(sep=' ') if modified else '未提供'}",
            source_hint="PDF文档属性-说明",
            review_action="核对文件是否为直接下载或导出的原始电子版",
        ))
        producer = info.get("Producer", "").strip()
        producer_ok = producer == "iText 2.1.7 by 1T3XT"
        rules.append(rule(
            "P1B",
            titles["P1B"],
            "pass" if producer_ok else "possible",
            "PDF制作者符合原始版要求" if producer_ok else "PDF制作者与原始版要求不一致或属性缺失",
            "有造假可能",
            expected="PDF制作者为 iText 2.1.7 by 1T3XT",
            observed=f"PDF制作者：{producer or '未提供'}",
            source_hint="PDF文档属性-说明",
            review_action="确认材料是否经过另存、打印或二次编辑",
        ))
        version = info.get("PDF version", "").strip()
        version_ok = version == "1.4"
        rules.append(rule(
            "P1C",
            titles["P1C"],
            "pass" if version_ok else "possible",
            "PDF版本为1.4" if version_ok else "PDF版本不是1.4或属性缺失",
            "有造假可能",
            expected="PDF版本为 1.4",
            observed=f"PDF版本：{version or '未提供'}",
            source_hint="PDF文档属性-说明",
            review_action="确认材料是否为原始电子版",
        ))
    font_rows = [
        line.strip()
        for line in fonts.splitlines()
        if line.strip()
        and not line.lower().lstrip().startswith("name")
        and not set(line.strip()) <= {"-", " "}
    ]
    normalized_font_names = {
        re.sub(r"^[A-Z]{6}\+", "", line.split()[0], flags=re.I).lower()
        for line in font_rows
        if line.split()
    }
    allowed_font_names = {"helvetica", "sourcehanserifcn-regular"}
    helvetica_ok = any(
        re.sub(r"^[A-Z]{6}\+", "", line.split()[0], flags=re.I).lower() == "helvetica"
        and re.search(r"\bType\s+1\s+WinAnsi\s+no\s+no\s+no\b", line, re.I)
        for line in font_rows
    )
    source_han_ok = any(
        re.sub(r"^[A-Z]{6}\+", "", line.split()[0], flags=re.I).lower() == "sourcehanserifcn-regular"
        and re.search(r"\bCID\s+TrueType\s+Identity-H\s+yes\s+yes\s+yes\b", line, re.I)
        for line in font_rows
    )
    fonts_ok = helvetica_ok and source_han_ok and normalized_font_names == allowed_font_names
    if not fonts_readable:
        rules.append(rule(
            "P1D",
            titles["P1D"],
            "manual",
            "PDF字体属性未能稳定读取",
            "有造假可能",
            "manual",
            expected="字体资源符合原始电子版要求",
            observed="字体属性读取失败",
            source_hint="PDF文档属性-字体",
            review_action="使用PDF阅读器查看字体属性后人工核对",
        ))
    else:
        observed_fonts = sorted(normalized_font_names)
        rules.append(rule(
            "P1D",
            titles["P1D"],
            "pass" if fonts_ok else "possible",
            "字体资源符合原始版要求" if fonts_ok else "字体资源与原始版要求不一致",
            "有造假可能",
            expected="包含规则图示中的 Helvetica 与 SourceHanSerifCN-Regular 字体属性",
            observed="已识别字体：" + ("、".join(observed_fonts) if observed_fonts else "未识别到要求字体"),
            source_hint="PDF文档属性-字体",
            review_action="确认文件是否经过转换、另存或二次编辑",
        ))
    report_time = report_datetime(text, require_seconds=True)
    if not metadata_readable or not report_time or not creation:
        rules.append(rule(
            "P2",
            titles["P2"],
            "manual",
            "报告时间或PDF创建时间无法稳定解析",
            mode="manual",
            expected="报告时间不得晚于PDF创建时间",
            observed=f"报告时间：{report_time.isoformat(sep=' ') if report_time else '未完整识别'}；创建时间：{creation.isoformat(sep=' ') if creation else '未提供'}",
            source_hint="报告首页与PDF文档属性-说明",
            review_action="补充完整报告时间和PDF创建时间后重新比对",
        ))
    elif report_time <= creation and creation - report_time <= timedelta(days=1):
        delta_seconds = int((creation - report_time).total_seconds())
        rules.append(rule(
            "P2",
            titles["P2"],
            "pass",
            "报告时间早于或等于PDF创建时间",
            expected="报告时间不得晚于PDF创建时间",
            observed=f"报告时间：{report_time.isoformat(sep=' ')}；创建时间：{creation.isoformat(sep=' ')}；相差 {delta_seconds} 秒",
            source_hint="报告首页与PDF文档属性-说明",
        ))
    elif report_time < creation:
        delta = creation - report_time
        rules.append(rule(
            "P2",
            titles["P2"],
            "manual",
            "报告时间早于PDF创建时间超过1天，是否属于正常导出时差需进一步确认",
            mode="manual",
            expected="报告时间与PDF创建时间一致或仅略早于创建时间",
            observed=f"报告时间：{report_time.isoformat(sep=' ')}；创建时间：{creation.isoformat(sep=' ')}；相差 {delta}",
            source_hint="报告首页与PDF文档属性-说明",
            review_action="核对文件导出流程、时区及是否经过延后生成",
        ))
    else:
        rules.append(rule(
            "P2",
            titles["P2"],
            "fail",
            "报告时间晚于PDF创建时间",
            expected="报告时间不得晚于PDF创建时间",
            observed=f"报告时间：{report_time.isoformat(sep=' ')}；创建时间：{creation.isoformat(sep=' ')}",
            source_hint="报告首页与PDF文档属性-说明",
            review_action="核对报告是否经过重新生成或修改",
        ))
    return rules


def personal_identity_section(text: str) -> str:
    pages = [page for page in re.split(r"\f+", normalize_text(text)) if page.strip()]
    for page in pages[:6]:
        lines = page.splitlines()
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if "个人基本信息" in compact_text(line)
            ),
            None,
        )
        if start is None:
            continue
        scoped = []
        for offset, line in enumerate(lines[start:start + 100]):
            compact = compact_text(line)
            if offset > 1 and any(
                heading in compact
                for heading in ("信息概要", "配偶信息", "共同借款人信息", "其他人员信息")
            ):
                break
            scoped.append(line)
        return "\n".join(scoped)
    return ""


def personal_identity_values(section: str) -> tuple[set[str], set[str]]:
    if not section:
        return set(), set()
    lines = section.splitlines()
    id_values: set[str] = set()
    birth_values: set[str] = set()
    field_boundary = r"(?<![A-Za-z\u3400-\u9fff])"
    id_label = re.compile(field_boundary + r"(?:证件号码|身份证号码)\s*[:：]?\s*(.*)$")
    birth_label = re.compile(field_boundary + r"出生(?:日期|年月日)\s*[:：]?\s*(.*)$")
    date_value = re.compile(r"((?:19|20)\d{2})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})")

    def candidate_text(index: int, tail: str) -> str:
        if tail.strip():
            return tail
        for following in lines[index + 1:index + 4]:
            if following.strip():
                return following
        return ""

    for index, line in enumerate(lines):
        id_match = id_label.search(line)
        if id_match:
            candidate = re.sub(r"\s+", "", candidate_text(index, id_match.group(1)))
            id_values.update(ID_RE.findall(candidate))
        birth_match = birth_label.search(line)
        if birth_match:
            candidate = candidate_text(index, birth_match.group(1))
            match = date_value.search(candidate)
            if match:
                birth_values.add(
                    f"{int(match.group(1)):04d}{int(match.group(2)):02d}{int(match.group(3)):02d}"
                )
    return id_values, birth_values


def personal_rules(text: str, variant: str, embedded: bool) -> list[Rule]:
    rules: list[Rule] = []
    is_online = variant in {"online_personal", "scanned_online_personal"}
    is_print = variant == "pboc_print_personal"
    if is_online:
        if not embedded:
            rules.append(rule(
                "A1",
                "网银个人征信须取得原始电子版",
                "manual",
                "当前为扫描或图片版，需补充原始电子版",
                "材料前提",
                "manual",
                expected="网银查询版个人征信应取得原始电子版",
                observed="当前材料为扫描或图片版",
                source_hint="文件形态",
                review_action="向材料提供方补充索取原始电子版",
            ))
        compact = compact_text(text)
        has_summary = "信息概要" in compact and "发生过逾期的账户数" in compact
        has_detail = "发生过逾期" in compact and "账户明细如下" in compact
        if has_summary and has_detail and embedded:
            rules.append(rule(
                "O1",
                "逾期账户概要数与明细数一致",
                "manual",
                "已定位概要与逾期明细，需按账户编号完成逐项勾稽",
                mode="table_parser",
                expected="信息概要中的逾期账户数与明细所列账户笔数一致",
                observed="概要与明细均已定位，但表格笔数尚需逐项确认",
                source_hint="信息概要及逾期账户明细",
                review_action="按账户编号去重后核对概要数与明细笔数",
            ))
        else:
            rules.append(rule(
                "O1",
                "逾期账户概要数与明细数一致",
                "manual",
                "概要或逾期明细未被完整读取",
                mode="manual",
                expected="信息概要中的逾期账户数与明细所列账户笔数一致",
                observed="缺少可完整勾稽的概要或明细数据",
                source_hint="信息概要及逾期账户明细",
                review_action="查看原件并补录概要数、账户编号及明细笔数",
            ))
    else:
        rules.append(rule(
            "O1",
            "逾期账户概要数与明细数一致",
            "not_applicable",
            "仅适用于网银查询版个人征信",
            expected="仅对网银查询版个人征信执行",
            observed="当前报告不属于该版本",
        ))

    print_titles = {
        "H1": "证件号码与出生日期一致",
        "H2": "违约信息概要表不得为空白",
        "H3": "违约概要与明细还款记录一致",
        "H4": "异常还款代码须有对应违约记录",
        "H5": "概要账户数与信贷明细账户数一致",
        "H6": "授信及负债概要金额与明细一致",
    }
    if not is_print:
        rules.extend(rule(key, title, "not_applicable", "仅适用于人行打印版个人征信") for key, title in print_titles.items())
        return rules
    rules.append(rule(
        "A2",
        "人行打印版建议取得纸质原件",
        "manual",
        "数字文件无法确认是否为人行直接打印的纸质原件",
        "材料前提",
        "manual",
        expected="建议取得人民银行直接打印的纸质原件",
        observed="当前仅有数字文件",
        source_hint="材料载体",
        review_action="核对纸质原件或由材料提供方确认原件来源",
    ))
    ids, births = personal_identity_values(personal_identity_section(text))
    if len(ids) == len(births) == 1 and embedded:
        identity = next(iter(ids))
        id_birth = identity[6:14] if len(identity) == 18 else "19" + identity[6:12]
        shown = next(iter(births))
        rules.append(rule(
            "H1",
            print_titles["H1"],
            "pass" if id_birth == shown else "fail",
            "证件号码推导生日与报告出生日期一致" if id_birth == shown else "证件号码推导生日与报告出生日期不一致",
            expected="证件号码中的出生日期与报告所列出生日期一致",
            observed="两处出生日期一致" if id_birth == shown else "两处出生日期不一致（具体值已隐藏）",
            source_hint="个人基本信息中的证件号码与出生日期",
            review_action="查看原件核对证件号码及出生日期" if id_birth != shown else "",
        ))
    else:
        rules.append(rule(
            "H1",
            print_titles["H1"],
            "manual",
            "证件号码或出生日期未被完整稳定读取",
            mode="ocr_review",
            expected="证件号码中的出生日期与报告所列出生日期一致",
            observed="缺少可稳定比对的完整字段",
            source_hint="个人基本信息中的证件号码与出生日期",
            review_action="查看原件并人工核对，客户页面不展示证件号码原值",
        ))
    compact = compact_text(text)
    if "信贷交易违约信息概要" not in compact:
        rules.append(rule(
            "H2",
            print_titles["H2"],
            "manual",
            "尚无法确认违约信息概要表是否存在",
            "有造假可能",
            "manual",
            expected="若出现信贷交易违约信息概要表，表内不得全部为空白",
            observed="缺少可证明该表存在或不存在的完整章节证据",
            source_hint="信息概要",
            review_action="查看原件确认是否存在该表；如存在，核对表内数据是否为空白",
        ))
    else:
        rules.append(rule(
            "H2",
            print_titles["H2"],
            "manual",
            "已发现违约信息概要表，需核对表内数据单元格是否全部为空白",
            "有造假可能",
            "table_parser",
            expected="出现该概要表时，表内应包含对应历史违约数据",
            observed="已定位概要表，表内数据完整性待确认",
            source_hint="信贷交易违约信息概要表",
            review_action="查看原件确认概要表是否存在全空白数据区",
        ))
    table_requirements = {
        "H3": ("违约概要与明细还款记录一致", "违约信息概要表与明细还款记录"),
        "H4": ("异常还款代码均有对应违约记录", "明细还款记录与违约信息概要表"),
        "H5": ("信息概要中的贷款及信用卡账户数与明细账户数一致", "信息概要与信贷交易信息明细"),
        "H6": ("授信及负债概要金额与未结清、未销户明细一致", "授信及负债信息概要与信贷交易信息明细"),
    }
    for key in ("H3", "H4", "H5", "H6"):
        expected, source_hint = table_requirements[key]
        rules.append(rule(
            key,
            print_titles[key],
            "manual",
            "需结合完整表格和跨页明细完成勾稽",
            mode="table_parser",
            expected=expected,
            observed="当前结果缺少可直接定性的完整跨页表格勾稽数据",
            source_hint=source_hint,
            review_action="按账户编号去重并逐项核对概要、明细和还款记录",
        ))
    return rules


def enterprise_identity_sections(text: str) -> dict[str, str]:
    """Return the cover and first page with a standalone identity heading."""
    pages = [page for page in re.split(r"\f+", text) if page.strip()]
    if not pages:
        return {"cover": text[:16000], "identity": ""}
    identity = ""
    for page in pages[1:8]:
        lines = page.splitlines()
        heading = next((index for index, line in enumerate(lines[:60]) if compact_text(line).strip(":：|｜") == "身份标识"), None)
        if heading is None:
            continue
        scoped, nonempty = [], 0
        for line in lines[heading:]:
            scoped.append(line)
            if line.strip():
                nonempty += 1
            if nonempty >= 30:
                break
        identity = "\n".join(scoped)
        break
    return {"cover": pages[0], "identity": identity}


def labeled_identity_values(text: str, label: str, value_kind: str) -> list[str]:
    """Extract subject values immediately adjacent to an exact identity label.

    Keeping line boundaries is important: compacting the whole report can join
    an empty ``中征码`` field to a loan account many lines later.
    """
    lines = normalize_text(text).splitlines()
    label_pattern = re.compile(rf"^\s*{re.escape(label)}\s*:?[ \t]*(.*)$", re.I)
    other_labels = ("企业名称", "中征码", "统一社会信用代码", "组织机构代码", "查询机构", "报告时间")
    values: list[str] = []

    def normalize_candidate(candidate: str) -> str:
        candidate = re.split("|".join(re.escape(item) for item in other_labels), candidate, maxsplit=1)[0]
        candidate = re.sub(r"\s+", "", candidate).strip(":：|｜")
        if value_kind == "name":
            if not (2 <= len(candidate) <= 80 and re.search(r"[\u3400-\u9fff]", candidate)):
                return ""
            if candidate in {"信息概要", "身份标识", "企业信用报告"}:
                return ""
            return candidate.upper()
        if value_kind == "zhongzheng":
            return candidate if re.fullmatch(r"\d{16}", candidate) else ""
        if value_kind == "social_credit":
            candidate = candidate.upper()
            return candidate if re.fullmatch(r"[0-9A-Z]{18}", candidate) else ""
        return ""

    for index, line in enumerate(lines):
        match = label_pattern.match(line)
        if not match:
            continue
        candidate = normalize_candidate(match.group(1))
        if not candidate:
            # Inspect only the first following non-empty line. If it is another
            # label/header, the value is missing and must remain MANUAL.
            for following in lines[index + 1:index + 5]:
                following = following.strip()
                if not following:
                    continue
                if any(re.match(rf"^\s*{re.escape(item)}\s*:?[ \t]*", following, re.I) for item in other_labels):
                    break
                candidate = normalize_candidate(following)
                break
        if candidate:
            values.append(candidate)
    return values


def enterprise_identity_rule(text: str, embedded: bool) -> Rule:
    # Word asks for report-wide consistency of the subject identity fields.
    # Only cover/identity-page labels count; loan accounts and counterparties do
    # not. A FAIL requires two high-confidence observations that contradict.
    sections = enterprise_identity_sections(text)
    kinds = {"企业名称": "name", "中征码": "zhongzheng", "统一社会信用代码": "social_credit"}
    observations = {
        label: {
            section: set(labeled_identity_values(section_text, label, kind))
            for section, section_text in sections.items()
        }
        for label, kind in kinds.items()
    }
    ambiguous = [
        label for label, values in observations.items()
        if any(len(section_values) > 1 for section_values in values.values())
    ]
    if embedded and ambiguous:
        return rule(
            "C2",
            "报告前后企业名称及代码一致",
            "manual",
            f"{','.join(ambiguous)}在同一章节出现多个候选值，需查看原件",
            mode="ocr_review",
            expected="封面与身份标识页的企业名称、中征码和统一社会信用代码一致",
            observed=f"{','.join(ambiguous)}存在多个候选值（具体值已隐藏）",
            source_hint="报告封面与身份标识页",
            review_action="查看原件确认对应字段的唯一规范值",
        )
    conflicts = [
        label for label, values in observations.items()
        if len(values["cover"]) == len(values["identity"]) == 1 and values["cover"] != values["identity"]
    ]
    if embedded and conflicts:
        return rule(
            "C2",
            "报告前后企业名称及代码一致",
            "fail",
            f"{','.join(conflicts)}在封面与身份标识页出现不同规范值",
            expected="封面与身份标识页的企业名称、中征码和统一社会信用代码一致",
            observed=f"不一致字段：{','.join(conflicts)}（具体值已隐藏）",
            source_hint="报告封面与身份标识页",
            review_action="逐项核对封面与身份标识页对应字段",
        )
    complete = embedded and all(
        len(values["cover"]) == len(values["identity"]) == 1 and values["cover"] == values["identity"]
        for values in observations.values()
    )
    if complete:
        return rule(
            "C2",
            "报告前后企业名称及代码一致",
            "pass",
            "企业名称、中征码和统一社会信用代码在封面与身份标识页一致",
            expected="封面与身份标识页的企业名称、中征码和统一社会信用代码一致",
            observed="三项主体标识字段前后一致（具体值已隐藏）",
            source_hint="报告封面与身份标识页",
        )
    return rule(
        "C2",
        "报告前后企业名称及代码一致",
        "manual",
        "企业名称或企业代码缺少可前后对照的稳定读取值",
        mode="ocr_review",
        expected="封面与身份标识页的企业名称、中征码和统一社会信用代码一致",
        observed="至少一项主体标识字段缺少可稳定比对的前后值",
        source_hint="报告封面与身份标识页",
        review_action="查看原件并补录缺失字段后重新比对",
    )


def enterprise_rules(text: str, variant: str, embedded: bool) -> list[Rule]:
    titles = {
        "C1": "法人工商信息与权威数据一致",
        "C2": "报告前后企业名称及代码一致",
        "C3A": "被追偿业务概要与明细及附件一致",
        "C3B": "公共记录概要条数与明细一致",
        "C3C": "未结清关注及不良类账户与余额一致",
        "C3D": "已结清关注及不良类账户与余额一致",
    }
    if "enterprise" not in variant:
        return [rule(key, title, "not_applicable", "仅适用于法人征信") for key, title in titles.items()]
    rules = []
    if not embedded:
        rules.append(rule(
            "A3",
            "法人征信建议取得原始电子版",
            "manual",
            "当前为扫描或图片版，需补充原始电子版",
            "材料前提",
            "manual",
            expected="法人征信尽量取得网银导出的原始电子版",
            observed="当前材料为扫描或图片版",
            source_hint="文件形态",
            review_action="向材料提供方补充索取原始电子版",
        ))
    rules.append(rule(
        "C1",
        titles["C1"],
        "manual",
        "需结合报告时点的权威工商历史信息核验",
        mode="external_registry",
        expected="报告所列法人工商信息与报告时点的权威登记信息一致",
        observed="当前批次未附报告时点的权威工商历史数据",
        source_hint="报告企业基本信息与权威工商登记信息",
        review_action="按报告时间查询权威工商历史档案并逐项核对",
    ))
    rules.append(enterprise_identity_rule(text, embedded))
    requirements = {
        "C3A": (
            "被追偿业务概要与信贷明细余额、附件历史表现一致",
            "信息概要、信贷记录明细及附件1被追偿业务历史表现",
        ),
        "C3B": (
            "信息概要中的公共记录条数与公共记录明细条数一致",
            "信息概要与公共记录明细",
        ),
        "C3C": (
            "未结清关注及不良类账户数和余额与明细、附件历史表现一致；适用的未结清银行承兑汇票和信用证按同一口径核对",
            "未结清信贷及授信信息概要、信贷记录明细与附件1",
        ),
        "C3D": (
            "已结清关注及不良类账户数和余额与明细、附件历史表现一致",
            "已结清信贷信息概要、信贷记录明细与附件1",
        ),
    }
    for key in ("C3A", "C3B", "C3C", "C3D"):
        expected, source_hint = requirements[key]
        rules.append(rule(
            key,
            titles[key],
            "manual",
            "需结合完整章节表格、账户去重和附件历史状态完成勾稽",
            mode="table_parser",
            expected=expected,
            observed="当前结果缺少可直接定性的完整跨页表格勾稽数据",
            source_hint=source_hint,
            review_action="按账户编号及业务类型逐项核对概要、明细和附件",
        ))
    return rules


def status_counts(rules: list[Rule]) -> dict[str, int]:
    counts = Counter(item.status for item in rules)
    return {
        key: counts.get(key, 0)
        for key in ("fail", "possible", "manual", "pass", "not_applicable")
    }


def summarize_status(rules: list[Rule]) -> tuple[str, dict[str, int]]:
    """Summarize only the 22 numbered Word rules.

    A0-A3 represent source-document prerequisites copied from Word notes. They
    remain useful review guidance, but must not inflate the advertised 22-rule
    count or determine the report conclusion.
    """
    numbered_rules = [item for item in rules if item.rule_id in WORD_RULE_IDS]
    counts = Counter(item.status for item in numbered_rules)
    if counts["fail"]:
        overall = "fail"
    elif counts["possible"]:
        overall = "possible"
    elif counts["manual"]:
        overall = "manual"
    else:
        overall = "pass"
    return overall, status_counts(numbered_rules)


def validate_rule_set(rules: list[Rule]) -> None:
    ids = [item.rule_id for item in rules]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    missing = sorted(set(WORD_RULE_IDS) - set(ids))
    unexpected = sorted(set(ids) - set(WORD_RULE_IDS) - SUPPORT_RULE_IDS)
    if duplicates or missing or unexpected:
        raise ValueError(
            f"invalid Word rule mapping: missing={missing}, duplicates={duplicates}, unexpected={unexpected}"
        )


def analyze(
    row: dict,
    data_root: Path,
    ocr_by_id: dict[str, dict],
    object_by_id: dict[str, dict] | None = None,
    ocr_text_root: Path | None = None,
) -> dict:
    path = Path(row["path"])
    if not path.is_absolute():
        path = data_root / path
    source_id = row["document_id"]
    is_pdf = path.suffix.lower() == ".pdf"
    info, metadata_readable = pdf_info(path) if is_pdf else ({}, False)
    fonts, fonts_readable = pdf_fonts(path) if is_pdf else ("", False)
    embedded_text, text_readable = pdf_text(path) if is_pdf else ("", True)
    analysis_warnings = []
    if not text_readable:
        embedded_text = ""
        analysis_warnings.append("pdftotext_unavailable")
    embedded_chars = len(re.sub(r"\s+", "", embedded_text))
    has_native_text = embedded_chars >= 300
    object_features = (object_by_id or {}).get(source_id)
    embedded = source_is_original(has_native_text, object_features)
    extracted_ocr_text = ocr_text(ocr_by_id.get(source_id) or {}, ocr_text_root)
    analysis_text = embedded_text if embedded else (extracted_ocr_text or embedded_text)
    variant = report_variant(analysis_text, embedded)
    reliable_text = embedded and has_native_text
    g1_reliable = reliable_text or mixed_g1_reliable(embedded_text, object_features, fonts)
    common_text = embedded_text if g1_reliable else analysis_text
    rules = common_rules(common_text, reliable_text, g1_reliable)
    rules.extend(original_pdf_rules(path, analysis_text, info, fonts, embedded, metadata_readable, fonts_readable))
    rules.extend(personal_rules(analysis_text, variant, reliable_text))
    rules.extend(enterprise_rules(analysis_text, variant, reliable_text))
    if variant == "unknown":
        rules.append(rule("A0", "报告版本分类", "manual", "报告版本需结合原件确认，专属规则暂不作自动定性", "材料前提", "manual"))
    validate_rule_set(rules)
    overall, counts = summarize_status(rules)
    support_counts = status_counts(
        [item for item in rules if item.rule_id in SUPPORT_RULE_IDS]
    )
    return {
        "source_id": source_id,
        "source_format": "original_electronic" if embedded else "scanned_or_image",
        "report_variant": variant,
        "overall_status": overall,
        "rule_counts": counts,
        "support_counts": support_counts,
        "rule_results": [item.as_dict() for item in rules],
        "analysis_warnings": analysis_warnings,
        "analysis_complete": True,
        "has_pdf": is_pdf and path.is_file(),
    }


def _main_impl() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined-csv", type=Path, required=True)
    parser.add_argument("--ocr-csv", type=Path, required=True)
    parser.add_argument("--ocr-text-dir", type=Path)
    parser.add_argument("--object-csv", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-synthetic", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    rows = [row for row in read_csv(args.combined_csv) if row.get("doc_type") == "credit_report"]
    if not args.include_synthetic:
        rows = [row for row in rows if not Path(row.get("path", "")).name.startswith("synthetic_fake_")]
    if args.limit:
        rows = rows[:args.limit]
    ocr_by_id = {row["document_id"]: row for row in read_csv(args.ocr_csv)}
    ocr_text_root = args.ocr_text_dir or args.ocr_csv.with_name(args.ocr_csv.stem + "_text")
    object_path = args.object_csv or args.combined_csv.with_name("pdf_object_features.csv")
    pdf_rows = [
        row
        for row in rows
        if str(row.get("ext") or Path(row.get("path", "")).suffix).lower() == ".pdf"
    ]
    if pdf_rows and not object_path.is_file():
        raise FileNotFoundError(f"PDF object feature file is required: {object_path}")
    object_by_id = {row["document_id"]: row for row in read_csv(object_path)} if object_path.is_file() else {}
    missing_object_ids = [
        row.get("document_id", "")
        for row in pdf_rows
        if row.get("document_id", "") not in object_by_id
    ]
    if missing_object_ids:
        raise ValueError(f"PDF object features missing for {len(missing_object_ids)} credit reports")
    results = []
    for index, row in enumerate(rows, 1):
        try:
            results.append(analyze(row, args.data_root.resolve(), ocr_by_id, object_by_id, ocr_text_root))
        except Exception as exc:
            results.append({
                "source_id": row.get("document_id", ""), "source_format": "unknown", "report_variant": "unknown",
                "overall_status": "manual", "rule_counts": {"fail": 0, "possible": 0, "manual": len(WORD_RULE_IDS), "pass": 0, "not_applicable": 0},
                "support_counts": {"fail": 0, "possible": 0, "manual": 0, "pass": 0, "not_applicable": 0},
                "rule_results": [rule("SYS", "规则分析完整性", "manual", f"分析未完成：{type(exc).__name__}", mode="system").as_dict()],
                "analysis_complete": False,
                "has_pdf": False,
            })
        source_ref = hashlib.sha256(
            str(row.get("document_id") or index).encode("utf-8")
        ).hexdigest()[:12]
        print(
            f"[{index}/{len(rows)}] {source_ref} -> {results[-1]['overall_status']}",
            flush=True,
        )
    payload = {
        "schema_version": "credit-word-rules-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_document": "征信报告造假识别(1).docx",
        "word_rule_count": len(WORD_RULE_IDS),
        "synthetic_excluded": not args.include_synthetic,
        "document_count": len(results),
        "documents": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        os.chmod(args.output, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(f"wrote {len(results)} results to {args.output}")


def main() -> None:
    previous_umask = os.umask(0o077)
    try:
        _main_impl()
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
