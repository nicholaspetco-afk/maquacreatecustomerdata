"""Utilities to parse sales scripts and submit customer applications."""

from __future__ import annotations

import json
import os
import sys
import time
import importlib.util
import re
import logging
import calendar
from dataclasses import dataclass
from datetime import datetime, date
import copy
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
from datetime import timedelta

from .crm_client import CRMClient

def _ensure_builder_path() -> bool:
    """確保 customer_builder 所在的 `新增優化` 目錄已加入 sys.path。"""
    candidates = []
    current_file = Path(__file__).resolve()
    # 依序嘗試：模組根目錄、當前工作目錄、祖先目錄
    candidates.append(current_file.parents[2] / "新增優化")
    candidates.append(Path.cwd() / "新增優化")
    for parent in current_file.parents:
        candidates.append(parent / "新增優化")

    added = False
    for path in candidates:
        if (path / "customer_builder.py").exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            added = True
    return added


def _load_customer_builder():
    """直接由檔案路徑載入 customer_builder，避免 sys.path 問題。"""
    candidates = []
    current_file = Path(__file__).resolve()
    candidates.append(current_file.parents[2] / "新增優化" / "customer_builder.py")
    candidates.append(Path.cwd() / "新增優化" / "customer_builder.py")
    for parent in current_file.parents:
        candidates.append(parent / "新增優化" / "customer_builder.py")

    for file_path in candidates:
        if file_path.exists():
            spec = importlib.util.spec_from_file_location("customer_builder", str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                sys.modules["customer_builder"] = module
                return module
    raise ImportError("customer_builder.py not found in candidates")


ROOT_DIR = Path(__file__).resolve().parents[2]
_ensure_builder_path()

try:
    _builder_module = _load_customer_builder()
    parse_customer_text = _builder_module.parse_customer_text  # type: ignore
    generate_unique_customer_code = _builder_module.generate_unique_customer_code  # type: ignore
    parse_install_time = getattr(_builder_module, "parse_install_time", None)  # type: ignore
except Exception:  # pragma: no cover

    def parse_customer_text(text: str) -> Dict[str, Any]:  # type: ignore
        raise RuntimeError(
            "無法載入 customer_builder，請確認新增優化/customer_builder.py 是否存在。"
        )

    def generate_unique_customer_code(prefix: str = "C") -> str:  # type: ignore
        raise RuntimeError("generate_unique_customer_code 不可用（未載入 builder）")

    def parse_install_time(value: Optional[str]) -> Optional[Dict[str, str]]:  # type: ignore
        return None

# 如果 builder 未提供 parse_install_time，使用簡易後備解析
if "parse_install_time" not in globals() or parse_install_time is None:  # type: ignore
    def parse_install_time(value: Optional[str]) -> Optional[Dict[str, str]]:  # type: ignore
        if not value:
            return None
        text = str(value).strip()
        full_match = re.search(
            r"(20\\d{2})[./年-]\\s*(\\d{1,2})[./月-]\\s*(\\d{1,2})(?:[日号]\\s*)?(?:(\\d{1,2}):(\\d{2}))?",
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
        md_match = re.search(r"(\\d{1,2})\\s*月\\s*(\\d{1,2})\\s*日", text)
        time_match = re.search(r"(\\d{1,2}):(\\d{2})", text)
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

OPPORTUNITY_DIR = ROOT_DIR / "新增商機"
if str(OPPORTUNITY_DIR) not in sys.path:
    sys.path.insert(0, str(OPPORTUNITY_DIR))

try:
    from opportunity_builder import parse_opportunity_text  # type: ignore
except ImportError:  # pragma: no cover
    parse_opportunity_text = None

SESSION_TTL_SECONDS = 3600
OPPORTUNITY_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
RAW_TEXT_BY_CUSTOMER_CODE: Dict[str, str] = {}
_STAGE_CACHE: Dict[str, str] = {}
_TRANS_TYPE_CACHE: Dict[str, str] = {}

# 統一任務負責人：預設用客服003，可用環境覆蓋
def _task_owner(settings: SubmissionSettings) -> Tuple[str, str]:
    # 固定任務負責人為客服003，除非外部環境覆蓋
    owner_id = os.getenv("CFG_TASK_OWNER_ID") or "1482551268133044232"
    owner_name = os.getenv("CFG_TASK_OWNER_NAME") or "客服003"
    return owner_id.strip(), owner_name.strip()

def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_payment_code(value: str, fallback: str = "99") -> str:
    clean = (value or "").strip()
    if not clean:
        return fallback
    if clean.isdigit():
        # 支援長整數 ID，仍保留舊有兩位碼
        return clean.zfill(2) if len(clean) <= 2 else clean
    # 支持 "01-07" 這樣的格式
    if "-" in clean and len(clean) <= 5:
        return clean
    return fallback


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_list(key: str, default: str) -> Tuple[str, ...]:
    raw = os.getenv(key)
    text = raw if raw is not None else default
    if not text:
        return ()
    return tuple(item.strip() for item in text.split(",") if item.strip())


def _short_resubmit(prefix: str = "task", max_len: int = 32) -> str:
    """生成不超過 max_len 的 resubmitCheckKey。"""
    base = uuid4().hex
    available = max_len - len(prefix) - 1  # 減去底線
    if available < 1:
        available = 1
    return f"{prefix}_{base[:available]}"


PAYMENT_INDUSTRY_ENV_MAP = {
    "01": "CFG_PAYMENT_ONETIME_INDUSTRY_ID",
    "02": "CFG_PAYMENT_CARD_INSTALLMENT_INDUSTRY_ID",
    "03": "CFG_PAYMENT_AUTO_DEBIT_INDUSTRY_ID",
    "04": "CFG_PAYMENT_QUARTERLY_INDUSTRY_ID",
    "05": "CFG_PAYMENT_ANNUAL_INDUSTRY_ID",
    "06": "CFG_PAYMENT_TRIAL_INDUSTRY_ID",
    "07": "CFG_PAYMENT_MONTHLY_INDUSTRY_ID",
}

PENDING_APPLICATION_CODE = "090-501-200376"
PAYMENT_PENDING_CODE = "090-501-200377"

# 商机阶段ID - 正確映射（租=運維期，買=簽訂合同）
# 運維期（租）：opptStage=1587859872035110919, processStage=1607907035615068223
DEFAULT_STAGE_RENT_ID = "1587859872035110919"
DEFAULT_STAGE_RENT_PROCESS_ID = "1607907035615068175"
DEFAULT_STAGE_RENT_PROCESS_STAGE_ID = "1607907035615068223"

# 簽訂合同（買）：opptStage=1476791442110679300, processStage=1607907035615068211
DEFAULT_STAGE_BUY_ID = "1476791442110679300"
DEFAULT_STAGE_BUY_PROCESS_ID = "1607907035615068175"
DEFAULT_STAGE_BUY_PROCESS_STAGE_ID = "1607907035615068211"



@dataclass
class SubmissionSettings:
    system_source: str = _env("CFG_SYSTEM_SOURCE", "auto_crm")
    bustype_id: str = _env("CFG_CUSTOMER_BUSTYPE_ID", "1779393122472558598")
    applicant_org_id: str = _env("CFG_APPLY_ORG_ID", "2816765183021312")
    applicant_user_id: str = _env("CFG_APPLICANT_USER_ID", "1634633148216115210")
    applicant_dept_id: str = _env("CFG_APPLICANT_DEPT_ID", "1482538237314465798")
    service_owner_id: str = _env("CFG_OWNER_SERVICE_ID", "1482551268133044232")
    service_owner_name: str = _env("CFG_OWNER_SERVICE_NAME", "客服003")
    service_dept_id: str = _env("CFG_OWNER_SERVICE_DEPT_ID", applicant_dept_id)
    service_dept_name: str = _env("CFG_OWNER_SERVICE_DEPT_NAME", "客服部")
    # 各個銷售的 ID 映射
    owner_james_id: str = _env("CFG_OWNER_JAMES_ID", applicant_user_id)
    owner_liang_id: str = _env("CFG_OWNER_LIANG_ID", "1675717018645954563")
    owner_liz_id: str = _env("CFG_OWNER_LIZ_ID", "1804041613437042698")
    sales_org_id: str = _env("CFG_SALES_ORG_ID", applicant_org_id)
    trans_type_id: str = _env("CFG_CUSTOMER_TRANS_TYPE_ID", "1476790952607089117")
    customer_industry_id: str = _env("CFG_CUSTOMER_INDUSTRY_ID", "1580721825339932673")
    tax_category: int = _env_int("CFG_TAX_CATEGORY", 0)
    enterprise_nature: int = _env_int("CFG_ENTERPRISE_NATURE", 1)
    license_type: int = _env_int("CFG_LICENSE_TYPE", 3)
    payment_way: str = _sanitize_payment_code(_env("CFG_CUSTOMER_PAYWAY", "99"))
    customer_level_id: Optional[str] = os.getenv("CFG_CUSTOMER_LEVEL_ID") or None
    searchcode_prefix: str = _env("CFG_SEARCHCODE_PREFIX", "AUTO")
    default_region_code: str = _env("CFG_DEFAULT_REGION_CODE", "").strip()
    parent_manage_org_id: Optional[str] = os.getenv("CFG_PARENT_MANAGE_ORG_ID") or ""
    payment_field: str = _env("CFG_FIELD_PAYMENT", "merchantAppliedDetail!payway")
    plan_field: str = _env("CFG_FIELD_PLAN", "merchantCharacter__customerDefine6")
    remark_field: str = _env("CFG_FIELD_REMARK", "merchantCharacter__customerDefine7")
    # 額外欄位：使用方式與月費，可由 .env 指定實際綁定欄位，預設沿用 largeText1/largeText3
    usage_field: str = _env("CFG_FIELD_USAGE", "largeText1")
    monthly_fee_field: str = _env("CFG_FIELD_MONTHLY_FEE", "largeText3")
    attach_contact_records: bool = _env_bool("CFG_ATTACH_CONTACT_RECORDS", False)
    create_opportunity: bool = _env_bool("CFG_CREATE_OPPORTUNITY", False)
    opportunity_stage_rent: str = _env("CFG_OPPORTUNITY_STAGE_RENT", "")
    opportunity_stage_buy: str = _env("CFG_OPPORTUNITY_STAGE_BUY", "")
    opportunity_stage_default: str = _env("CFG_OPPORTUNITY_STAGE_DEFAULT", "")
    opportunity_bustype_id: str = _env("CFG_OPPORTUNITY_BUSTYPE_ID", bustype_id)
    opportunity_trans_type_id: str = _env("CFG_OPPORTUNITY_TRANS_TYPE_ID", trans_type_id)
    opportunity_main_bill_num: str = _env("CFG_OPPORTUNITY_MAIN_BILL", "sfa_opptcard")
    opportunity_currency: str = _env("CFG_OPPORTUNITY_CURRENCY", "MOP")
    opportunity_source: Optional[str] = os.getenv("CFG_OPPORTUNITY_SOURCE") or None
    opportunity_contract_default_years: int = _env_int(
        "CFG_OPPORTUNITY_CONTRACT_DEFAULT_YEARS", 2
    )
    opportunity_contract_extended_years: int = _env_int(
        "CFG_OPPORTUNITY_CONTRACT_EXTENDED_YEARS", 3
    )
    opportunity_contract_keywords: Tuple[str, ...] = _env_list(
        "CFG_OPPORTUNITY_CONTRACT_KEYWORDS", "HS990,HM190,HM290"
    )
    opportunity_system_code: str = _env("CFG_OPPORTUNITY_SYSTEM_CODE", "opptOpenApIAdd")


def _text_map(value: str) -> Dict[str, str]:
    return {"zh_TW": value, "zh_CN": value}


def _assign_field(data: Dict[str, Any], field_code: str, value: Any) -> None:
    if not field_code or value in (None, "", [], {}):
        return
    data[field_code] = value

    if "." in field_code and "__" not in field_code:
        segments = [segment for segment in field_code.split(".") if segment]
        if len(segments) >= 2:
            target = data
            for segment in segments[:-1]:
                next_value = target.setdefault(segment, {})
                if not isinstance(next_value, dict):
                    return
                target = next_value
            target[segments[-1]] = value
            data.pop(field_code, None)
        return

    if "__" not in field_code:
        return

    prefix, nested_key = field_code.split("__", 1)
    if not nested_key:
        return

    if prefix == "merchantCharacter":
        entity_key = "merchantCharacterEntity!merchantCharacter"
        entity = data.setdefault(entity_key, {})
        if isinstance(entity, dict):
            entity[nested_key] = value
        mirror = data.setdefault("merchantCharacter", {})
        if isinstance(mirror, dict):
            mirror[nested_key] = value
    elif prefix == "customerAddApplyCharacter":
        entity = data.setdefault("customerAddApplyCharacter", {})
        if isinstance(entity, dict):
            entity[nested_key] = value


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for parser in (lambda v: datetime.fromisoformat(v), lambda v: datetime.strptime(v[:10], "%Y-%m-%d")):
        try:
            return parser(text).date()
        except ValueError:
            continue
    return None


def _date_to_string(value: Optional[date]) -> Optional[str]:
    if not value:
        return None
    return value.strftime("%Y-%m-%d")


def _add_years(base: date, years: int) -> date:
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        # handle leap day
        return base.replace(month=2, day=28, year=base.year + years)


def _contains_keyword(text: str, keywords: Tuple[str, ...]) -> bool:
    if not text or not keywords:
        return False
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _resolve_opportunity_stage(usage_label: str, settings: SubmissionSettings) -> Optional[str]:
    label = (usage_label or "").lower()
    if label:
        rent_tokens = ("租", "rent", "租用", "租賃")
        buy_tokens = ("買", "购", "购買", "buy", "買斷", "買入")
        # 只有在環境有顯式配置時才回寫階段，避免寫到尾端節點
        if any(token in label for token in rent_tokens) and settings.opportunity_stage_rent:
            return settings.opportunity_stage_rent
        if any(token in label for token in buy_tokens) and settings.opportunity_stage_buy:
            return settings.opportunity_stage_buy
    # 無配置時讓 CRM 默認階段處理
    return settings.opportunity_stage_default or None


def _resolve_payment_industry_id(
    payment_code: Optional[str], settings: SubmissionSettings
) -> Optional[str]:
    default_id = settings.customer_industry_id
    if payment_code:
        normalized = payment_code.zfill(2)
        env_key = PAYMENT_INDUSTRY_ENV_MAP.get(normalized)
        if env_key:
            env_value = os.getenv(env_key)
            if env_value:
                env_value = env_value.strip()
                if env_value:
                    return env_value
        # 若未設定對應的環境變數，優先使用使用者輸入的付款代碼
        return normalized
    return default_id or None


# 付款方式代码到中文名称的映射
PAYMENT_CODE_TO_LABEL = {
    "01": "一次性全繳",
    "02": "信用卡分期",
    "03": "銀行卡自動轉賬",
    "04": "季度收費",
    "05": "年度收費",
    "06": "試用",
    "07": "每月收費",
}


def _get_payment_label_from_code(payment_code: Optional[str]) -> Optional[str]:
    """根据付款代码获取对应的中文名称"""
    if not payment_code:
        return None
    # 标准化为两位数字
    normalized = payment_code.zfill(2) if payment_code.isdigit() and len(payment_code) <= 2 else payment_code
    return PAYMENT_CODE_TO_LABEL.get(normalized)



def _fallback_address_code(normalized: Dict[str, Any]) -> str:
    for key in ("customerCode", "shortName", "displayName", "contactTel"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"ADDR{datetime.now():%Y%m%d%H%M%S}"


def _is_pending_application_error(message: str) -> bool:
    return (PENDING_APPLICATION_CODE in message) or ("在申请" in message)


def _is_payment_pending_error(message: str) -> bool:
    return PAYMENT_PENDING_CODE in message


def _apply_new_customer_code(normalized: Dict[str, Any]) -> Optional[str]:
    current_code = normalized.get("customerCode") or ""
    new_code = generate_unique_customer_code(current_code)
    if not new_code or new_code == current_code:
        return None
    normalized["customerCode"] = new_code
    base_name = normalized.get("baseName") or ""
    contact_tel = normalized.get("contactTel") or ""
    normalized["displayName"] = (
        f"{new_code}{base_name}{contact_tel}".strip() or new_code
    )
    normalized["shortName"] = f"{new_code}{base_name}".strip() or new_code
    raw_fields = normalized.get("rawFields")
    if isinstance(raw_fields, dict):
        original_name = raw_fields.get("customerName") or ""
        if original_name:
            raw_fields["customerName"] = f"{base_name} {new_code}".strip()
    return new_code


def _determine_contract_years(
    plan_text: str, settings: SubmissionSettings
) -> int:
    if _contains_keyword(plan_text or "", settings.opportunity_contract_keywords):
        return max(settings.opportunity_contract_extended_years, settings.opportunity_contract_default_years)
    return settings.opportunity_contract_default_years


def _as_number(value: Any) -> Optional[float]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _get_address_text(
    normalized: Dict[str, Any], application_data: Dict[str, Any]
) -> str:
    if normalized.get("address"):
        return str(normalized["address"])
    source = application_data.get("address")
    if isinstance(source, dict):
        return (
            source.get("zh_TW")
            or source.get("zh_CN")
            or source.get("en_US")
            or ""
        )
    if isinstance(source, str):
        return source
    return ""


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            candidate = value.strip()
        else:
            candidate = str(value).strip()
        if candidate:
            return candidate
    return None


def _extract_created_customer_id(application_response: Dict[str, Any]) -> Optional[str]:
    data = application_response.get("data") or {}
    candidates: List[Optional[str]] = []
    for payload in (data, data.get("newBizObject")):
        if not isinstance(payload, dict):
            continue
        customer_block = payload.get("customer")
        if isinstance(customer_block, dict):
            candidates.append(customer_block.get("id"))
        candidates.extend(
            [
                payload.get("customerId"),
                payload.get("customerID"),
                payload.get("custId"),
                payload.get("custID"),
            ]
        )
    return _first_non_empty(*candidates)


def _extract_customer_entity_id(application_response: Dict[str, Any]) -> Optional[str]:
    data = application_response.get("data") or {}
    direct = data.get("id")
    if direct:
        return str(direct)
    collections = (
        "customerAreas",
        "merchantAddressInfos",
        "merchantAppliedDetail",
        "merchantApplyRanges",
        "principals",
    )
    for key in collections:
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = item.get("customerId") or item.get("merchantId") or item.get("merchantApplyRangeId")
            if candidate:
                return str(candidate)
    return None


def _format_amount(value: Optional[Any]) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    return text or None


def _merge_notes(*parts: Optional[str]) -> str:
    lines: List[str] = []
    for part in parts:
        if not isinstance(part, str):
            continue
        text = part.strip()
        if text and text not in lines:
            lines.append(text)
    return "\n".join(lines)


def _purge_opportunity_sessions() -> None:
    now = time.time()
    expired = [
        token
        for token, entry in OPPORTUNITY_SESSION_CACHE.items()
        if now - entry.get("createdAt", now) > SESSION_TTL_SECONDS
    ]
    for token in expired:
        OPPORTUNITY_SESSION_CACHE.pop(token, None)


def _remember_opportunity_session(
    normalized: Dict[str, Any],
    application_response: Dict[str, Any],
) -> str:
    _purge_opportunity_sessions()
    token = uuid4().hex
    OPPORTUNITY_SESSION_CACHE[token] = {
        "normalized": copy.deepcopy(normalized),
        "applicationResponse": copy.deepcopy(application_response),
        "createdAt": time.time(),
    }
    return token


def _get_opportunity_session(token: str) -> Dict[str, Any]:
    _purge_opportunity_sessions()
    entry = OPPORTUNITY_SESSION_CACHE.get(token)
    if not entry:
        raise LookupError("找不到對應的商機資料，請重新送出客戶資訊。")
    return entry


def _lookup_customer_id_by_code(
    customer_code: Optional[str], client: CRMClient, retries: int = 3
) -> Optional[str]:
    if not customer_code:
        return None
    for attempt in range(max(retries, 1)):
        customer_id = _search_customer_id(customer_code, client)
        if customer_id:
            return customer_id
        time.sleep(1.0)
    return None


def _is_duplicate_rule_missing_error(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return ("未设置查重规则" in text) or ("090-501-101397" in text)


def _search_customer_id(customer_code: str, client: CRMClient) -> Optional[str]:
    code_upper = customer_code.upper()

    def _match_record(record: Dict[str, Any]) -> bool:
        candidates = [
            record.get("customerCode"),
            record.get("customer_code"),
            record.get("customerName"),
            record.get("customer_name"),
            record.get("name"),
        ]
        customer_block = record.get("customer")
        if isinstance(customer_block, dict):
            candidates.extend(
                [
                    customer_block.get("code"),
                    customer_block.get("name"),
                ]
            )
        for value in candidates:
            if isinstance(value, str) and code_upper in value.upper():
                return True
        return False

def _extract_customer_id(records: List[Dict[str, Any]]) -> Optional[str]:
    if not records:
        return None
    for record in records:
        customer_id = _first_non_empty(
            record.get("customer"),
            record.get("customerId"),
            record.get("customer_id"),
            record.get("customerID"),
        )
        if not customer_id:
            customer_block = record.get("customer")
            if isinstance(customer_block, dict):
                customer_id = customer_block.get("id")
        if customer_id and _match_record(record):
            return str(customer_id)
    return None

    attempts: List[Tuple[str, str, str]] = [
        ("customer.code", "eq", customer_code),
        ("customer.code", "like", customer_code),
        ("customer_name", "like", customer_code),
        ("customer.name", "like", customer_code),
    ]
    for field, operator, value in attempts:
        try:
            resp = client.get_followups(
                value,
                page=1,
                page_size=10,
                search_field=field,
                search_operator=operator,
            )
        except Exception:
            continue
        record_list = resp.get("data", {}).get("recordList") or []
        customer_id = _extract_customer_id(record_list)
        if customer_id:
            return customer_id

    for page in range(1, 4):
        try:
            resp = client.get_followups("", page=page, page_size=20)
        except Exception:
            continue
        record_list = resp.get("data", {}).get("recordList") or []
        matches = [
            record
            for record in record_list
            if _match_record(record)
        ]
        customer_id = _extract_customer_id(matches or record_list)
        if customer_id:
            return customer_id
    return None


def _normalize_stage_candidate(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        return None
    return text


def _ensure_stage_cache(client: CRMClient) -> None:
    global _STAGE_CACHE
    if _STAGE_CACHE:
        return
    try:
        resp = client.get_opportunities(page=1, page_size=50)
    except Exception:
        return
    record_list = resp.get("data", {}).get("recordList") or []
    for record in record_list:
        stage_id = _first_non_empty(
            record.get("opptStage"),
            record.get("stage"),
            record.get("opptStageId"),
        )
        stage_name = _first_non_empty(
            record.get("opptStage_name"),
            record.get("stageName"),
        )
        if stage_id and stage_name:
            key = stage_name.lower()
            if key not in _STAGE_CACHE:
                _STAGE_CACHE[key] = str(stage_id)


def _find_stage_id_by_label(label: str, client: CRMClient) -> Optional[str]:
    normalized = label.strip().lower()
    if not normalized:
        return None
    _ensure_stage_cache(client)
    if not _STAGE_CACHE:
        return None
    for name, stage_id in _STAGE_CACHE.items():
        if normalized in name or name in normalized:
            return stage_id
    return None


def _resolve_stage_value(
    context: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> Tuple[Optional[str], Optional[str]]:
    usage_label = context.get("usageLabel") or ""
    candidates: List[Optional[str]] = [
        context.get("stageHint"),
    ]
    stage_type: Optional[str] = None
    usage_lower = usage_label.lower()
    if usage_lower:
        # 需求：租 -> 簽訂合同階段；買 -> 運維期階段
        if any(token in usage_lower for token in ("租", "rent", "租用", "租賃")):
            stage_type = "buy"
            candidates.append(settings.opportunity_stage_buy or DEFAULT_STAGE_BUY_ID)
        elif any(token in usage_lower for token in ("買", "购", "購", "buy", "買斷", "買入")):
            stage_type = "rent"
            candidates.append(settings.opportunity_stage_rent or DEFAULT_STAGE_RENT_ID)
    candidates.extend(
        [
            settings.opportunity_stage_default,
            settings.opportunity_stage_rent,
            settings.opportunity_stage_buy,
        ]
    )
    for candidate in candidates:
        text = _normalize_stage_candidate(candidate)
        if not text:
            continue
        if text.isdigit():
            return text, stage_type or _infer_stage_type(text, settings)
        stage_id = _find_stage_id_by_label(text, client)
        if stage_id:
            return stage_id, stage_type or _infer_stage_type(stage_id, settings)
    fallback_stage = _extract_cached_stage_any(client)
    if fallback_stage:
        return fallback_stage, stage_type or _infer_stage_type(fallback_stage, settings)
    stage_from_context = context.get("opptStage") or context.get("opptStageId")
    if stage_from_context:
        return str(stage_from_context), stage_type or _infer_stage_type(str(stage_from_context), settings)
    return None, None


def _infer_stage_type(stage_id: str, settings: SubmissionSettings) -> Optional[str]:
    normalized = (stage_id or "").strip()
    if not normalized:
        return None
    rent_candidates = {
        settings.opportunity_stage_rent or "",
        DEFAULT_STAGE_RENT_ID,
    }
    buy_candidates = {
        settings.opportunity_stage_buy or "",
        DEFAULT_STAGE_BUY_ID,
    }
    if normalized in rent_candidates:
        return "rent"
    if normalized in buy_candidates:
        return "buy"
    return None


def _extract_cached_stage_any(client: CRMClient) -> Optional[str]:
    _ensure_stage_cache(client)
    if not _STAGE_CACHE:
        return None
    return next(iter(_STAGE_CACHE.values()))


def create_opportunity_from_session(token: str) -> Dict[str, Any]:
    entry = _get_opportunity_session(str(token).strip())
    normalized = copy.deepcopy(entry.get("normalized") or {})
    application_response = copy.deepcopy(entry.get("applicationResponse") or {})
    settings = SubmissionSettings()
    client = CRMClient()
    result = _create_opportunity_for_customer(
        normalized,
        settings,
        application_response,
        audit_passed=True,
        client=client,
    )
    if isinstance(result, dict) and result.get("success"):
        OPPORTUNITY_SESSION_CACHE.pop(token, None)
    return result


def _ensure_trans_type_cache(client: CRMClient) -> None:
    global _TRANS_TYPE_CACHE
    if _TRANS_TYPE_CACHE:
        return
    try:
        resp = client.get_opportunities(page=1, page_size=50)
    except Exception:
        return
    record_list = resp.get("data", {}).get("recordList") or []
    for record in record_list:
        trans_id = _first_non_empty(
            record.get("opptTransType"),
            record.get("bustype"),
            record.get("transType"),
        )
        trans_name = _first_non_empty(
            record.get("opptTransType_name"),
            record.get("bustype_name"),
            record.get("transType_name"),
        )
        if trans_id and trans_name:
            key = trans_name.lower()
            if key not in _TRANS_TYPE_CACHE:
                _TRANS_TYPE_CACHE[key] = str(trans_id)


def _find_trans_type_id(label: str, client: CRMClient) -> Optional[str]:
    normalized = label.strip().lower()
    if not normalized:
        return None
    _ensure_trans_type_cache(client)
    if not _TRANS_TYPE_CACHE:
        return None
    for name, trans_id in _TRANS_TYPE_CACHE.items():
        if normalized in name or name in normalized:
            return trans_id
    return None


def _resolve_trans_type_value(
    context: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> Optional[str]:
    candidates: List[Optional[str]] = [
        context.get("transTypeHint"),
        context.get("transactionType"),
        settings.opportunity_trans_type_id,
        settings.trans_type_id,
    ]
    for candidate in candidates:
        text = _normalize_stage_candidate(candidate)
        if not text:
            continue
        if text.isdigit():
            return text
        lookup = _find_trans_type_id(text, client)
        if lookup:
            return lookup
    _ensure_trans_type_cache(client)
    if _TRANS_TYPE_CACHE:
        return next(iter(_TRANS_TYPE_CACHE.values()))
    return None


def _generate_opportunity_code(customer_code: Optional[str] = None) -> str:
    prefix = (customer_code or "OPPT")[:6].upper()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{timestamp}"

# 客戶相關 payload builders
def build_duplicate_payload(
    normalized: Dict[str, Any], settings: SubmissionSettings
) -> Dict[str, Any]:
    data = {
        "name": normalized.get("displayName"),
        "code": normalized.get("customerCode"),
        "contactTel": normalized.get("contactTel"),
        "address": normalized.get("address"),
        "customerClass": (normalized.get("customerClass") or {}).get("id"),
    }
    return {
        "systemSource": settings.system_source,
        "action": "browse",
        "mainBillNum": "cust_customerCard",
        "data": data,
        "tabInfo": [{"billNum": "cust_customerCard", "mappingType": "0"}],
    }


def build_apply_payload(
    normalized: Dict[str, Any], settings: SubmissionSettings
) -> Dict[str, Any]:
    now = datetime.now()
    apply_code = f"CUST{now:%Y%m%d%H%M%S}"
    sale_area = normalized.get("saleArea") or {}
    owner = normalized.get("owner") or {}
    owner_hint = (normalized.get("ownerHint") or "").strip().lower()
    
    # 銷售白名單：liz/LIZ、james/James/成/寧，其他統一為客服003
    # 根據名字映射到對應的 ID 和顯示名稱
    owner_mapping = {
        "liz": {"id": settings.owner_liz_id, "name": "LIZ"},
        "james": {"id": settings.owner_james_id, "name": "James"},
        "成": {"id": settings.owner_liang_id, "name": "成"},
        "寧": {"id": settings.owner_james_id, "name": "寧"},
    }
    
    print(f"[DEBUG] owner_hint = '{owner_hint}'", flush=True)
    
    if owner_hint in owner_mapping:
        # 在白名單中，使用對應的 owner信息
        owner = owner_mapping[owner_hint]
        dept_id = settings.applicant_dept_id
        print(f"[DEBUG] 匹配成功 -> ID={owner['id']}, Name={owner['name']}", flush=True)
    else:
        # 不在白名單，使用客服003
        owner = {"id": settings.service_owner_id, "name": settings.service_owner_name}
        dept_id = settings.service_dept_id
        print(f"[DEBUG] 不在白名單，使用客服003", flush=True)
    customer_class = normalized.get("customerClass") or {}
    customer_industry = normalized.get("customerIndustry") or {}
    address_text = normalized.get("address") or ""
    contact_name = normalized.get("contactName") or "聯絡人"
    contact_tel = normalized.get("contactTel") or ""

    address_payload = {"zh_TW": address_text, "zh_CN": address_text}
    contact_block = {
        "isDefault": True,
        "org": settings.sales_org_id,
        "dept": settings.applicant_dept_id,
        "fullName": _text_map(contact_name),
        "mobile": contact_tel,
        "telePhone": contact_tel,
        "_status": "Insert",
        "contacterCharacter": {},
    }
    address_code = _fallback_address_code(normalized)
    address_block = {
        "isDefault": True,
        "addressCode": address_code,
        "address": address_text,
        "receiver": contact_name,
        "mobile": contact_tel,
        "telePhone": contact_tel,
        "_status": "Insert",
        "addressInfoCharacter": {},
    }
    if settings.default_region_code:
        address_block["regionCode"] = settings.default_region_code
        address_block["mergerName"] = address_text

    person_label = (
        normalized.get("shortName") or normalized.get("baseName") or contact_name
    )

    parent_org_value = (
        settings.parent_manage_org_id
        or normalized.get("parentManageOrg")
        or settings.sales_org_id
    )

    payment_entry = normalized.get("paymentMethod") or {}
    payment_code = _sanitize_payment_code(
        payment_entry.get("id") or "", settings.payment_way
    )
    payment_label = payment_entry.get("label") or ""
    plan_value = normalized.get("installContent")
    remark_value = normalized.get("remark")
    
    customer_industry_info = normalized.get("customerIndustry") or {}
    customer_industry_name = (
        customer_industry_info.get("name")
        or customer_industry_info.get("label")
        or payment_label
        or ""
    )
    customer_industry_id = (
        customer_industry_info.get("id")
        or _resolve_payment_industry_id(payment_code, settings)
    )
    customer_industry = {
        "id": customer_industry_id,
        "name": customer_industry_name,
        "label": customer_industry_name,
    }

    payload: Dict[str, Any] = {
        "systemSource": settings.system_source,
        "bustype": settings.bustype_id,
        "applyTime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "code": apply_code,
        "org": settings.applicant_org_id,
        "transType": settings.trans_type_id,
        "ower": owner.get("id") or settings.applicant_user_id,
        "dept": dept_id,
        "saleArea": sale_area.get("id"),
        "name": _text_map(
            normalized.get("displayName") or normalized.get("shortName") or "新客戶"
        ),
        "shortname": _text_map(
            normalized.get("shortName") or normalized.get("displayName") or "新客戶"
        ),
        "custCode": normalized.get("customerCode"),
        "retailInvestors": False,
        "internalOrg": False,
        "customerClass": customer_class.get("id"),
        "customerIndustry": customer_industry.get("id"),
        "parentManageOrg": parent_org_value,
        "merchantAppliedDetail!belongOrg": settings.sales_org_id,
        "merchantAppliedDetail!searchcode": f"{settings.searchcode_prefix}{normalized.get('customerCode', '')}",
        "merchantAppliedDetail!customerLevel": settings.customer_level_id,
        "enterpriseNature": settings.enterprise_nature,
        "licenseType": settings.license_type,
        "taxPayingCategories": settings.tax_category,
        "enterpriseName": normalized.get("displayName"),
        "leaderName": person_label,
        "leaderNameIdNo": "",
        "creditCode": "",
        "businessLicenseNo": "",
        "regionCode": settings.default_region_code or None,
        "address": address_payload,
        "personName": person_label,
        "contactName": contact_name,
        "contactTel": contact_tel,
        "buildTime": now.strftime("%Y-%m-%d"),
        "money": normalized.get("totalAmount"),
        "scopeModel": 0,
        "scope": {"zh_TW": "", "zh_CN": ""},
        "website": "",
        "wid": "",
        "largeText1": (normalized.get("usageMode") or {}).get("label"),
        "largeText3": normalized.get("monthlyFee"),
        "principals": [
            {
                "isDefault": True,
                "professSalesman": owner.get("id") or settings.applicant_user_id,
                "specialManagementDep": dept_id,
                "_status": "Insert",
            }
        ],
        "customerAreas": [
            {
                "isDefault": True,
                "saleAreaId": sale_area.get("id"),
                "_status": "Insert",
            }
        ],
        "merchantAddressInfos": [address_block],
        "merchantContacterInfos": [contact_block]
        if settings.attach_contact_records
        else [],
        "_status": "Insert",
        "customerAddApplyCharacter": {},
        "merchantAppliedDetail!merchantApplyRangeDetailCharacter": {},
        "merchantCharacterEntity!merchantCharacter": {},
    }
    if plan_value:
        payload["largeText2"] = plan_value
    if remark_value:
        payload["largeText4"] = remark_value
    default_payment_field = "merchantAppliedDetail!payway"
    _assign_field(payload, default_payment_field, payment_code)
    custom_payment_field = settings.payment_field
    if (
        custom_payment_field
        and custom_payment_field != default_payment_field
    ):
        payment_value_for_custom = payment_label or payment_code
        _assign_field(payload, custom_payment_field, payment_value_for_custom)
    _assign_field(payload, settings.plan_field, normalized.get("installContent"))
    _assign_field(payload, settings.remark_field, normalized.get("remark"))
    
    # 設置 customerIndustry.name 字段，用於存儲支付方式描述
    # 根據用戶反饋，這是訂製的存儲位置
    if customer_industry.get("name"):
        _assign_field(payload, "customerIndustry.name", customer_industry.get("name"))
    
    # 強制綁定「使用方式」與「月費」到指定欄位，避免 UI 僅顯示 character 欄位而忽略 largeText1/3
    _assign_field(
        payload,
        settings.usage_field,
        (normalized.get("usageMode") or {}).get("label"),
    )
    _assign_field(payload, settings.monthly_fee_field, normalized.get("monthlyFee"))
    return {"data": _cleanup(payload)}


def build_audit_payload(
    application_id: str, settings: SubmissionSettings
) -> Dict[str, Any]:
    return {
        "data": [
            {
                "systemSource": settings.system_source,
                "id": application_id,
            }
        ]
    }


def _build_opportunity_duplicate_request(
    context: Dict[str, Any], settings: SubmissionSettings
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "name": context.get("name"),
        "customer": context.get("customerCode") or context.get("customerId"),
        "customerName": context.get("customerName"),
        "org": settings.sales_org_id,
        "dept": settings.applicant_dept_id,
        "ower": context.get("ownerId") or settings.applicant_user_id,
        "saleArea": context.get("saleAreaId"),
        "address": context.get("installLocation"),
        "opptDate": context.get("opportunityDate"),
        "expectSignDate": context.get("expectSignDate"),
        "expectSignMoney": _format_amount(context.get("expectSignMoney")),
        "expectSignNum": _format_amount(context.get("expectSignNum")),
        "opptTransType": settings.opportunity_trans_type_id,
        "opptSource": settings.opportunity_source,
        "winningRate": context.get("winningRate"),
        "description": context.get("planType") or context.get("remark"),
    }
    item_hint = context.get("itemHint")
    if isinstance(item_hint, dict) and item_hint:
        data["opptItemList"] = item_hint
    return {
        "data": _cleanup(data),
        "system_source": settings.system_source,
        "action": "browse",
        "main_bill_num": settings.opportunity_main_bill_num,
        "bill_num": settings.opportunity_main_bill_num,
        "tab_info": [{"billNum": settings.opportunity_main_bill_num, "mappingType": "0"}],
    }


def _build_opportunity_create_payload(
    context: Dict[str, Any],
    normalized: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> Dict[str, Any]:
    owner_id = context.get("ownerId") or settings.applicant_user_id
    stage_value, stage_kind = _resolve_stage_value(context, settings, client)
    contract_years = context.get("contractYears")
    if not contract_years:
        contract_years = _determine_contract_years(
            context.get("planType") or "", settings
        )
        context["contractYears"] = contract_years
    if contract_years and context.get("contractStartDate") and not context.get(
        "contractEndDate"
    ):
        start_date = _parse_date(context["contractStartDate"])
        if start_date:
            context["contractEndDate"] = _date_to_string(
                _add_years(start_date, int(contract_years))
            )
    # 描述僅用使用者備註，避免自動拼接金額/聯絡方式干擾顯示
    remark_text = context.get("remark") or ""
    # character_payload 初始化为空字典即可，后面在 headDef 部分统一设置
    character_payload: Dict[str, Any] = {}
    
    # 注意：所有字段映射都在下面的 headDef/opptDefineCharacter 部分统一处理
    # 避免在这里重复设置导致冲突
    item_payload = _build_opportunity_items(context, settings)
    opportunity_code = context.get("code") or _generate_opportunity_code(
        context.get("customerCode")
    )
    context["code"] = opportunity_code
    trans_type_value = _resolve_trans_type_value(context, settings, client)
    customer_ref = context.get("customerCode") or context.get("customerId")
    # 修复：确保 opptDefineCharacter 始终为字典，避免 None 导致后续 setdefault 失败
    data = {
        "code": opportunity_code,
        "resubmitCheckKey": opportunity_code,
        # 修正：商机名称优先使用安装位置（地址），如果为空才使用客户名称
        "name": context.get("installLocation") or context.get("address") or context.get("name"),
        "customer": customer_ref,
        "settleCustomer": customer_ref,
        "finalUser": customer_ref,
        "opptDate": context.get("opportunityDate"),
        "opptTransType": trans_type_value or settings.opportunity_trans_type_id,
        "opptStage": stage_value,
        "winningRate": _format_amount(context.get("winningRate")) or "0",
        "opptSource": settings.opportunity_source,
        "opptState": 0,
        "expectSignNum": _format_amount(context.get("expectSignNum")),
        "currency": context.get("currency") or settings.opportunity_currency,
        "expectSignMoney": _format_amount(context.get("expectSignMoney")),
        "expectSignDate": context.get("expectSignDate"),
        "expectFee": None,
        "address": context.get("installLocation"),
        "expectTenderDate": context.get("expectTenderDate"),
        "ower": owner_id,
        "dept": settings.applicant_dept_id,
        "saleArea": context.get("saleAreaId"),
        "org": settings.sales_org_id,
        # 描述僅顯示備註（避免整段輸入佔用描述欄）
        "description": remark_text or context.get("planType") or normalized.get("installContent"),
        "regionCode": settings.default_region_code or None,
        "contractBeginDate": context.get("contractStartDate"),
        "contractEndDate": context.get("contractEndDate"),
        "contractYear": contract_years,
        "remark": remark_text or context.get("remark"),
        "opptDefineCharacter": character_payload if character_payload else {},
        "opptItemList": item_payload,
        "_status": "Insert",
        "systemCode": settings.opportunity_system_code,
    }
    
    # 调试日志：打印关键字段上下文
    print(f"[DEBUG] Opportunity Context: paymentCode={context.get('paymentCode')}, usageLabel={context.get('usageLabel')}, deposit={context.get('deposit')}, prepay={context.get('prepay')}")

    head_def = data.setdefault("headDef", {})
    oppt_char = data.setdefault("opptDefineCharacter", {})
    # 保存原始輸入全文到自訂欄位，供任務內容使用，避免覆蓋描述
    raw_full_text = context.get("rawText") or normalized.get("_raw_text")
    if raw_full_text:
        head_def["define20"] = raw_full_text
        data["headDef!define20"] = raw_full_text
    
    # --- 1. 目前付款方式 (Payment Method) ---
    # 根据真实 CRM 数据（C4612），付款方式应该写入 industry 字段
    # 这与客户创建的逻辑一致（customer_builder.py:527）
    payment_code = context.get("paymentCode")
    if payment_code:
        # 解析付款方式对应的 industry ID
        industry_id = _resolve_payment_industry_id(payment_code, settings)
        if industry_id:
            data["industry"] = industry_id
            # 也可以尝试写入 industry_name（虽然API可能会忽略）
            # 根据 payment_code 获取对应的 label
            payment_label = _get_payment_label_from_code(payment_code)
            if payment_label:
                data["industry_name"] = payment_label
        
    # --- 2. 使用方式 (Usage Mode) ---
    # 使用方式對應 define8 / attrext8；交易類型名稱放在 transType_name
    usage_label = context.get("usageLabel")
    normalized_usage = usage_label
    if usage_label in ("租", "租用"):
        normalized_usage = "租用"
    elif usage_label in ("買", "買斷", "買入", "購買", "购买"):
        normalized_usage = "買斷"
    if usage_label:
        head_def["define8"] = str(normalized_usage)
        data["headDef!define8"] = str(normalized_usage)
        oppt_char["attrext8"] = str(normalized_usage)
        
    # 交易類型名稱
    trans_label = context.get("transactionType") or usage_label
    if trans_label:
        data["transType_name"] = str(trans_label)
        data["opptTransType_name"] = str(trans_label)
        
    # --- 3. 方案類型 / 安裝位置 ---
    # 根据 C4612 真实数据：define9/attrext9 是方案类型
    plan_value = context.get("planType")
    if plan_value:
        # 方案类型写入 define9/attrext9
        head_def["define9"] = str(plan_value)
        data["headDef!define9"] = str(plan_value)
        oppt_char["attrext9"] = str(plan_value)
    
    # 安装位置单独处理，写入 address 和可能的其他字段
    location_value = context.get("installLocation")
    addr_fallback = normalized.get("address") or ""
    
    # 如果 location 像客戶編碼+姓名且有地址，換成地址
    if location_value and re.search(r"C\d+.+\d{6,}", str(location_value)):
        if addr_fallback:
            location_value = addr_fallback
    
    # 写入安装位置（只使用主字段，避免冲突）
    final_location = location_value or addr_fallback or plan_value
    if final_location:
        data["address"] = str(final_location)
    
    # --- 3B. 合约相关字段 - 广撒网策略 ---
    # 合约开始日期
    if context.get("contractStartDate"):
        data["contractBeginDate"] = context["contractStartDate"]
        data["contractStartDate"] = context["contractStartDate"]
        head_def["define17"] = context["contractStartDate"]
        oppt_char["attrext2"] = context["contractStartDate"]
    
    # 合约结束日期
    if context.get("contractEndDate"):
        data["contractEndDate"] = context["contractEndDate"]
        data["contractEnd"] = context["contractEndDate"]
        head_def["define18"] = context["contractEndDate"]
        oppt_char["attrext3"] = context["contractEndDate"]
    
    # 合约年期
    contract_years = context.get("contractYears")
    if contract_years:
        data["contractYear"] = contract_years
        data["contractYears"] = contract_years
        head_def["define19"] = str(contract_years)
        oppt_char["attrext4"] = str(contract_years)
    
    
    # --- 4. 月费 (Monthly Fee) - 广撒网策略 ---
    # 尝试写入多个可能的字段位置，提高命中率
    if context.get("monthlyFee") is not None:
        try:
            monthly_float = float(context.get("monthlyFee"))
            monthly_str = _format_amount(context.get("monthlyFee"))
            
            # 只写入核心字段，避免冲突
            head_def["define10"] = monthly_str
            data["headDef!define10"] = monthly_str
            oppt_char["attrext10"] = monthly_float
            data["monthlyFee"] = monthly_float
        except (ValueError, TypeError):
            pass
    
    # --- 5. 预缴金 (Prepay) ---
    # 根據實際 CRM 數據（如 2025111115447），預繳金落在 define11 / attrext16
    if context.get("prepay") is not None:
        try:
            prepay_float = float(context.get("prepay"))
            prepay_str = _format_amount(context.get("prepay"))
            
            head_def["define11"] = prepay_str
            data["headDef!define11"] = prepay_str
            oppt_char["attrext16"] = prepay_float
            data["prepay"] = prepay_float
        except (ValueError, TypeError):
            pass
    
    # --- 6. 按金 (Deposit) ---
    # 按金對應 define12 / attrext17
    if context.get("deposit") is not None:
        try:
            deposit_float = float(context.get("deposit"))
            deposit_str = _format_amount(context.get("deposit"))
            
            head_def["define12"] = deposit_str
            data["headDef!define12"] = deposit_str
            oppt_char["attrext17"] = deposit_float
            data["deposit"] = deposit_float
        except (ValueError, TypeError):
            pass


    process_value = context.get("process")
    process_stage_value = context.get("processStage")
    # 若客戶未指定階段，避免強塞流程到末段；只有當 stage_kind 存在時才補流程
    if stage_kind == "rent":
        process_value = process_value or DEFAULT_STAGE_RENT_PROCESS_ID
        process_stage_value = process_stage_value or DEFAULT_STAGE_RENT_PROCESS_STAGE_ID
    elif stage_kind == "buy":
        process_value = process_value or DEFAULT_STAGE_BUY_PROCESS_ID
        process_stage_value = process_stage_value or DEFAULT_STAGE_BUY_PROCESS_STAGE_ID
    if stage_value and process_value:
        data["process"] = process_value
    if stage_value and process_stage_value:
        data["processStage"] = process_stage_value
        
    # 调试日志：打印最终 Payload 的自定义字段部分
    print(f"[DEBUG] Final Opportunity Payload headDef: {data.get('headDef')}")
    print(f"[DEBUG] Final Opportunity Payload opptDefineCharacter: {data.get('opptDefineCharacter')}")
    print(f"[DEBUG] Complete Opportunity Payload:")
    import json
    print(json.dumps(data, ensure_ascii=False, indent=2))
    
    return {"data": _cleanup(data)}


def _build_opportunity_items(
    context: Dict[str, Any], settings: SubmissionSettings
) -> List[Dict[str, Any]]:
    items = _parse_install_items(
        context.get("installContent") or context.get("planType") or "",
        context.get("planType") or "",
    )

    def _valid_identifier(value: Optional[str]) -> Optional[str]:
        text = _normalize_stage_candidate(value)
        if text and text.isdigit():
            return text
        return None

    install_date = (
        context.get("contractStartDate")
        or context.get("expectSignDate")
        or context.get("opportunityDate")
    )

    built: List[Dict[str, Any]] = []
    if not items:
        items = [
            {
                "name": context.get("planType") or context.get("name"),
                "code": None,
                "cycle": None,
                "qty": 1,
            }
        ]

    for item in items:
        product_name = item.get("name") or context.get("planType") or context.get("name")
        code_text = item.get("code") or ""
        qty = item.get("qty") or 1
        cycle_months = item.get("cycle")

        item_payload: Dict[str, Any] = {
            "itemCurrency": context.get("currency") or settings.opportunity_currency,
            "unitPrice": _format_amount(_as_number(context.get("expectSignMoney")) or 0.0) or "0",
            "num": qty,
            "money": _format_amount((_as_number(context.get("expectSignMoney")) or 0.0) * float(qty)) or "0",
            "remark": "",
            "productName": product_name,
            "_status": "Insert",
            "systemCode": settings.opportunity_system_code,
        }
        if code_text:
            item_payload["productCode"] = code_text
            if code_text.isdigit():
                item_payload["product"] = code_text

        product_id = _valid_identifier(item.get("product_code"))
        if product_id:
            item_payload["product"] = product_id

        # 換芯資訊
        def_char: Dict[str, Any] = {}
        if install_date:
            def_char["attrext11"] = install_date
            def_char["attrext14"] = install_date
        if cycle_months:
            def_char["attrext12"] = cycle_months
            try:
                from datetime import datetime

                base = datetime.fromisoformat(str(install_date)[:10])
                month = base.month - 1 + int(cycle_months)
                year = base.year + month // 12
                month = month % 12 + 1
                day = min(
                    base.day,
                    [
                        31,
                        29
                        if year % 4 == 0 and not year % 100 == 0 or year % 400 == 0
                        else 28,
                        31,
                        30,
                        31,
                        30,
                        31,
                        31,
                        30,
                        31,
                        30,
                        31,
                    ][month - 1],
                )
                next_date = f"{year:04d}-{month:02d}-{day:02d}"
                def_char["attrext13"] = next_date
            except Exception:
                pass
        if def_char:
            item_payload["opptItemDefineCharacter"] = def_char
            body_def: Dict[str, Any] = {}
            if def_char.get("attrext11"):
                body_def["define1"] = def_char["attrext11"]
            if def_char.get("attrext12") is not None:
                body_def["define2"] = def_char["attrext12"]
            if def_char.get("attrext14"):
                body_def["define4"] = def_char["attrext14"]
            if def_char.get("attrext13"):
                body_def["define3"] = def_char["attrext13"]
            if body_def:
                item_payload["bodyDef"] = body_def

        built.append(item_payload)

    return built


def _normalize_replacement_cycle(plan_type: Optional[str], item_hint: Dict[str, Any]) -> Optional[int]:
    lookup = (plan_type or "").upper()
    # Excel 表映射
    mapped = _lookup_product(plan_type)
    if mapped.get("cycle"):
        try:
            return int(mapped["cycle"])
        except ValueError:
            return None
    # 若 item_hint 提供週期
    cycle = item_hint.get("replacement_cycle_months")
    try:
        return int(cycle) if cycle is not None else None
    except Exception:
        return None


def _lookup_products(plan_type: Optional[str]) -> List[Dict[str, str]]:
    lookup = (plan_type or "").upper()
    if not lookup:
        return []
    catalog = {
        # 套件與子件
        "RO900S": {
            "code": "1414",
            "name": "RO-900S E.P微電腦可調式RO純水機",
            "cycle": "",
            "children": ["R-002", "R-001"],
        },
        "RO600G": {
            "code": "1581",
            "name": "EVERPOLL-RO-600G RO機",
            "cycle": "",
            "children": ["RO600G主", "RO500G膜", "RO500G炭", "RO500GPP"],
        },
        "RO600G主": {"code": "1581", "name": "EVERPOLL-RO-600G RO機", "cycle": ""},
        "RO500G膜": {"code": "1558", "name": "RO500G 第二道RO逆滲透膜", "cycle": "24"},
        "RO500G炭": {"code": "1559", "name": "RO500G 第三道活性炭濾芯", "cycle": "12"},
        "RO500GPP": {"code": "1557", "name": "RO500G 第一道玄武岩合成活性PP", "cycle": "6"},
        "ONYX": {
            "code": "1587",
            "name": "LIVINGCARE-Onyx-即冷熱直飲機",
            "cycle": "",
            "children": ["ONYX濾芯1", "ONYX濾芯2"],
        },
        "ONYX濾芯1": {"code": "1592", "name": "ONYX-鈣抑正電荷 E-Positive Ak Filter", "cycle": "12"},
        "ONYX濾芯2": {"code": "1591", "name": "ONYX-活性碳PH Carbon Block Ak Filter", "cycle": "12"},
        "CHP101": {
            "code": "1586",
            "name": "LIVINGCARE-CHP-101即冷熱直飲機",
            "cycle": "",
            "children": ["CHP101濾芯1", "CHP101濾芯2"],
        },
        "CHP101濾芯1": {"code": "1594", "name": "CHP101-鈣抑正電荷E-Positive Ak Filter", "cycle": "12"},
        "CHP101濾芯2": {"code": "1593", "name": "CHP101-活性碳Carbon Block Ak Filter", "cycle": "12"},
        "MF330": {
            "code": None,
            "name": "MF330 組合",
            "cycle": "",
            "children": ["MF110", "MF220"],
        },
        "MF110": {"code": "1192", "name": "MF110 EVERPOLL商用高流量飲用水過濾系統", "cycle": "12"},
        "MF220": {"code": "1193", "name": "MF220 EVERPOLL商用高流量樹脂離子交換系統", "cycle": "6"},
        "DC3000": {
            "code": None,
            "name": "DC3000 組合",
            "cycle": None,
            "children": ["DC2000", "DC1000"],
        },
        # 單一物料
        "HS990": {"code": "1005", "name": "HS990智慧節能殺菌飲水機", "cycle": ""},
        "HM290": {"code": "1087", "name": "HM290 直立式冰溫熱飲水機(白色)", "cycle": ""},
        "EP298": {"code": "1116", "name": "EVERPOLL- EVB-298 智能雙溫飲水機", "cycle": ""},
        "HM190": {"code": "1089", "name": "HM190 桌上型冰冷熱飲水機(白)", "cycle": ""},
        "EP398": {"code": "1649", "name": "EVB-398 智能櫥下型三溫UV觸控飲水機", "cycle": ""},
        "EP168PLUS": {"code": "1650", "name": "EP-168PLUS-廚下型調溫無壓飲水機", "cycle": ""},
        "M3": {"code": "1613", "name": "HS-M3 櫥下型冰溫熱飲水機", "cycle": ""},
        "十秒機": {"code": "1194", "name": "10SM EVERPOLL-十秒機(OZONE活氧)", "cycle": ""},
        "UVC-902": {"code": "1267", "name": "UVC-902滅菌設備", "cycle": ""},
        "MAXTEC": {"code": "1003", "name": "Maxtec X-6 紫外線殺菌燈組", "cycle": ""},
        "壓力桶3G": {"code": "1206", "name": "壓力桶（3L)", "cycle": ""},
        "壓力桶1.5G": {"code": "1474", "name": "壓力桶（1.5l）", "cycle": ""},
        "龍頭": {"code": "1138", "name": "EVERPURE-TOP 原裝水龍頭", "cycle": ""},
        "4GUV": {"code": "1199", "name": "PHILIPS-UV-SET 紫外線殺菌燈組-4G", "cycle": "12"},
        "6GUV": {"code": "1015", "name": "PHILIPS-UV-SET 紫外線殺菌燈組-6G/25W", "cycle": "12"},
        "1GUV": {"code": "1099", "name": "PHILIPS-UV-SET 紫外線殺菌燈組-1G/6W", "cycle": "12"},
        "12GUV": {"code": "1014", "name": "PHILIPS-UV-SET 紫外線殺菌燈組-12G/40W", "cycle": "12"},
        "2GUV": {"code": "1016", "name": "PHILIPS-UV-SET 紫外線殺菌燈組-2G/16W", "cycle": "12"},
        # UV 殺菌燈別名（部分客戶文案寫 Phillips/Philips 2G/16W）
        # PHILIPS 2G/16W 殺菌燈 => 對應 2GUV，同步設置代碼讓 CRM 顯示名稱
        "PHILIPS 2G/16W 殺菌燈": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "PHILLIPS 2G/16W 殺菌燈": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "PHILIPS2G16W": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "PHILLIPS2G16W": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "2G/16W 殺菌燈": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "2GUV16W": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "PHILIPS 2G UV 殺菌燈": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "PHILLIPS 2G UV 殺菌燈": {"code": "1016", "name": "PHILIPS 2G/16W 殺菌燈", "cycle": "12"},
        "UF": {"code": "1439", "name": "MAXTEC-UF超濾膜濾芯", "cycle": "12"},
        "PBS400": {"code": "1183", "name": "EVERPURE-PBS400直飲過濾系統", "cycle": "12"},
        "H104": {"code": "1182", "name": "EVERPURE-H104直飲過濾系統", "cycle": "12"},
        "EF6000": {"code": "1217", "name": "EVERPURE-EF6000直飲過濾系統", "cycle": "12"},
        "FH301": {"code": "1214", "name": "EVERPOLL-FH301全屋過濾系統", "cycle": "12"},
        "FH500": {"code": "1339", "name": "EVERPOLL-FH500中央過濾系統", "cycle": "12"},
        "FH230": {"code": "1563", "name": "EVERPOLL-FH230 全屋過濾淨系統", "cycle": "12"},
        "FH200": {"code": "1578", "name": "EVERPOLL-FH200全屋過濾淨系統", "cycle": "12"},
        "DC2000": {"code": "1119", "name": "EVERPOLL-DC2000 英國無納離子交換樹脂系統", "cycle": "6"},
        "DC1000": {"code": "1120", "name": "EVERPOLL-DC1000 單道雙效複合式系統", "cycle": "12"},
        "AHP150": {"code": "1137", "name": "EVERPOLL-AHP150中央過濾系統", "cycle": "12"},
        "10吋PP": {"code": "1101", "name": "10吋-PP過濾棉", "cycle": "6"},
        "20吋PP": {"code": "1100", "name": "20吋-PP過濾棉", "cycle": "6"},
        "T33": {"code": "1017", "name": "Filter T33 Small濾芯", "cycle": "12"},
        "CLARIS-XL": {"code": "1682", "name": "EVERPURE-CLARIS-XL", "cycle": "12"},
        "PWCE16F10": {"code": "1512", "name": "EVERPURE軟水系統PWCE16F10", "cycle": ""},
        "RO150G": {"code": "1019", "name": 'Filter PP1um 10"濾芯', "cycle": "6"},
        "RO100G": {"code": "1019", "name": 'Filter PP1um 10"濾芯', "cycle": "6"},
        "RO400G": {"code": "1019", "name": 'Filter PP1um 10"濾芯', "cycle": "6"},
        "雙頭MC": {"code": "1249", "name": "EVERPURE-QC71-TWIN-MC2", "cycle": ""},
        "雙頭I2000": {"code": "1227", "name": "EVERPURE-QC71-TWIN-I20002", "cycle": ""},
        # RO900S 專用耗材
        "R-001": {"code": "1350", "name": "R-001多折式雙效復合濾芯", "cycle": "12"},
        "R-002": {"code": "1351", "name": "R-002高效抗污RO膜", "cycle": "24"},
        # MC2 耗材
        "MC2": {"code": "1146", "name": "EVERPURE-MC2 濾芯", "cycle": "12"},
    }
    results: List[Dict[str, str]] = []
    
    # 第一步：精確匹配（key 在 lookup 中）
    for key, data in catalog.items():
        if key in lookup:
            # 如果有 children，僅加入子物料，忽略父項
            if data.get("children"):
                for child in data["children"]:
                    child_data = catalog.get(child)
                    if child_data and child_data not in results:
                        results.append(child_data)
            else:
                if data not in results:
                    results.append(data)
    
    # 如果精確匹配成功，直接返回，不再進行 fallback 匹配
    if results:
        return results
    
    # 第二步：特殊關鍵詞匹配（包含「龍頭」）
    if "龍頭" in lookup:
        tap_data = catalog.get("龍頭")
        if tap_data:
            return [tap_data]
    
    # 第三步：fallback - 用物料名稱包含關係匹配（含空白/破折號/大小寫）
    normalized_lookup = lookup.replace(" ", "").replace("-", "")
    for key, data in catalog.items():
        # 跳過有 children 的父項
        if data.get("children"):
            continue
        name_upper = (data.get("name") or "").upper()
        name_norm = name_upper.replace(" ", "").replace("-", "")
        if normalized_lookup and (normalized_lookup in name_norm or name_norm in normalized_lookup):
            if data not in results:
                results.append(data)
    
    return results


def _lookup_product_single(key: str) -> Dict[str, str]:
    results = _lookup_products(key)
    return results[0] if results else {}


def _parse_install_items(text: str, plan_type: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not text:
        return items
    tokens = [t.strip() for t in re.split(r"[+,，；;]", text) if t.strip()]
    for token in tokens:
        qty = 1
        qty_match = re.search(r"\*(\d+)", token)
        if qty_match:
            try:
                qty = int(qty_match.group(1))
            except ValueError:
                qty = 1
        name = re.sub(r"\*\d+", "", token).strip()
        mapped_list = _lookup_products(name)
        if not mapped_list:
            # 未在物料表命中時跳過，避免產生空白行項
            continue
        for mapped in mapped_list:
            cycle = _normalize_cycle(mapped.get("cycle"))
            items.append(
                {
                    "name": mapped.get("name") or name,
                    "code": mapped.get("code"),
                    "cycle": cycle,
                    "qty": qty,
                }
            )
    # 去重：同 code+name+cycle+qty 只保留一筆
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for it in items:
        key = (
            (it.get("code") or "").strip(),
            (it.get("name") or "").strip(),
            str(it.get("cycle") or ""),
            str(it.get("qty") or "1"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return deduped
    return items


def _normalize_cycle(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        val = str(value).strip()
        return int(val) if val else None
    except ValueError:
        return None


def _cleanup(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _cleanup(v) for k, v in obj.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [_cleanup(item) for item in obj if item not in (None, "", [], {})]
    return obj


def _is_success_response(response: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(response, dict):
        return False
    code = response.get("code")
    if code is None:
        return False
    return str(code) in {"200", "00000"}


def _auto_create_tasks_for_opportunity(
    context: Dict[str, Any],
    create_response: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> None:
    owner_id, owner_name = _task_owner(settings)
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    data = create_response.get("data") or {}
    
    # 調試日誌：打印 context 和 data 的關鍵信息
    print(f"[task] Debug - context keys: {list(context.keys())}", flush=True)
    print(f"[task] Debug - context.customerId: {context.get('customerId')}", flush=True)
    print(f"[task] Debug - context.customerName: {context.get('customerName')}", flush=True)
    print(f"[task] Debug - context.customerCode: {context.get('customerCode')}", flush=True)
    print(f"[task] Debug - data keys: {list(data.keys())}", flush=True)
    print(f"[task] Debug - data.customer: {data.get('customer')}", flush=True)
    print(f"[task] Debug - data.customer_name: {data.get('customer_name')}", flush=True)
    
    oppt_id = data.get("id") or context.get("opptId")
    oppt_stage = data.get("opptStage") or context.get("opptStage")
    customer_id = context.get("customerId") or data.get("customer")
    customer_name = context.get("customerName") or data.get("customer_name")
    
    print(f"[task] Debug - final customer_id: {customer_id}", flush=True)
    print(f"[task] Debug - final customer_name: {customer_name}", flush=True)
    
    # 驗證客戶ID
    if not customer_id:
        # 嘗試通過客戶編碼查詢
        customer_code = context.get("customerCode")
        if customer_code:
            print(f"[task] 嘗試通過客戶編碼 {customer_code} 查詢客戶ID", flush=True)
            try:
                customer_id = _lookup_customer_id_by_code(customer_code, client)
                if customer_id:
                    print(f"[task] ✅ 查詢到客戶ID: {customer_id}", flush=True)
                else:
                    print(f"[task] ❌ 無法查詢到客戶ID", flush=True)
            except Exception as e:
                print(f"[task] ❌ 查詢客戶ID失敗: {e}", flush=True)
        
        if not customer_id:
            error_msg = (
                f"無法創建任務：缺少客戶ID。"
                f"context.customerId={context.get('customerId')}, "
                f"context.customerCode={context.get('customerCode')}, "
                f"data.customer={data.get('customer')}, "
                f"context keys={list(context.keys())}"
            )
            print(f"[task] ❌ ERROR: {error_msg}", flush=True)
            raise ValueError(error_msg)
    
    if not customer_name:
        customer_name = context.get("customerCode") or f"客戶_{customer_id}"
        print(f"[task] Warning: 使用後備客戶名稱: {customer_name}", flush=True)
    sale_area = context.get("saleAreaId") or data.get("saleArea")
    dept_id = settings.service_dept_id
    dept_name = settings.service_dept_name
    amount = context.get("totalAmount") or context.get("expectSignMoney")
    start_date = None
    raw_install = context.get("installTime")
    parsed_install = parse_install_time(raw_install) if raw_install else None
    if parsed_install:
        start_date = parsed_install["display"].split(" ")[0]
    if not start_date:
        raw_date = (
            str(context.get("opportunityDate") or "")
            or str(context.get("expectSignDate") or "")
        )
        if raw_date:
            start_date = str(raw_date).split(" ")[0]
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    end_date = start_date

    # 任務類型：新增項目
    task_type_id = "1984155894542237704"  # 新增項目
    task_bustype_id = "1984154580281720833"
    task_action_trans_type = "1597134252596527112"
    task_action_bustype = "1597128428638699526"

    executors = [
        {"id": "1655434173036888070", "name": "維修幫005"},
        {"id": "1634618416471998473", "name": "出納008"},
    ]

    summary = ""
    raw_text = (context.get("rawText") or "").strip()
    if raw_text:
        content = raw_text
    else:
        content_parts = [
            f"客戶：{customer_name or ''}",
            f"方案：{context.get('planType') or ''}",
            f"地址：{context.get('installLocation') or ''}",
            f"內容：{context.get('remark') or ''}",
        ]
        content = "\n".join([p for p in content_parts if p and p.strip(': ')])
    task_code = datetime.now().strftime("%Y%m%d%H%M%S") + uuid4().hex[:4]

    payload = {
        "data": {
            "code": task_code,
            "resubmitCheckKey": _short_resubmit("task"),
            "org": settings.sales_org_id,
            "org_name": "",
            "taskTransType": task_type_id,
            "taskTransType_actionTransType": task_action_trans_type,
            "taskTransType_actionTransTypeBustype": task_action_bustype,
            "bustype": task_bustype_id,
            "startDate": f"{start_date} 00:00:00",
            "endDate": f"{end_date} 23:59:59",
            "customer": customer_id,
            "customer_name": customer_name,
            "originator": owner_id,
            "originator_name": owner_name,
            "saleArea": sale_area,
        "dept": dept_id,
        "dept_name": dept_name,
        "summary": summary,
        "content": content,
            "oppt": oppt_id,
            "opptStage": oppt_stage,
            "ower": owner_id,
            "ower_name": owner_name,
            "systemSource": settings.system_source,
            # 更換濾芯任務不寫金額
            "taskDefineCharacter": {},
            "taskExecutorList": [],
            "taskRemindRuleList": [
                {
                    "remindPoint": "0",
                    "advanceTime": "0",
                    "timeUnit": "0",
                    "_status": "Insert",
                }
            ],
            "_status": "Insert",
        }
    }
    # 更換濾芯任務不寫金額

    for ex in executors:
        payload["data"]["taskExecutorList"].append(
            {
                "executor": ex["id"],
                "executor_name": ex["name"],
                "executeStatus": "0",
                "reformStatus": "0",
                "acceptStatus": "0",
                "isUnlock": "0",
                "startDate": f"{start_date} 00:00:00",
                "endDate": f"{end_date} 23:59:59",
                "excutorDefineCharacter": {},
                "_status": "Insert",
            }
        )

    log_line = json.dumps(payload, ensure_ascii=False)
    print(f"[task] payload {log_line}", flush=True)
    resp = client.create_task(payload)
    print(f"[task] response {json.dumps(resp, ensure_ascii=False)}", flush=True)
    if str(resp.get("code")) not in {"200", "00000"}:
        raise RuntimeError(f"task save failed: {resp}")


def _find_next_replacement_date(create_data: Dict[str, Any]) -> Optional[Tuple[date, str]]:
    items = create_data.get("opptItemList") or []
    candidates: List[Tuple[date, str]] = []
    for item in items:
        body = item.get("bodyDef") or {}
        prod_name = (
            body.get("productName")
            or item.get("productName")
            or item.get("product_name")
            or item.get("product")
            or body.get("name")
            or ""
        )
        define3 = body.get("define3")
        if define3:
            try:
                candidates.append(
                    (datetime.strptime(str(define3).split(" ")[0], "%Y-%m-%d").date(), prod_name)
                )
                continue
            except Exception:
                pass
        def_char = item.get("opptItemDefineCharacter") or {}
        att13 = def_char.get("attrext13")
        if att13:
            try:
                candidates.append(
                    (datetime.strptime(str(att13).split(" ")[0], "%Y-%m-%d").date(), prod_name)
                )
                continue
            except Exception:
                pass
        # 若有週期，嘗試用 define1 + define2 生成
        define1 = body.get("define1")
        cycle = body.get("define2")
        if define1 and cycle:
            try:
                base = datetime.strptime(str(define1).split(" ")[0], "%Y-%m-%d").date()
                months = int(cycle)
                candidates.append((_add_months(base, months), prod_name))
            except Exception:
                pass
    if not candidates:
        return None
    today = date.today()
    future = [c for c in candidates if c[0] >= today]
    if future:
        return min(future, key=lambda x: x[0])
    return min(candidates, key=lambda x: x[0])


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _create_filter_change_task(
    context: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
    customer_id: str,
    customer_name: str,
    sale_area: Optional[str],
    dept_id: str,
    dept_name: str,
    oppt_id: Optional[str],
    oppt_stage: Optional[str],
    amount: Optional[Any],
    next_date: date,
    product_name: str,
) -> None:
    owner_id, owner_name = _task_owner(settings)
    start_date = next_date - timedelta(days=14)
    start_s = start_date.strftime("%Y-%m-%d")
    end_s = start_s
    task_code = "FLT" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid4().hex[:4]
    # 任務內容：優先顯示「更換濾芯」或物料名；若物料名是純數字則寫「更換濾芯」
    content = product_name or "更換濾芯"
    try:
        if str(product_name).isdigit():
            content = "更換濾芯"
    except Exception:
        pass
    payload = {
        "data": {
            "code": task_code,
            "resubmitCheckKey": _short_resubmit("task"),
            "org": settings.sales_org_id,
            "taskTransType": "1587879680409075716",  # 更換濾芯
            "taskTransType_actionTransType": "1587879199387942917",
            "taskTransType_actionTransTypeBustype": "1587877885106454533",
            "bustype": "1587876974596980738",
            "startDate": f"{start_s} 00:00:00",
            "endDate": f"{end_s} 23:59:59",
            "customer": customer_id,
            "customer_name": customer_name,
            "originator": owner_id,
            "originator_name": owner_name,
            "saleArea": sale_area,
            "dept": dept_id,
            "dept_name": dept_name,
            "summary": "",
            "content": content,
            "oppt": oppt_id,
            "opptStage": oppt_stage,
            "ower": owner_id,
            "ower_name": owner_name,
            "systemSource": settings.system_source,
            # 更換濾芯任務不寫金額
            "taskDefineCharacter": {},
            "taskExecutorList": [],
            "taskRemindRuleList": [
                {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
            ],
            "_status": "Insert",
        }
    }
    # 更換濾芯任務不寫金額
    # 執行人：003 + 005
    for ex in (
        {"id": "1482551268133044232", "name": "客服003"},
        {"id": "1655434173036888070", "name": "維修幫005"},
    ):
        payload["data"]["taskExecutorList"].append(
            {
                "executor": ex["id"],
                "executor_name": ex["name"],
                "executeStatus": "0",
                "reformStatus": "0",
                "acceptStatus": "0",
                "isUnlock": "0",
                "startDate": f"{start_s} 00:00:00",
                "endDate": f"{end_s} 23:59:59",
                "excutorDefineCharacter": {},
                "_status": "Insert",
            }
        )
    print(f"[task] payload {json.dumps(payload, ensure_ascii=False)}", flush=True)
    resp = client.create_task(payload)
    print(f"[task] response {json.dumps(resp, ensure_ascii=False)}", flush=True)
    if str(resp.get("code")) not in {"200", "00000"}:
        raise RuntimeError(f"task save failed: {resp}")


def _create_renew_task(
    context: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
    customer_id: str,
    customer_name: str,
    sale_area: Optional[str],
    dept_id: str,
    dept_name: str,
    oppt_id: Optional[str],
    oppt_stage: Optional[str],
    amount: Optional[Any],
    create_data: Dict[str, Any],
) -> None:
    owner_id, owner_name = _task_owner(settings)
    raw_end = (
        context.get("contractEndDate")
        or create_data.get("contractEndDate")
        or create_data.get("contractEnd")
        or (context.get("headDef") or {}).get("define18")
    )
    if not raw_end:
        return
    try:
        end_date_obj = datetime.strptime(str(raw_end).split(" ")[0], "%Y-%m-%d").date()
    except Exception:
        return
    start_date = end_date_obj - timedelta(days=14)
    start_s = start_date.strftime("%Y-%m-%d")
    task_code = "TREN" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid4().hex[:4]
    # 任務內容固定寫「續約」，不帶原文
    content = "續約"
    payload = {
        "data": {
            "code": task_code,
        "resubmitCheckKey": _short_resubmit("task"),
            "org": settings.sales_org_id,
            "taskTransType": "1984155413509046278",  # 續約換芯
            "taskTransType_actionTransType": "1587879199387942917",
            "taskTransType_actionTransTypeBustype": "1587877885106454533",
            "bustype": "1984154477184679941",
            "startDate": f"{start_s} 00:00:00",
            "endDate": f"{start_s} 23:59:59",
            "customer": customer_id,
            "customer_name": customer_name,
            "originator": owner_id,
            "originator_name": owner_name,
            "saleArea": sale_area,
            "dept": dept_id,
            "dept_name": dept_name,
            "summary": "",
            "content": content,
            "oppt": oppt_id,
            "opptStage": oppt_stage,
            "ower": owner_id,
            "ower_name": owner_name,
            "systemSource": settings.system_source,
            "taskDefineCharacter": {},
            "taskExecutorList": [],
            "taskRemindRuleList": [
                {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
            ],
            "_status": "Insert",
        }
    }
    # 續約換芯任務不寫金額
    # 執行人：003 + 005
    for ex in (
        {"id": "1482551268133044232", "name": "客服003"},
        {"id": "1655434173036888070", "name": "維修幫005"},
    ):
        payload["data"]["taskExecutorList"].append(
            {
                "executor": ex["id"],
                "executor_name": ex["name"],
                "executeStatus": "0",
                "reformStatus": "0",
                "acceptStatus": "0",
                "isUnlock": "0",
                "startDate": f"{start_s} 00:00:00",
                "endDate": f"{start_s} 23:59:59",
                "excutorDefineCharacter": {},
                "_status": "Insert",
            }
        )
    print(f"[task] payload {json.dumps(payload, ensure_ascii=False)}", flush=True)
    resp = client.create_task(payload)
    print(f"[task] response {json.dumps(resp, ensure_ascii=False)}", flush=True)
    if str(resp.get("code")) not in {"200", "00000"}:
        raise RuntimeError(f"task save failed: {resp}")


def create_tasks_for_customer_code(customer_code: str) -> Dict[str, Any]:
    settings = SubmissionSettings()
    owner_id, owner_name = _task_owner(settings)
    client = CRMClient()
    opp_resp = client.get_opportunities(customer_code, page_size=1)
    record_list = (opp_resp.get("data") or {}).get("recordList") or []
    if not record_list:
        raise LookupError(f"找不到客戶 {customer_code} 的商機")
    latest = record_list[0]
    oppt_id = latest.get("id")
    if not oppt_id:
        raise LookupError("商機缺少 ID")
    detail = client.get_opportunity_detail(oppt_id)
    data = detail.get("data") or {}
    head = data.get("headDef") or {}
    customer_id = str(latest.get("customer") or data.get("customer") or "")
    customer_name = latest.get("customer_name") or latest.get("customerName") or data.get("customer_name") or customer_code
    sale_area = latest.get("saleArea") or data.get("saleArea")
    opp_stage = latest.get("opptStage") or data.get("opptStage")
    amount = latest.get("expectSignMoney") or data.get("expectSignMoney")
    monthly_fee = (
        head.get("define10")
        or latest.get("headDef!define10")
        or (data.get("opptDefineCharacter") or {}).get("attrext10")
    )
    payment_label = (latest.get("industry_name") or data.get("industry_name") or "").strip()
    payment_code = str(latest.get("industry") or data.get("industry") or "").strip()
    # 優先使用本地緩存/自訂欄位存的全文（與描述脫鉤），缺少時再改用組合欄位
    raw_text_full = (
        RAW_TEXT_BY_CUSTOMER_CODE.get(customer_code)
        or head.get("define20")
        or data.get("define20")
        or (data.get("opptDefineCharacter") or {}).get("define20")
        or ""
    )
    plan_type = latest.get("description") or latest.get("name") or data.get("description") or ""
    install_loc = latest.get("address") or data.get("address") or ""
    contract_end = data.get("contractEndDate") or data.get("contractEnd") or head.get("define3") or head.get("define18")
    contract_start = data.get("contractBeginDate") or head.get("define2") or head.get("define17") or data.get("opptDate")
    contact_tel = latest.get("contactTel") or data.get("contactTel") or ""
    # 解析日期
    install_date = None
    if contract_start:
        try:
            install_date = str(contract_start).split(" ")[0]
        except Exception:
            install_date = None
    if not install_date:
        install_date = datetime.now().strftime("%Y-%m-%d")
    # 新增項目內容：優先用完整原文；若無，組合所有可用欄位為一段完整文字
    if raw_text_full and str(raw_text_full).strip():
        content_full = str(raw_text_full).strip()
    else:
        content_parts = [
            f"客戶名稱：{customer_name}",
            f"聯繫電話：{contact_tel}",
            f"安裝時間：{install_date}",
            f"方案類型：{plan_type}",
            f"總金額：{amount}",
            f"聯絡地址：{install_loc}",
            f"使用方式：{latest.get('usage') or head.get('define8') or ''}",
            f"付款方式：{latest.get('industry_name') or ''}",
            f"月費金額：{head.get('define10') or ''}",
            f"按金：{latest.get('deposit') or ''}",
            f"預繳金：{latest.get('prepay') or ''}",
        ]
        remark_text = latest.get("remark") or data.get("remark") or ""
        if remark_text:
            content_parts.append(f"備注：{remark_text}")
        content_full = "\n".join([p for p in content_parts if p and str(p).strip().strip(':')])
    results = []

    def _do_create(code_prefix: str, payload: Dict[str, Any]) -> None:
        resp = client.create_task({"data": payload})
        results.append({"type": code_prefix, "resp": resp})

    def _parse_date_only(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        text = str(value).strip().split(" ")[0]
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except Exception:
                continue
        return None

    def _add_months(dt: date, months: int) -> date:
        month = dt.month - 1 + months
        year = dt.year + month // 12
        month = month % 12 + 1
        last_day = calendar.monthrange(year, month)[1]
        day = min(dt.day, last_day)
        return date(year, month, day)

    # 任務1：新增項目（執行人 005+008）
    task_code_new = "TASKNEW" + datetime.now().strftime("%Y%m%d%H%M%S")
    new_payload = {
        "code": task_code_new,
        "resubmitCheckKey": _short_resubmit("task"),
        "org": settings.sales_org_id,
        "taskTransType": "1984155894542237704",
        "taskTransType_actionTransType": "1597134252596527112",
        "taskTransType_actionTransTypeBustype": "1597128428638699526",
        "bustype": "1984154580281720833",
        "startDate": f"{install_date} 00:00:00",
        "endDate": f"{install_date} 23:59:59",
        "customer": customer_id,
        "customer_name": customer_name,
        "originator": owner_id,
        "originator_name": owner_name,
        "saleArea": sale_area,
        "dept": settings.service_dept_id,
        "dept_name": settings.service_dept_name,
        "summary": "",
        "content": content_full,
        "oppt": oppt_id,
        "opptStage": opp_stage,
        "ower": owner_id,
        "ower_name": owner_name,
        "systemSource": settings.system_source,
        "taskDefineCharacter": {"RW01": amount} if amount else {},
        "taskExecutorList": [],
        "taskRemindRuleList": [
            {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
        ],
        "_status": "Insert",
    }
    for ex in (
        {"id": "1655434173036888070", "name": "維修幫005"},
        {"id": "1634618416471998473", "name": "出納008"},
    ):
        new_payload["taskExecutorList"].append(
            {
                "executor": ex["id"],
                "executor_name": ex["name"],
                "executeStatus": "0",
                "reformStatus": "0",
                "acceptStatus": "0",
                "isUnlock": "0",
                "startDate": f"{install_date} 00:00:00",
                "endDate": f"{install_date} 23:59:59",
                "excutorDefineCharacter": {},
                "_status": "Insert",
            }
        )
    _do_create("new", new_payload)

    # 任務：季度收費定期月費（僅付款方式=季度收費時觸發）
    contract_start_date = _parse_date_only(contract_start)
    contract_end_date = _parse_date_only(contract_end)
    is_quarterly = payment_label == "季度收費" or payment_code in {"04", "4", "004"}
    if is_quarterly and contract_start_date and contract_end_date:
        first_date = _add_months(contract_start_date, 3)
        last_date = _add_months(contract_end_date, -3)
        amount_quarter = None
        try:
            if monthly_fee is not None:
                amount_quarter = float(monthly_fee) * 3
        except Exception:
            amount_quarter = None

        current = first_date
        while current <= last_date:
            next_period_end = _add_months(current, 3)
            task_code_q = "TASKQFEE" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid4().hex[:2]
            qfee_payload = {
                "code": task_code_q,
                "resubmitCheckKey": _short_resubmit("task"),
                "org": settings.sales_org_id,
                # 定期月費任務：使用 CRM 既有的「定期月費」類型
                "taskTransType": "1705112066885419012",
                "taskTransType_actionTransType": "1597134252596527112",
                "taskTransType_actionTransTypeBustype": "1597128428638699526",
                "bustype": "1700013665820344329",
                "startDate": f"{current} 00:00:00",
                "endDate": f"{current} 23:59:59",
                "customer": customer_id,
                "customer_name": customer_name,
                "originator": owner_id,
                "originator_name": owner_name,
                "saleArea": sale_area,
                "dept": settings.service_dept_id,
                "dept_name": settings.service_dept_name,
                "summary": "（季度收費）",
                "content": f"{current} — {next_period_end}",
                "oppt": oppt_id,
                "opptStage": opp_stage,
                "ower": owner_id,
                "ower_name": owner_name,
                "systemSource": settings.system_source,
                "taskDefineCharacter": {"RW01": amount_quarter} if amount_quarter is not None else {},
                "taskExecutorList": [],
                "taskRemindRuleList": [
                    {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
                ],
                "_status": "Insert",
            }
            # 執行人只寫 008
            qfee_payload["taskExecutorList"].append(
                {
                    "executor": "1634618416471998473",
                    "executor_name": "出納008",
                    "executeStatus": "0",
                    "reformStatus": "0",
                    "acceptStatus": "0",
                    "isUnlock": "0",
                    "startDate": f"{current} 00:00:00",
                    "endDate": f"{current} 23:59:59",
                    "excutorDefineCharacter": {},
                    "_status": "Insert",
                }
            )
            _do_create("qfee", qfee_payload)
            current = _add_months(current, 3)

    # 任務2：更換濾芯（最近下次換芯日 -14，內容用物料名；執行人 003+005）
    next_info = _find_next_replacement_date(data)
    if next_info:
        next_date, prod_name = next_info
        start_date2 = next_date - timedelta(days=14)
        start_s = start_date2.strftime("%Y-%m-%d")
        task_code_flt = "TASKFLT" + datetime.now().strftime("%Y%m%d%H%M%S")
        flt_payload = {
            "code": task_code_flt,
            "resubmitCheckKey": _short_resubmit("task"),
            "org": settings.sales_org_id,
            "taskTransType": "1587879680409075716",
            "taskTransType_actionTransType": "1587879199387942917",
            "taskTransType_actionTransTypeBustype": "1587877885106454533",
            "bustype": "1587876974596980738",
            "startDate": f"{start_s} 00:00:00",
            "endDate": f"{start_s} 23:59:59",
            "customer": customer_id,
            "customer_name": customer_name,
            "originator": owner_id,
            "originator_name": owner_name,
            "saleArea": sale_area,
            "dept": settings.service_dept_id,
            "dept_name": settings.service_dept_name,
            "summary": "",
            # 任務內容：使用匹配到的物料全名；若缺失則寫更換濾芯
            "content": prod_name or "更換濾芯",
            "oppt": oppt_id,
            "opptStage": opp_stage,
            "ower": owner_id,
            "ower_name": owner_name,
            "systemSource": settings.system_source,
            "taskDefineCharacter": {},
            "taskExecutorList": [],
            "taskRemindRuleList": [
                {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
            ],
            "_status": "Insert",
        }
        for ex in (
            {"id": "1482551268133044232", "name": "客服003"},
            {"id": "1655434173036888070", "name": "維修幫005"},
        ):
            flt_payload["taskExecutorList"].append(
                {
                    "executor": ex["id"],
                    "executor_name": ex["name"],
                    "executeStatus": "0",
                    "reformStatus": "0",
                    "acceptStatus": "0",
                    "isUnlock": "0",
                    "startDate": f"{start_s} 00:00:00",
                    "endDate": f"{start_s} 23:59:59",
                    "excutorDefineCharacter": {},
                    "_status": "Insert",
                }
            )
        _do_create("flt", flt_payload)

    # 任務3：續約換芯（合約到期日 -14，內容「續約」，執行人 003+005+008）
    if contract_end:
        try:
            end_dt = datetime.strptime(str(contract_end).split(" ")[0], "%Y-%m-%d").date()
            renew_start = end_dt - timedelta(days=14)
            renew_s = renew_start.strftime("%Y-%m-%d")
            task_code_ren = "TASKREN" + datetime.now().strftime("%Y%m%d%H%M%S")
            ren_payload = {
                "code": task_code_ren,
                "resubmitCheckKey": _short_resubmit("task"),
                "org": settings.sales_org_id,
                "taskTransType": "1984155413509046278",
                "taskTransType_actionTransType": "1587879199387942917",
                "taskTransType_actionTransTypeBustype": "1587877885106454533",
                "bustype": "1984154477184679941",
                "startDate": f"{renew_s} 00:00:00",
                "endDate": f"{renew_s} 23:59:59",
                "customer": customer_id,
                "customer_name": customer_name,
                "originator": owner_id,
                "originator_name": owner_name,
                "saleArea": sale_area,
                "dept": settings.service_dept_id,
                "dept_name": settings.service_dept_name,
                "summary": "",
                "content": "續約",
                "oppt": oppt_id,
                "opptStage": opp_stage,
                "ower": owner_id,
                "ower_name": owner_name,
                "systemSource": settings.system_source,
                "taskDefineCharacter": {},
                "taskExecutorList": [],
                "taskRemindRuleList": [
                    {"remindPoint": "0", "advanceTime": "0", "timeUnit": "0", "_status": "Insert"}
                ],
                "_status": "Insert",
            }
            for ex in (
                {"id": "1482551268133044232", "name": "客服003"},
                {"id": "1655434173036888070", "name": "維修幫005"},
                {"id": "1634618416471998473", "name": "出納008"},
            ):
                ren_payload["taskExecutorList"].append(
                    {
                        "executor": ex["id"],
                        "executor_name": ex["name"],
                        "executeStatus": "0",
                        "reformStatus": "0",
                        "acceptStatus": "0",
                        "isUnlock": "0",
                        "startDate": f"{renew_s} 00:00:00",
                        "endDate": f"{renew_s} 23:59:59",
                        "excutorDefineCharacter": {},
                        "_status": "Insert",
                    }
                )
            _do_create("ren", ren_payload)
        except Exception as e:
            print(f"[task] 創建續約任務失敗: {e}", flush=True)
            import traceback
            traceback.print_exc()

    return {"message": "tasks created", "responses": results}


def _create_opportunity_for_customer(
    normalized: Dict[str, Any],
    settings: SubmissionSettings,
    application_response: Dict[str, Any],
    *,
    audit_passed: bool,
    client: CRMClient,
) -> Dict[str, Any]:
    context = dict(normalized.get("opportunityContext") or {})
    if not context:
        return {"skipped": True, "reason": "未提供商機欄位"}
    if not audit_passed:
        return {"skipped": True, "reason": "客戶尚未審核通過，暫不建立商機"}
    customer_id = (
        _extract_created_customer_id(application_response)
        or context.get("customerId")
        or _extract_customer_entity_id(application_response)
        or _lookup_customer_id_by_code(
            context.get("customerCode") or normalized.get("customerCode"), client
        )
    )
    if not customer_id:
        return {"skipped": True, "reason": "CRM 回傳缺少客戶 ID，無法建立商機"}
    context.setdefault("customerId", customer_id)
    context.setdefault(
        "customerName",
        context.get("customerName")
        or normalized.get("displayName")
        or normalized.get("baseName"),
    )
    context.setdefault(
        "customerCode",
        context.get("customerCode") or normalized.get("customerCode"),
    )
    
    # 調試日誌：確認 context 已正確設置
    print(f"[opportunity] Context設置完成 - customerId: {context.get('customerId')}", flush=True)
    print(f"[opportunity] Context設置完成 - customerName: {context.get('customerName')}", flush=True)
    print(f"[opportunity] Context設置完成 - customerCode: {context.get('customerCode')}", flush=True)
    context.setdefault(
        "installLocation", context.get("installLocation") or normalized.get("address")
    )
    context.setdefault(
        "usageLabel",
        context.get("usageLabel")
        or normalized.get("usageLabel")
        or (normalized.get("usageMode") or {}).get("label"),
    )
    context.setdefault(
        "planType", context.get("planType") or normalized.get("installContent")
    )
    context.setdefault("rawText", normalized.get("_raw_text"))
    context.setdefault(
        "monthlyFee", context.get("monthlyFee") or normalized.get("monthlyFee")
    )
    context.setdefault("deposit", context.get("deposit") or normalized.get("deposit"))
    context.setdefault("prepay", context.get("prepay") or normalized.get("prepay"))
    context.setdefault(
        "saleAreaId",
        context.get("saleAreaId") or (normalized.get("saleArea") or {}).get("id"),
    )
    context.setdefault(
        "contactTel", context.get("contactTel") or normalized.get("contactTel")
    )
    owner_block = normalized.get("owner") or {}
    # 保存使用者輸入的銷售信息（原始文本）
    context.setdefault("ownerHint", context.get("ownerHint") or normalized.get("ownerHint"))
    context.setdefault("ownerId", context.get("ownerId") or owner_block.get("id"))
    context.setdefault(
        "ownerName", context.get("ownerName") or owner_block.get("name")
    )
    # 銷售白名單：liz/LIZ、james/James/成/寧，其他統一為客服003
    # 根據名字映射到對應的 ID 和顯示名稱
    owner_mapping = {
        "liz": {"id": settings.owner_liz_id, "name": "LIZ"},
        "james": {"id": settings.owner_james_id, "name": "James"},
        "成": {"id": settings.owner_liang_id, "name": "成"},
        "寧": {"id": settings.owner_james_id, "name": "寧"},
    }
    
    owner_hint_lower = (context.get("ownerHint") or "").strip().lower()
    
    if owner_hint_lower in owner_mapping:
        # 在白名單中，使用對應的 owner 信息
        context["ownerId"] = owner_mapping[owner_hint_lower]["id"]
        context["ownerName"] = owner_mapping[owner_hint_lower]["name"]
    else:
        # 非白名單（包含空白），統一使用客服003
        context["ownerId"] = settings.service_owner_id
        context["ownerName"] = settings.service_owner_name
    context.setdefault("itemHint", context.get("itemHint") or {})
    context.setdefault("winningRate", context.get("winningRate") or "0")
    if not context.get("name"):
        context["name"] = f"{context.get('customerName', '商機')} - {context.get('planType', '方案')}"
    if not context.get("expectSignMoney"):
        monthly_value = context.get("monthlyFee")
        years_value = context.get("contractYears")
        if not years_value:
            years_value = _determine_contract_years(context.get("planType") or "", settings)
            context["contractYears"] = years_value
        if monthly_value and years_value:
            context["expectSignMoney"] = float(monthly_value) * int(years_value) * 12.0
    else:
        context.setdefault(
            "contractYears",
            context.get("contractYears")
            or _determine_contract_years(context.get("planType") or "", settings),
        )
    if not context.get("contractStartDate"):
        install_time = (normalized.get("installTime") or {}).get("display")
        if install_time:
            context["contractStartDate"] = install_time.split(" ")[0]
    if context.get("contractStartDate") and not context.get("contractEndDate"):
        years_value = context.get("contractYears")
        if years_value:
            start = _parse_date(context["contractStartDate"])
            if start:
                context["contractEndDate"] = _date_to_string(
                    _add_years(start, int(years_value))
                )
    if not context.get("expectSignDate"):
        context["expectSignDate"] = (
            context.get("contractStartDate")
            or context.get("opportunityDate")
            or datetime.now().strftime("%Y-%m-%d")
        )
    if not context.get("opportunityDate"):
        context["opportunityDate"] = context.get("expectSignDate")
    context.setdefault("currency", context.get("currency") or settings.opportunity_currency)
    if not context.get("contactMethod"):
        context["contactMethod"] = context.get("contactTel")
    context.setdefault(
        "stageHint",
        context.get("stageHint")
        or normalized.get("opportunityStage")
        or settings.opportunity_stage_default
        or settings.opportunity_stage_rent
        or settings.opportunity_stage_buy,
    )
    context.setdefault(
        "transTypeHint",
        context.get("transTypeHint")
        or context.get("transactionType")
        or normalized.get("transactionType"),
    )

    # 付款兜底：若缺 paymentCode 但方案類型是純數字碼，視為付款方式代碼
    if not context.get("paymentCode"):
        plan_raw = (context.get("planType") or "").strip()
        if re.fullmatch(r"\d{2,3}", plan_raw):
            context["paymentCode"] = plan_raw

    # 安裝位置兜底：若像「C碼+姓名+電話」且有地址，用客戶地址替換
    if context.get("installLocation") and re.search(r"C\d+.+\d{6,}", str(context["installLocation"])):
        addr = normalized.get("address") or context.get("address")
        if addr:
            context["installLocation"] = addr
    duplicate_request = _build_opportunity_duplicate_request(context, settings)
    skip_duplicate_check = False
    try:
        duplicate_response = client.check_opportunity_repeat(**duplicate_request)
        duplicates_payload = duplicate_response.get("data")
        if isinstance(duplicates_payload, dict):
            duplicates = (
                duplicates_payload.get("recordList")
                or duplicates_payload.get("data")
                or []
            )
        else:
            duplicates = duplicates_payload or []
    except RuntimeError as exc:
        message = str(exc)
        if _is_duplicate_rule_missing_error(message):
            skip_duplicate_check = True
            duplicate_response = {"error": message, "skipRule": True}
            duplicates = []
        else:
            return {
                "skipped": True,
                "reason": f"商機查重失敗：{message}",
                "duplicateResponse": {"error": message},
                "context": context,
            }
    result: Dict[str, Any] = {
        "duplicateResponse": duplicate_response,
        "context": context,
    }
    if not skip_duplicate_check and duplicates:
        result["skipped"] = True
        result["reason"] = "商機查重已存在記錄，未重新建立。"
        result["duplicates"] = duplicates
        return result
    payload = _build_opportunity_create_payload(context, normalized, settings, client)
    try:
        create_response = client.create_opportunity(payload)
    except RuntimeError as exc:
        result["createResponse"] = {"error": str(exc)}
        result["skipped"] = False
        result["success"] = False
        result["reason"] = str(exc)
        return result
    result["createResponse"] = create_response
    result["success"] = str(create_response.get("code")) in {"200", "00000"}
    if not result["success"]:
        result["reason"] = create_response.get("message")
    result["skipped"] = False
    if result["success"]:
        # 註釋掉自動創建任務，改為手動創建（通過前端"新增任務"按鈕觸發）
        # try:
        #     _auto_create_tasks_for_opportunity(context, create_response, settings, client)
        # except Exception as exc:
        #     print(f"[task] auto-create error: {exc}", flush=True)
        pass
    return result


def run_submission(text: str, *, skip_audit: bool = False, **kwargs) -> Dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("請提供銷售文案內容。")

    # 先初始化 settings，後續代碼需要使用
    settings = SubmissionSettings()

    parse_result = parse_customer_text(text)
    normalized = parse_result["normalized"]
    normalized["_raw_text"] = text
    if normalized.get("customerCode"):
        RAW_TEXT_BY_CUSTOMER_CODE[normalized["customerCode"]] = text
    warnings = list(parse_result.get("warnings") or [])
    
    if parse_opportunity_text:
        try:
            opportunity_info = parse_opportunity_text(text, normalized)
            opportunity_context = opportunity_info.get("context") or {}
            if opportunity_context:
                normalized["opportunityContext"] = opportunity_context
            opp_warnings = opportunity_info.get("warnings") or []
            for message in opp_warnings:
                if message:
                    warnings.append(f"商機：{message}")
        except Exception as exc:
            warnings.append(f"商機欄位解析失敗：{exc}")
    
    # 處理額外欄位，如 payway
    payway_value = None
    payway_source = None
    
    # 從 kwargs 提取支付方式，優先使用 payment_method，其次是 payway
    payment_code = (
        kwargs.get("payment_method") or
        kwargs.get("payway") or
        normalized.get("paymentMethod", {}).get("value") or
        ""
    ).strip()

    # 移除硬編碼映射，支付方式將由 _format_payment_display 函數動態處理
    payment_description = payment_code  # 直接使用原始代碼，避免硬編碼映射
    
    if payment_code:
        payway_code = _sanitize_payment_code(str(payment_code))
        normalized["paymentMethod"] = {"id": payway_code, "label": payment_description}
        
        # 同時設置 customerIndustry.name 欄位，用於存儲支付方式描述
        # 根據用戶反饋，這是訂製的存儲位置
        # 使用相同的 industry ID (1580721825339932673) 但存儲不同的支付方式名稱
        # CRM系統只認可這個ID，所以我們用 name 字段來區分支付方式
        normalized["customerIndustry"] = {
            "id": settings.customer_industry_id,  # 始終使用相同的ID
            "name": payment_description,  # 用 name 字段存儲支付方式描述
            "label": payment_description
        }
    
    client = CRMClient()

    duplicate_payload = build_duplicate_payload(normalized, settings)
    try:
        duplicate_response = client.customer_duplicate_check(duplicate_payload)
        duplicates = duplicate_response.get("data") or []
    except RuntimeError as exc:
        duplicate_response = {"error": str(exc)}
        duplicates = []

    result: Dict[str, Any] = {
        "duplicateResponse": duplicate_response,
        "submitted": False,
        "applicationResponse": None,
        "auditResponse": None,
        "warnings": warnings,
        "opportunityContext": normalized.get("opportunityContext"),
    }

    if duplicates:
        result["message"] = "發現重複客戶，已停止送出。"
        return result

    def _do_submit(payload_data: Dict[str, Any]) -> Dict[str, Any]:
        return client.submit_customer_application(payload_data)

    apply_payload = build_apply_payload(normalized, settings)
    try:
        application_response = _do_submit(apply_payload)
    except RuntimeError as exc:
        message = str(exc)
        if _is_pending_application_error(message):
            regenerated_code = _apply_new_customer_code(normalized)
            if regenerated_code:
                warnings.append(
                    f"原客戶編碼因 CRM 待審申請被鎖定，已改為 {regenerated_code} 後重新送出。"
                )
                duplicate_payload = build_duplicate_payload(normalized, settings)
                try:
                    duplicate_response = client.customer_duplicate_check(
                        duplicate_payload
                    )
                    duplicates = duplicate_response.get("data") or []
                except RuntimeError as dup_exc:
                    duplicate_response = {"error": str(dup_exc)}
                    duplicates = []
                result["duplicateResponse"] = duplicate_response
                if duplicates:
                    result["message"] = "發現重複客戶，已停止送出。"
                    return result
                apply_payload = build_apply_payload(normalized, settings)
                try:
                    application_response = _do_submit(apply_payload)
                except RuntimeError as retry_exc:
                    retry_message = str(retry_exc)
                    result["applicationResponse"] = {
                        "error": retry_message,
                        "codeRetry": True,
                    }
                    result["message"] = retry_message
                    return result
            else:
                result["applicationResponse"] = {"error": message}
                result["message"] = message
                return result
        elif _is_payment_pending_error(message):
            warnings.append(
                "CRM 回報付款方式欄位待審，已改用原始中文描述回填 customerIndustry。"
            )
            normalized.pop("customerIndustry", None)
            normalized.pop("paymentMethod", None)
            apply_payload = build_apply_payload(normalized, settings)
            try:
                application_response = _do_submit(apply_payload)
            except RuntimeError as retry_pending:
                pending_message = str(retry_pending)
                result["applicationResponse"] = {"error": pending_message}
                result["message"] = pending_message
                return result
        else:
            result["applicationResponse"] = {"error": message}
            result["message"] = message
            return result

    result["submitted"] = True
    result["applicationResponse"] = application_response
    response_code = str(application_response.get("code") or "")
    if response_code not in {"200", "00000"}:
        result["message"] = application_response.get("message") or "客戶申請提交失敗"
        return result

    audit_response: Dict[str, Any]
    audit_success = False
    if skip_audit:
        audit_response = {"skipped": True}
        audit_success = True
    else:
        application_id = application_response.get("data", {}).get(
            "id"
        ) or application_response.get("data", {}).get("newBizObject", {}).get("id")
        if not application_id:
            audit_response = {"skipped": True, "reason": "未取得申請ID"}
            result["message"] = "已送出申請，但取不到申請單 ID，請到 CRM 後台確認。"
        else:
            audit_payload = build_audit_payload(str(application_id), settings)
            try:
                audit_response = client.audit_customer_application(audit_payload)
            except RuntimeError as exc:
                audit_response = {"error": str(exc)}
        audit_success = _is_success_response(audit_response)
        if isinstance(audit_response, dict) and audit_response.get("error"):
            result["message"] = audit_response["error"]
    result["auditResponse"] = audit_response
    customer_entity_id = _extract_customer_entity_id(application_response)
    if customer_entity_id:
        normalized["customerId"] = customer_entity_id
        context_ref = normalized.get("opportunityContext")
        if isinstance(context_ref, dict):
            context_ref.setdefault("customerId", customer_entity_id)
    try:
        session_token = _remember_opportunity_session(normalized, application_response)
        result["opportunitySession"] = {
            "token": session_token,
            "expiresIn": SESSION_TTL_SECONDS,
        }
    except Exception:
        pass

    if settings.create_opportunity:
        opportunity_response = _create_opportunity_for_customer(
            normalized,
            settings,
            application_response,
            audit_passed=audit_success,
            client=client,
        )
        result["opportunityResponse"] = opportunity_response
        if isinstance(opportunity_response, dict) and opportunity_response.get("context"):
            result["opportunityContext"] = opportunity_response["context"]

    return result


__all__ = [
    "SubmissionSettings",
    "build_duplicate_payload",
    "build_apply_payload",
    "build_audit_payload",
    "run_submission",
    "create_opportunity_from_session",
]
