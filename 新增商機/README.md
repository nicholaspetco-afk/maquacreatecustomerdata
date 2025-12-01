# 新增商機

此資料夾提供「商機」欄位解析工具，方便將銷售文案轉成 CRM 需要的欄位：

- `opportunity_builder.py`：解析銷售文案（與新增客戶相同的原始文字），輸出包含商機欄位、契約起訖日、預估簽單金額等資訊，後續由 `services/customer_submission.py` 重新組裝為 CRM API payload。
- CLI 範例：
  ```bash
  cd "../一鍵新增客戶0501"
  python3 新增商機/opportunity_builder.py sample_input.txt --pretty
  ```
  若已經執行 `customer_builder.py`，也可將 JSON 結果傳入，補齊客戶欄位預設值：
  ```bash
  python3 新增商機/opportunity_builder.py sample_input.txt --customer-json 新增優化/customer_result.json
  ```

## 解析結果
程式會輸出：

- `fields`：由文案逐行解析的原始欄位。
- `context`：建議的商機欄位（商機名稱、商機日期、使用方式、付款代碼、契約年期、月費、按金、預繳、安裝位置、備註等）。
- `warnings`：缺漏欄位或推導時的提示。

`maqua-members/services/customer_submission.py` 會在新增客戶後引用這些欄位，自動完成商機查重與新增流程。
