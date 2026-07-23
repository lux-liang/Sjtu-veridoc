#!/usr/bin/env python3
from __future__ import annotations
import re
from datetime import date
from dataclasses import dataclass, field
from typing import Any


AMOUNT_RE = re.compile(r"(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"((?:19|20)[0-9]{2})[-/.年](0?[1-9]|1[0-2])[-/.月](3[01]|[12][0-9]|0?[1-9])日?")
ID_RE = re.compile(r"\b[0-9]{17}[0-9Xx]\b|\b[0-9]{15}\b")
ACCOUNT_RE = re.compile(r"\b[0-9]{12,24}\b")
TAX_ID_RE = re.compile(r"\b[0-9A-Z]{15,20}\b")
INVOICE_NO_RE = re.compile(r"\b(?:INV|FP|FAPIAO|INVOICE)?[-_A-Z0-9]{6,24}\b", re.I)
CN_AMOUNT_RE = re.compile(r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿元整角分]+")


@dataclass
class BusinessRuleFinding:
    rule: str
    severity: int
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def risk_level(score: float) -> str:
    if score >= 60:
        return "high"
    if score >= 25:
        return "medium"
    if score >= 15:
        return "low"
    return "clean"


def parse_amounts(text: str) -> list[float]:
    values = []
    for raw in AMOUNT_RE.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if 0 <= value < 10**12:
            values.append(value)
    return values


def normalize_dates(text: str) -> list[str]:
    dates = []
    for year, month, day in DATE_RE.findall(text or ""):
        dates.append(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return dates


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _credit_report_fields(text: str, fields: dict[str, Any]) -> None:
    """Extract the stable, text-visible credit-report fields used by the rules.

    This deliberately does not infer a result from missing OCR text: a scan may
    be perfectly legitimate.  Rules which need page pixels, fonts, or an
    external enterprise registry are exposed as review items by the UI/service.
    """
    report_number = re.search(r"(?:报告编号|报告号码|Report\s*(?:No\.?|Number))\s*[:：#]?\s*([A-Za-z0-9]{14,})", text, re.I)
    report_date = re.search(
        r"(?:报告时间|报告日期|查询时间|Report\s*Date)\s*[:：]?\s*(20[0-9]{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:3[01]|[12][0-9]|0?[1-9])日?)",
        text,
        re.I,
    )
    birth_date = re.search(r"(?:出生日期|出生年月日|Birth\s*Date)\s*[:：]?\s*(?:20[0-9]{2}|19[0-9]{2})[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:3[01]|[12][0-9]|0?[1-9])日?", text, re.I)
    fields["credit_report_number"] = report_number.group(1) if report_number else ""
    fields["credit_report_date"] = normalize_dates(report_date.group(1))[:1] if report_date else []
    fields["birth_date"] = normalize_dates(birth_date.group(0))[:1] if birth_date else []


def _id_birth_date(id_number: str) -> str | None:
    if re.fullmatch(r"\d{17}[\dXx]", id_number or ""):
        raw = id_number[6:14]
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if re.fullmatch(r"\d{15}", id_number or ""):
        raw = id_number[6:12]
        return f"19{raw[:2]}-{raw[2:4]}-{raw[4:6]}"
    return None


def extract_fields(text: str, base_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = dict(base_fields or {})
    fields.setdefault("amounts", parse_amounts(text))
    fields.setdefault("dates", normalize_dates(text))
    fields.setdefault("id_numbers", ID_RE.findall(text or ""))
    fields.setdefault("account_numbers", ACCOUNT_RE.findall(text or ""))
    fields.setdefault("tax_ids", TAX_ID_RE.findall(text or ""))
    fields.setdefault("invoice_numbers", INVOICE_NO_RE.findall(text or "")[:10])
    fields.setdefault("chinese_amounts", CN_AMOUNT_RE.findall(text or "")[:10])
    _credit_report_fields(text or "", fields)
    return fields


class BusinessLogicDetector:
    """Rule-based business consistency detector for common material types."""

    def detect(self, doc_type: str, text: str = "", fields: dict[str, Any] | None = None) -> dict[str, Any]:
        doc_type = (doc_type or "other").strip()
        extracted = extract_fields(text, fields)
        findings: list[BusinessRuleFinding] = []
        if doc_type == "invoice":
            findings.extend(self._invoice_rules(extracted))
        elif doc_type == "contract":
            findings.extend(self._contract_rules(text, extracted))
        elif doc_type == "bank_page":
            findings.extend(self._bank_rules(extracted))
        elif doc_type == "credit_report":
            findings.extend(self._credit_report_rules(extracted))
        elif doc_type in {"settlement_statement", "receipt"}:
            findings.extend(self._settlement_rules(extracted))
        else:
            findings.extend(self._generic_rules(extracted))
        score = min(100.0, sum(item.severity for item in findings))
        return {
            "detector": "business_logic",
            "score": round(score, 2),
            "level": risk_level(score),
            "findings": [item.__dict__ for item in findings],
            "fields": extracted,
        }

    def _invoice_rules(self, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        amounts = fields.get("amounts", [])
        if len(amounts) >= 3:
            a, b, c = amounts[:3]
            combos_ok = any(abs((x + y) - z) <= max(0.1, z * 0.002) for x, y, z in [(a, b, c), (a, c, b), (b, c, a)])
            if not combos_ok:
                findings.append(BusinessRuleFinding("invoice_amount_tax_total_mismatch", 25, "发票金额、税额、价税合计之间不满足加和关系", {"amounts": amounts[:6]}))
        else:
            findings.append(BusinessRuleFinding("invoice_amount_fields_sparse", 8, "发票金额字段不足，无法校验价税合计", {"amount_count": len(amounts)}))
        if not fields.get("invoice_numbers"):
            findings.append(BusinessRuleFinding("invoice_number_missing", 8, "未识别到稳定的发票号码候选", {}))
        if not fields.get("tax_ids"):
            findings.append(BusinessRuleFinding("invoice_tax_id_missing", 6, "未识别到纳税人识别号候选", {}))
        return findings

    def _contract_rules(self, text: str, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        if len(fields.get("amounts", [])) < 1:
            findings.append(BusinessRuleFinding("contract_amount_missing", 8, "合同未识别到金额字段", {}))
        if not fields.get("dates"):
            findings.append(BusinessRuleFinding("contract_date_missing", 8, "合同未识别到签署/生效日期", {}))
        if "甲方" not in text and "Party A" not in text:
            findings.append(BusinessRuleFinding("contract_party_a_missing", 6, "合同未识别到甲方字段", {}))
        if "乙方" not in text and "Party B" not in text:
            findings.append(BusinessRuleFinding("contract_party_b_missing", 6, "合同未识别到乙方字段", {}))
        if fields.get("amounts") and fields.get("chinese_amounts") and len(fields["chinese_amounts"][0]) <= 2:
            findings.append(BusinessRuleFinding("contract_uppercase_amount_weak", 6, "大写金额候选过短，建议复核大小写金额一致性", {"chinese_amounts": fields["chinese_amounts"][:3]}))
        return findings

    def _bank_rules(self, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        if not fields.get("account_numbers"):
            findings.append(BusinessRuleFinding("bank_account_number_missing", 8, "银行流水未识别到账户号码候选", {}))
        if len(fields.get("amounts", [])) < 3:
            findings.append(BusinessRuleFinding("bank_amount_sequence_sparse", 8, "银行流水金额序列过少，无法做余额递推", {"amount_count": len(fields.get("amounts", []))}))
        if not fields.get("dates"):
            findings.append(BusinessRuleFinding("bank_date_missing", 6, "银行流水未识别到交易日期", {}))
        return findings

    def _credit_report_rules(self, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        report_number = str(fields.get("credit_report_number") or "")
        report_dates = fields.get("credit_report_date") or []
        report_date = report_dates[0] if report_dates else None
        # Rule 1: the first 14 digits of the report number encode YYYYMMDDHHMMSS.
        if report_number and report_date and re.fullmatch(r"\d{14}.*", report_number):
            number_day = f"{report_number[:4]}-{report_number[4:6]}-{report_number[6:8]}"
            if number_day != report_date:
                findings.append(BusinessRuleFinding("credit_report_number_date_mismatch", 35, "报告编号前 14 位中的日期与报告时间不一致", {"report_number": report_number[:14], "report_date": report_date}))
        # Rule 2: no document date may be later than its report date.
        if report_date:
            end = _parse_iso_date(report_date)
            later = [item for item in fields.get("dates", []) if _parse_iso_date(item) and end and _parse_iso_date(item) > end]
            if later:
                findings.append(BusinessRuleFinding("credit_report_future_date", 35, "报告中存在晚于报告时间的日期", {"report_date": report_date, "later_dates": later[:5]}))
        # 人行打印版规则：身份证出生日期与页面出生日期必须一致。
        birth_dates = fields.get("birth_date") or []
        id_births = [value for value in (_id_birth_date(item) for item in fields.get("id_numbers", [])) if value]
        if birth_dates and id_births and birth_dates[0] not in id_births:
            findings.append(BusinessRuleFinding("credit_report_id_birth_date_mismatch", 35, "证件号码推导的出生日期与报告载明出生日期不一致", {"id_birth_dates": id_births, "reported_birth_date": birth_dates[0]}))
        # 原始 PDF 规则：报告时间不得晚于文件创建时间（由调用方传入元数据）。
        creation = fields.get("pdf_creation_date")
        if report_date and creation:
            creation_date = normalize_dates(str(creation))
            if creation_date and _parse_iso_date(report_date) and _parse_iso_date(report_date) > _parse_iso_date(creation_date[0]):
                findings.append(BusinessRuleFinding("credit_report_report_after_pdf_creation", 35, "报告时间晚于 PDF 文件创建时间", {"report_date": report_date, "pdf_creation_date": creation_date[0]}))
        return findings

    def _settlement_rules(self, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        if not fields.get("amounts"):
            findings.append(BusinessRuleFinding("settlement_amount_missing", 10, "结算单/回单未识别到金额字段", {}))
        if not fields.get("account_numbers"):
            findings.append(BusinessRuleFinding("settlement_account_missing", 8, "结算单/回单未识别到账户字段", {}))
        if not fields.get("dates"):
            findings.append(BusinessRuleFinding("settlement_date_missing", 6, "结算单/回单未识别到交易日期", {}))
        return findings

    def _generic_rules(self, fields: dict[str, Any]) -> list[BusinessRuleFinding]:
        findings = []
        if not fields.get("amounts") and not fields.get("dates") and not fields.get("account_numbers"):
            findings.append(BusinessRuleFinding("generic_core_fields_missing", 8, "未识别到金额、日期或账号等核心字段", {}))
        return findings
