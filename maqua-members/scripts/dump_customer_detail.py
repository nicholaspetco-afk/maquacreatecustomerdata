#!/usr/bin/env python3
"""Fetch raw CRM customer detail (getbyid) data by customer code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _bootstrap_python_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))


_bootstrap_python_path()

from services.crm_client import CRMClient  # noqa: E402


def _extract_customer_pointer(
    record: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    customer_id = str(record.get("customer") or record.get("customerId") or "").strip()
    org_id = str(record.get("org") or record.get("orgId") or "").strip()
    return customer_id or None, org_id or None


def find_customer_pointer(
    client: CRMClient,
    code: str,
    *,
    pages: int = 3,
    page_size: int = 20,
    customer_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Return (customer_id, org_id, sample_record) for the given customer code."""

    if customer_id and org_id:
        return str(customer_id), str(org_id), {}

    def _search_records() -> List[Dict[str, Any]]:
        attempts = [
            ("customer.code", "eq", code),
            ("customer.code", "like", code),
            ("customer_name", "like", code),
            ("customer.name", "like", code),
        ]
        for field, operator, value in attempts:
            try:
                resp = client.get_followups(
                    value,
                    page=1,
                    page_size=page_size,
                    search_field=field,
                    search_operator=operator,
                )
            except Exception:
                continue
            records = resp.get("data", {}).get("recordList") or []
            if records:
                return records
        collected: List[Dict[str, Any]] = []
        for page in range(1, pages + 1):
            try:
                resp = client.get_followups("", page=page, page_size=page_size)
            except Exception:
                continue
            records = resp.get("data", {}).get("recordList") or []
            for record in records:
                customer_name = str(record.get("customer_name") or "").upper()
                if code.upper() in customer_name:
                    collected.append(record)
            if collected:
                break
        return collected

    record_list = _search_records()
    if not record_list:
        raise LookupError(
            f"找不到客戶編碼 {code} 的任何跟進紀錄，因此無法取得 customer id/org id"
        )

    code_upper = code.upper()
    for record in record_list:
        customer_id, org_id = _extract_customer_pointer(record)
        customer_name = str(record.get("customer_name") or "").upper()
        if customer_id and org_id and code_upper in customer_name:
            return customer_id, org_id, record

    for record in record_list:
        customer_id, org_id = _extract_customer_pointer(record)
        if customer_id and org_id:
            return customer_id, org_id, record

    raise LookupError(
        f"找到 {len(record_list)} 筆紀錄，但都缺少 customer/org id，無法調用 getbyid"
    )


def fetch_customer_detail_by_code(
    code: str,
    *,
    pretty: bool = False,
    customer_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    client = CRMClient()
    customer_id, org_id, followup_record = find_customer_pointer(
        client,
        code,
        pages=5,
        page_size=20,
        customer_id=customer_id,
        org_id=org_id,
    )
    detail_resp = client.get_customer_detail(customer_id, org_id)
    data = detail_resp.get("data") or {}
    payload: Dict[str, Any] = {
        "customerCode": code,
        "customerId": customer_id,
        "orgId": org_id,
        "followupRecord": followup_record,
        "detail": data,
    }
    if pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump CRM customer detail by code.")
    parser.add_argument("code", help="客戶編碼，例如 C4583")
    parser.add_argument("--pretty", action="store_true", help="Pretty print JSON")
    parser.add_argument("--customer-id", help="已知的 customer 主鍵 ID")
    parser.add_argument("--org-id", help="已知的 org ID")
    args = parser.parse_args()

    try:
        fetch_customer_detail_by_code(
            args.code.strip().upper(),
            pretty=args.pretty,
            customer_id=args.customer_id,
            org_id=args.org_id,
        )
    except Exception as exc:  # pragma: no cover - CLI convenience
        parser.error(str(exc))


if __name__ == "__main__":
    main()
