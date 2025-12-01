#!/usr/bin/env python3
"""
直接調用 extract_choice 函數並打印詳細信息
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import extract_choice, CONFIG

def debug_extract_choice_simple():
    # 測試別名
    test_alias = "一次性付款"
    
    print("=== 直接調用 extract_choice 函數 ===")
    print(f"測試別名: '{test_alias}'")
    
    # 直接調用 extract_choice 函數
    result = extract_choice(test_alias, CONFIG["paymentMethods"].keys())
    print(f"extract_choice 結果: '{result}'")
    
    # 檢查 CONFIG 中的內容
    print("\n=== 檢查 CONFIG 中的內容 ===")
    for key, value in CONFIG["paymentMethods"].items():
        if key == "一次性付款":
            print(f"鍵: '{key}', 值: {value}")
            # 檢查這個值是否與其他鍵共享
            for other_key, other_value in CONFIG["paymentMethods"].items():
                if other_value is value and other_key != key:
                    print(f"  與鍵 '{other_key}' 共享同一個值對象")
                    if other_key in ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]:
                        print(f"  '{other_key}' 是規範鍵名")

if __name__ == "__main__":
    debug_extract_choice_simple()