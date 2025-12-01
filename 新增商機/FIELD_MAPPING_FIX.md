# 商机字段映射修复说明

## 问题描述

根据用户提供的截图，在创建商机时发现以下字段没有正确填写，显示为 `--`：

1. **目前付款方式** - 应该显示支付方式代码（如 "01", "02" 等）
2. **使用方式** - 应该显示使用模式标签
3. **预测金** - 应该显示按金金额
4. **最近跟进时间** - 时间字段（需要进一步确认映射）
5. **税金** - 税金字段（需要进一步确认映射）

## 根本原因

在 `customer_submission.py` 的 `_build_opportunity_create_payload` 函数中存在以下问题：

### 问题 1: 使用方式和付款方式混淆（第 1107 行）

**原代码：**
```python
usage_label = context.get("usageLabel") or context.get("paymentCode")
if usage_label:
    head_def["define8"] = usage_label
    data["headDef!define8"] = usage_label
```

**问题：** 
- `usageLabel`（使用方式）和 `paymentCode`（付款方式）是两个不同的字段
- 当 `usageLabel` 为空时，会错误地使用 `paymentCode` 填充
- 导致付款方式没有独立的字段设置

### 问题 2: character_payload 字段映射冲突（第 1049 行）

**原代码：**
```python
monthly_value = _format_amount(context.get("monthlyFee"))
if monthly_value:
    character_payload["attrext12"] = monthly_value
    character_payload["attrext10"] = monthly_value  # 冲突！
```

**问题：**
- `attrext10` 应该是预缴金 `prepay` 的字段
- 但被错误地设置为月费 `monthlyFee`
- 导致预缴金字段无法正确设置

### 问题 3: 缺少 paymentCode 的独立映射

**问题：**
- `paymentCode` 没有自己的字段映射
- 导致"目前付款方式"字段在CRM中显示为空

### 问题 4: deposit 和 prepay 在 character_payload 中缺失

**问题：**
- `deposit`（按金/预测金）和 `prepay`（预缴金）只在 `headDef` 中设置
- 但没有在 `character_payload` 中设置
- 导致某些UI可能读取不到这些值

## 修复方案

### 修复 1: 分离使用方式和付款方式的设置

```python
# 设置使用方式 (define8/attrext8)
usage_label = context.get("usageLabel")
if usage_label:
    head_def["define8"] = usage_label
    data["headDef!define8"] = usage_label
    data.setdefault("opptDefineCharacter", {})
    data["opptDefineCharacter"].setdefault("attrext8", usage_label)

# 设置目前付款方式 (define7/attrext7) - 独立字段
payment_code = context.get("paymentCode")
if payment_code:
    head_def["define7"] = payment_code
    data["headDef!define7"] = payment_code
    data.setdefault("opptDefineCharacter", {})
    data["opptDefineCharacter"].setdefault("attrext7", payment_code)
```

### 修复 2: 完善 character_payload 字段映射

```python
character_payload: Dict[str, Any] = {}

# 设置使用方式
if context.get("usageLabel"):
    character_payload["attrext8"] = context["usageLabel"]

# 设置方案类型
if context.get("planType"):
    character_payload["attrext9"] = context["planType"]

# 设置目前付款方式
if context.get("paymentCode"):
    character_payload["attrext7"] = context["paymentCode"]

# 设置预缴金（attrext10）
if context.get("prepay") is not None:
    prepay_value = _format_amount(context.get("prepay"))
    if prepay_value:
        character_payload["attrext10"] = prepay_value

# 设置预测金/按金（attrext11）
if context.get("deposit") is not None:
    deposit_value = _format_amount(context.get("deposit"))
    if deposit_value:
        character_payload["attrext11"] = deposit_value

# 设置月费金额（attrext12）- 不再设置到 attrext10
monthly_value = _format_amount(context.get("monthlyFee"))
if monthly_value:
    character_payload["attrext12"] = monthly_value
```

## 字段映射总结

| CRM字段名称 | headDef 字段 | character 字段 | context 来源 |
|------------|-------------|---------------|-------------|
| 目前付款方式 | define7 | attrext7 | paymentCode |
| 使用方式 | define8 | attrext8 | usageLabel |
| 安装位置 | define9 | attrext9 | planType |
| 预缴金 | define10 | attrext10 | prepay |
| 预测金/按金 | define11 | attrext11 | deposit |
| 月费金额 | (无) | attrext12 | monthlyFee |
| 合约开始日期 | (无) | attrext2 | contractStartDate |
| 合约结束日期 | (无) | attrext3 | contractEndDate |
| 合约年期 | (无) | attrext4 | contractYears |
| 合约号码 | (无) | attrext19 | contractNumber |

## 测试建议

1. **测试付款方式字段**：确认"目前付款方式"能正确显示支付代码
2. **测试使用方式字段**：确认"使用方式"能正确显示使用模式标签
3. **测试预测金字段**：确认"预测金"能正确显示按金金额
4. **测试预缴金字段**：确认"预缴金"不会被月费覆盖
5. **测试月费字段**：确认月费仍能正确显示

## 注意事项

1. **最近跟进时间** 和 **税金** 字段可能需要额外的映射，目前代码中未找到相关设置
2. 如果这两个字段仍然显示为空，需要：
   - 确认 CRM 系统中这些字段的实际字段名
   - 在 `_build_opportunity_create_payload` 函数中添加相应的映射
   - 在 `opportunity_builder.py` 中添加对应的解析逻辑

## 后续排查

如果修复后仍有字段显示为 `--`，请检查：

1. **输入文本格式**：确认用户输入的文本中包含了所有必要的字段标签和值
2. **字段解析**：在 `opportunity_builder.py` 的 `LABEL_MAP` 中确认所有标签都有对应的映射
3. **context 传递**：确认 `parse_opportunity_text` 正确解析并传递了所有字段
4. **CRM API 响应**：检查 CRM 返回的错误信息，确认字段名是否正确
