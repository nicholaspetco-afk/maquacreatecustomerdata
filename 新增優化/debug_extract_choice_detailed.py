#!/usr/bin/env python3
"""
調試 extract_choice 函數的執行過程
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import extract_choice, CONFIG

def debug_extract_choice():
    # 測試別名
    test_aliases = [
        "一次性付款",
        "信用卡付款",
        "銀行轉帳",
        "季度月費",
        "年度付款",
        "免費試用",
        "月費"
    ]
    
    print("=== 調試 extract_choice 函數 ===")
    print(f"候選項數量: {len(CONFIG['paymentMethods'].keys())}")
    
    for alias in test_aliases:
        print(f"\n測試別名: '{alias}'")
        
        # 檢查別名是否在 CONFIG 中
        if alias in CONFIG["paymentMethods"]:
            print(f"  別名 '{alias}' 存在於 CONFIG 中")
            config = CONFIG["paymentMethods"][alias]
            print(f"  配置: {config}")
        else:
            print(f"  別名 '{alias}' 不存在於 CONFIG 中")
        
        # 檢查清理後的別名
        cleaned = alias.replace(" ", "")
        print(f"  清理後: '{cleaned}'")
        
        # 檢查是否有匹配的配置
        for key, value in CONFIG["paymentMethods"].items():
            if key.replace(" ", "") == cleaned:
                print(f"  找到匹配的鍵: '{key}'")
                print(f"  配置: {value}")
                
                # 查找這個配置對應的規範鍵名
                for norm_key, norm_value in CONFIG["paymentMethods"].items():
                    if norm_value is value and norm_key in ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]:
                        print(f"  規範鍵名: '{norm_key}'")
                        break
                else:
                    print(f"  未找到規範鍵名")
                break
        else:
            print(f"  未找到匹配的配置")
        
        # 調用 extract_choice 函數
        result = extract_choice(alias, CONFIG["paymentMethods"].keys())
        print(f"  extract_choice 結果: '{result}'")

if __name__ == "__main__":
    debug_extract_choice()