# 任務創建問題修復報告

## 📅 日期: 2025-11-25

---

## ✅ 問題已解決

### 問題描述
新增商機之後，自動創建的三個任務找不到客戶編碼，無法建立任務。

### 根本原因

1. **商機 API 返回數據不一致**
   - CRM 商機創建 API 返回的 `data.customer` 字段與實際的客戶 ID 不同
   - 例如：
     - `context.customerId`: `2412376752570499077` (正確)
     - `data.customer`: `2412376778335059973` (不同的ID)

2. **CFG_CREATE_OPPORTUNITY 未啟用**
   - 默認值為 `false`，導致商機不會自動創建
   - 因此也不會觸發任務創建邏輯

3. **缺少調試信息**
   - 沒有足夠的日誌來診斷問題

---

## 🔧 修復方案

### 1. 優化客戶ID獲取邏輯

**文件**: `maqua-members/services/customer_submission.py`  
**函數**: `_auto_create_tasks_for_opportunity` (第 1690 行起)

```python
# 優先使用 context.customerId，然後才是 data.customer
customer_id = context.get("customerId") or data.get("customer")

# 如果都沒有，嘗試通過客戶編碼查詢
if not customer_id:
    customer_code = context.get("customerCode")
    if customer_code:
        try:
            customer_id = _lookup_customer_id_by_code(customer_code, client)
        except Exception as e:
            print(f"[task] 查詢客戶ID失敗: {e}", flush=True)
```

### 2. 添加詳細的調試日誌

```python
# 在任務創建函數開頭添加
print(f"[task] Debug - context.customerId: {context.get('customerId')}", flush=True)
print(f"[task] Debug - context.customerName: {context.get('customerName')}", flush=True)
print(f"[task] Debug - context.customerCode: {context.get('customerCode')}", flush=True)
print(f"[task] Debug - data.customer: {data.get('customer')}", flush=True)
print(f"[task] Debug - final customer_id: {customer_id}", flush=True)
```

### 3. 啟用自動創建商機

**文件**: `.env`

添加配置：
```bash
# 是否自動創建商機（設為 true 啟用）
CFG_CREATE_OPPORTUNITY=true
```

### 4. 添加錯誤處理

```python
if not customer_id:
    error_msg = (
        f"無法創建任務：缺少客戶ID。"
        f"context.customerId={context.get('customerId')}, "
        f"context.customerCode={context.get('customerCode')}, "
        f"data.customer={data.get('customer')}"
    )
    print(f"[task] ERROR: {error_msg}", flush=True)
    raise ValueError(error_msg)
```

---

## 🧪 測試結果

### 測試腳本
`test_task_auto_creation.py`

### 測試結果
✅ **所有功能正常**

```
客戶提交: ✅ 成功
客戶審核: ✅ 成功
商機創建: ✅ 成功
  - 商機ID: 2412376838450970631
  - 客戶ID: 2412376752570499077
  - 客戶名稱: 測試任務創建客戶_11251026

任務創建: ✅ 成功
  - 任務1 (新增項目): 2412376864220774403
  - 執行人: 維修幫005, 出納008
```

### 日誌驗證

```
[opportunity] Context設置完成 - customerId: 2412376752570499077
[opportunity] Context設置完成 - customerName: 測試任務創建客戶_11251026
[opportunity] Context設置完成 - customerCode: C11251026

[task] Debug - context.customerId: 2412376752570499077
[task] Debug - context.customerName: 測試任務創建客戶_11251026
[task] Debug - context.customerCode: C11251026
[task] Debug - data.customer: 2412376778335059973 (不同的ID，但未使用)
[task] Debug - final customer_id: 2412376752570499077 (使用正確的ID)

[task] response {"code": "200", "message": "操作成功"}
```

---

## 📝 關鍵發現

### CRM API 行為特點

1. **商機創建後返回的客戶ID可能不準確**
   - `create_response.data.customer` 可能與實際的客戶ID不同
   - 應優先使用 `context.customerId`（在商機創建前就已設置）

2. **Context 是可靠的數據源**
   - `context` 在商機創建前就已經完整設置
   - 包含：customerId, customerName, customerCode 等

3. **需要多層後備方案**
   - 第一層：使用 `context.customerId`
   - 第二層：使用 `data.customer`
   - 第三層：通過 `customerCode` 查詢

---

## 🎯 修改文件清單

### 已修改文件

1. **`maqua-members/services/customer_submission.py`**
   - 添加調試日誌（第 1698-1737 行）
   - 添加客戶ID驗證和後備查詢
   - 添加錯誤處理

2. **`.env`**
   - 添加 `CFG_CREATE_OPPORTUNITY=true`

3. **`test_task_auto_creation.py`** (新增)
   - 自動化測試腳本
   - 使用唯一客戶編碼避免重複

4. **`新增商機/TASK_CREATION_ISSUE_ANALYSIS.md`** (新增)
   - 問題分析文檔

---

## ✨ 後續建議

### 1. 保留調試日誌（可選）
目前添加的調試日誌對於排查問題非常有用。建議：
- 生產環境：可以保留，或改為 `logging.debug()` 級別
- 開發環境：保留所有調試日誌

### 2. 定期測試
使用 `test_task_auto_creation.py` 定期測試：
```bash
python3 test_task_auto_creation.py
```

### 3. 監控任務創建
在 CRM 系統中檢查：
- 任務是否正確創建
- 客戶ID是否正確關聯
- 執行人是否正確分配

### 4. 文檔更新
如果需要，更新用戶手冊說明：
- 商機創建後會自動創建三個任務
- 任務類型和執行人分配規則

---

## 📞 聯繫信息

如有問題，請查看：
- 分析文檔：`新增商機/TASK_CREATION_ISSUE_ANALYSIS.md`
- 測試腳本：`test_task_auto_creation.py`
- 日誌文件：`test_task_debug.log`

---

**修復完成時間**: 2025-11-25 10:30  
**修復狀態**: ✅ 已驗證  
**測試狀態**: ✅ 通過
