#!/usr/bin/env python3
"""
直接測試 extract_choice 函數的執行過程
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import CONFIG

def test_extract_choice():
    # 直接複製 extract_choice 函數的邏輯
    def extract_choice_debug(value, candidates):
        if not value:
            return None
        
        import re
        cleaned = re.sub(r"\s+", "", value)
        
        # 檢查是否為 CONFIG["paymentMethods"] 中的別名
        for alias, config in CONFIG["paymentMethods"].items():
            if alias.replace(" ", "") == cleaned:
                print(f"找到匹配的別名: '{alias}'")
                # 查找這個配置對應的規範鍵名
                canonical_keys = ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]
                for key in canonical_keys:
                    if key in CONFIG["paymentMethods"] and CONFIG["paymentMethods"][key] is config:
                        print(f"找到規範鍵名: '{key}'")
                        print(f"返回規範鍵名: '{key}'")
                        return key
                # 如果找不到規範鍵名，返回別名
                print(f"找不到規範鍵名，返回別名: '{alias}'")
                return alias
        
        print("沒有找到匹配的別名")
        return None
    
    # 測試
    test_alias = "一次性付款"
    result = extract_choice_debug(test_alias, CONFIG["paymentMethods"].keys())
    print(f"最終結果: '{result}'")

if __name__ == "__main__":
    test_extract_choice()