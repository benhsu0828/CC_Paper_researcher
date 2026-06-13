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
RESEARCH_PROFILE_PATH = PROJECT_ROOT / "research_profile.md"

load_dotenv(PROJECT_ROOT / ".env")


def load_config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_research_profile(max_chars: int | None = None) -> str:
    """讀使用者的研究脈絡（research_profile.md：目前進度/實驗架構/想解決的問題等）。

    檔案不存在或空白 → 回傳 ''（系統退回只依 topic + 摘要判斷，行為不變）。
    max_chars 給 rank（haiku，context 要省）用的精簡上限；screen（opus）不截斷。
    """
    try:
        text = RESEARCH_PROFILE_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    if not text:
        return ""
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…（後略，完整脈絡見 research_profile.md）"
    return text


def research_profile_block(text: str) -> str:
    """把研究脈絡包成可注入 prompt 的區塊；空字串給中性佔位（不破壞 .format）。"""
    if not text:
        return "（使用者未提供研究脈絡，請僅依研究主題與論文摘要判斷。）"
    return "## 我的研究脈絡（請一併評估每篇論文對它的可用性與啟發性）\n" + text


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
