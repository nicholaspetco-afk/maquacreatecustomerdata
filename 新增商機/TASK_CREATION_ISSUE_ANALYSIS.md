# 任務創建找不到客戶編碼問題分析

## 📅 日期: 2025-11-25

---

## 🔴 問題描述

新增商機成功後，自動創建三個任務時，提示**找不到客戶編碼，無法建立任務**。

---

## 🔍 根本原因分析

### 問題發生的位置

**文件**: `maqua-members/services/customer_submission.py`  
**函數**: `_auto_create_tasks_for_opportunity` (第 1690-1811 行)

### 關鍵代碼分析

```python
def _auto_create_tasks_for_opportunity(
    context: Dict[str, Any],
    create_response: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> None:
    data = create_response.get("data") or {}
    oppt_id = data.get("id") or context.get("opptId")
    oppt_stage = data.get("opptStage") or context.get("opptStage")
    
    # ⚠️ 問題在這裡
    customer_id = context.get("customerId") or data.get("customer")
    customer_name = context.get("customerName") or data.get("customer_name")
```

### 三種可能的原因

#### 原因 1: `context` 中缺少 `customerId` ✅ 最可能

在商機創建時，`context` 可能沒有正確設置 `customerId`。

**檢查點**:
- 在 `_create_opportunity_for_customer` 函數中 (第 2287-2481 行)
- 第 2300-2310 行設置了 `customerId`
- 但是這個 `context` 可能沒有傳遞到任務創建函數

#### 原因 2: CRM API 返回的 `data.customer` 為空

商機創建成功後，CRM 返回的數據中可能沒有包含 `customer` 字段。

#### 原因 3: 客戶 ID 類型不匹配

- `customer_id` 可能是字符串類型的客戶編碼（如 `"C45636"`）
- 但 CRM 任務 API 需要的是數字 ID（如 `"1779393122472558598"`）

---

## 🔎 診斷步驟

### Step 1: 檢查 `context` 傳遞

在調用 `_auto_create_tasks_for_opportunity` 之前 (第 2478 行):

```python
if result["success"]:
    try:
        _auto_create_tasks_for_opportunity(context, create_response, settings, client)
    except Exception as exc:
        print(f"[task] auto-create error: {exc}", flush=True)
```

**問題**: 這裡使用的 `context` 是從第 2295 行獲取的:
```python
context = dict(normalized.get("opportunityContext") or {})
```

但是 `customerId` 是在第 2310 行才設置的:
```python
context.setdefault("customerId", customer_id)
```

**✅ 所以 `context` 中應該有 `customerId`**

### Step 2: 檢查 `create_response` 的結構

需要查看 CRM 商機創建 API 返回的實際數據結構。

商機創建時使用的 API: `/yonbip/crm/bill/opptsave`

返回的 `data` 結構可能是:
```json
{
  "code": "200",
  "data": {
    "id": "商機ID",
    "customer": "客戶ID",  // ← 這個字段可能為空或不存在
    "customer_name": "客戶名稱"
  }
}
```

### Step 3: 添加調試日誌

在 `_auto_create_tasks_for_opportunity` 函數開頭添加:

```python
print(f"[task] context.customerId: {context.get('customerId')}", flush=True)
print(f"[task] context.customerName: {context.get('customerName')}", flush=True)
print(f"[task] data.customer: {data.get('customer')}", flush=True)
print(f"[task] data.customer_name: {data.get('customer_name')}", flush=True)
print(f"[task] final customer_id: {customer_id}", flush=True)
print(f"[task] final customer_name: {customer_name}", flush=True)
```

---

## 💡 解決方案

### 方案 A: 增強錯誤處理和日誌 ⭐ 推薦

在 `_auto_create_tasks_for_opportunity` 函數中添加驗證:

