#!/usr/bin/env python3
"""
直接測試原始的 extract_choice 函數的執行過程
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import extract_choice, CONFIG

def test_original_extract_choice():
    # 測試
    test_alias = "一次性付款"
    print(f"測試別名: '{test_alias}'")
    
    # 直接調用原始的 extract_choice 函數
    result = extract_choice(test_alias, CONFIG["paymentMethods"].keys())
    print(f"extract_choice 結果: '{result}'")
    
    # 檢查 CONFIG 中的內容
    print("\n=== 檢查 CONFIG 中的內容 ===")
    for key, value in CONFIG["paymentMethods"].items():
        if key in ["一次性付款", "一次性全繳"]:
            print(f"鍵: '{key}', 值: {value}, id: {id(value)}")

if __name__ == "__main__":
    test_original_extract_choice()