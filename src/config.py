"""共用設定：PROJECT_ROOT、載入 config.yaml 與 .env。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
PAPERS_DIR = DATA_DIR / "papers"
DB_PATH = DATA_DIR / "queue.db"
CSV_PATH = DATA_DIR / "papers_log.csv"

load_dotenv(PROJECT_ROOT / ".env")


def load_config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
