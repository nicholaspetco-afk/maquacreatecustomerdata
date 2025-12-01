#!/usr/bin/env python3
"""CLI wrapper for customer submission + audit workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_env_files() -> None:
    candidates = [
        Path(__file__).with_name(".env"),
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for env_path in candidates:
        try:
            if not env_path.exists():
                continue
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass


_load_env_files()

ROOT_DIR = Path(__file__).resolve().parents[1]
CRM_DIR = ROOT_DIR / "maqua-members"
if str(CRM_DIR) not in sys.path:
    sys.path.insert(0, str(CRM_DIR))

from services.customer_submission import run_submission  # type: ignore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse sales text → duplicate check → add apply → (optional) audit"
    )
    parser.add_argument("--text", help="Raw sales script text.")
    parser.add_argument("--file", help="Path to text file.")
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON result."
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Submit application only, skip auto audit.",
    )
    args = parser.parse_args()

    if not args.text and not args.file:
        parser.error("請提供 --text 或 --file")

    content = args.text or Path(args.file).read_text(encoding="utf-8")
    result = run_submission(content, skip_audit=args.no_audit)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
