"""SQLite 持久化佇列 + papers_log.csv 匯出。

狀態流：queued → selected → analyzed → illustrated → reviewed → published
其他終端/中斷狀態：deferred（額度耗盡，下次續跑）、skipped（screener 篩掉）、error。
設計內失敗（如未產出檔案）走 mark_failed：累加 retry_count、留原狀態下次續跑；
累計達 MAX_RETRIES 才設 status='error' 終止重試（避免永久壞掉的論文每晚重跑、霸佔名額）。
orchestrator 抓到未預期例外（程式bug）也直接設 status='error'。

每篇論文一列，arxiv_id 為主鍵，天然去重。
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from typing import Any

from src.config import CSV_PATH, DB_PATH, DATA_DIR

# 進行中狀態（中斷後下一晚要續跑的）
ACTIVE_STATUSES = ("selected", "analyzed", "illustrated", "enriched", "reviewed", "deferred")

# ponytail: 每篇累計失敗達此數 → status='error' 停止重試（跨夜累計、不重置）。夠用，真要分 stage 再說。
MAX_RETRIES = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id        TEXT PRIMARY KEY,
    title           TEXT,
    authors         TEXT,
    published       TEXT,
    source          TEXT,
    url             TEXT,
    pdf_url         TEXT,
    abstract        TEXT,
    citation_count  INTEGER DEFAULT 0,
    topic           TEXT,
    discovered_date TEXT,
    rank_score      REAL,
    screen_decision TEXT,
    screen_reason   TEXT,
    status          TEXT DEFAULT 'queued',
    read_date       TEXT,
    notion_url      TEXT,
    innovation_score REAL,
    relevance_score  REAL,
    error_msg       TEXT,
    retry_count     INTEGER DEFAULT 0,
    updated_at      TEXT
);
"""

# 既有 DB 的輕量遷移：補上後加的欄位
_MIGRATIONS = [
    "ALTER TABLE papers ADD COLUMN innovation_score REAL",
    "ALTER TABLE papers ADD COLUMN relevance_score REAL",
    "ALTER TABLE papers ADD COLUMN figures TEXT",
    "ALTER TABLE papers ADD COLUMN review_verdict TEXT",   # 審稿結論（如 推薦/小修/大修/拒）
    "ALTER TABLE papers ADD COLUMN review_take TEXT",      # 一句銳評
    "ALTER TABLE papers ADD COLUMN published_at TEXT",     # 寫入 Notion 的時間
    "ALTER TABLE papers ADD COLUMN screen_track TEXT",     # 篩選軌：core（貼近進度）/ explore（跳脫進度的創新）
    "ALTER TABLE papers ADD COLUMN retry_count INTEGER DEFAULT 0",  # 累計失敗次數，達 MAX_RETRIES 設 error
]


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # 欄位已存在


def known_ids() -> set[str]:
    """已在庫的 arxiv_id（給 discovery 去重）。"""
    with connect() as conn:
        return {r["arxiv_id"] for r in conn.execute("SELECT arxiv_id FROM papers")}


def upsert_candidates(papers: list[dict[str, Any]]) -> int:
    """插入新候選論文；已存在的略過（保留其 status）。回傳新增筆數。"""
    init_db()
    today = date.today().isoformat()
    new = 0
    with connect() as conn:
        for p in papers:
            exists = conn.execute(
                "SELECT 1 FROM papers WHERE arxiv_id = ?", (p["arxiv_id"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO papers
                   (arxiv_id, title, authors, published, source, url, pdf_url,
                    abstract, citation_count, topic, discovered_date, status, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?)""",
                (
                    p["arxiv_id"], p.get("title"), p.get("authors"), p.get("published"),
                    p.get("source"), p.get("url"), p.get("pdf_url"), p.get("abstract"),
                    p.get("citation_count", 0), p.get("topic"), today,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            new += 1
    return new


def update(arxiv_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE papers SET {cols} WHERE arxiv_id = ?",
            (*fields.values(), arxiv_id),
        )


def set_status(arxiv_id: str, status: str) -> None:
    update(arxiv_id, status=status)


def mark_failed(arxiv_id: str, msg: str) -> None:
    """記一次設計內失敗：累加 retry_count。達 MAX_RETRIES → status='error' 終止重試；
    否則留原狀態，下次夜跑 fetch_active() 續跑。"""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            "SELECT retry_count FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
        n = ((row["retry_count"] if row else 0) or 0) + 1
        set_err = ", status='error'" if n >= MAX_RETRIES else ""
        conn.execute(
            f"UPDATE papers SET retry_count = ?{set_err}, error_msg = ?, updated_at = ? "
            "WHERE arxiv_id = ?",
            (n, msg[:200], now, arxiv_id),
        )


def get(arxiv_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
        ).fetchone()
    return dict(row) if row else None


def fetch(status: str | None = None, order: str = "rank_score DESC") -> list[dict]:
    sql = "SELECT * FROM papers"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += f" ORDER BY {order}"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def fetch_active() -> list[dict]:
    """進行中（中斷待續）的論文，優先處理。"""
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM papers WHERE status IN ({placeholders}) "
            "ORDER BY rank_score DESC",
            ACTIVE_STATUSES,
        )
        return [dict(r) for r in rows]


def export_csv() -> None:
    """匯出人讀紀錄到 data/papers_log.csv。"""
    cols = [
        "arxiv_id", "title", "discovered_date", "rank_score",
        "screen_decision", "status", "read_date", "notion_url",
    ]
    rows = fetch(order="discovered_date DESC, rank_score DESC")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
