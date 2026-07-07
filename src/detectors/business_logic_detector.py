#!/usr/bin/env python3
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any


AMOUNT_RE = re.compile(r"(?:CNY|RMB|¥)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
DATE_RE = re.compile(r"(20[0-9]{2})[-/.年](0?[1-9]|1[0-2])[-/.月](0?[1-9]|[12][0-9]|3[01])日?")
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


def extract_fields(text: str, base_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = dict(base_fields or {})
    fields.setdefault("amounts", parse_amounts(text))
    fields.setdefault("dates", normalize_dates(text))
    fields.setdefault("id_numbers", ID_RE.findall(text or ""))
    fields.setdefault("account_numbers", ACCOUNT_RE.findall(text or ""))
    fields.setdefault("tax_ids", TAX_ID_RE.findall(text or ""))
    fields.setdefault("invoice_numbers", INVOICE_NO_RE.findall(text or "")[:10])
    fields.setdefault("chinese_amounts", CN_AMOUNT_RE.findall(text or "")[:10])
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
        if not fields.get("id_numbers"):
            findings.append(BusinessRuleFinding("credit_report_id_missing", 8, "征信报告未识别到身份证号候选", {}))
        if not fields.get("dates"):
            findings.append(BusinessRuleFinding("credit_report_date_missing", 8, "征信报告未识别到报告日期", {}))
        if len(fields.get("amounts", [])) < 3:
            findings.append(BusinessRuleFinding("credit_report_numeric_summary_sparse", 5, "征信报告数值字段偏少，建议复核 OCR 质量", {"amount_count": len(fields.get("amounts", []))}))
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
