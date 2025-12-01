# MAQUA Create Customer (deployable bundle)

This folder is a clean bundle for deployment (Render). Includes Flask app + helper scripts.

## Structure
- `maqua-members/` : Flask app (backend + frontend)
- `新增優化/` : customer builder scripts (includes `customer_builder.py`)
- `.env.example` : sample env vars (copy to `.env` before running; do not commit secrets)
- `.gitignore`

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r maqua-members/requirements.txt
PYTHONPATH=. HOST=0.0.0.0 PORT=8001 FLASK_DEBUG=0 python maqua-members/app.py
```
Open http://localhost:8001/ , password: `maqua28453792`.

## Render deploy
- Root Directory: `maquacreatdata`
- Build Command: `pip install -r maqua-members/requirements.txt`
- Start Command: `PYTHONPATH=. HOST=0.0.0.0 PORT=$PORT FLASK_DEBUG=0 python maqua-members/app.py`

## Notes
- `.env.example` has placeholders only; never commit real secrets.
- `.gitignore` excludes virtualenv/logs/pyc.
