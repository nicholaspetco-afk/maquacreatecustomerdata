"""Standalone MAQUA membership service."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數
load_dotenv()

from services import config
from services.crm_client import CRMClient

try:  # pragma: no cover - optional import
    from services.customer_submission import (
        run_submission as submit_customer_workflow,
        create_opportunity_from_session,
    )
except Exception:  # pragma: no cover
    submit_customer_workflow = None
    create_opportunity_from_session = None

CRM_CLIENT = CRMClient()
app = Flask(__name__)

PAYMENT_METHOD_LABELS: Dict[int, str] = {
    0: "未設定",
    1: "一次性全繳",
    2: "信用卡分期",
    3: "銀行卡自動轉賬",
    4: "季度收費",
    5: "年度收費",
    6: "試用",
    7: "每月收費",
    90: "銀行轉帳",
    97: "信用卡",
    98: "信用卡",
    99: "信用卡分期",
}

PAYMENT_KEYWORD_LABELS: Sequence[tuple[str, str]] = (
    ("分期", "信用卡分期"),
    ("信用卡", "信用卡"),
    ("現金", "現金"),
    ("轉帳", "銀行轉帳"),
    ("轉賬", "銀行轉帳"),
    ("轉帐", "銀行轉帳"),
    ("轉款", "銀行轉帳"),
    ("轉帳", "銀行轉帳"),
    ("匯款", "銀行匯款"),
    ("匯數", "銀行轉帳"),
    ("支票", "支票"),
    ("扣款", "自動扣款"),
    ("自動轉賬", "自動扣款"),
    ("銀行扣賬", "自動扣款"),
    ("中銀信用卡", "信用卡分期"),
    ("中銀卡", "信用卡分期"),
    ("免息分期", "信用卡分期"),
    ("中銀", "信用卡分期"),
)

STANDARD_DATE_RE = re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})")
CJK_DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})[日號]?")
CODE_TOKEN_RE = re.compile(r"\bC\d{2,}\b", re.IGNORECASE)
TASK_OWNER_KEYWORD = getattr(config, "MAINTENANCE_TASK_OWNER_KEYWORD", "客服003")


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/profile", methods=["POST"])
def profile_api():
    payload = request.get_json(silent=True) or {}
    identifier = str(payload.get("identifier", "")).strip()
    if not identifier:
        return jsonify({"message": "請輸入客戶編碼或電話"}), 400

    try:
        profile = _build_member_profile(identifier)
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 404
    except Exception:  # pragma: no cover
        app.logger.exception("Failed to build member profile")
        return jsonify({"message": "查詢時發生錯誤，請稍後再試。"}), 500

    return jsonify({"code": "OK", "profile": profile})


@app.route("/api/customers/import", methods=["POST"])
def import_customer_api():
    if submit_customer_workflow is None:
        return jsonify({"message": "提交模組尚未啟用"}), 503

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    skip_audit = bool(payload.get("skipAudit") or payload.get("skip_audit"))
    if not text:
        return jsonify({"message": "請貼上銷售文案"}), 400

    try:
        # 傳遞額外欄位給提交函數，過濾掉已經使用的欄位
        extra_fields = {k: v for k, v in payload.items() if k not in ["text", "skipAudit", "skip_audit"]}
        result = submit_customer_workflow(text, skip_audit=skip_audit, **extra_fields)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except RuntimeError as exc:  # pragma: no cover - network failures
        app.logger.exception("Customer submission failed")
        return jsonify({"message": "送出失敗，請稍後再試。", "details": str(exc)}), 502

    return jsonify({"code": "OK", "result": result})


@app.route("/api/opportunities/create", methods=["POST"])
def create_opportunity_api():
    if create_opportunity_from_session is None:
        return jsonify({"message": "商機模組尚未啟用"}), 503
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", "")).strip()
    if not token:
        return jsonify({"message": "缺少商機識別碼"}), 400
    try:
        result = create_opportunity_from_session(token)
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except RuntimeError as exc:
        app.logger.exception("Opportunity creation failed")
        return jsonify({"message": "商機建立失敗，請稍後重試。", "details": str(exc)}), 502
    return jsonify({"code": "OK", "result": result})


@app.route("/api/tasks/create", methods=["POST"])
def create_tasks_api():
    try:
        from services.customer_submission import create_tasks_for_customer_code
    except Exception:
        return jsonify({"message": "任務模組尚未啟用"}), 503

    payload = request.get_json(silent=True) or {}
    customer_code = str(payload.get("customerCode") or payload.get("code") or "").strip()
    if not customer_code:
        return jsonify({"message": "請提供客戶編碼"}), 400
    try:
        result = create_tasks_for_customer_code(customer_code)
        return jsonify({"code": "OK", "result": result})
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 404
    except RuntimeError as exc:
        app.logger.exception("Task creation failed")
        return jsonify({"message": "建立任務失敗", "details": str(exc)}), 502
    except Exception as exc:
        app.logger.exception("Task creation failed")
        return jsonify({"message": "建立任務失敗", "details": str(exc)}), 502


def _build_member_profile(identifier: str) -> Dict[str, Optional[str]]:
    normalized_identifier = str(identifier or "").strip()
    if not normalized_identifier:
        raise LookupError("請輸入查詢關鍵字")

    search_kwargs: Dict[str, Any] = {}
    expected_code: Optional[str] = None

    phone_mode = _looks_like_phone(normalized_identifier)
    if phone_mode:
        search_kwargs = {"search_field": "customer.name", "search_operator": "like"}
    else:
        expected_code = normalized_identifier.upper()

    followups = CRM_CLIENT.get_followups(
        normalized_identifier,
        page=1,
        page_size=20,
        **search_kwargs,
    )
    record_list: List[Dict[str, Any]] = (
        followups.get("data", {}).get("recordList", []) or []
    )

    if phone_mode and not record_list:
        fallback_fields: Sequence[Tuple[str, str]] = (
            ("customer_name", "like"),
            ("customer.name", "eq"),
            ("customer.code", "eq"),
            ("contactTel", "eq"),
            ("contactMobile", "eq"),
            ("customer.contactMobile", "eq"),
            ("customer.mobile", "eq"),
        )
        tried: Set[Tuple[str, str]] = {
            (
                search_kwargs.get("search_field") or "",
                search_kwargs.get("search_operator") or "",
            )
        }
        for field, operator in fallback_fields:
            key = (field, operator)
            if key in tried:
                continue
            tried.add(key)
            try:
                resp = CRM_CLIENT.get_followups(
                    normalized_identifier,
                    page=1,
                    page_size=20,
                    search_field=field,
                    search_operator=operator,
                )
            except Exception as exc:  # pragma: no cover - logging only
                app.logger.debug(
                    "Phone fallback search failed for %s (%s %s): %s",
                    normalized_identifier,
                    field,
                    operator,
                    exc,
                )
                continue
            record_list = resp.get("data", {}).get("recordList", []) or []
            if record_list:
                break

    detail_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
    resolved_code: Optional[str] = None
    suggestions: List[str] = []

    if expected_code:
        record_list, resolved_code, suggestions = _filter_records_for_code(
            record_list,
            expected_code,
            detail_cache,
        )
        if not record_list:
            if suggestions:
                hint = "、".join(suggestions[:5])
                raise LookupError(
                    f"找不到對應的客戶編碼，可能是：{hint}，請輸入完整的編碼。"
                )
            raise LookupError("找不到對應的客戶編碼，請輸入完整的編碼。")

    maintenance_records = [
        item for item in record_list if "維修幫" in str(item.get("ower_name") or "")
    ]
    
    # 如果找不到維修幫記錄，但仍然嘗試獲取付款方式信息
    if not maintenance_records:
        app.logger.info("DEBUG: 找不到維修幫的保養紀錄，但將嘗試獲取付款方式信息")
        # 使用第一條記錄來獲取客戶信息
        if record_list:
            latest_record = record_list[0]  # 使用任意一條記錄
            app.logger.info(f"DEBUG: 使用替代記錄獲取客戶信息: {latest_record.get('customer_name', '未知')}")
        else:
            # 如果完全沒有記錄，仍然嘗試從detail_data獲取付款方式
            latest_record = {}
            app.logger.info("DEBUG: 完全沒有記錄，將嘗試從客戶詳情獲取付款方式")
    else:
        latest_record = maintenance_records[0]  # 正常使用維修幫記錄

    followup_data = {"data": {"recordList": record_list}}
    target_code = (resolved_code or expected_code or normalized_identifier).upper()

    task_records: List[Dict[str, Any]] = []
    task_page_size = getattr(
        config, "DEFAULT_TASK_PAGE_SIZE", getattr(config, "DEFAULT_PAGE_SIZE", 20)
    )
    if target_code:
        try:
            tasks_resp = CRM_CLIENT.get_tasks(
                target_code, page=1, page_size=task_page_size
            )
            task_records = tasks_resp.get("data", {}).get("recordList", []) or []
        except Exception as exc:  # pragma: no cover - runtime logging
            app.logger.warning("Failed to fetch tasks for %s: %s", target_code, exc)

    summary = _extract_maintenance_summary(
        target_code,
        followup_data,
        task_records,
    )
    latest_service_date = summary.get("latestServiceDate")
    next_service_date = summary.get("nextServiceDate")
    if next_service_date:
        parsed_next = _parse_follow_date(next_service_date)
        if parsed_next:
            next_service_date = (parsed_next + timedelta(days=14)).isoformat()
    payment_status = _resolve_payment_status(record_list)
    resolved_code = summary.get("customerCode") or target_code

    latest_record = _find_record_by_date(maintenance_records, latest_service_date)
    if not latest_record:
        latest_record = _select_latest_service_record(maintenance_records)
        if latest_record and not latest_service_date:
            latest_service_date = _format_follow_date(latest_record.get("followTime"))

    if not latest_record:
        app.logger.info("DEBUG: 找不到符合條件的保養紀錄，但將繼續嘗試獲取付款方式信息")
        # 創建一個空的記錄對象，以便後續代碼可以正常執行
        latest_record = {}

    customer_id = str(latest_record.get("customer") or "")
    org_id = str(latest_record.get("org") or "")

    detail_data: Dict[str, Any] = {}
    addresses: List[Dict[str, Any]] = []
    if customer_id and org_id:
        detail_data = _get_detail_data(customer_id, org_id, detail_cache)
    else:
        detail_data = {}

    if detail_data:
        addresses = detail_data.get("merchantAddressInfos") or []
        if (not addresses) and detail_data.get("code"):
            addr_resp = CRM_CLIENT.get_addresses_by_codes([detail_data["code"]])
            addresses = addr_resp.get("data") or []

    selected_address = None
    if isinstance(addresses, list) and addresses:
        for item in addresses:
            if item.get("isDefault"):
                selected_address = item
                break
        if not selected_address:
            selected_address = addresses[0]

    address_text = None
    contact_name = None
    contact_phone = None

    if isinstance(selected_address, dict):
        address_text = _resolve_text(
            selected_address.get("mergerName")
            or selected_address.get("address")
            or selected_address.get("addressInfo")
        )
        contact_name = selected_address.get("receiver")
        contact_phone = selected_address.get("mobile") or selected_address.get(
            "telePhone"
        )

    if not address_text:
        address_text = _resolve_text(detail_data.get("address"))
    if not contact_name:
        contact_name = detail_data.get("contactName")
    if not contact_phone:
        contact_phone = detail_data.get("contactTel") or detail_data.get(
            "contactMobile"
        )

    recent_follow_info = _extract_recent_follow_info(detail_data)
    merchant_detail = (
        detail_data.get("merchantAppliedDetail")
        if isinstance(detail_data, dict)
        else {}
    )
    if not isinstance(merchant_detail, dict):
        merchant_detail = {}
    contract_number = _first_non_empty(
        detail_data.get("contractNumber"),
        merchant_detail.get("contractNumber"),
        merchant_detail.get("contractNo"),
        merchant_detail.get("contractCode"),
        merchant_detail.get("merchantApplyRangeId"),
        merchant_detail.get("id"),
        (
            (detail_data.get("merchantDefine") or {}).get("define1")
            if isinstance(detail_data.get("merchantDefine"), dict)
            else None
        ),
        (
            (detail_data.get("merchantCharacter") or {}).get("attrext21")
            if isinstance(detail_data.get("merchantCharacter"), dict)
            else None
        ),
        recent_follow_info.get("合約編號"),
        recent_follow_info.get("合同編號"),
        recent_follow_info.get("合同號"),
        recent_follow_info.get("合約號"),
    )
    usage = _first_non_empty(
        detail_data.get("largeText1"),
        detail_data.get("usage"),
        recent_follow_info.get("使用方式"),
    )
    plan_type = _first_non_empty(
        detail_data.get("largeText2"),
        recent_follow_info.get("設備"),
    )
    if (not plan_type) and recent_follow_info.get("內容"):
        content_text = _clean_text(recent_follow_info.get("內容"))
        if content_text and not _seems_like_schedule_text(content_text):
            plan_type = content_text
    monthly_fee = _first_non_empty(
        detail_data.get("largeText3"),
        recent_follow_info.get("月費"),
        recent_follow_info.get("金額"),
    )
    detected_payment_value = _detect_payment_method(detail_data, recent_follow_info)
    payment_method_code = _normalize_payment_code(detected_payment_value)
    payment_method = (
        payment_method_code
        if payment_method_code
        else (detected_payment_value if isinstance(detected_payment_value, str) else None)
    )
    app.logger.info(
        "DEBUG: payment_value='%s' -> code='%s' -> label='%s'",
        detected_payment_value,
        payment_method_code,
        payment_method,
    )
    customer_industry_name = _extract_customer_industry_name(
        detail_data, latest_record, recent_follow_info
    )
    if (not payment_method) and customer_industry_name:
        fallback_payment = _label_for_payway(customer_industry_name)
        if fallback_payment:
            app.logger.info(
                "DEBUG: payment_method fallback from customerIndustry '%s' -> '%s'",
                customer_industry_name,
                fallback_payment,
            )
            payment_method = fallback_payment
            if not payment_method_code:
                payment_method_code = _normalize_payment_code(fallback_payment)

    candidate_codes = _candidate_codes(latest_record, detail_cache)
    resolved_code = _first_non_empty(
        detail_data.get("code"),
        resolved_code,
        candidate_codes[0] if candidate_codes else None,
    )

    plans: List[Dict[str, Any]] = []
    if resolved_code:
        plans = _build_opportunity_plans(
            resolved_code, latest_record, detail_data, record_list
        )
        if plans:
            summary_names = [
                (plan.get("summary") or plan.get("title") or "").strip()
                for plan in plans
            ]
            summary_names = [name for name in summary_names if name]
            if summary_names:
                plan_type = " / ".join(summary_names)
            if not contract_number:
                for plan in plans:
                    if plan.get("contractNumber"):
                        contract_number = plan["contractNumber"]
                        break
            # 若 detail_data 缺值時，再從 plans 中補付費方式
            plans_payment_method = None
            for plan in plans:
                if plan.get("paymentMethod"):
                    plans_payment_method = plan["paymentMethod"]
                    break
            
            if plans_payment_method and not payment_method:
                app.logger.info(
                    "DEBUG: payment_method missing, using plans value '%s'",
                    plans_payment_method,
                )
                payment_method = plans_payment_method
                if not payment_method_code:
                    payment_method_code = _normalize_payment_code(plans_payment_method)
            elif plans_payment_method:
                app.logger.info(
                    "DEBUG: keep detected payment_method '%s', ignore plans value '%s'",
                    payment_method,
                    plans_payment_method,
                )
            else:
                app.logger.info(
                    "DEBUG: No payment_method found in plans, keeping '%s'",
                    payment_method,
                )
            if not monthly_fee:
                for plan in plans:
                    if plan.get("monthlyFee"):
                        monthly_fee = plan["monthlyFee"]
                        break
            if not usage:
                for plan in plans:
                    if plan.get("usage"):
                        usage = plan["usage"]
                        break

    if not next_service_date:
        next_service_date = _resolve_next_service_date(
            latest_service_date,
            recent_follow_info,
            record_list,
        )

    # 動態處理付費方式，避免硬編碼映射
    if payment_method_code:
        payment_display = _format_payment_display(payment_method_code, payment_method)
    else:
        payment_display = payment_method

    profile = {
        "keyword": normalized_identifier,
        "customerCode": resolved_code,
        "customerName": (
            _resolve_text(detail_data.get("name"))
            or detail_data.get("enterpriseName")
            or latest_record.get("customer_name")
        ),
        "latestServiceDate": latest_service_date,
        "nextServiceDate": next_service_date,
        "contractNumber": contract_number,
        "paymentMethodCode": payment_method_code,
        "paymentMethod": payment_display,
        "customerIndustry": customer_industry_name,
        "usage": usage,
        "planType": plan_type,
        "monthlyFee": monthly_fee,
        "address": address_text,
        "contact": {
            "name": contact_name,
            "phone": contact_phone,
        },
        "plans": plans,
        "points": None,
        "paymentStatus": payment_status,
    }

    return profile


def _resolve_text(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, dict):
        return value.get("zh_TW") or value.get("zh_CN") or value.get("en_US")
    return str(value)


def _clean_text(value: Any) -> Optional[str]:
    text = _resolve_text(value)
    if text is None:
        return None
    text = str(text).strip()
    return text or None


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return None


def _extract_recent_follow_info(detail_data: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    merchant_detail = detail_data.get("merchantAppliedDetail") or {}
    if not isinstance(merchant_detail, dict):
        return result

    content = merchant_detail.get("recentFollowContent")
    text = _clean_text(content)
    if not text:
        return result

    result["__raw__"] = text
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.replace("：", ":", 1).replace("﹕", ":", 1)
        if ":" not in normalized:
            continue
        key, value = normalized.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def _detect_payment_method(
    detail_data: Dict[str, Any], follow_info: Dict[str, str]
) -> Optional[str]:
    def _extract_payment_label(value: Any) -> Optional[str]:
        if value in (None, "", [], {}):
            return None
        if isinstance(value, list):
            for item in value:
                label = _extract_payment_label(item)
                if label:
                    return label
            return None
        
        # 特別處理字符串類型的信用卡分期
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            # 動態處理信用卡分期顯示
            if "信用卡分期" in text:
                return _format_payment_display("02", "信用卡付費")
        
        return _label_for_payway(value)

    def _check_and_log(value: Any, source: str) -> Optional[str]:
        label = _extract_payment_label(value)
        if label:
            app.logger.info(
                "DEBUG: payment source '%s' -> '%s'",
                source,
                label,
            )
            return label
        return None

    # 優先檢查 customerIndustry.name 字段（根據用戶反饋，這是主要存儲位置）
    customer_industry = detail_data.get("customerIndustry")
    if isinstance(customer_industry, dict):
        # Check both "name" and "Name" keys
        industry_name = customer_industry.get("name") or customer_industry.get("Name")
        if industry_name:
            # 特別處理信用卡分期 - 確保優先識別
            if "信用卡分期" in industry_name:
                app.logger.info("DEBUG: Found 信用卡分期 in customerIndustry.name, mapping to 信用卡付費")
                return _format_payment_display("02", "信用卡付費")
            
            # 先嘗試從包含"維修幫"的字符串中提取付費方式
            if "維修幫" in industry_name or "维修帮" in industry_name:
                # 從格式如"信用卡付費-維修幫"中提取付費方式部分
                parts = industry_name.split("-")
                if len(parts) >= 2:
                    payment_part = parts[0].strip()
                    # 檢查分割後的部分是否包含信用卡分期
                    if "信用卡分期" in payment_part:
                        app.logger.info("DEBUG: Found 信用卡分期 in split part, mapping to 信用卡付費")
                        return _format_payment_display("02", "信用卡付費")
                    payment_result = _check_and_log(
                        payment_part, "customerIndustry.name (維修幫)"
                    )
                    if payment_result:
                        return payment_result

            # 直接嘗試將這個名稱作為付款方式處理
            payment_result = _check_and_log(
                industry_name, "customerIndustry.name (dict)"
            )
            if payment_result:
                return payment_result
    elif isinstance(customer_industry, str):
        # 特別處理字符串類型的信用卡分期
        if "信用卡分期" in customer_industry:
            app.logger.info("DEBUG: Found 信用卡分期 in customerIndustry string, mapping to 信用卡付費")
            return _format_payment_display("02", "信用卡付費")
        
        payment_result = _check_and_log(
            customer_industry, "customerIndustry (str)"
        )
        if payment_result:
            return payment_result

    # 檢查其它 customerIndustry 相關欄位（例如 CRM 直接展開的 customerIndustry.name）
    customer_industry_keys = (
        "customerIndustry.name",
        "customerIndustryName",
        "customerIndustry_name",
        "customerIndustry_Name",
        "customerIndustryLabel",
    )
    for key in customer_industry_keys:
        if key in detail_data:
            field_value = detail_data.get(key)
            # 特別檢查信用卡分期
            if isinstance(field_value, str) and "信用卡分期" in field_value:
                app.logger.info(f"DEBUG: Found 信用卡分期 in {key}, mapping to 信用卡付費")
                return _format_payment_display("02", "信用卡付費")
            
            payment_result = _check_and_log(field_value, f"detail_data[{key}]")
            if payment_result:
                return payment_result

    merchant_detail = detail_data.get("merchantAppliedDetail")
    merchant_fields = (
        "paymentMethod",
        "payway",
        "paymentWay",
        "paywayId",
        "paymentMethodId",
    )
    if isinstance(merchant_detail, dict):
        for field in merchant_fields:
            payment_result = _check_and_log(
                merchant_detail.get(field),
                f"merchantAppliedDetail[{field}]",
            )
            if payment_result:
                return payment_result
    elif isinstance(merchant_detail, list):
        for index, entry in enumerate(merchant_detail):
            if isinstance(entry, dict):
                for field in merchant_fields:
                    payment_result = _check_and_log(
                        entry.get(field),
                        f"merchantAppliedDetail[{index}].{field}",
                    )
                    if payment_result:
                        return payment_result
            else:
                payment_result = _check_and_log(
                    entry, f"merchantAppliedDetail[{index}]"
                )
                if payment_result:
                    return payment_result

    # 檢查頂層 payway / paymentMethod 欄位以及 legacy 字段
    direct_payment_fields = (
        "merchantAppliedDetail!payway",
        "merchantAppliedDetail!paywayId",
        "merchantAppliedDetail.payway",
        "merchantAppliedDetail_payway",
        "merchantAppliedDetail!paymentMethod",
        "merchantAppliedDetail!paymentMethodId",
        "paymentMethod",
        "payway",
        "paymentMethodId",
        "paywayId",
    )
    for field in direct_payment_fields:
        if field in detail_data:
            payment_result = _check_and_log(detail_data.get(field), f"detail_data[{field}]")
            if payment_result:
                return payment_result

    text_candidates: List[Optional[str]] = [
        follow_info.get("付款方式"),
        follow_info.get("付費方式"),
        follow_info.get("目前付費方式"),
        follow_info.get("月費"),
        follow_info.get("金額"),
        follow_info.get("__raw__"),
    ]
    text_candidates.extend(
        value for value in follow_info.values() if isinstance(value, str)
    )

    text_based = _extract_payment_from_texts(text_candidates)
    if text_based:
        app.logger.info("DEBUG: payment source 'follow_info' -> '%s'", text_based)
        return text_based

    return None


def _extract_customer_industry_name(
    detail_data: Dict[str, Any],
    latest_record: Optional[Dict[str, Any]] = None,
    recent_follow_info: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Extract customer industry name from multiple potential sources."""

    def _clean(value: Any) -> Optional[str]:
        if value in (None, "", [], {}):
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, dict):
            for key in (
                "name",
                "Name",
                "label",
                "zh_TW",
                "zh_CN",
                "en_US",
                "displayName",
                "display_name",
                "customerIndustry_Name",
            ):
                data = value.get(key)
                cleaned = _clean(data)
                if cleaned:
                    return cleaned
        return None

    def _pick_from(source: Optional[Dict[str, Any]], *keys: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        for key in keys:
            if key is None:
                continue
            if key in source:
                cleaned = _clean(source.get(key))
                if cleaned:
                    return cleaned
        return None

    candidates: List[Optional[str]] = []

    # detail_data direct
    candidates.append(_clean(detail_data.get("customerIndustry")))
    candidates.append(_clean(detail_data.get("customerIndustry_name")))
    candidates.append(_clean(detail_data.get("customerIndustryName")))
    candidates.append(_clean(detail_data.get("customerIndustry_Name")))

    # merchantAppliedDetail
    merchant_detail = (
        detail_data.get("merchantAppliedDetail")
        if isinstance(detail_data, dict)
        else None
    )
    if isinstance(merchant_detail, dict):
        candidates.append(_clean(merchant_detail.get("customerIndustry")))
        candidates.append(_clean(merchant_detail.get("customerIndustryName")))
        candidates.append(_clean(merchant_detail.get("customerIndustry_name")))

    # nested merchantCharacter
    merchant_character = detail_data.get("merchantCharacter")
    if isinstance(merchant_character, dict):
        candidates.append(_clean(merchant_character.get("customerIndustry_Name")))

    # latest record fallbacks
    candidates.append(_pick_from(latest_record, "customer_industry_name", "customerIndustry_name", "customerIndustry"))

    # follow-up info (maybe labeled in中文)
    candidates.append(_pick_from(recent_follow_info, "客戶行業", "行業", "customerIndustry"))

    for candidate in candidates:
        if candidate:
            return candidate
    return None


# 已移除硬編碼支付方式字典，支付方式將動態處理，避免固定化顯示

_PAYMENT_LABEL_ALIASES: Dict[str, str] = {
    "一次性付款": "01",
    "一次性繳交": "01",
    "一次性全繳": "01",
    "信用卡": "02",
    "信用卡分期": "02",
    "信用卡付费": "02",  # 简体中文
    "信用卡付費": "02",  # 繁體中文
    "銀行轉帳": "03",
    "銀行轉賬": "03",
    "銀行匯款": "03",
    "自動扣款": "03",
    "自動轉帳": "03",
    "銀行卡自動扣款": "03",
    "銀行卡自動轉帳": "03",
    "銀行卡自動轉賬": "03",  # 简体中文
    "银行卡自动转账": "03",  # 简体中文
    "银行卡转账": "03",  # 简体中文
    "季度月費": "04",
    "季度付款": "04",
    "季度收費": "04",  # 繁體中文 - 標準名稱
    "季度收费": "04",  # 简体中文
    "季度消費": "04",  # 用戶反映的錯誤顯示
    "年度月費": "05",
    "年度付款": "05",
    "年度收費": "05",  # 繁體中文 - 標準名稱
    "年度收费": "05",  # 简体中文
    "每月月費": "07",
    "月費": "07",
    "每月收費": "07",  # 繁體中文 - 標準名稱
    "每月收费": "07",  # 简体中文
    "月费": "07",  # 简体中文
    "每月月费": "07",  # 简体中文
    "一次性全缴": "01",  # 简体中文
}


def _build_payment_industry_map() -> Dict[str, str]:
    env_pairs = (
        ("01", "CFG_PAYMENT_ONETIME_INDUSTRY_ID"),
        ("02", "CFG_PAYMENT_CARD_INSTALLMENT_INDUSTRY_ID"),
        ("03", "CFG_PAYMENT_AUTO_DEBIT_INDUSTRY_ID"),
        ("04", "CFG_PAYMENT_QUARTERLY_INDUSTRY_ID"),
        ("05", "CFG_PAYMENT_ANNUAL_INDUSTRY_ID"),
        ("06", "CFG_PAYMENT_TRIAL_INDUSTRY_ID"),
        ("07", "CFG_PAYMENT_MONTHLY_INDUSTRY_ID"),
    )
    mapping: Dict[str, str] = {}
    for code, env_key in env_pairs:
        env_value = os.getenv(env_key)
        if env_value:
            normalized = env_value.strip()
            if normalized:
                mapping[normalized] = code
    return mapping


PAYMENT_INDUSTRY_ID_TO_CODE = _build_payment_industry_map()


def _normalize_payment_label_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip()).lower()


