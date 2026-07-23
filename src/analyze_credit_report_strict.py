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
import json
import math
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATUS = {"pass", "fail", "possible", "manual", "not_applicable"}
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

    def as_dict(self) -> dict:
        if self.status not in STATUS:
            raise ValueError(f"invalid rule status: {self.status}")
        return self.__dict__.copy()


def run(command: list[str], timeout: int = 90) -> str:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=timeout, check=False)
    return process.stdout if process.returncode == 0 else ""


def read_csv(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(text.splitlines()))


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


def pdf_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in run(["pdfinfo", str(path)]).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info


def pdf_fonts(path: Path) -> str:
    return run(["pdffonts", str(path)])


def pdf_text(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="credit_text_") as temp_dir:
        target = Path(temp_dir) / "report.txt"
        subprocess.run(["pdftotext", "-layout", str(path), str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        return target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""


def report_datetime(text: str) -> datetime | None:
    normalized = normalize_text(text)
    patterns = [
        re.compile(r"报告\s*时\s*间\s*:?\s*" + DATE_RE.pattern),
        re.compile(r"报告\s*日\s*期\s*:?\s*" + DATE_RE.pattern),
    ]
    for pattern in patterns:
        match = pattern.search(normalized)
        if match:
            # DATE_RE has six capture groups; prefix has none.
            return parse_date_match(match)
    return None


def report_number(text: str) -> str:
    normalized = normalize_text(text)
    match = REPORT_NUMBER_RE.search(normalized)
    if not match:
        # OCR often inserts spaces between the four Chinese characters.
        match = re.search(r"报\s*告\s*编\s*号\s*:?\s*([0-9OIl\s]{14,28})", normalized, re.I)
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
    if not has_native_text:
        return False
    features = object_features or {}
    try:
        pages = max(0, int(float(features.get("pages") or 0)))
        large_images = max(0, int(float(features.get("large_image_count") or 0)))
    except (TypeError, ValueError):
        pages = large_images = 0
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


def rule(rule_id: str, title: str, status: str, message: str, word_level: str = "提示造假", mode: str = "automatic") -> Rule:
    return Rule(rule_id, title, status, message, word_level, mode)


def common_rules(text: str, embedded: bool, g1_reliable: bool | None = None) -> list[Rule]:
    rules: list[Rule] = []
    number = report_number(text)
    report_time = report_datetime(text)
    reliable = embedded
    g1_reliable = reliable if g1_reliable is None else g1_reliable
    if number and len(number) >= 14 and report_time:
        expected = report_time.strftime("%Y%m%d%H%M%S")
        if number[:14] == expected:
            rules.append(rule("G1", "报告编号与报告时间一致", "pass", "报告编号前14位与报告时间精确到秒一致"))
        else:
            status = "fail" if g1_reliable else "manual"
            rules.append(rule("G1", "报告编号与报告时间一致", status, "编号时间与报告时间不一致" if g1_reliable else "文字提取显示编号时间可能不一致，需查看原件", mode="automatic" if g1_reliable else "ocr_review"))
    else:
        rules.append(rule("G1", "报告编号与报告时间一致", "manual", "报告编号或报告时间未被稳定识别，需查看原件", mode="ocr_review"))

    if not report_time:
        rules.append(rule("G2", "报告内日期不得晚于报告时间", "manual", "报告时间未被稳定识别，无法执行日期逻辑校验", mode="manual"))
    else:
        historical_later, unknown_later, forward_later = [], [], []
        for line in normalize_text(text).splitlines():
            for match in DATE_RE.finditer(line):
                value = parse_date_match(match)
                if not value or value <= report_time:
                    continue
                context = line.strip()[:160]
                kind = date_field_kind(line, match.start())
                if kind == "forward":
                    forward_later.append(context)
                elif kind == "historical":
                    historical_later.append(context)
                else:
                    unknown_later.append(context)
        if historical_later and reliable:
            rules.append(rule("G2", "报告内日期不得晚于报告时间", "fail", f"发现{len(historical_later)}处历史事实日期晚于报告时间"))
        elif historical_later or unknown_later:
            rules.append(rule("G2", "报告内日期不得晚于报告时间", "manual", f"发现{len(historical_later) + len(unknown_later)}处未来日期，字段语义或OCR需人工确认", mode="manual"))
        else:
            suffix = f"；另有{len(forward_later)}处到期/截止类前瞻日期不计异常" if forward_later else ""
            rules.append(rule("G2", "报告内日期不得晚于报告时间", "pass", "未发现历史事实日期晚于报告时间" + suffix))

    rules.append(rule("G3", "水印位置与完整性一致", "manual", "需结合对应渠道和版本的逐页水印基准确认", mode="visual_template"))
    rules.append(rule("G4", "字体、字号与行间距符合对应版本", "manual", "需结合对应报告版本的版式基准确认", mode="layout_model"))
    return rules


def original_pdf_rules(path: Path, text: str, info: dict[str, str], fonts: str, embedded: bool) -> list[Rule]:
    titles = {
        "P1A": "PDF创建时间与修改时间一致",
        "P1B": "PDF制作者符合原始版要求",
        "P1C": "PDF版本为1.4",
        "P1D": "PDF字体资源符合原始版要求",
        "P2": "报告时间不晚于PDF创建时间",
    }
    if not embedded:
        return [rule(key, title, "not_applicable", "扫描或图片版不适用原始电子PDF属性规则", "有造假可能" if key.startswith("P1") else "提示造假") for key, title in titles.items()]
    creation = parse_pdfinfo_date(info.get("CreationDate", ""))
    modified = parse_pdfinfo_date(info.get("ModDate", ""))
    rules = []
    rules.append(rule("P1A", titles["P1A"], "pass" if creation and modified and creation == modified else "possible", "创建时间与修改时间一致" if creation and modified and creation == modified else "创建时间与修改时间不一致或缺失", "有造假可能"))
    producer_ok = info.get("Producer", "").strip() == "iText 2.1.7 by 1T3XT"
    rules.append(rule("P1B", titles["P1B"], "pass" if producer_ok else "possible", "PDF制作者符合Word基线" if producer_ok else "PDF制作者与Word基线不一致", "有造假可能"))
    version_ok = info.get("PDF version", "").strip() == "1.4"
    rules.append(rule("P1C", titles["P1C"], "pass" if version_ok else "possible", "PDF版本为1.4" if version_ok else "PDF版本不是1.4或无法识别", "有造假可能"))
    font_lower = fonts.lower()
    helvetica_ok = "helvetica" in font_lower and "type 1" in font_lower and ("winansi" in font_lower or "ansi" in font_lower)
    source_han_ok = "sourcehanserifcn-regular" in font_lower and "identity-h" in font_lower
    fonts_ok = helvetica_ok and source_han_ok
    rules.append(rule("P1D", titles["P1D"], "pass" if fonts_ok else "possible", "字体资源符合Helvetica与SourceHanSerifCN基线" if fonts_ok else "字体资源与Word图示基线不一致", "有造假可能"))
    report_time = report_datetime(text)
    if not report_time or not creation:
        rules.append(rule("P2", titles["P2"], "manual", "报告时间或PDF创建时间无法稳定解析", mode="manual"))
    elif report_time <= creation:
        rules.append(rule("P2", titles["P2"], "pass", "报告时间早于或等于PDF创建时间"))
    else:
        rules.append(rule("P2", titles["P2"], "fail", "报告时间晚于PDF创建时间"))
    return rules


def personal_rules(text: str, variant: str, embedded: bool) -> list[Rule]:
    rules: list[Rule] = []
    is_online = variant in {"online_personal", "scanned_online_personal"}
    is_print = variant == "pboc_print_personal"
    if is_online:
        if not embedded:
            rules.append(rule("A1", "网银个人征信须取得原始电子版", "manual", "当前为扫描/图片版，Word要求取得原始电子版", "材料前提", "manual"))
        compact = compact_text(text)
        has_summary = "信息概要" in compact and "发生过逾期的账户数" in compact
        has_detail = "发生过逾期" in compact and "账户明细如下" in compact
        if has_summary and has_detail and embedded:
            rules.append(rule("O1", "逾期账户概要数与明细数一致", "manual", "需按账户编号去重并核对概要与明细", mode="table_parser"))
        else:
            rules.append(rule("O1", "逾期账户概要数与明细数一致", "manual", "概要或逾期明细未被完整稳定提取", mode="manual"))
    else:
        rules.append(rule("O1", "逾期账户概要数与明细数一致", "not_applicable", "仅适用于网银查询版个人征信"))

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
    rules.append(rule("A2", "人行打印版建议取得纸质原件", "manual", "数字文件无法确认是否为人行直接打印纸质原件", "材料前提", "manual"))
    ids = ID_RE.findall(compact_text(text))
    birth_matches = re.findall(r"出生(?:日期|年月日)?[:：]?((?:19|20)\d{2})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})", compact_text(text))
    if ids and birth_matches and embedded:
        id_birth = ids[0][6:14] if len(ids[0]) == 18 else "19" + ids[0][6:12]
        shown = f"{int(birth_matches[0][0]):04d}{int(birth_matches[0][1]):02d}{int(birth_matches[0][2]):02d}"
        rules.append(rule("H1", print_titles["H1"], "pass" if id_birth == shown else "fail", "证件号码推导生日与报告出生日期一致" if id_birth == shown else "证件号码推导生日与报告出生日期不一致"))
    else:
        rules.append(rule("H1", print_titles["H1"], "manual", "证件号码或出生日期未被高置信度完整识别", mode="ocr_review"))
    compact = compact_text(text)
    if "信贷交易违约信息概要" not in compact:
        rules.append(rule("H2", print_titles["H2"], "pass", "未发现信贷交易违约信息概要表"))
    else:
        rules.append(rule("H2", print_titles["H2"], "manual", "已发现违约概要表，需核对数据单元格是否为空白", "有造假可能", "table_parser"))
    for key in ("H3", "H4", "H5", "H6"):
        rules.append(rule(key, print_titles[key], "manual", "需完整表格结构、账户去重及跨页明细重建", mode="table_parser"))
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
        return rule("C2", "报告前后企业名称及代码一致", "manual", f"{','.join(ambiguous)}在同一章节出现多个候选值，需查看原件", mode="ocr_review")
    conflicts = [
        label for label, values in observations.items()
        if len(values["cover"]) == len(values["identity"]) == 1 and values["cover"] != values["identity"]
    ]
    if embedded and conflicts:
        return rule("C2", "报告前后企业名称及代码一致", "fail", f"{','.join(conflicts)}在封面与身份标识页出现不同规范值")
    complete = embedded and all(
        len(values["cover"]) == len(values["identity"]) == 1 and values["cover"] == values["identity"]
        for values in observations.values()
    )
    if complete:
        return rule("C2", "报告前后企业名称及代码一致", "pass", "企业名称、中征码和统一社会信用代码在封面与身份标识页一致")
    return rule("C2", "报告前后企业名称及代码一致", "manual", "企业名称或企业代码缺少可前后对照的高置信度值", mode="ocr_review")


def enterprise_rules(text: str, variant: str, embedded: bool) -> list[Rule]:
    titles = {
        "C1": "法人工商信息与权威数据一致",
        "C2": "报告前后企业名称及代码一致",
        "C3A": "被追偿业务概要与明细及附件一致",
        "C3B": "公共记录概要条数与明细一致",
        "C3C": "未结清关注及不良类账户与余额一致",
        "C3D": "已结清关注及不良类账户与余额一致",
        "C3E": "承兑汇票及信用证分类与金额一致",
    }
    if "enterprise" not in variant:
        return [rule(key, title, "not_applicable", "仅适用于法人征信") for key, title in titles.items()]
    rules = []
    if not embedded:
        rules.append(rule("A3", "法人征信建议取得原始电子版", "manual", "当前为扫描/图片版，无法确认原始导出属性", "材料前提", "manual"))
    rules.append(rule("C1", titles["C1"], "manual", "需结合报告时点的权威工商历史信息核验", mode="external_registry"))
    rules.append(enterprise_identity_rule(text, embedded))
    for key in ("C3A", "C3B", "C3C", "C3D", "C3E"):
        rules.append(rule(key, titles[key], "manual", "需完整章节表格、账户去重和附件历史状态重建", mode="table_parser"))
    return rules


def summarize_status(rules: list[Rule]) -> tuple[str, dict[str, int]]:
    counts = Counter(item.status for item in rules)
    if counts["fail"]:
        overall = "fail"
    elif counts["possible"]:
        overall = "possible"
    elif counts["manual"]:
        overall = "manual"
    else:
        overall = "pass"
    return overall, {key: counts.get(key, 0) for key in ("fail", "possible", "manual", "pass", "not_applicable")}


def analyze(row: dict, data_root: Path, ocr_by_id: dict[str, dict], object_by_id: dict[str, dict] | None = None) -> dict:
    path = Path(row["path"])
    if not path.is_absolute():
        path = data_root / path
    source_id = row["document_id"]
    info = pdf_info(path)
    fonts = pdf_fonts(path)
    embedded_text = pdf_text(path)
    embedded_chars = len(re.sub(r"\s+", "", embedded_text))
    has_native_text = embedded_chars >= 300
    object_features = (object_by_id or {}).get(source_id)
    embedded = source_is_original(has_native_text, object_features)
    ocr_preview = (ocr_by_id.get(source_id) or {}).get("ocr_text_preview", "")
    analysis_text = embedded_text if has_native_text else ocr_preview
    variant = report_variant(analysis_text, embedded)
    reliable_text = embedded and has_native_text
    g1_reliable = reliable_text or mixed_g1_reliable(embedded_text, object_features, fonts)
    rules = common_rules(analysis_text, reliable_text, g1_reliable)
    rules.extend(original_pdf_rules(path, analysis_text, info, fonts, embedded))
    rules.extend(personal_rules(analysis_text, variant, reliable_text))
    rules.extend(enterprise_rules(analysis_text, variant, reliable_text))
    if variant == "unknown":
        rules.append(rule("A0", "报告版本分类", "manual", "报告版本需结合原件确认，专属规则暂不作自动定性", "材料前提", "manual"))
    overall, counts = summarize_status(rules)
    return {
        "source_id": source_id,
        "source_format": "original_electronic" if embedded else "scanned_or_image",
        "report_variant": variant,
        "overall_status": overall,
        "rule_counts": counts,
        "rule_results": [item.as_dict() for item in rules],
        "has_pdf": path.is_file(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined-csv", type=Path, required=True)
    parser.add_argument("--ocr-csv", type=Path, required=True)
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
    object_path = args.object_csv or args.combined_csv.with_name("pdf_object_features.csv")
    object_by_id = {row["document_id"]: row for row in read_csv(object_path)} if object_path.is_file() else {}
    results = []
    for index, row in enumerate(rows, 1):
        try:
            results.append(analyze(row, args.data_root.resolve(), ocr_by_id, object_by_id))
        except Exception as exc:
            results.append({
                "source_id": row.get("document_id", ""), "source_format": "unknown", "report_variant": "unknown",
                "overall_status": "manual", "rule_counts": {"fail": 0, "possible": 0, "manual": 1, "pass": 0, "not_applicable": 0},
                "rule_results": [rule("SYS", "规则分析完整性", "manual", f"分析未完成：{type(exc).__name__}", mode="system").as_dict()], "has_pdf": False,
            })
        print(f"[{index}/{len(rows)}] {row.get('document_id')} -> {results[-1]['overall_status']}", flush=True)
    payload = {
        "schema_version": "credit-word-rules-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_document": "征信报告造假识别(1).docx",
        "synthetic_excluded": not args.include_synthetic,
        "document_count": len(results),
        "documents": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(f"wrote {len(results)} results to {args.output}")


if __name__ == "__main__":
    main()
