#!/usr/bin/env python3
"""Web server for testing customer_builder functionality on port 5025."""

from flask import Flask, request, jsonify, render_template_string
import json
import os
from pathlib import Path

# Import our customer_builder
import customer_builder

app = Flask(__name__)

# HTML template for the web interface
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>客戶資料測試器 - 端口 5025</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .input-section, .output-section { 
            border: 1px solid #ccc; 
            padding: 20px; 
            margin: 20px 0; 
            border-radius: 5px; 
        }
        textarea { 
            width: 100%; 
            height: 200px; 
            font-family: monospace; 
            font-size: 12px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        button:hover { background-color: #45a049; }
        .result { 
            background-color: #f9f9f9; 
            padding: 15px; 
            border-radius: 5px; 
            white-space: pre-wrap; 
            font-family: monospace;
            font-size: 12px;
            max-height: 500px;
            overflow-y: auto;
        }
        .highlight { 
            background-color: #ffffcc; 
            padding: 2px 4px; 
            border-radius: 2px;
        }
        .field-mapping {
            background-color: #e8f5e8;
            padding: 10px;
            margin: 10px 0;
            border-left: 4px solid #4CAF50;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🧪 客戶資料測試器 (端口 5025)</h1>
        <p>測試修改後的代碼，特別是付款方式映射到 merchantAppliedDetail.payway 的功能</p>
        
        <div class="input-section">
            <h3>輸入客戶資料</h3>
            <textarea id="customerText" placeholder="請輸入客戶資料...
客戶名稱: 測試客戶
聯繫電話: 63588818
安裝時間: 11/17 10:00
總金額: 29131.2
備註: 測試備註
客戶分類: 餐飲業
付款方式: 季度收費
使用方式: 租
月費金額: 2856
按金: 10282
預繳金: 10281
安裝內容: fh200*2+mf220*2">{{ sample_text }}</textarea>
            <br><br>
            <button onclick="parseCustomer()">🔍 解析客戶資料</button>
            <button onclick="loadSample()">📋 載入範例</button>
            <button onclick="clearAll()">🗑️ 清除</button>
        </div>
        
        <div id="results" class="output-section" style="display: none;">
            <h3>解析結果</h3>
            <div class="field-mapping">
                <strong>🎯 重點測試欄位映射:</strong><br>
                • paymentMethod → merchantAppliedDetail.payway<br>
                • usageMode → largeText1<br>
                • installContent → largeText2<br>
                • monthlyFee → largeText3<br>
                • remark → largeText4
            </div>
            <div id="resultContent" class="result"></div>
        </div>
    </div>

    <script>
        function parseCustomer() {
            const text = document.getElementById('customerText').value;
            if (!text.trim()) {
                alert('請輸入客戶資料');
                return;
            }
            
            fetch('/parse', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ text: text })
            })
            .then(response => response.json())
            .then(data => {
                document.getElementById('results').style.display = 'block';
                document.getElementById('resultContent').textContent = JSON.stringify(data, null, 2);
                
                // 高亮顯示重點欄位
                let content = document.getElementById('resultContent').innerHTML;
                content = content.replace(/"merchantAppliedDetail":/g, '<span class="highlight">"merchantAppliedDetail":</span>');
                content = content.replace(/"largeText[1-4]":/g, '<span class="highlight">$&</span>');
                document.getElementById('resultContent').innerHTML = content;
            })
            .catch(error => {
                alert('解析失敗: ' + error.message);
            });
        }
        
        function loadSample() {
            document.getElementById('customerText').value = `客戶名稱: 粵匠餐飲集團
聯繫電話: 63588818
安裝時間: 11/17 10:00
總金額: 29131.2
備註: fh200、mf220、dc2000、10吋pp每六個月更換一次，mc2、RO900S第一道每一年換一次，RO900S第二道每兩年更換一次
客戶分類: 餐飲業
付款方式: 季度收費
使用方式: 租
月費金額: 2856
按金: 10282
預繳金: 10281
安裝內容: fh200*2+mf220*2+hs990+MC2+dc2000+RO900S*3+10吋pp*3+3G壓力桶*3個+304直飲龍頭*2個`;
        }
        
        function clearAll() {
            document.getElementById('customerText').value = '';
            document.getElementById('results').style.display = 'none';
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    sample_text = """客戶名稱: 粵匠餐飲集團
聯繫電話: 63588818
安裝時間: 11/17 10:00
總金額: 29131.2
備註: fh200、mf220、dc2000、10吋pp每六個月更換一次，mc2、RO900S第一道每一年換一次，RO900S第二道每兩年更換一次
客戶分類: 餐飲業
付款方式: 季度收費
使用方式: 租
月費金額: 2856
按金: 10282
預繳金: 10281
安裝內容: fh200*2+mf220*2+hs990+MC2+dc2000+RO900S*3+10吋pp*3+3G壓力桶*3個+304直飲龍頭*2個"""
    return render_template_string(HTML_TEMPLATE, sample_text=sample_text)

@app.route('/parse', methods=['POST'])
def parse_customer():
    try:
        data = request.get_json()
        text = data.get('text', '')
        
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        
        # Use the customer_builder to parse the text
        result = customer_builder.build_crm_payload(text)
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'port': 5025})

if __name__ == '__main__':
    print("🚀 啟動客戶資料測試器...")
    print("📍 訪問 http://localhost:5025 來測試修改後的代碼")
    print("🎯 特別測試: paymentMethod → merchantAppliedDetail.payway 映射")
    
    app.run(host='0.0.0.0', port=5025, debug=True)
