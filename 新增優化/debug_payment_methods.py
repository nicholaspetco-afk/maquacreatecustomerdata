#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import CONFIG

print("CONFIG[\"paymentMethods\"] 的內容:")
for key, value in CONFIG["paymentMethods"].items():
    print(f"  {key}: {value}")

print("\n檢查特定別名是否存在:")
aliases_to_check = ["一次性付款", "信用卡付款", "銀行轉帳", "季度月費", "年度付款", "免費試用", "月費"]
for alias in aliases_to_check:
    exists = alias in CONFIG["paymentMethods"]
    print(f"  {alias}: {'存在' if exists else '不存在'}")
    if exists:
        print(f"    值: {CONFIG['paymentMethods'][alias]}")