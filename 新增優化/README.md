# 新增優化 · 客戶文案轉建檔

把行政提供的文案轉成可直接提交 CRM 的欄位。`customer_builder.py` 會解析每一行「標籤：內容」，自動帶入以下規則：

- **客戶名稱格式**：`客戶編碼 + 名稱 + 電話`。
- **資質資訊**：預設 `個人 / 其他`，可透過環境變數覆寫。
- **銷售區域**：依地址判斷（澳門島 / 氹仔 / 珠海）。
- **負責人對應**：寧 → James、成 → 梁必成、Liz → 李潤婷，無匹配則使用預設。
- **付款/使用方式**：從複選文字（含括號提示）自動選擇，並同步到自定義特徵欄位。

解析完成後會輸出：

1. `normalized`：整合後的欄位（含原始行）。
2. `crmPayload`：分成重複檢查、申請單與客戶檔案三段 payload。
3. `warnings`：缺漏或無法判斷的欄位提醒。

## 快速使用

```bash
cd "V8 查詢用戶指定資料 "/新增優化
python3 customer_builder.py sample_input.txt --pretty
```

或直接貼上文字：

```bash
python3 customer_builder.py --text "$(pbpaste)" --pretty
```

若要輸出到檔案：

```bash
python3 customer_builder.py sample_input.txt --pretty --output payload.json
```

## 自訂參數

可透過環境變數覆寫 CRM 欄位 ID。常用項目：

| 環境變數 | 預設值 | 說明 |
| --- | --- | --- |
| `CFG_SALEAREA_MO_ID` | `SALEAREA_MO_ID` | 澳門島銷售區域 ID |
| `CFG_CLASS_BIZ_ID` | `CLASS_BIZ_ID` | 商用客戶分類 ID |
| `CFG_PAYMENT_QUARTERLY_ID` | `PAYMENT_QUARTERLY_ID` | 季度收費付款方式 ID |
| `CFG_USAGE_RENT_ID` | `USAGE_RENT_ID` | 使用方式（租） ID |
| `CFG_OWNER_JAMES_ID` | `OWNER_JAMES_ID` | James 的 CRM 使用者 ID |
| `CFG_QUAL_ENTERPRISE` | `個人` | 資質資訊中的企業類型 |
| `CFG_QUAL_TYPE` | `其他` | 資質資訊中的資格分類 |
| `CFG_CHAR_*` | `CHAR_…` | 自定義特徵欄位 key（總金額 / 月費 / 按金 / 預繳金 / 安裝時間 / 備註等） |

> 以 `export CFG_CLASS_BIZ_ID=1482638189791805446` 方式寫入終端環境即可。

### 環境變數載入

`customer_builder.py` 會在初始化時自動載入 `.env`（優先讀取專案根目錄，其次讀取 `新增優化/.env`）。你可以直接把常用的 CRM 字典 ID 寫入 `.env`，例如：

```
# 銷售區域
CFG_SALEAREA_MO_ID=1482639830460399618
CFG_SALEAREA_MO_CODE=001
CFG_SALEAREA_TAI_ID=1482639942129549313
CFG_SALEAREA_TAI_CODE=002
CFG_SALEAREA_ZH_ID=1789854460290793480
CFG_SALEAREA_ZH_CODE=003

# 客戶分類
CFG_CLASS_HOME_ID=1482638121070755844
CFG_CLASS_HOME_CODE=001
CFG_CLASS_BIZ_ID=1482638189791805446
CFG_CLASS_BIZ_CODE=002
CFG_CLASS_GOV_ID=1482638816869613570
CFG_CLASS_GOV_CODE=006

# Owner（可擴充）
CFG_OWNER_JAMES_ID=1634633148216115210
CFG_OWNER_LIANG_ID=1675717018645954563
CFG_OWNER_LIZ_ID=1804041613437042698
CFG_OWNER_DEFAULT_ID=1634633148216115210
CFG_OWNER_DEFAULT_NAME=James

# 使用/付款（待補實際 ID）
CFG_USAGE_RENT_ID=USAGE_RENT_ID
CFG_USAGE_BUY_ID=USAGE_BUY_ID
CFG_PAYMENT_QUARTERLY_ID=PAYMENT_QUARTERLY_ID
CFG_PAYMENT_ONETIME_ID=PAYMENT_ONETIME_ID

# 特徵欄位 key（待補，如 decimal7/largeText3）
CFG_CHAR_TOTAL_AMOUNT=CHAR_TOTAL_AMOUNT
CFG_CHAR_MONTHLY_FEE=CHAR_MONTHLY_FEE
CFG_CHAR_DEPOSIT=CHAR_DEPOSIT
CFG_CHAR_PREPAY=CHAR_PREPAY
CFG_CHAR_INSTALL_TIME=CHAR_INSTALL_TIME
CFG_CHAR_INSTALL_CONTENT=CHAR_INSTALL_CONTENT
CFG_CHAR_REMARK=CHAR_REMARK
CFG_CHAR_USAGE_MODE=CHAR_USAGE_MODE
CFG_CHAR_PAYMENT_METHOD=CHAR_PAYMENT_METHOD
```

## 測試檔案

- `sample_input.txt`：題示中的「粵匠餐飲集團 C99999」文案，可直接重現截圖欄位。

執行後可見系統自動判斷：

- 客戶分類 → 商用客戶
- 付款方式 → 季度收費
- 使用方式 → 租
- 銷售區域 → 澳門島
- 負責人 → James（因地址判斷為澳門且無特別指定）

若有額外欄位或新的關鍵字，只需要在 `customer_builder.py` 內擴充對應表即可。歡迎再提供其它文案範本，我們可以一併加入測試案例。💪
