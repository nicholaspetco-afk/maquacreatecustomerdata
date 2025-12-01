# 商机字段映射问题彻底分析报告

## 发现日期: 2025-11-20

---

## 问题现象

创建商机后，以下字段在 CRM 中显示为 `--`：
- 目前付款方式
- 使用方式
- 按金
- 預繳金
- 常用聯絡方式

## 根本原因分析

### 1. 数据解析阶段缺失

**文件**: `新增商機/opportunity_builder.py`  
**函数**: `_build_context` (第 335-337 行)

```python
payment_code = _normalize_payment_code(fields.get("paymentMethod"))
if not payment_code:
    payment_code = (customer.get("paymentMethod") or {}).get("id")
```

**问题**: 
- `paymentCode` 只从输入的 "目前付款方式" 字段解析
- 如果用户输入中没有这个字段，`paymentCode` 会是 `None`
- Context 中 `paymentCode=None` 导致后续无法写入

### 2. 字段概念混淆

根据真实 API 数据分析：

| 中文显示名称 | 传统理解 | CRM 实际含义 | define字段 |
|------------|---------|------------|----------|
| 使用方式 | 租/买 | **交易类型** | transType_name |
| 目前付款方式 | 一次性/分期 | **使用方式（租/买）** | define8/attrext8 |
| 常用联络方式 | 电话/邮件 | **方案类型/安装位置** | define9/attrext9 |

**这是语义混乱的根源！**

---

## 解决方案

### 方案 A: 修复解析逻辑（推荐）

在 `opportunity_builder.py` 的 `_build_context` 函数中，添加默认值逻辑：

```python
# 在第 337 行后添加
payment_code = _normalize_payment_code(fields.get("paymentMethod"))
if not payment_code:
    payment_code = (customer.get("paymentMethod") or {}).get("id")

# 添加默认值逻辑
if not payment_code and usage_label:
    # 如果没有付款方式，但有使用方式，将使用方式作为付款方式
    payment_code = usage_label  # "租" 或 "买"
```

### 方案 B: 在写入阶段兜底

在 `customer_submission.py` 的 `_build_opportunity_create_payload` 函数中：

```python
# 第 1145-1154 行，修改为：
payment_code = context.get("paymentCode")
if not payment_code:
    # 兜底逻辑：使用 usageLabel 或固定值
    payment_code = context.get("usageLabel") or "租"

if payment_code:
    head_def["define8"] = str(payment_code)
    data["headDef!define8"] = str(payment_code)
    oppt_char["attrext8"] = str(payment_code)
```

---

## 测试用例

### 测试 1: 完整输入（理想情况）

**输入**:
```
商機名稱: C45636测试商机
客戶: C45636 测试客户
目前付款方式: 信用卡分期
使用方式: 租
按金: 100
預繳金: 100
月費金額: 288
```

**预期结果**:
- `paymentCode = "信用卡分期"`
- `usageLabel = "租"`
- 所有字段都有值

### 测试 2: 缺少付款方式（当前问题）

**输入**:
```
商機名稱: C45636测试商机
客戶: C45636 测试客户
使用方式: 租
按金: 100
```

**当前行为**:
- `paymentCode = None` ❌
- CRM 显示 `--`

**修复后预期**:
- `paymentCode = "租"` ✅
- CRM 显示 "租"

---

## 立即行动项

1. ✅ 确认字段语义（已通过 Network 分析确认）
2. ⬜ 选择修复方案（A 或 B）
3. ⬜ 修改代码
4. ⬜ 测试验证
5. ⬜ 部署上线

---

## 建议

**采用方案 A + B 组合**：
1. 在解析阶段添加默认值（方案 A）
2. 在写入阶段也添加兜底逻辑（方案 B）
3. 双重保障，确保字段不为空

这样即使用户输入不完整，系统也能自动填充合理的默认值。
