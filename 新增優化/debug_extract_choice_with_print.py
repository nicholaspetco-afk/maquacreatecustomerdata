#!/usr/bin/env python3
"""
修改 extract_choice 函數，添加調試輸出
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from customer_builder import CONFIG
import re
from typing import Optional, Iterable

def extract_choice_debug(value: Optional[str], candidates: Iterable[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", "", value)
    print(f"清理後的值: '{cleaned}'")
    
    # 首先檢查是否為數字代碼（01、02 等）
    if re.match(r'^\d{2}$', cleaned):
        print("檢測到數字代碼")
        # 通過數字代碼查找對應的候選項
        for choice in candidates:
            config = CONFIG["paymentMethods"].get(choice, {})
            if config.get("id") == cleaned:
                print(f"找到匹配的數字代碼: '{choice}'")
                return choice
    
    # 檢查括號內的內容
    paren_matches = re.findall(r"[（(]([^（）()]+)[）)]", cleaned)
    if paren_matches:
        print("檢測到括號內容")
        candidate = re.sub(r"(這次試試|本次|試試)", "", paren_matches[-1])
        candidate = candidate.strip()
        if candidate:
            for choice in candidates:
                if choice in candidate:
                    print(f"找到匹配的括號內容: '{choice}'")
                    return choice
    
    # 檢查是否直接匹配候選項
    for choice in candidates:
        if choice.replace(" ", "") == cleaned:
            print(f"直接匹配候選項: '{choice}'")
            return choice
    
    # 檢查是否為候選項的一部分
    for choice in candidates:
        if cleaned in choice.replace(" ", ""):
            print(f"候選項的一部分: '{choice}'")
            return choice
    
    # 檢查候選項是否為輸入的一部分
    for choice in candidates:
        if choice.replace(" ", "") in cleaned:
            print(f"輸入的一部分: '{choice}'")
            return choice
    
    # 檢查是否為 CONFIG["paymentMethods"] 中的別名
    print("檢查是否為 CONFIG 中的別名")
    for alias, config in CONFIG["paymentMethods"].items():
        if alias.replace(" ", "") == cleaned:
            print(f"找到匹配的別名: '{alias}'")
            # 查找這個配置對應的規範鍵名
            canonical_keys = ["一次性全繳", "信用卡分期", "銀行卡自動轉賬", "季度收費", "年度收費", "試用", "每月收費"]
            for key in canonical_keys:
                if key in CONFIG["paymentMethods"] and CONFIG["paymentMethods"][key] is config:
                    print(f"找到規範鍵名: '{key}'")
                    return key
            # 如果找不到規範鍵名，返回別名
            print(f"找不到規範鍵名，返回別名: '{alias}'")
            return alias
    
    print("沒有找到匹配項")
    return None

def test_debug_extract_choice():
    # 測試
    test_alias = "一次性付款"
    print(f"測試別名: '{test_alias}'")
    
    # 調用修改後的 extract_choice 函數
    result = extract_choice_debug(test_alias, CONFIG["paymentMethods"].keys())
    print(f"最終結果: '{result}'")

if __name__ == "__main__":
    test_debug_extract_choice()