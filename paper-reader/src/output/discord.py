"""Discord 通知：論文處理完成後發一則訊息到頻道，附上本地完整報告 HTML。

純 Python（httpx REST + Bot token），零 LLM 額度。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from src.config import env

log = logging.getLogger("discord")

API = "https://discord.com/api/v10"


def configured() -> bool:
    return bool(env("DISCORD_BOT_TOKEN")) and bool(env("DISCORD_CHANNEL_ID"))


def _compose(row: dict, review: dict, notion_url: str) -> str:
    aid = row.get("arxiv_id") or ""
    title = row.get("title") or "(無題)"
    url = row.get("url") or f"https://arxiv.org/abs/{aid}"
    inv = row.get("innovation_score")
    rel = row.get("relevance_score")
    verdict = review.get("verdict") or row.get("review_verdict") or "?"
    take = review.get("sharp_take") or row.get("review_take") or ""

    lines = [
        f"📄 **{title}**",
        f"`{aid}`　創新 {inv if inv is not None else '?'}/10　相關 {rel if rel is not None else '?'}/10　審稿：**{verdict}**",
    ]
    if take:
        lines.append(f"> 🔥 {take}")
    lines.append(f"🔗 arXiv：<{url}>")
    if notion_url:
        lines.append(f"🗂️ Notion：{notion_url}")
    msg = "\n".join(lines)
    return msg[:1990]


def notify(row: dict, review: dict, notion_url: str = "",
           report_path: str | None = None) -> bool:
    """發 Discord 通知。回傳是否成功。附上 report.html（若存在且不過大）。"""
    if not configured():
        log.info("未設定 Discord，略過通知")
        return False

    channel = env("DISCORD_CHANNEL_ID")
    headers = {"Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}"}
    content = _compose(row, review, notion_url)
    payload = {"content": content}

    report = Path(report_path) if report_path else None
    attach = report and report.exists() and report.stat().st_size < 7_500_000  # <7.5MB

    try:
        with httpx.Client(timeout=60) as client:
            if attach:
                files = {
                    "payload_json": (None, json.dumps(payload), "application/json"),
                    "files[0]": (f"{row.get('arxiv_id', 'report')}.html",
                                 report.read_bytes(), "text/html"),
                }
                r = client.post(f"{API}/channels/{channel}/messages",
                                headers=headers, files=files)
            else:
                r = client.post(f"{API}/channels/{channel}/messages",
                                headers={**headers, "Content-Type": "application/json"},
                                json=payload)
        if r.status_code >= 400:
            log.warning("Discord 通知失敗 %s：%s", r.status_code, r.text[:300])
            return False
        log.info("Discord 通知已送出：%s", row.get("arxiv_id"))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Discord 通知例外：%s", e)
        return False
