# CRM 字段映射终极分析报告

## 基于真实 CRM API 响应的字段映射

### 日期: 2025-11-21

---

## 1. 实际 CRM 字段映射（从 getbyid 响应确认）

### 已确认的映射：

| CRM 显示名称 | headDef 字段 | opptDefineCharacter 字段 | 实际示例值 |
|------------|-------------|------------------------|----------|
| **使用方式** | `define8` | `attrext8` | "購買" / "租用" |
| **方案类型/摘要** | `define9` | `attrext9` | "HS990+4FC..." |

### 付款方式字段（多源容错）：

付款方式**不在** define/attrext 系统中！程序会尝试从以下字段读取：

1. `paymentMethod` / `paymentMethodName`
2. `paymentWay` / `payWay_name` / `paywayName`
3. `merchantAppliedDetail.payway` / `.paywayId` / `.paymentMethodId`
4. 备用：`customerIndustry.name`（包含关键词如"信用卡分期"）

### 费用字段：

| 字段名 | 候选位置 |
|-------|---------|
| 月费 | `monthlyFee`, `rentAmount`, `headDef.define10/11`, `attrext12/10` |
| 按金 | `opptDefineCharacter.attrext16` + 兼容 `attrext12` |
| 预缴金 | `opptDefineCharacter.attrext11` + 兼容 `attrext10` |

---

## 2. 当前问题分析

### 问题 1: 安装位置显示客户名字

**现象**: 安装位置显示为 "C45641澳門張學友66777629" 而不是真实地址

**原因**:
1. 用户输入的格式可能是：
   ```
   安裝位置
   C45641澳門張學友66777629
   ```
   标签和值在不同行

2. 或者用户直接将客户名复制到了安装位置字段

**您的修复** (opportunity_builder.py:375-405):
- 添加了检测逻辑：如果 `installLocation` 包含 `C\d+` 模式，则替换为客户地址
- 如果方案类型像地址（包含"座"、"楼"等），则使用方案类型作为安装位置

**建议**:
- ✅ 修复逻辑正确
- 需要确保 `customer.address` 有值
- 或者从 `planType` 中提取地址信息

### 问题 2: 付款方式/预缴金/按金显示 `--`

**可能原因**:

#### A) CRM 不认可这些字段
您尝试写入的字段（define7, define9, payWay等）可能 CRM 根本不接受

**证据**: 从实际响应看，只有 define8/define9 有值，其他都没有

#### B) 保存时被清理掉了
`_cleanup` 函数会移除 None/空值，可能字段被清理了

#### C) CRM API 需要特定的字段结构
paymentMethod 等可能需要特殊格式（如对象而非字符串）

---

## 3. 建议的修复方案

### 方案 A: 简化字段映射，只写入确认有效的字段

```python
#只写入已验证的字段：
head_def["define8"] = str(usage_label)  # 使用方式
oppt_char["attrext8"] = str(usage_label)

head_def["define9"] = str(payment_code or plan_type)  # 付款方式或方案类型
oppt_char["attrext9"] = str(payment_code or plan_type)

# 费用字段单独处理
data["remark"] = f"按金:{deposit}, 预缴金:{prepay}, 月费:{monthly_fee}"
```

### 方案 B: 使用 CRM 的主字段而非自定义字段

```python
# 付款方式写入主字段
data["merchantAppliedDetail"] = {
    "payway": payment_code,
    "paywayId": payment_code,
    "paymentMethod": payment_code
}

# 或者
data["paymentMethod"] = payment_code
data["paymentWay"] = payment_code
```

### 方案 C: 检查保存响应，确认哪些字段被接受

添加日志打印 CRM 的保存响应：
```python
print(f"[DEBUG] CRM Save Response: {create_response}")
```

---

## 4. 立即行动项

### 测试 1: 验证当前修改是否生效

创建一个测试商机，使用完整输入：
```
商機名稱: TEST001测试
客戶: C12345 测试客户
安裝位置: 澳门氹仔濠庭都會13座10樓B
目前付款方式: 01
使用方式: 租用
方案類型: FH301测试方案
按金: 1000
預繳金: 500
月費金額: 288
```

### 测试 2: 查看服务器完整 payload 日志

我已添加的日志会打印：
```
[DEBUG] Complete Opportunity Payload:
{完整 JSON}
```

请创建测试商机后，分享完整的日志输出

### 测试 3: 对比手动创建的商机

在 CRM 界面手动创建一个商机，填写所有字段，然后：
1. 用 Chrome DevTools 捕获保存请求
2. 对比我们发送的 payload 和手动的有什么不同

---

## 5. 您已完成的优秀修复

✅ `_normalize_placeholder` - 处理 "--" 占位符
✅ `_parse_lines` 改进 - 支持多行格式  
✅ 安装位置智能检测 - 避免使用客户名
✅ 多字段冗余写入 - 提高兼容性

---

## 下一步

请提供：
1. 创建测试商机后的完整服务器日志（包含 payload）
2. 或者手动创建商机时的 Network 请求 payload
3. CRM 的保存响应中是否有错误信息

这样我才能确定是哪个环节的问题！