# 已移除基於 PAYMENT_DESCRIPTIONS 的標籤到代碼映射，避免硬編碼
PAYMENT_LABEL_TO_CODE = {}
for alias, code in _PAYMENT_LABEL_ALIASES.items():
    PAYMENT_LABEL_TO_CODE[_normalize_payment_label_text(alias)] = code


def _normalize_payment_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        try:
            return f"{int(digits):02d}"
        except ValueError:
            return None
    normalized = _normalize_payment_label_text(text)
    return PAYMENT_LABEL_TO_CODE.get(normalized)


def _format_payment_display(code: Optional[str], label: Optional[str]) -> Optional[str]:
    """動態處理支付方式顯示，不使用硬編碼映射"""
    if code:
        # 直接返回標籤，如果沒有標籤則返回原始代碼
        display_label = label or code
        if display_label:
            return display_label
        return code
    return label

def _label_for_payway(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        # first try id/code fields
        for key in (
            "id",
            "code",
            "value",
            "payway",
            "paywayId",
            "paymentMethodId",
            "paymentMethod",
        ):
            if key in value:
                normalized = _label_for_payway(value.get(key))
                if normalized:
                    return normalized
        # fall back to textual fields
        for key in ("name", "label", "displayName", "zh_TW", "zh_CN", "en_US"):
            if key in value:
                normalized = _label_for_payway(value.get(key))
                if normalized:
                    return normalized
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            mapped_code = PAYMENT_INDUSTRY_ID_TO_CODE.get(digits)
            if mapped_code:
                return mapped_code  # 直接返回代碼，避免硬編碼映射
            try:
                code = f"{int(digits):02d}"
                return code  # 直接返回數字代碼
            except ValueError:
                pass
        mapped_code = PAYMENT_INDUSTRY_ID_TO_CODE.get(text)
        if mapped_code:
            return mapped_code  # 直接返回代碼，避免硬編碼映射
        normalized_code = PAYMENT_LABEL_TO_CODE.get(
            _normalize_payment_label_text(text)
        )
        if normalized_code:
            return normalized_code  # 直接返回代碼，避免硬編碼映射
        return None
    try:
        key = int(value)
    except (TypeError, ValueError):
        return None
    code = f"{key:02d}"
    mapped_code = PAYMENT_INDUSTRY_ID_TO_CODE.get(str(value))
    if mapped_code:
        return mapped_code  # 直接返回代碼，避免硬編碼映射
    return code  # 直接返回代碼，避免硬編碼映射


def _extract_payment_from_texts(texts: Sequence[Optional[str]]) -> Optional[str]:
    for text in texts:
        label = _extract_payment_from_text(text)
        if label:
            return label
    return None


def _extract_payment_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = str(text).strip()
    if not normalized:
        return None
    normalized = normalized.replace("（", "(").replace("）", ")")
    for needle, label in PAYMENT_KEYWORD_LABELS:
        if needle and needle in normalized:
            code = _label_for_payway(label)
            if code:
                return code
            return label
    match = re.search(r"\(([^)]+)\)", normalized)
    if match:
        inner = match.group(1).strip()
        if inner:
            for needle, label in PAYMENT_KEYWORD_LABELS:
                if needle in inner:
                    code = _label_for_payway(label)
                    if code:
                        return code
                    return label
            code = _label_for_payway(inner)
            if code:
                return code
            return inner
    code = _label_for_payway(normalized)
    if code:
        return code
    return normalized


def _parse_follow_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    base_part = text.split("T")[0].split(" ")[0].replace("/", "-").strip()
    try:
        return date.fromisoformat(base_part)
    except ValueError:
        return None


def _date_to_iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if isinstance(value, date) else None


def _format_follow_date(value: Any) -> Optional[str]:
    parsed = _parse_follow_date(value)
    return parsed.isoformat() if parsed else None


def _select_latest_service_record(
    records: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    today = date.today()
    parsed_records: List[Tuple[Dict[str, Any], date]] = []
    for item in records:
        parsed = _parse_follow_date(item.get("followTime") or item.get("followUpTime"))
        if parsed:
            parsed_records.append((item, parsed))

    if not parsed_records:
        return records[0] if records else None

    past = [entry for entry in parsed_records if entry[1] <= today]
    if past:
        past.sort(key=lambda entry: entry[1], reverse=True)
        return past[0][0]

    parsed_records.sort(key=lambda entry: entry[1])
    return parsed_records[0][0]


def _task_contains_keyword(task: Dict[str, Any], keyword: Optional[str]) -> bool:
    if not keyword:
        return True

    keyword = str(keyword)
    text_fields = (
        "ower_name",
        "originator_name",
        "executor_name",
        "executorAndExecuteStatus",
        "content",
        "title",
        "executor",
    )
    for field in text_fields:
        value = task.get(field)
        if isinstance(value, str) and keyword in value:
            return True

    executors = task.get("executors")
    if isinstance(executors, list):
        for entry in executors:
            if isinstance(entry, dict):
                for key in ("executor_name", "ower_name", "originator_name", "name"):
                    val = entry.get(key)
                    if isinstance(val, str) and keyword in val:
                        return True
            elif isinstance(entry, str) and keyword in entry:
                return True

    return False


def _select_next_service_from_tasks(tasks: Sequence[Dict[str, Any]]) -> Optional[str]:
    if not tasks:
        return None

    keyword = TASK_OWNER_KEYWORD
    today = date.today()
    candidate_dates: List[date] = []

    for task in tasks:
        if not _task_contains_keyword(task, keyword):
            continue
        for field in ("startDate", "planDate", "endDate"):
            task_date = _parse_follow_date(task.get(field))
            if task_date:
                candidate_dates.append(task_date)
                break

    future_dates = sorted(dt for dt in candidate_dates if dt > today)
    if future_dates:
        base = future_dates[0]
        return (base + timedelta(days=14)).isoformat()

    return None


def _build_opportunity_plans(
    customer_code: str,
    latest_record: Optional[Dict[str, Any]] = None,
    detail_data: Optional[Dict[str, Any]] = None,
    followup_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    path = getattr(config, "OPPORTUNITY_LIST_PATH", "").strip()
    if not path:
        return []

    values: List[str] = []
    if customer_code:
        values.append(customer_code)

    def _add_value(value: Any) -> None:
        text = _clean_text(value)
        if text and text not in values:
            values.append(text)

    primary_ids: Set[str] = set()

    def _add_primary(value: Any) -> None:
        text = _clean_text(value)
        if text:
            primary_ids.add(text)

    if latest_record:
        _add_value(
            latest_record.get("customer_code") or latest_record.get("customerCode")
        )
        _add_value(latest_record.get("customer"))
        _add_primary(latest_record.get("oppt") or latest_record.get("opptId"))
        _add_primary(
            latest_record.get("opportunityId") or latest_record.get("businessId")
        )
    if followup_records:
        for entry in followup_records:
            if not isinstance(entry, dict):
                continue
            _add_primary(entry.get("oppt") or entry.get("opptId"))
            _add_primary(entry.get("opportunityId") or entry.get("businessId"))
    if detail_data:
        _add_value(detail_data.get("code"))
        _add_value(detail_data.get("id"))
        _add_value(detail_data.get("customerCode"))
        _add_value(detail_data.get("customer"))
    detail_sources = (
        detail_data.get("merchantAppliedDetail")
        if isinstance(detail_data, dict)
        else None
    )
    if isinstance(detail_sources, dict):
        _add_value(detail_sources.get("contractNo"))

    filters: List[tuple[str, Optional[str], Optional[str]]] = []
    for value in values or [customer_code]:
        if not value:
            continue
        filters.append(
            (
                value,
                getattr(config, "OPPORTUNITY_CUSTOMER_FIELD", "customer.code"),
                getattr(config, "OPPORTUNITY_CUSTOMER_OPERATOR", "eq"),
            )
        )
        if value.isdigit():
            filters.append((value, "customer", "eq"))
        if len(value) > 3 and not value.isdigit():
            filters.append((value, "customer.name", "like"))

    seen_ids: Set[str] = set()
    record_list: List[Dict[str, Any]] = []

    for value, field, operator in filters:
        if not value:
            continue
        try:
            response = CRM_CLIENT.get_opportunities(
                value,
                page=1,
                page_size=20,
                field=field,
                operator=operator,
            )
        except Exception as exc:  # pragma: no cover - runtime logging only
            app.logger.debug(
                "Opportunity lookup failed for %s (%s %s): %s",
                value,
                field,
                operator,
                exc,
            )
            continue
        items = response.get("data", {}).get("recordList", []) or []
        for item in items:
            key = _clean_text(
                item.get("id")
                or item.get("oppt")
                or item.get("opptId")
                or item.get("opportunityId")
                or item.get("code")
            )
            if key and key in seen_ids:
                continue
            if key:
                seen_ids.add(key)
            record_list.append(item)

    if not record_list:
        return []

    if primary_ids:
        prioritized: List[Dict[str, Any]] = []
        for item in record_list:
            record_id = _clean_text(
                item.get("id")
                or item.get("oppt")
                or item.get("opptId")
                or item.get("opportunityId")
                or item.get("businessId")
            )
            if record_id and record_id in primary_ids:
                prioritized.append(item)
        if prioritized:
            record_list = prioritized

    plans: List[Dict[str, Any]] = []
    for record in record_list:
        opportunity_id = _first_non_empty(
            record.get("id"),
            record.get("oppt"),
            record.get("opptId"),
            record.get("opportunityId"),
            record.get("businessId"),
        )

        detail_resp_data: Dict[str, Any] = {}
        if opportunity_id:
            try:
                detail_resp = CRM_CLIENT.get_opportunity_detail(str(opportunity_id))
                detail_resp_data = (
                    detail_resp.get("data") or detail_resp.get("result") or detail_resp
                )
            except Exception as exc:  # pragma: no cover - best effort
                app.logger.debug(
                    "Opportunity detail lookup failed for %s: %s",
                    opportunity_id,
                    exc,
                )
        plan = _build_plan_model(record, detail_resp_data, opportunity_id)
        if plan:
            plans.append(plan)

    return plans


def _build_plan_model(
    record: Dict[str, Any],
    detail: Dict[str, Any],
    opportunity_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    sources = _collect_sources(record, detail)

    plan_id = (
        _extract_value(sources, "id", "oppt", "opptId", "opportunityId", "businessId")
        or opportunity_id
    )
    title = _extract_value(sources, "oppt_name", "name", "商機名稱")
    stage = _extract_value(sources, "opptStage_name", "stageName", "商機階段")
    summary = _extract_value(
        sources,
        "planType",
        "plan_type",
        "方案類型",
        "schemeName",
        "productName",
        "address",
        "headDef!define9",
        "opptDefineCharacter.attrext9",
    )
    usage_value = _extract_value(
        sources,
        "usage",
        "useType",
        "使用方式",
        "headDef!define8",
        "opptDefineCharacter.attrext8",
    )
    payment_value = _extract_value(
        sources,
        "paymentMethod",
        "paymentMethodName",
        "paymentWay",
        "payWay_name",
        "paywayName",
        "目前付費方式",
        "付款方式",
        "headDef!define7",
        "headDef!define9",
        "opptDefineCharacter.attrext7",
        "opptDefineCharacter.attrext9",
    )
    monthly_value = _extract_value(
        sources,
        "monthlyFee",
        "rentAmount",
        "rent",
        "月費金額",
        "headDef!define10",
        "headDef!define11",
        "opptDefineCharacter.attrext12",
        "opptDefineCharacter.attrext10",
    )
    contract_number = _extract_value(
        sources,
        "contractNo",
        "contractNumber",
        "合約編號",
        "合同編號",
        "headDef!define13",
        "opptDefineCharacter.attrext19",
    )
    contract_begin = _extract_value(
        sources,
        "contractBeginDate",
        "startDate",
        "合約開始日期",
        "開始日期",
        "headDef!define2",
        "opptDefineCharacter.attrext2",
    )
    contract_end = _extract_value(
        sources,
        "contractEndDate",
        "endDate",
        "合約結束日期",
        "結束日期",
        "headDef!define3",
        "opptDefineCharacter.attrext3",
    )
    contract_term = _extract_value(
        sources,
        "contractYear",
        "合約年期",
        "headDef!define4",
        "opptDefineCharacter.attrext4",
    )

    detail_url = _extract_value(sources, "pcUrl", "detailUrl", "detail_url", "url")
    if not detail_url:
        template = getattr(config, "OPPORTUNITY_DETAIL_WEB_URL_TEMPLATE", "")
        if template and plan_id:
            try:
                detail_url = template.format(id=plan_id, code=plan_id)
            except Exception:  # pragma: no cover - best effort
                detail_url = None

    details: List[Dict[str, str]] = []

    def _add_detail(label: str, *keys: str) -> None:
        value = _extract_value(sources, *keys)
        if value:
            details.append({"label": label, "value": value})

    display_summary = summary
    if not display_summary:
        display_summary = _extract_value(
            sources, "solutionName", "方案名稱", "planName"
        )

    if display_summary:
        details.append({"label": "方案類型", "value": display_summary})

    _add_detail(
        "使用方式",
        "usage",
        "useType",
        "使用方式",
        "headDef!define8",
        "opptDefineCharacter.attrext8",
    )
    # 转换付款方式为可读描述
    if payment_value:
        # 如果payment_value是代码（如01, 02等），转换为描述
        payment_display = _format_payment_display(payment_value, payment_value)
        # 更新details中的付費方式
        details.append({"label": "付費方式", "value": payment_display})
    else:
        _add_detail(
            "付費方式",
            "paymentMethod",
            "paymentMethodName",
            "paymentWay",
            "payWay_name",
            "paywayName",
            "目前付費方式",
            "付款方式",
        )
    _add_detail(
        "月費金額",
        "monthlyFee",
        "rentAmount",
        "rent",
        "月費金額",
        "headDef!define11",
        "opptDefineCharacter.attrext12",
    )
    _add_detail(
        "合約編號",
        "contractNo",
        "contractNumber",
        "合同編號",
        "合約編號",
        "headDef!define13",
        "opptDefineCharacter.attrext19",
    )
    _add_detail(
        "合約開始日",
        "contractBeginDate",
        "startDate",
        "合約開始日期",
        "開始日期",
        "headDef!define2",
        "opptDefineCharacter.attrext2",
    )
    _add_detail(
        "合約結束日",
        "contractEndDate",
        "endDate",
        "合約結束日期",
        "結束日期",
        "headDef!define3",
        "opptDefineCharacter.attrext3",
    )
    _add_detail(
        "合約年期",
        "contractYear",
        "合約年期",
        "headDef!define4",
        "opptDefineCharacter.attrext4",
    )
    _add_detail(
        "預計簽單金額", "expectSignMoney", "planAmount", "amount", "預計簽單金額"
    )
    _add_detail("商機階段", "opptStage_name", "stageName", "商機階段")
    _add_detail("方案負責人", "ownerName", "ower_name", "負責人")
    _add_detail("交易類型", "opptTransType_name", "bustype_name", "交易類型")
    _add_detail("安裝位置", "installLocation", "address", "安裝位置")

    details = _deduplicate_details(details)
    if details:
        preferred_order = [
            "合約編號",
            "方案類型",
            "使用方式",
            "付費方式",
            "月費金額",
            "合約開始日",
            "合約結束日",
            "合約年期",
            "預計簽單金額",
            "商機階段",
            "方案負責人",
            "交易類型",
            "安裝位置",
        ]
        ordered: List[Dict[str, str]] = []
        seen_pairs: Set[Tuple[str, str]] = set()
        detail_map = {(item["label"], item["value"]): item for item in details}
        label_to_items: Dict[str, List[Dict[str, str]]] = {}
        for item in details:
            label_to_items.setdefault(item["label"], []).append(item)
        for label in preferred_order:
            for entry in label_to_items.get(label, []):
                key = (entry["label"], entry["value"])
                if key not in seen_pairs:
                    ordered.append(entry)
                    seen_pairs.add(key)
        for entry in details:
            key = (entry["label"], entry["value"])
            if key not in seen_pairs:
                ordered.append(entry)
                seen_pairs.add(key)
        details = ordered

    if not (display_summary or title or details):
        return None

    # 转换plan对象中的paymentMethod为可读描述
    payment_method_display = _format_payment_display(payment_value, payment_value) if payment_value else payment_value
    
    return {
        "id": plan_id,
        "title": title or display_summary or "商機",
        "stage": stage,
        "summary": display_summary or title,
        "usage": usage_value,
        "paymentMethod": payment_method_display,
        "monthlyFee": monthly_value,
        "contractNumber": contract_number,
        "contractBegin": contract_begin,
        "contractEnd": contract_end,
        "contractTerm": contract_term,
        "detailUrl": detail_url,
        "details": details,
        "raw": {
            "list": record,
            "detail": detail,
        },
    }


def _collect_sources(*items: Any) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen: Set[int] = set()
    stack: List[Any] = list(items)
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        sources.append(current)
        for value in current.values():
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        stack.append(entry)
    return sources


def _extract_value(sources: Sequence[Dict[str, Any]], *keys: str) -> Optional[str]:
    for key in keys:
        if not key:
            continue
        for src in sources:
            if "." in key:
                value = _extract_nested(src, key)
            else:
                value = src.get(key)
            text = _clean_text(value)
            if text:
                return text
    return None


def _deduplicate_details(items: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    deduped: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for item in items:
        if not item:
            continue
        label = item.get("label") or ""
        value = item.get("value") or ""
        if not value:
            continue
        key = (label, value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"label": label, "value": value})
    return deduped


def _collect_dates_from_texts(texts: Sequence[Optional[str]]) -> List[date]:
    dates: List[date] = []
    for text in texts:
        if not text:
            continue
        normalized = str(text)
        for year, month, day in STANDARD_DATE_RE.findall(normalized):
            try:
                dates.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
        for year, month, day in CJK_DATE_RE.findall(normalized):
            try:
                dates.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return dates


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    base_part = text.split("T")[0].replace("/", "-")
    try:
        return datetime.strptime(base_part, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_next_service_date(
    latest_service_date: Optional[str],
    follow_info: Dict[str, str],
    records: Sequence[Dict[str, Any]],
) -> Optional[str]:
    texts: List[Optional[str]] = [
        follow_info.get("內容"),
        follow_info.get("月費"),
        follow_info.get("金額"),
        follow_info.get("日期"),
        follow_info.get("時間"),
        follow_info.get("__raw__"),
    ]
    for record in records:
        follow_context = _clean_text(record.get("followContext"))
        if follow_context:
            texts.append(follow_context)

    candidates = _collect_dates_from_texts(texts)
    if not candidates:
        return None

    unique_dates = sorted(set(candidates))
    latest_obj = _parse_iso_date(latest_service_date)
    if latest_obj:
        future_after_latest = [dt for dt in unique_dates if dt > latest_obj]
        if future_after_latest:
            if len(future_after_latest) >= 2:
                return future_after_latest[-1].isoformat()
            return future_after_latest[0].isoformat()

    today = date.today()
    future_today = [dt for dt in unique_dates if dt >= today]
    if future_today:
        return future_today[0].isoformat()

    return unique_dates[-1].isoformat()


def _resolve_payment_status(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    today = date.today()
    best_record: Optional[Dict[str, Any]] = None
    best_date: Optional[date] = None

    for item in records:
        owner = _clean_text(item.get("ower_name"))
        if owner != "出納008":
            continue
        follow_date = _parse_follow_date(
            item.get("followTime") or item.get("createTime")
        )
        if not follow_date or follow_date > today:
            continue
        if best_date is None or follow_date > best_date:
            best_record = item
            best_date = follow_date

    if not (best_record and best_date):
        return None

    note = _clean_text(best_record.get("followContext")) or _clean_text(
        best_record.get("remark")
    )
    date_text = _clean_text(best_record.get("followTime")) or best_date.isoformat()
    if note:
        return f"{date_text} · {note}"
    return date_text


def _extract_upcoming_task_date(
    task_records: Sequence[Dict[str, Any]],
    reference_date: date,
    owner_keyword: Optional[str] = None,
    max_gap_days: Optional[int] = None,
) -> Optional[str]:
    if not task_records:
        return None

    owner_future: List[date] = []
    general_future: List[date] = []
    owner_past: List[date] = []
    general_past: List[date] = []

    for task in task_records:
        task_date: Optional[date] = None
        for field in ("startDate", "planDate", "endDate"):
            task_date = _parse_follow_date(task.get(field))
            if task_date:
                break
        if not task_date:
            continue
        if max_gap_days is not None and task_date - reference_date > timedelta(
            days=max_gap_days
        ):
            continue

        is_owner = bool(
            owner_keyword and owner_keyword in str(task.get("ower_name") or "")
        )
        if task_date >= reference_date:
            if is_owner:
                owner_future.append(task_date)
            else:
                general_future.append(task_date)
        else:
            if is_owner:
                owner_past.append(task_date)
            else:
                general_past.append(task_date)

    if owner_future:
        return min(owner_future).isoformat()
    if general_future:
        return min(general_future).isoformat()
    if owner_past:
        owner_past.sort(reverse=True)
        return owner_past[0].isoformat()
    if general_past:
        general_past.sort(reverse=True)
        return general_past[0].isoformat()
    return None


def _select_task_base_date(
    task_records: Sequence[Dict[str, Any]],
    owner_keyword: Optional[str],
    latest_date: Optional[date],
    previous_date: Optional[date],
) -> Optional[date]:
    if not task_records:
        return None

    today = date.today()
    owner_future_today: List[date] = []
    owner_future_latest: List[date] = []
    owner_past: List[date] = []
    general_future_today: List[date] = []
    general_future_latest: List[date] = []
    general_past: List[date] = []

    for task in task_records:
        start = _parse_follow_date(task.get("startDate")) or _parse_follow_date(
            task.get("planDate")
        )
        if not start:
            start = _parse_follow_date(task.get("endDate"))
        if not start:
            continue

        is_owner = bool(
            owner_keyword and owner_keyword in str(task.get("ower_name") or "")
        )
        bucket_future_today = owner_future_today if is_owner else general_future_today
        bucket_future_latest = (
            owner_future_latest if is_owner else general_future_latest
        )
        bucket_past = owner_past if is_owner else general_past

        if start > today:
            bucket_future_today.append(start)
        elif latest_date and start > latest_date:
            bucket_future_latest.append(start)
        else:
            bucket_past.append(start)

    if owner_future_today:
        return min(owner_future_today)
    if general_future_today:
        return min(general_future_today)
    if owner_future_latest:
        return min(owner_future_latest)
    if general_future_latest:
        return min(general_future_latest)
    if owner_past:
        owner_past.sort(reverse=True)
        return owner_past[0]
    if general_past:
        general_past.sort(reverse=True)
        return general_past[0]
    return previous_date


def _extract_maintenance_summary(
    customer_code: str,
    followup_data: Dict[str, Any],
    task_records: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Optional[str]]:
    record_list: List[Dict[str, Any]] = (
        followup_data.get("data", {}).get("recordList", []) or []
    )

    maintenance = [
        item for item in record_list if "維修幫" in str(item.get("ower_name") or "")
    ]

    owner_keyword = getattr(config, "MAINTENANCE_TASK_OWNER_KEYWORD", None)

    parsed_records: List[Tuple[Dict[str, Any], date]] = []
    for item in maintenance:
        parsed = _parse_follow_date(item.get("followTime"))
        if parsed:
            parsed_records.append((item, parsed))

    task_records = list(task_records or [])

    if not parsed_records:
        task_date_iso = _extract_upcoming_task_date(
            task_records,
            reference_date=date.today(),
            owner_keyword=owner_keyword,
            max_gap_days=getattr(config, "MAINTENANCE_TASK_MAX_GAP_DAYS", None),
        )
        task_date = _parse_follow_date(task_date_iso)
        return {
            "customerCode": customer_code,
            "customerName": None,
            "latestServiceDate": None,
            "previousServiceDate": None,
            "nextServiceDate": _date_to_iso(task_date),
        }

    parsed_records.sort(key=lambda entry: entry[1], reverse=True)
    today = date.today()

    latest_index = 0
    for idx, (_, record_date) in enumerate(parsed_records):
        if record_date <= today:
            latest_index = idx
            break

    latest_item, latest_date = parsed_records[latest_index]
    previous_item, previous_date = (None, None)
    if latest_index + 1 < len(parsed_records):
        previous_item, previous_date = parsed_records[latest_index + 1]

    task_date = _select_task_base_date(
        task_records,
        owner_keyword,
        latest_date,
        previous_date,
    )

    next_base_date: Optional[date] = task_date or previous_date or latest_date

    customer_name = str(latest_item.get("customer_name") or "") or None

    previous_norm = _date_to_iso(previous_date)
    return {
        "customerCode": customer_code,
        "customerName": customer_name,
        "latestServiceDate": _date_to_iso(latest_date),
        "previousServiceDate": previous_norm,
        "nextServiceDate": _date_to_iso(next_base_date),
    }


def _seems_like_schedule_text(text: str) -> bool:
    return bool(STANDARD_DATE_RE.search(text) or CJK_DATE_RE.search(text))


def _looks_like_phone(text: str) -> bool:
    digits = [ch for ch in text if ch.isdigit()]
    if len(digits) < 6:
        return False
    non_digits = [ch for ch in text if not (ch.isdigit() or ch in {"+", "-", " ", "#"})]
    return len(non_digits) <= 3


def _matches_code(
    item: Dict[str, Any],
    expected_code: str,
    detail_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> bool:
    expected = expected_code.strip().upper()
    if not expected:
        return False

    for key in ("customer_code", "customerCode"):
        val = _clean_text(item.get(key))
        if val and val.upper() == expected:
            return True

    cust = item.get("customer")
    if isinstance(cust, str):
        val = cust.strip().upper()
        if val and val == expected and _has_alpha(val):
            return True

    nested_code = _extract_nested(item, "customer.code")
    if isinstance(nested_code, str) and nested_code.strip().upper() == expected:
        return True

    for key in ("customer_name", "customer.name", "customerName"):
        name_val = item.get(key) if "." not in key else _extract_nested(item, key)
        if isinstance(name_val, str) and name_val:
            token = CODE_TOKEN_RE.search(name_val.upper())
            if token and token.group(0) == expected:
                return True

    detail_code = _detail_code(item, detail_cache)
    if detail_code and detail_code.upper() == expected:
        return True

    return False


def _detail_code(
    item: Dict[str, Any],
    detail_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> Optional[str]:
    cust_id = item.get("customer")
    org_id = item.get("org")
    if not cust_id:
        return None

    key = (str(cust_id), str(org_id or ""))
    data = detail_cache.get(key)
    if data is None:
        try:
            if not org_id:
                raise ValueError("missing org id")
            data = _get_detail_data(str(cust_id), str(org_id), detail_cache)
        except Exception:
            data = {}
        detail_cache[key] = data

    code = _clean_text((data or {}).get("code"))
    return code.upper() if code else code


def _get_detail_data(
    customer_id: str,
    org_id: str,
    detail_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[str, Any]:
    key = (str(customer_id), str(org_id))
    cached = detail_cache.get(key)
    if cached is not None:
        return cached

    detail_resp = CRM_CLIENT.get_customer_detail(customer_id, org_id)
    detail_data = detail_resp.get("data") or {}
    detail_cache[key] = detail_data
    return detail_data


def _extract_nested(source: Dict[str, Any], path: str) -> Any:
    if not path:
        return None
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _has_alpha(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def _find_record_by_date(
    records: Sequence[Dict[str, Any]],
    iso_date: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not iso_date:
        return None
    target = _parse_follow_date(iso_date)
    if not target:
        return None
    for item in records:
        record_date = _parse_follow_date(
            item.get("followTime") or item.get("followUpTime")
        )
        if record_date == target:
            return item
    return None


def _filter_records_for_code(
    records: Sequence[Dict[str, Any]],
    expected_code: str,
    detail_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Optional[str], List[str]]:
    expected = expected_code.strip().upper()
    if not expected:
        return list(records), None, []

    exact_records = [
        item for item in records if _matches_code(item, expected, detail_cache)
    ]
    if exact_records:
        return exact_records, expected, []

    code_to_records: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        for code in _candidate_codes(item, detail_cache):
            key = code.upper()
            code_to_records.setdefault(key, []).append(item)

    if expected in code_to_records:
        return code_to_records[expected], expected, []

    prefix_matches = sorted(
        code for code in code_to_records if code.startswith(expected)
    )
    if prefix_matches:
        return [], None, prefix_matches

    if len(code_to_records) == 1:
        resolved = next(iter(code_to_records))
        return code_to_records[resolved], resolved, []

    suggestions = sorted(code_to_records.keys())
    return [], None, suggestions


def _candidate_codes(
    item: Dict[str, Any],
    detail_cache: Dict[Tuple[str, str], Dict[str, Any]],
) -> List[str]:
    codes: List[str] = []
    for key in ("customer_code", "customerCode"):
        val = _clean_text(item.get(key))
        if val:
            codes.append(val.upper())

    name_val = _clean_text(
        item.get("customer_name")
        or item.get("customer.name")
        or item.get("customerName")
    )
    if name_val:
        token = CODE_TOKEN_RE.search(name_val.upper())
        if token:
            codes.append(token.group(0))

    detail = _detail_code(item, detail_cache)
    if detail:
        codes.append(detail.upper())

    return list(dict.fromkeys(codes))  # preserve order, remove duplicates


if __name__ == "__main__":  # pragma: no cover
    host = os.getenv("HOST", "0.0.0.0")
    try:
        port = int(os.getenv("PORT", "7010"))
    except ValueError:
        port = 7010
    debug = os.getenv("FLASK_DEBUG", "1") not in {"0", "false", "False"}
    app.run(host=host, port=port, debug=debug)
