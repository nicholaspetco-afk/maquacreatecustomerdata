#!/usr/bin/env python3
"""Parse customer briefing text and build CRM-friendly payloads."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# 簡易 .env 讀取：在專案根目錄或本目錄存在 .env 時，預先載入環境變量
def _load_env_if_exists() -> None:
    root_env = Path(__file__).resolve().parent.parent / ".env"
    local_env = Path(__file__).resolve().parent / ".env"
    for env_path in (root_env, local_env):
        try:
            if not env_path.exists():
                continue
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # 若已存在同名環境變量則保留現有值，避免覆蓋外部設定
                if k and (k not in os.environ):
                    os.environ[k] = v
        except Exception:
            # 靜默失敗，不影響後續執行
            pass


_load_env_if_exists()


def env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _payment_code(env_key: str, fallback: str) -> str:
    value = env(env_key, fallback).strip()
    if not value:
        return fallback
    clean = value.replace(" ", "")
    if clean.isdigit():
        # 支持 CRM 的實際字典 ID（可能為長整數）
        return clean.zfill(2) if len(clean) <= 2 else clean
    return fallback


CONFIG: Dict[str, Any] = {
    "saleAreas": [
        {
            "label": "澳門島",
            "id": env("CFG_SALEAREA_MO_ID", "1482639830460399618"),
            "code": env("CFG_SALEAREA_MO_CODE", "001"),
            "keywords": ["澳門", "澳門島", "macau", "macao"],
        },
        {
            "label": "氹仔",
            "id": env("CFG_SALEAREA_TAI_ID", "1482639942129549313"),
            "code": env("CFG_SALEAREA_TAI_CODE", "002"),
            "keywords": ["氹仔", "taipa"],
        },
        {
            "label": "珠海",
            "id": env("CFG_SALEAREA_ZH_ID", "1789854460290793480"),
            "code": env("CFG_SALEAREA_ZH_CODE", "003"),
            "keywords": ["珠海", "zhuhai"],
        },
    ],
    "customerClasses": {
        "家用客戶": {
            "id": env("CFG_CLASS_HOME_ID", "1482638121070755844"),
            "code": env("CFG_CLASS_HOME_CODE", "001"),
        },
        "商用客戶": {
            "id": env("CFG_CLASS_BIZ_ID", "1482638189791805446"),
            "code": env("CFG_CLASS_BIZ_CODE", "002"),
        },
        "政府專案": {
            "id": env("CFG_CLASS_GOV_ID", "1482638816869613570"),
            "code": env("CFG_CLASS_GOV_CODE", "006"),
        },
    },
    "usageModes": {
        "租": {"label": "租", "id": env("CFG_USAGE_RENT_ID", "USAGE_RENT_ID")},
        "買": {"label": "買", "id": env("CFG_USAGE_BUY_ID", "USAGE_BUY_ID")},
    },
    "ownerOptions": [
        {
            "keywords": ["寧", "james", "ning"],
            "owner": {
                "id": env("CFG_OWNER_JAMES_ID", "1634633148216115210"),
                "name": "James",
            },
        },
        {
            "keywords": ["成", "cheng"],
            "owner": {
                "id": env("CFG_OWNER_LIANG_ID", "1675717018645954563"),
                "name": "梁必成",
            },
        },
        {
            "keywords": ["liz", "莉茲"],
            "owner": {
                "id": env("CFG_OWNER_LIZ_ID", "1804041613437042698"),
                "name": "李潤婷",
            },
        },
    ],
    "defaultOwner": {
        "id": env(
            "CFG_OWNER_SERVICE_ID",
            env("CFG_OWNER_DEFAULT_ID", "1482551268133044232"),
        ),
        "name": env(
            "CFG_OWNER_SERVICE_NAME", env("CFG_OWNER_DEFAULT_NAME", "客服003")
        ),
    },
    "qualification": {
        "enterpriseType": env("CFG_QUAL_ENTERPRISE", "個人"),
        "qualificationType": env("CFG_QUAL_TYPE", "其他"),
    },
    "defaultCustomerIndustryId": env("CFG_CUSTOMER_INDUSTRY_ID", "").strip(),
    "characterKeys": {
        "totalAmount": env("CFG_CHAR_TOTAL_AMOUNT", "CHAR_TOTAL_AMOUNT"),
        "monthlyFee": env("CFG_CHAR_MONTHLY_FEE", "CHAR_MONTHLY_FEE"),
        "deposit": env("CFG_CHAR_DEPOSIT", "CHAR_DEPOSIT"),
        "prepay": env("CFG_CHAR_PREPAY", "CHAR_PREPAY"),
        "installTime": env("CFG_CHAR_INSTALL_TIME", "CHAR_INSTALL_TIME"),
        "installContent": env("CFG_CHAR_INSTALL_CONTENT", "CHAR_INSTALL_CONTENT"),
        "remark": env("CFG_CHAR_REMARK", "CHAR_REMARK"),
        "usageMode": env("CFG_CHAR_USAGE_MODE", "CHAR_USAGE_MODE"),
        "paymentMethod": env("CFG_CHAR_PAYMENT_METHOD", "CHAR_PAYMENT_METHOD"),
    },
}

_credit_installment = {
    "label": "信用卡分期",
    "id": _payment_code("CFG_PAYMENT_CARD_INSTALLMENT_ID", "02"),
}

CONFIG["paymentMethods"] = {
    "一次性全繳": {
        "label": "一次性全繳",
        "id": _payment_code("CFG_PAYMENT_ONETIME_ID", "01"),
    },
    "信用卡分期": _credit_installment,
    "銀行卡自動轉賬": {
        "label": "銀行卡自動轉賬",
        "id": _payment_code("CFG_PAYMENT_AUTO_DEBIT_ID", "03"),
    },
    "季度收費": {
        "label": "季度收費",
        "id": _payment_code("CFG_PAYMENT_QUARTERLY_ID", "04"),
    },
    "年度收費": {
        "label": "年度收費",
        "id": _payment_code("CFG_PAYMENT_ANNUAL_ID", "05"),
    },
    "試用": {
        "label": "試用",
        "id": _payment_code("CFG_PAYMENT_TRIAL_ID", "06"),
    },
    "每月收費": {
        "label": "每月收費",
        "id": _payment_code("CFG_PAYMENT_MONTHLY_ID", "07"),
    },
}

_PAYMENT_ALIASES = {
    "一次性付款": "一次性全繳",
    "一次性繳交": "一次性全繳",
    "一次性全款": "一次性全繳",
    "全額付款": "一次性全繳",
    "全額繳交": "一次性全繳",
    "季度月費": "季度收費",
    "季度付款": "季度收費",
    "季度繳費": "季度收費",
    "季付": "季度收費",
    "年度月費": "年度收費",
    "年度付款": "年度收費",
    "年度繳費": "年度收費",
    "年付": "年度收費",
    "月費": "每月收費",
    "每月月費": "每月收費",
    "每月付款": "每月收費",
    "每月繳費": "每月收費",
    "月付": "每月收費",
    "銀行轉帳": "銀行卡自動轉賬",
    "銀行匯款": "銀行卡自動轉賬",
    "自動扣款": "銀行卡自動轉賬",
    "銀行卡自動扣款": "銀行卡自動轉賬",
    "轉帳": "銀行卡自動轉賬",
    "轉賬": "銀行卡自動轉賬",
    "信用卡": "信用卡分期",
    "信用卡付款": "信用卡分期",
    "信用卡刷卡": "信用卡分期",
    "信用卡付費": "信用卡分期",
    "信用卡分期付款": "信用卡分期",
    "試用期": "試用",
    "免費試用": "試用",
}

for alias, canonical in _PAYMENT_ALIASES.items():
    target = CONFIG["paymentMethods"].get(canonical)
    if target:
        CONFIG["paymentMethods"][alias] = target

for alias in ("信用卡付費", "信用卡付款", "信用卡刷卡", "信用卡"):
    CONFIG["paymentMethods"][alias] = CONFIG["paymentMethods"]["信用卡分期"]

# 添加銀行自動轉賬的別名
CONFIG["paymentMethods"]["銀行自動轉賬"] = CONFIG["paymentMethods"]["銀行卡自動轉賬"]

# 添加銀行轉帳的別名
CONFIG["paymentMethods"]["銀行轉帳"] = CONFIG["paymentMethods"]["銀行卡自動轉賬"]

_PAYMENT_INDUSTRY_ENV_MAP = {
    "01": "CFG_PAYMENT_ONETIME_INDUSTRY_ID",
    "02": "CFG_PAYMENT_CARD_INSTALLMENT_INDUSTRY_ID",
    "03": "CFG_PAYMENT_AUTO_DEBIT_INDUSTRY_ID",
    "04": "CFG_PAYMENT_QUARTERLY_INDUSTRY_ID",
    "05": "CFG_PAYMENT_ANNUAL_INDUSTRY_ID",
    "06": "CFG_PAYMENT_TRIAL_INDUSTRY_ID",
    "07": "CFG_PAYMENT_MONTHLY_INDUSTRY_ID",
}


def _resolve_payment_industry_id(code: Optional[str]) -> Optional[str]:
    default_id = CONFIG.get("defaultCustomerIndustryId") or ""
    if code:
        normalized = code.zfill(2)
        env_key = _PAYMENT_INDUSTRY_ENV_MAP.get(normalized)
        if env_key:
            env_value = env(env_key, "").strip()
            if env_value:
                return env_value
        return default_id or normalized
    return default_id or None

LABEL_MAP = {
    "客戶名稱": "customerName",
    "客戶編碼": "customerCode",
    "聯繫電話": "contactPhone",
    "目前付費方式": "paymentMethod",
    "安裝時間": "installTime",
    "安裝內容": "installContent",
    "方案類型": "installContent",
    "總金額": "totalAmount",
    "聯絡地址": "address",
    "安裝地址": "address",
    "裝地址": "address",
    "地址": "address",
    "住址": "address",
    "位置": "address",
    "安裝位置": "address",
    "聯絡位置": "address",
    "聯繫地址": "address",
    "地點": "address",
    "備註": "remark",
    "備注": "remark",
    "客戶分類": "customerCategory",
    "付款方式": "paymentMethod",
    "使用方式": "usageMode",
    "月費金額": "monthlyFee",
    "按金": "deposit",
    "預繳金": "prepay",
    "負責人": "ownerHint",
    "銷售": "ownerHint",  # 添加銷售字段映射
    "销售": "ownerHint",  # 簡體版本
}


def strip(value: Optional[str]) -> str:
    return value.strip() if value else ""


def check_customer_code_exists(customer_code: str) -> bool:
    """檢查客戶代碼是否已存在（模擬函數，實際應用中需要調用CRM API）
    
    Args:
        customer_code: 客戶代碼
        
    Returns:
        客戶代碼是否存在
    """
    # 這裡應該調用CRM API來檢查客戶代碼是否存在
    # 目前返回False，表示代碼不存在（允許創建）
    # 在實際應用中，這裡應該調用CRM的查重接口
    return False


def generate_unique_customer_code(base_code: str = "") -> str:
    """生成唯一的客戶代碼
    
    Args:
        base_code: 基礎客戶代碼，如果提供則在基礎上生成變體
        
    Returns:
        唯一的客戶代碼
    """
    if base_code:
        # 如果提供了基礎代碼，生成基於時間戳的變體
        timestamp = datetime.now().strftime("%m%d%H%M")
        return f"{base_code[:3]}{timestamp}"
    else:
        # 生成全新的代碼
        date_part = datetime.now().strftime("%y%m%d")
        random_part = str(uuid.uuid4())[:4].upper()
        return f"C{date_part}{random_part}"


def parse_lines(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    last_key: Optional[str] = None
    for raw_line in text.splitlines():
        line = strip(raw_line)
        if not line:
            continue
        
        # 嘗試多種分隔符
        parts = None
        for separator in ['：', ':', '=']:
            if separator in line:
                parts = line.split(separator, 1)
                break
        
        if not parts or len(parts) != 2:
            # 若無分隔符且上一個欄位是備註，視為備註續行
            if last_key == "remark":
                parsed["remark"] = (parsed.get("remark", "") + "\n" + line).strip()
                last_key = "remark"
            continue
            
        label, value = strip(parts[0]), strip(parts[1])
        key = LABEL_MAP.get(label)
        if key:
            parsed[key] = value
            last_key = key
        else:
            # 若非已知欄位，且前一欄為備註，則作為備註續行
            if last_key == "remark":
                parsed["remark"] = (parsed.get("remark", "") + "\n" + line).strip()
                last_key = "remark"
            else:
                last_key = None
    return parsed


def extract_choice(value: Optional[str], candidates: Iterable[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", "", value)
    
    # 首先檢查是否為數字代碼（01、02 等）
    if re.match(r'^\d{2}$', cleaned):
        # 通過數字代碼查找對應的候選項
        for choice in candidates:
            config = CONFIG["paymentMethods"].get(choice, {})
            if config.get("id") == cleaned:
                return choice
    
    # 檢查括號內的內容
    paren_matches = re.findall(r"[（(]([^（）()]+)[）)]", cleaned)
    if paren_matches:
        candidate = re.sub(r"(這次試試|本次|試試)", "", paren_matches[-1])
        candidate = candidate.strip()
        if candidate:
            for choice in candidates:
                if choice in candidate:
                    return choice
    
    # 檢查是否為 CONFIG["paymentMethods"] 中的別名
    for alias, config in CONFIG["paymentMethods"].items():
        if alias.replace(" ", "") == cleaned:
            # 查找這個配置對應的規範鍵名
            canonical_keys = ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]
            for key in canonical_keys:
                if key in CONFIG["paymentMethods"] and CONFIG["paymentMethods"][key] is config:
                    return key
            # 如果找不到規範鍵名，返回別名
            return alias
    
    # 檢查是否直接匹配候選項
    for choice in candidates:
        if choice.replace(" ", "") == cleaned:
            return choice
    
    return None


def number_from_string(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    
    # 首先嘗試解析數學表達式 (例如: 288*24=6912)
    # 查找等號後的數字（最終結果）
    equals_match = re.search(r'=\s*([0-9,]+)', value)
    if equals_match:
        result_str = equals_match.group(1).replace(',', '')
        try:
            return float(result_str)
        except ValueError:
            pass
    
    # 如果沒有等號，嘗試計算乘法表達式
    multiply_match = re.search(r'([0-9,]+)\s*\*\s*([0-9,]+)', value)
    if multiply_match:
        try:
            num1 = float(multiply_match.group(1).replace(',', ''))
            num2 = float(multiply_match.group(2).replace(',', ''))
            return num1 * num2
        except ValueError:
            pass
    
    # 傳統方法：提取所有數字字符
    normalized = re.sub(r"[^0-9.\-]", "", value)
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def resolve_sale_area(address: str) -> Optional[Dict[str, Any]]:
    lowered = address.lower()
    for area in CONFIG["saleAreas"]:
        if any(keyword.lower() in lowered for keyword in area.get("keywords", [])):
            return area
    return None


def resolve_owner(raw_text: str, owner_hint: Optional[str]) -> Dict[str, Any]:
    candidates = []
    if owner_hint:
        candidates.append(owner_hint)
    candidates.append(raw_text)
    for candidate in candidates:
        lowered = candidate.lower()
        for option in CONFIG["ownerOptions"]:
            if any(keyword in lowered for keyword in option["keywords"]):
                return option["owner"]
    return CONFIG["defaultOwner"]


def parse_install_time(value: Optional[str]) -> Optional[Dict[str, str]]:
    if not value:
        return None
    text = value.strip()
    full_match = re.search(
        r"(20\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})(?:[日号]\s*)?(?:(\d{1,2}):(\d{2}))?",
        text,
    )
    if full_match:
        year, month, day, hour, minute = full_match.groups()
        hour = hour or "00"
        minute = minute or "00"
        return {
            "display": f"{int(year):04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}",
            "iso": datetime(
                int(year), int(month), int(day), int(hour), int(minute)
            ).isoformat(),
        }
    md_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    time_match = re.search(r"(\d{1,2}):(\d{2})", text)
    if md_match:
        now_year = datetime.now().year
        month, day = md_match.groups()
        hour = time_match.group(1) if time_match else "00"
        minute = time_match.group(2) if time_match else "00"
        return {
            "display": f"{now_year:04d}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{int(minute):02d}",
            "iso": datetime(
                now_year, int(month), int(day), int(hour), int(minute)
            ).isoformat(),
        }
    return {"display": text, "iso": None}


def build_crm_payload(normalized: Dict[str, Any]) -> Dict[str, Any]:
    ck = CONFIG["characterKeys"]
    character_payload: Dict[str, Any] = {}

    def add_if_present(key: str, value: Any) -> None:
        if value is not None:
            character_payload[key] = value

    add_if_present(ck["totalAmount"], normalized.get("totalAmount"))
    add_if_present(ck["monthlyFee"], normalized.get("monthlyFee"))
    add_if_present(ck["deposit"], normalized.get("deposit"))
    add_if_present(ck["prepay"], normalized.get("prepay"))
    add_if_present(ck["installTime"], (normalized.get("installTime") or {}).get("display"))
    add_if_present(ck["installContent"], normalized.get("installContent"))
    add_if_present(ck["remark"], normalized.get("remark"))
    add_if_present(ck["usageMode"], normalized.get("usageMode", {}).get("label"))
    add_if_present(
        ck["paymentMethod"], normalized.get("paymentMethod", {}).get("label")
    )

    # 建立 CRM 直接使用的欄位映射
    add_application_payload = {
        "name": normalized.get("displayName"),
        "code": normalized.get("customerCode"),
        "contactTel": normalized.get("contactTel"),
        "contactName": normalized.get("contactName"),
        "saleAreaId": (normalized.get("saleArea") or {}).get("id"),
        "ownerId": (normalized.get("owner") or {}).get("id"),
        "usageMode": (normalized.get("usageMode") or {}).get("label"),
        "paymentMethod": (normalized.get("paymentMethod") or {}).get("label"),
        "monthlyFee": normalized.get("monthlyFee"),
        "deposit": normalized.get("deposit"),
        "prepay": normalized.get("prepay"),
        "totalAmount": normalized.get("totalAmount"),
        "installTime": (normalized.get("installTime") or {}).get("display"),
        "installContent": normalized.get("installContent"),
        "remark": normalized.get("remark"),
        "customerClassId": (normalized.get("customerClass") or {}).get("id"),
        "qualification": normalized.get("qualification"),
        "characters": character_payload,
    }

    # 加入直接映射到 CRM 欄位的資料
    # 付款方式 -> merchantAppliedDetail!payway
    if (normalized.get("paymentMethod") or {}).get("label"):
        add_application_payload["merchantAppliedDetail"] = {
            "payway": (normalized.get("paymentMethod") or {}).get("id")
            or (normalized.get("paymentMethod") or {}).get("label")
        }
    
    # 付款方式 -> customerIndustry.name (自定義欄位)
    customer_industry = normalized.get("customerIndustry")
    if customer_industry:
        add_application_payload["customerIndustry"] = customer_industry

    # 使用方式 -> largeText1
    if (normalized.get("usageMode") or {}).get("label"):
        add_application_payload["largeText1"] = (normalized.get("usageMode") or {}).get("label")
    
    # 方案內容/方案類型 -> largeText2
    if normalized.get("installContent"):
        add_application_payload["largeText2"] = normalized.get("installContent")
    
    # 月費金額 -> largeText3
    if normalized.get("monthlyFee") is not None:
        add_application_payload["largeText3"] = str(normalized.get("monthlyFee"))
    
    # 備註 -> largeText4 或保留原有 remark
    if normalized.get("remark"):
        add_application_payload["largeText4"] = normalized.get("remark")

    return {
        "duplicateCheck": {
            "name": normalized.get("displayName"),
            "code": normalized.get("customerCode"),
            "contactTel": normalized.get("contactTel"),
            "address": normalized.get("address"),
            "customerClassId": normalized.get("customerClass", {}).get("id"),
        },
        "addApplication": add_application_payload,
        "archive": {
            "name": normalized.get("displayName"),
            "code": normalized.get("customerCode"),
            "shortname": normalized.get("shortName"),
            "address": normalized.get("address"),
            "saleArea": normalized.get("saleArea"),
            "owner": normalized.get("owner"),
        },
    }


def parse_customer_text(text: str, auto_generate_code: bool = False, check_duplicate: bool = False) -> Dict[str, Any]:
    """解析客戶文本
    
    Args:
        text: 客戶輸入文本
        auto_generate_code: 是否自動生成客戶代碼（當檢測到重複或無代碼時）
        check_duplicate: 是否檢查客戶代碼重複
        
    Returns:
        解析結果字典
    """
    if not strip(text):
        raise ValueError("輸入內容不可為空")

    raw_fields = parse_lines(text)
    warnings: List[str] = []

    name_field = raw_fields.get("customerName", "")
    code_match = re.search(r"c\d{3,}", name_field, re.IGNORECASE)
    original_code = code_match.group(0).upper() if code_match else ""
    if not original_code:
        # 如果客戶名稱欄位沒有，嘗試在全文中尋找 C 開頭的編碼
        global_match = re.search(r"c\d{3,}", text, re.IGNORECASE)
        if global_match:
            original_code = global_match.group(0).upper()
    
    customer_code = original_code
    
    if not original_code:
        if auto_generate_code:
            # 自動生成客戶代碼
            customer_code = generate_unique_customer_code()
            warnings.append(f"未偵測到客戶編碼，已自動生成：{customer_code}")
        else:
            customer_code = ""
            warnings.append("未偵測到客戶編碼 (需包含 C 開頭的編號)")
    elif check_duplicate and auto_generate_code:
        # 檢查客戶代碼是否重複
        if check_customer_code_exists(customer_code):
            # 生成基於原代碼的變體
            new_code = generate_unique_customer_code(customer_code)
            warnings.append(f"客戶代碼 {customer_code} 已存在，已自動生成新代碼：{new_code}")
            customer_code = new_code

    base_name = strip(re.sub(customer_code, "", name_field, flags=re.IGNORECASE))
    contact_phone_field = raw_fields.get("contactPhone", "")
    # 保留原始電話欄位（含文字/多個號碼）
    contact_tel_raw = strip(contact_phone_field)
    digits = "".join(re.findall(r"\d+", contact_phone_field))
    # 以原始輸入為主，若為空再退回純數字
    contact_tel = contact_tel_raw or digits or ""
    if not contact_tel:
        warnings.append("未偵測到聯繫電話")
    contact_name = strip(contact_phone_field.replace(contact_tel, "")) or "聯絡人"
    display_name = f"{customer_code}{base_name}{contact_tel}".strip()
    short_name = f"{customer_code}{base_name}".strip() or customer_code

    category_label = (
        extract_choice(
            raw_fields.get("customerCategory"),
            CONFIG["customerClasses"].keys(),
        )
        or "商用客戶"
    )
    customer_class = CONFIG["customerClasses"].get(category_label)
    if not customer_class:
        warnings.append(
            f"無法識別的客戶分類：{raw_fields.get('customerCategory', '未提供')}"
        )

    # 處理付款方式 - 先檢查是否為數字代碼
    payment_method_input = raw_fields.get("paymentMethod", "")
    payment_label = None
    payment_method = None
    
    # 檢查是否為數字代碼（01、02 等）
    if payment_method_input and re.match(r'^\d{2}$', payment_method_input.strip()):
        # 通過數字代碼查找對應的付款方式
        for label, config in CONFIG["paymentMethods"].items():
            if config.get("id") == payment_method_input.strip():
                payment_label = label
                payment_method = config
                break
    
    # 如果不是數字代碼或未找到匹配項，使用原有的選擇邏輯
    if not payment_label:
        # 使用 CONFIG["paymentMethods"] 中的所有鍵（包括別名）作為候選項
        extracted_label = extract_choice(
            payment_method_input,
            CONFIG["paymentMethods"].keys(),
        )
        
        if extracted_label:
            # 如果提取的標籤在 CONFIG 中存在，使用它
            if extracted_label in CONFIG["paymentMethods"]:
                payment_label = extracted_label
            else:
                # 否則，查找具有相同 label 屬性的配置項
                for key, config in CONFIG["paymentMethods"].items():
                    if config.get("label") == extracted_label:
                        payment_label = key
                        break
        
        # 如果仍然沒有找到，使用預設值
        if not payment_label:
            payment_label = "一次性全繳"
        
        payment_method = CONFIG["paymentMethods"].get(payment_label)
    
    if not payment_method:
        warnings.append(
            f"無法識別的付款方式：{payment_method_input or '未提供'}"
        )

    usage_label = (
        extract_choice(
            raw_fields.get("usageMode"),
            CONFIG["usageModes"].keys(),
        )
        or "租"
    )
    usage_mode = CONFIG["usageModes"].get(usage_label)

    monthly_fee = number_from_string(raw_fields.get("monthlyFee"))
    deposit = number_from_string(raw_fields.get("deposit"))
    prepay = number_from_string(raw_fields.get("prepay"))
    total_amount = number_from_string(raw_fields.get("totalAmount"))

    address = raw_fields.get("address", "")
    sale_area = resolve_sale_area(address)
    if not sale_area and address:
        warnings.append("無法依地址判斷銷售區域，請手動確認")

    install_time = parse_install_time(raw_fields.get("installTime"))
    owner = resolve_owner(text, raw_fields.get("ownerHint"))

    normalized = {
        "customerCode": customer_code,
        "baseName": base_name,
        "displayName": display_name,
        "shortName": short_name,
        "contactTel": contact_tel,
        "contactName": contact_name,
        "address": address,
        "saleArea": sale_area,
        "paymentMethod": payment_method,
        "usageMode": usage_mode,
        "monthlyFee": monthly_fee,
        "deposit": deposit,
        "prepay": prepay,
        "totalAmount": total_amount,
        "installContent": raw_fields.get("installContent", ""),
        "remark": raw_fields.get("remark", ""),
        "installTime": install_time,
        "customerClass": (
            {**customer_class, "label": category_label} if customer_class else None
        ),
        "paymentLabel": payment_label,
        "usageLabel": usage_label,
        # 保留原始銷售輸入，供後續白名單判斷
        "ownerHint": raw_fields.get("ownerHint"),
        "owner": owner,
        "qualification": CONFIG["qualification"],
        "rawFields": raw_fields,
    }

    payment_code = (payment_method or {}).get("id")
    if payment_label:
        industry_id = _resolve_payment_industry_id(payment_code)
        if industry_id:
            normalized["customerIndustry"] = {
                "id": industry_id,
                "label": payment_label,
                "name": payment_label,
            }

    crm_payload = build_crm_payload(normalized)

    return {
        "normalized": normalized,
        "crmPayload": crm_payload,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse customer briefing text and output CRM payload JSON."
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to the briefing text file (defaults to STDIN if omitted).",
    )
    parser.add_argument(
        "--text",
        help="Raw text snippet to parse (overrides source file).",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the resulting JSON payload.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON with indentation.",
    )
    args = parser.parse_args()

    if args.text:
        content = args.text
    elif args.source:
        content = Path(args.source).read_text(encoding="utf-8")
    else:
        content = os.sys.stdin.read()

    result = parse_customer_text(content)
    json_text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2 if args.pretty or args.output else None,
    )

    if args.output:
        Path(args.output).write_text(json_text + "\n", encoding="utf-8")
    else:
        print(json_text)


if __name__ == "__main__":
    main()
