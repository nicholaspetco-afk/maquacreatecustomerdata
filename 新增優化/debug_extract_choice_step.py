#!/usr/bin/env python3
"""
直接調用 extract_choice 函數並打印詳細信息
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import extract_choice, CONFIG

def debug_extract_choice_detailed():
    # 測試別名
    test_alias = "一次性付款"
    
    print("=== 直接調用 extract_choice 函數 ===")
    print(f"測試別名: '{test_alias}'")
    
    # 手動模擬 extract_choice 函數的執行過程
    alias = test_alias
    cleaned = alias.replace(" ", "")
    
    print(f"清理後的別名: '{cleaned}'")
    
    # 檢查是否為 CONFIG["paymentMethods"] 中的別名
    found_alias = None
    found_config = None
    
    for a, config in CONFIG["paymentMethods"].items():
        if a.replace(" ", "") == cleaned:
            found_alias = a
            found_config = config
            print(f"找到匹配的別名: '{a}', 配置: {config}")
            break
    
    if found_alias:
        # 查找這個配置對應的規範鍵名
        canonical_keys = ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]
        for key in canonical_keys:
            if key in CONFIG["paymentMethods"] and CONFIG["paymentMethods"][key] is found_config:
                print(f"找到規範鍵名: '{key}'")
                print(f"CONFIG['paymentMethods'][{key}] is found_config: {CONFIG['paymentMethods'][key] is found_config}")
                print(f"id(CONFIG['paymentMethods'][{key}]): {id(CONFIG['paymentMethods'][key])}")
                print(f"id(found_config): {id(found_config)}")
                break
    
    # 直接調用 extract_choice 函數
    result = extract_choice(test_alias, CONFIG["paymentMethods"].keys())
    print(f"extract_choice 結果: '{result}'")

if __name__ == "__main__":
    debug_extract_choice_detailed()