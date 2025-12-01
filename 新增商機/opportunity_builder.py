#!/usr/bin/env python3
"""Parse sales text and derive opportunity payload hints."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LABEL_MAP: Dict[str, str] = {
    "商機名稱": "opportunityName",
    "商机名称": "opportunityName",
    "客戶名稱": "customerName",
    "客户名称": "customerName",
    "客戶": "customerLine",
    "客户": "customerLine",
    "聯絡地址": "address",
    "联系地址": "address",
    "聯繫電話": "contactPhone",
    "联系电话": "contactPhone",
    "安裝位置": "installLocation",
    "安装位置": "installLocation",
    "安裝位置（客戶地址）": "installLocation",
    "安装位置（客户地址）": "installLocation",
    "安裝位置（客戶地址 ）": "installLocation",
    "安装位置（客户地址 ）": "installLocation",
    "商機階段": "opportunityStage",
    "商机阶段": "opportunityStage",
    "交易類型": "transactionType",
    "交易类型": "transactionType",
    "客戶分類": "customerCategory",
    "客户分类": "customerCategory",
    "客戶分類（家用客戶 商業客戶 政府專案）": "customerCategory",
    "客户分类（家用客户 商业客户 政府专案）": "customerCategory",
    "商機日期": "opportunityDate",
    "商机日期": "opportunityDate",
    "負責人": "ownerHint",
    "负责人": "ownerHint",
    "銷售": "ownerHint",  # 改為 ownerHint，讓負責人邏輯生效
    "销售": "ownerHint",  # 改為 ownerHint，讓負責人邏輯生效
    "预计签单日期": "expectSignDate",
    "預計簽單日期": "expectSignDate",
    "预计签单金额": "expectSignMoney",
    "預計簽單金額": "expectSignMoney",
    "预计签单数量": "expectSignNum",
    "預計簽單數量": "expectSignNum",
    "預計簽單數": "expectSignNum",
    "币种": "currency",
    "幣種": "currency",
    "幣別": "currency",
    "目前付款方式": "paymentMethod",
    "目前付費方式": "paymentMethod",
    "目前付款方式（01-07）": "paymentMethod",
    "付款方式": "paymentMethod",
    "合約1開始日": "contractStartDate",
    "合约1开始日": "contractStartDate",
    "合約開始日": "contractStartDate",
    "合约开始日": "contractStartDate",
    "合同開始日": "contractStartDate",
    "合同开始日": "contractStartDate",
    "合約1結束日期": "contractEndDate",
    "合约1结束日期": "contractEndDate",
    "合約結束日": "contractEndDate",
    "合约结束日": "contractEndDate",
    "合同結束日": "contractEndDate",
    "合同结束日": "contractEndDate",
    "合約1年期": "contractYears",
    "合约1年期": "contractYears",
    "合約年期": "contractYears",
    "合约年期": "contractYears",
    "使用方式": "usageMode",
    "方案類型": "planType",
    "方案类型": "planType",
    "方案名稱": "planType",
    "方案名称": "planType",
    "方案类型（方案類型）": "planType",
    "方案类型（方案类型）": "planType",
    "月費金額": "monthlyFee",
    "月费金额": "monthlyFee",
    "按金": "deposit",
    "押金": "deposit",
    "預繳金": "prepay",
    "预缴金": "prepay",
    "總金額": "totalAmount",
    "总金额": "totalAmount",
    "合約號": "contractNumber",
    "合约号": "contractNumber",
    "合同號": "contractNumber",
    "合同号": "contractNumber",
    "常用聯絡方式": "contactMethod",
    "常用联系方式": "contactMethod",
    "備注": "remark",
    "备注": "remark",
    "贏單率": "winningRate",
    "赢单率": "winningRate",
    "安裝時間": "installTime",
    "安装时间": "installTime",
    "商機來源": "opportunitySource",
    "商机来源": "opportunitySource",
    "品牌": "brandName",
    "品牌名稱": "brandName",
    "品牌名称": "brandName",
    "產品名稱": "productName",
    "产品名称": "productName",
    "產品分類": "productClassName",
    "产品分类": "productClassName",
    "產品線": "productLineName",
    "产品线": "productLineName",
    "方案編號": "planCode",
    "方案编号": "planCode",
}

STANDARD_DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")
CJK_DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})[日號号]?")
MONTH_DAY_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日")
TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})")
CODE_TOKEN_RE = re.compile(r"\bC\d{3,}\b", re.IGNORECASE)
PLAN_CODE_RE = re.compile(r"([A-Z]{1,3}\d{2,4})")
PHONE_RE = re.compile(r"\d{6,}")


def _strip(value: Optional[str]) -> str:
    return value.strip() if isinstance(value, str) else ""


_PLACEHOLDER_TOKENS = {
    "--",
    "—",
    "-",
    "暫無",
    "暂无",
    "無",
    "无",
    "n/a",
    "n\\a",
    "na",
    "N/A",
    "N\\A",
    "NA",
}


def _normalize_placeholder(value: Optional[str]) -> str:
    """把 '--'、'暫無' 等占位符視為空字串，方便後續使用預設值。"""
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    if not clean:
        return ""
    if clean.lower() in _PLACEHOLDER_TOKENS:
        return ""
    return clean


def _normalize_label(label: str) -> str:
    clean = (
        label.replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
        .strip()
    )
    if not clean:
        return label
    return clean


def _parse_lines(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    lines = text.splitlines()
    
    # 處理標籤和值在不同行的情況
    i = 0
    while i < len(lines):
        line = _strip(lines[i])
        if not line:
            i += 1
            continue
            
        # 檢查是否是標籤（在LABEL_MAP中）
        if line in LABEL_MAP:
            # 檢查下一行是否是值
            if i + 1 < len(lines):
                next_line = _strip(lines[i + 1])
                # 如果下一行不是標籤，則認為是值
                if next_line and next_line not in LABEL_MAP:
                    parsed[LABEL_MAP[line]] = _normalize_placeholder(next_line)
                    i += 2
                    continue
        
        # 處理標準的標籤:值格式
        if (":" in line) or ("：" in line):
            parts = re.split(r"[:：]", line, maxsplit=1)
            if len(parts) == 2:
                label = _normalize_label(parts[0])
                value = _normalize_placeholder(_strip(parts[1]))
                key = LABEL_MAP.get(label)
                if key:
                    parsed[key] = value
        
        i += 1
    
    return parsed


def _parse_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    value = text.strip()
    if not value:
        return None
    match = STANDARD_DATE_RE.search(value)
    if match:
        year, month, day = match.groups()
        return date(int(year), int(month), int(day))
    match = CJK_DATE_RE.search(value)
    if match:
        year, month, day = match.groups()
        return date(int(year), int(month), int(day))
    match = MONTH_DAY_RE.search(value)
    if match:
        month, day = match.groups()
        year = datetime.now().year
        return date(year, int(month), int(day))
    digits = re.findall(r"\d{4}\d{2}\d{2}", value)
    if digits:
        token = digits[0]
        return date(int(token[0:4]), int(token[4:6]), int(token[6:8]))
    return None


def _parse_number(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    clean = text.strip()
    if not clean:
        return None
    equals_match = re.search(r"=\s*([0-9,]+)", clean)
    if equals_match:
        try:
            return float(equals_match.group(1).replace(",", ""))
        except ValueError:
            pass
    multiply_match = re.search(r"([0-9,.]+)\s*\*\s*([0-9,.]+)", clean)
    if multiply_match:
        try:
            left = float(multiply_match.group(1).replace(",", ""))
            right = float(multiply_match.group(2).replace(",", ""))
            return left * right
        except ValueError:
            pass
    normalized = re.sub(r"[^0-9.\-]", "", clean)
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _parse_int(text: Optional[str]) -> Optional[int]:
    number = _parse_number(text)
    if number is None:
        return None
    try:
        return int(number)
    except ValueError:
        return None


def _parse_contract_years(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digit = re.search(r"\d+", text)
    if digit:
        return int(digit.group(0))
    mapping = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5}
    for han, value in mapping.items():
        if han in text:
            return value
    return None


def _normalize_currency(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    if "澳" in text or "mop" in lowered:
        return "MOP"
    if "hkd" in lowered or "港" in text:
        return "HKD"
    return text.strip()


def _normalize_payment_code(text: Optional[str]) -> Optional[str]:
    """
    提取付款方式代碼。
    如果有多個選項（用 / 或 、 分隔），優先取第一個。
    """
    if not text:
        return None
    clean = _normalize_placeholder(text)
    if not clean:
        return None
    
    # 如果有多個選項（用斜杠或中文頓號分隔），取第一個
    # 例如："信用卡分期/一次性全繳/季度收費" -> "信用卡分期"
    if "/" in clean or "、" in clean:
        parts = re.split(r"[/、]", clean)
        if parts:
            # 取第一個非空部分
            first_option = next((p.strip() for p in parts if p.strip()), None)
            if first_option:
                clean = first_option
    
    # 嘗試提取兩位數字代碼（如 01, 02, 03 等）
    digits = re.search(r"\d{2}", clean)
    if digits:
        return digits.group(0)
    
    # 根據關鍵詞映射
    payment_keywords = {
        "信用卡分期": "02",
        "一次性全繳": "01",
        "一次性": "01",
        "全繳": "01",
        "季度收費": "04",
        "季度": "04",
        "年度收費": "05",
        "年度": "05",
        "試用": "06",
        "每月收費": "07",
        "月費": "07",
        "銀行卡自動轉賬": "03",
        "自動轉賬": "03",
        "自動扣款": "03",
    }
    
    for keyword, code in payment_keywords.items():
        if keyword in clean:
            return code
    
    return clean


def _normalize_percentage(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match:
        return match.group(0)
    return text.strip()


def _date_to_string(value: Optional[date]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d")


def _add_years(base: date, years: int) -> date:
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        return base.replace(month=2, day=28, year=base.year + years)


def _extract_phone(value: str) -> Optional[str]:
    matches = PHONE_RE.findall(value)
    for token in matches:
        if len(token) >= 6:
            return token
    return None


def _extract_customer_code(*values: str) -> Optional[str]:
    for value in values:
        if not value:
            continue
        match = CODE_TOKEN_RE.search(value)
        if match:
            return match.group(0).upper()
    return None


def _extract_plan_code(text: str) -> Optional[str]:
    match = PLAN_CODE_RE.search(text)
    if match:
        return match.group(1)
    return None


def _combine_text(*parts: Optional[str]) -> str:
    filtered = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return "\n".join(dict.fromkeys(filtered))  # preserve order


def _normalize_customer(source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not source:
        return {}
    if "normalized" in source:
        normalized = source.get("normalized")
        if isinstance(normalized, dict):
            return normalized
    return source


def _build_context(fields: Dict[str, str], customer: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    name = fields.get("opportunityName") or customer.get("displayName") or customer.get("shortName") or "新商機"
    
    # 修復安裝位置問題 - 確保安裝位置不會被客戶名稱覆蓋
    # 優先使用用戶輸入的安裝位置，其次使用方案類型（如果方案類型看起來像地址），最後使用客戶地址
    install_location = fields.get("installLocation") or ""
    
    # 如果安裝位置是客戶編碼+姓名（如 "C45641澳門張學友66777629"），優先換成客戶地址
    if install_location and re.search(r"C\d+", install_location):
        if customer.get("address"):
            install_location = customer.get("address")
        else:
            plan_type_raw = fields.get("planType", "")
            if plan_type_raw and any(keyword in plan_type_raw for keyword in ["座", "樓", "大廈", "廣場", "中心", "花園", "苑", "邨", "街", "路"]):
                install_location = plan_type_raw
            else:
                install_location = install_location
    elif not install_location:
        # 如果沒有安裝位置，優先用使用者提供的聯絡地址，其次方案類型看起來像地址，再來客戶地址
        if fields.get("address"):
            install_location = fields.get("address")
        else:
            plan_type = fields.get("planType", "")
            if plan_type and any(keyword in plan_type for keyword in ["座", "樓", "大廈", "廣場", "中心", "花園", "苑", "邨", "街", "路"]):
                install_location = plan_type
            else:
                install_location = customer.get("address") or ""
    
    # 方案類型處理 - 如果方案類型看起來像地址，則嘗試從其他字段獲取真實的方案類型
    plan_type = fields.get("planType") or customer.get("installContent") or ""
    if plan_type and any(keyword in plan_type for keyword in ["座", "樓", "大廈", "廣場", "中心", "花園", "苑", "邨", "街", "路"]):
        # 如果方案類型看起來像地址，則嘗試從其他字段獲取真實的方案類型
        # 如果沒有其他信息，則使用默認值
        plan_type = "MAQUA方案"
    
    usage_label = (
        fields.get("usageMode")
        or customer.get("usageLabel")
        or (customer.get("usageMode") or {}).get("label")
        or ""
    )
    monthly_fee = _parse_number(fields.get("monthlyFee"))
    if monthly_fee is None:
        monthly_fee = customer.get("monthlyFee")
    deposit = _parse_number(fields.get("deposit"))
    if deposit is None:
        deposit = customer.get("deposit")
    prepay = _parse_number(fields.get("prepay"))
    if prepay is None:
        prepay = customer.get("prepay")
    total_amount = _parse_number(fields.get("totalAmount"))
    if total_amount is None:
        total_amount = customer.get("totalAmount")
    contract_years = _parse_contract_years(fields.get("contractYears"))
    install_time_text = fields.get("installTime") or (customer.get("installTime") or {}).get("display")
    contract_start = _parse_date(fields.get("contractStartDate") or install_time_text)
    contract_end = _parse_date(fields.get("contractEndDate"))
    expect_sign_date = _parse_date(fields.get("expectSignDate"))
    expect_sign_money = _parse_number(fields.get("expectSignMoney")) or total_amount
    if expect_sign_money is None and monthly_fee and contract_years:
        expect_sign_money = monthly_fee * contract_years * 12
    expect_sign_num = _parse_int(fields.get("expectSignNum"))
    currency = _normalize_currency(fields.get("currency")) or customer.get("currency") or "MOP"
    payment_code = _normalize_payment_code(fields.get("paymentMethod"))
    if not payment_code:
        payment_code = (customer.get("paymentMethod") or {}).get("id")
    if not payment_code:
        # 若方案類型是純數字碼（如 01/001），作為付款方式碼兜底
        plan_raw = fields.get("planType") or ""
        if re.fullmatch(r"\d{2,3}", plan_raw.strip()):
            payment_code = plan_raw.strip()
    
    winning_rate = _normalize_percentage(fields.get("winningRate")) or "0"
    owner_hint = fields.get("ownerHint") or fields.get("salesName") or (customer.get("owner") or {}).get("name") or ""
    transaction_type = (
        fields.get("transactionType")
        or fields.get("customerCategory")
        or (customer.get("customerClass") or {}).get("label")
        or ""
    )
    stage_hint = fields.get("opportunityStage")
    contact_method = fields.get("contactMethod") or customer.get("contactTel") or fields.get("contactPhone")
    remark = _combine_text(fields.get("remark"), customer.get("remark"))
    contract_number = fields.get("contractNumber") or (customer.get("rawFields") or {}).get("contractNumber")
    opportunity_date = _parse_date(fields.get("opportunityDate"))
    if not contract_start and install_time_text:
        warnings.append("未取得合約開始日，已改用安裝時間。")
    if contract_start and not contract_end and contract_years:
        contract_end = _add_years(contract_start, contract_years)
    if not expect_sign_date:
        expect_sign_date = contract_start
    if expect_sign_money is None and total_amount is not None:
        expect_sign_money = total_amount
    if not opportunity_date:
        opportunity_date = expect_sign_date or contract_start or date.today()
    customer_code = customer.get("customerCode") or _extract_customer_code(fields.get("customerLine", ""), fields.get("customerName", ""))
    customer_name = (
        fields.get("customerName")
        or customer.get("baseName")
        or customer.get("displayName")
        or "客戶"
    )
    contact_phone = customer.get("contactTel") or fields.get("contactPhone")
    if not contact_phone and fields.get("customerLine"):
        contact_phone = _extract_phone(fields["customerLine"])
    plan_code = fields.get("planCode") or _extract_plan_code(plan_type or "")
    item_hint = {
        "brand_name": fields.get("brandName") or "MAQUA",
        "product_name": fields.get("productName") or plan_type or name,
        "product_code": plan_code or plan_type or customer_code or "",
        "productClass_name": fields.get("productClassName") or transaction_type or "",
        "productLine_name": fields.get("productLineName") or plan_type or "",
        "manageClass_name": transaction_type or "",
    }
    context: Dict[str, Any] = {
        "name": name,
        "installLocation": install_location,
        "usageLabel": usage_label,
        "planType": plan_type,
        "monthlyFee": monthly_fee,
        "totalAmount": total_amount,
        "deposit": deposit,
        "prepay": prepay,
        "contractNumber": contract_number,
        "contractStartDate": _date_to_string(contract_start),
        "contractEndDate": _date_to_string(contract_end),
        "contractYears": contract_years,
        "expectSignDate": _date_to_string(expect_sign_date),
        "expectSignMoney": expect_sign_money,
        "expectSignNum": expect_sign_num,
        "currency": currency,
        "paymentCode": payment_code,
        "winningRate": winning_rate,
        "ownerHint": owner_hint,
        "stageHint": stage_hint,
        "transactionType": transaction_type,
        "opportunityDate": _date_to_string(opportunity_date),
        "installTime": install_time_text,
        "contactMethod": contact_method,
        "remark": remark,
        "customerName": customer_name,
        "customerCode": customer_code,
        "contactTel": contact_phone,
        "address": install_location or customer.get("address"),
        "saleAreaId": (customer.get("saleArea") or {}).get("id"),
        "ownerId": (customer.get("owner") or {}).get("id"),
        "ownerName": (customer.get("owner") or {}).get("name"),
        "customerClassId": (customer.get("customerClass") or {}).get("id"),
        "customerClassLabel": (customer.get("customerClass") or {}).get("label"),
        "itemHint": item_hint,
    }
    if not contact_phone:
        warnings.append("未偵測到聯絡電話")
    if not customer_code:
        warnings.append("未偵測到客戶編碼")
    return context, warnings


def parse_opportunity_text(
    text: str, customer_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    normalized = _normalize_customer(customer_data)
    fields = _parse_lines(text)
    context, warnings = _build_context(fields, normalized)
    return {
        "fields": fields,
        "context": context,
        "warnings": warnings,
    }


def _load_customer_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    content = Path(path).read_text(encoding="utf-8")
    data = json.loads(content)
    if isinstance(data, dict) and "normalized" in data:
        return data
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse opportunity info from the sales script text."
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to the text snippet (uses STDIN when omitted)",
    )
    parser.add_argument("--text", help="Raw text input")
    parser.add_argument(
        "--customer-json",
        help="Optional path to the customer_builder output for default values",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty print JSON")
    args = parser.parse_args()

    if args.text:
        content = args.text
    elif args.source:
        content = Path(args.source).read_text(encoding="utf-8")
    else:
        data = sys.stdin.read()
        if not data:
            parser.error("請提供 --text 或傳入檔案/STDIN 內容")
        content = data

    customer_data = _load_customer_json(args.customer_json)
    result = parse_opportunity_text(content, customer_data)
    text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
    )
    print(text)


if __name__ == "__main__":
    main()