```python
def _auto_create_tasks_for_opportunity(
    context: Dict[str, Any],
    create_response: Dict[str, Any],
    settings: SubmissionSettings,
    client: CRMClient,
) -> None:
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    
    data = create_response.get("data") or {}
    oppt_id = data.get("id") or context.get("opptId")
    oppt_stage = data.get("opptStage") or context.get("opptStage")
    customer_id = context.get("customerId") or data.get("customer")
    customer_name = context.get("customerName") or data.get("customer_name")
    
    # ✅ 添加驗證和詳細日誌
    print(f"[task] Debug - context keys: {list(context.keys())}", flush=True)
    print(f"[task] Debug - data keys: {list(data.keys())}", flush=True)
    print(f"[task] Debug - customer_id: {customer_id}", flush=True)
    print(f"[task] Debug - customer_name: {customer_name}", flush=True)
    
    if not customer_id:
        error_msg = (
            f"無法創建任務：缺少客戶ID。"
            f"context.customerId={context.get('customerId')}, "
            f"data.customer={data.get('customer')}, "
            f"context keys={list(context.keys())}"
        )
        print(f"[task] ERROR: {error_msg}", flush=True)
        raise ValueError(error_msg)
    
    if not customer_name:
        # 如果沒有客戶名稱，使用客戶ID作為後備
        customer_name = f"客戶_{customer_id}"
        print(f"[task] Warning: 使用後備客戶名稱: {customer_name}", flush=True)
    
    # 繼續後續邏輯...
```

### 方案 B: 如果客戶 ID 缺失，嘗試從客戶編碼查詢

```python
# 在 customer_id 為空時，嘗試通過客戶編碼查詢
if not customer_id:
    customer_code = context.get("customerCode")
    if customer_code:
        print(f"[task] 嘗試通過客戶編碼 {customer_code} 查詢客戶ID", flush=True)
        try:
            # 使用 _lookup_customer_id_by_code 函數
            customer_id = _lookup_customer_id_by_code(customer_code, client)
            if customer_id:
                print(f"[task] 查詢到客戶ID: {customer_id}", flush=True)
            else:
                print(f"[task] 無法查詢到客戶ID", flush=True)
        except Exception as e:
            print(f"[task] 查詢客戶ID失敗: {e}", flush=True)
```

### 方案 C: 確保 context 完整性

在 `_create_opportunity_for_customer` 函數中，確保 `context` 包含所需的所有字段:

```python
# 在第 2310 行之後，確認 context 已完整
context.setdefault("customerId", customer_id)

# 添加日誌確認
print(f"[opportunity] context after setup - customerId: {context.get('customerId')}", flush=True)
print(f"[opportunity] context after setup - customerName: {context.get('customerName')}", flush=True)
print(f"[opportunity] context after setup - customerCode: {context.get('customerCode')}", flush=True)
```

---

## 🧪 測試方案

### 測試腳本

創建一個測試腳本來重現問題:

```python
#!/usr/bin/env python3
"""測試商機和任務創建流程"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "maqua-members"))

from services.customer_submission import run_submission

test_input = """
商機名稱: 測試商機_任務創建
客戶: C45636 測試客戶
使用方式: 租用
月費金額: 288
按金: 6912
預繳金: 0
合約1開始日: 2025-11-25
合約1結束日期: 2027-11-25
"""

try:
    result = run_submission(test_input.strip())
    
    print("\n" + "=" * 60)
    print("✅ 提交成功")
    print("=" * 60)
    
    # 檢查商機創建結果
    opp_resp = result.get("opportunityResponse") or {}
    print(f"\n商機創建: {'成功' if opp_resp.get('success') else '失敗'}")
    
    if opp_resp.get("createResponse"):
        create_data = opp_resp["createResponse"].get("data") or {}
        print(f"商機ID: {create_data.get('id')}")
        print(f"客戶ID: {create_data.get('customer')}")
        print(f"客戶名稱: {create_data.get('customer_name')}")
    
    # 檢查任務創建的錯誤信息
    print(f"\n完整結果:")
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
except Exception as e:
    print(f"\n❌ 錯誤: {e}")
    import traceback
    traceback.print_exc()
```

---

## 📋 下一步行動

1. ✅ **立即**: 添加調試日誌（方案 A）
2. ⬜ 運行測試腳本，查看日誌輸出
3. ⬜ 根據日誌確定具體原因
4. ⬜ 實施對應的修復方案
5. ⬜ 驗證修復效果

---

**生成時間**: 2025-11-25  
**優先級**: 🔴 高  
**狀態**: 待修復
