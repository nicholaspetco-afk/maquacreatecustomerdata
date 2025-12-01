#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import extract_choice, CONFIG

# 測試 extract_choice 函數
test_cases = ["一次性付款", "信用卡付款", "銀行轉帳", "季度月費", "年度付款", "免費試用", "月費"]

for test_input in test_cases:
    print(f"\n測試輸入: '{test_input}'")
    
    # 獲取所有候選項
    candidates = CONFIG["paymentMethods"].keys()
    print(f"候選項數量: {len(candidates)}")
    
    # 調用 extract_choice 函數
    result = extract_choice(test_input, candidates)
    print(f"結果: '{result}'")
    
    # 檢查是否在候選項中
    if result in candidates:
        config = CONFIG["paymentMethods"][result]
        print(f"配置: {config}")
    else:
        print("結果不在候選項中")