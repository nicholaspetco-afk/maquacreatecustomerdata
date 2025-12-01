#!/usr/bin/env python3
"""
測試付款方式識別功能
"""

import sys
import os

# 添加當前目錄到 Python 路徑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from customer_builder import parse_customer_text

def test_payment_methods():
    """測試各種付款方式的識別"""
    
    # 測試數字代碼
    test_cases = [
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 01", "一次性全繳"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 02", "信用卡分期"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 03", "銀行卡自動轉賬"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 04", "季度收費"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 05", "年度收費"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 06", "試用"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 07", "每月收費"),
    ]
    
    # 測試別名
    alias_test_cases = [
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 一次性付款", "一次性全繳"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 信用卡付款", "信用卡分期"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 銀行轉帳", "銀行卡自動轉賬"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 季度月費", "季度收費"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 年度付款", "年度收費"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 免費試用", "試用"),
        ("客戶名稱: C123 測試客戶\n聯繫電話: 12345678\n付款方式: 月費", "每月收費"),
    ]
    
    print("測試數字代碼識別...")
    for i, (input_text, expected) in enumerate(test_cases):
        result = parse_customer_text(input_text)
        actual = result["normalized"]["paymentLabel"]
        status = "✓" if actual == expected else "✗"
        print(f"{status} 測試 {i+1}: 輸入 '{input_text.split('付款方式: ')[1]}' -> 預期 '{expected}' -> 實際 '{actual}'")
    
    print("\n測試別名識別...")
    for i, (input_text, expected) in enumerate(alias_test_cases):
        result = parse_customer_text(input_text)
        actual = result["normalized"]["paymentLabel"]
        status = "✓" if actual == expected else "✗"
        print(f"{status} 測試 {i+1}: 輸入 '{input_text.split('付款方式: ')[1]}' -> 預期 '{expected}' -> 實際 '{actual}'")
    
    # 測試預設值
    print("\n測試預設值...")
    input_text = "客戶名稱: C123 測試客戶\n聯繫電話: 12345678"
    result = parse_customer_text(input_text)
    actual = result["normalized"]["paymentLabel"]
    expected = "一次性全繳"  # 修改後的預設值
    status = "✓" if actual == expected else "✗"
    print(f"{status} 預設值測試: 無付款方式輸入 -> 預期 '{expected}' -> 實際 '{actual}'")

if __name__ == "__main__":
    test_payment_methods()