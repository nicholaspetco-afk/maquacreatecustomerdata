# 一鍵新增客戶（精簡打包版）

此資料夾已整理好上傳到 GitHub 的必要檔案，直接部署即可。預設跑 Flask 服務（內含前端頁面）。

## 專案結構
- `maqua-members/`：Flask 後端＋前端頁面
- `.env.example`：環境變數範本（自行複製為 `.env` 並按需填寫）
- `.gitignore`：忽略清單
- `README.md`：你正在看的說明

## 本機執行
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r maqua-members/requirements.txt
HOST=0.0.0.0 PORT=8001 FLASK_DEBUG=0 python maqua-members/app.py
```
開啟 `http://localhost:8001/`，登入密碼：`maqua28453792`。

## Render 部署（推薦，最少步驟）
- Build Command: `pip install -r maqua-members/requirements.txt`
- Start Command: `HOST=0.0.0.0 PORT=$PORT FLASK_DEBUG=0 python maqua-members/app.py`
- 其他保持預設，Deploy 後會拿到網址，與密碼 `maqua28453792` 一起給同事使用。

## Cloudflare Pages + Tunnel（若要用 Pages）
1) 本機啟動：`HOST=0.0.0.0 PORT=8001 FLASK_DEBUG=0 python maqua-members/app.py`
2) 開 Tunnel：`cloudflared tunnel --url http://localhost:8001`
3) Pages 前端代理 /api 到 `BACKEND_URL`（填上 tunnel 公網網址）。

## 注意
- `.env.example` 未含密鑰，若需正式密鑰請自行填入 `.env`（不要 commit 真實密鑰）。
