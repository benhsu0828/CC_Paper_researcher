"""Discord 通知：論文處理完成後發一則訊息到頻道，附上本地完整報告 HTML。

純 Python（httpx REST + Bot token），零 LLM 額度。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import httpx

from src.config import env
from src.store import usage as usage_store

log = logging.getLogger("discord")

API = "https://discord.com/api/v10"

_TRACK_BADGE = {"core": "🎯 核心", "explore": "🧭 探索"}


def configured() -> bool:
    return bool(env("DISCORD_BOT_TOKEN")) and bool(env("DISCORD_CHANNEL_ID"))


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _compose(row: dict, review: dict, notion_url: str) -> str:
    aid = row.get("arxiv_id") or ""
    title = row.get("title") or "(無題)"
    url = row.get("url") or f"https://arxiv.org/abs/{aid}"
    inv = row.get("innovation_score")
    rel = row.get("relevance_score")
    verdict = review.get("verdict") or row.get("review_verdict") or "?"
    take = review.get("sharp_take") or row.get("review_take") or ""
    badge = _TRACK_BADGE.get(row.get("screen_track") or "", "")

    lines = [
        f"📄 **{badge + '｜' if badge else ''}{title}**",
        f"`{aid}`　創新 {inv if inv is not None else '?'}/10　相關 {rel if rel is not None else '?'}/10　審稿：**{verdict}**",
    ]
    if take:
        lines.append(f"> 🔥 {take}")
    lines.append(f"🔗 arXiv：<{url}>")
    if notion_url:
        lines.append(f"🗂️ Notion：{notion_url}")
    u = usage_store.paper_totals(aid)  # 本篇 token 小計（夜跑時才有值）
    if u.input_tokens or u.output_tokens:
        cost = f"　≈ ${u.cost_usd:.2f}" if u.cost_usd else ""
        lines.append(f"🪙 {_fmt_tokens(u.input_tokens)} in · {_fmt_tokens(u.output_tokens)} out{cost}")
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


def _post_text(content: str) -> bool:
    """發一則純文字訊息。回傳是否成功。"""
    if not configured():
        return False
    channel = env("DISCORD_CHANNEL_ID")
    headers = {"Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}",
               "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{API}/channels/{channel}/messages",
                            headers=headers, json={"content": content[:1990]})
        if r.status_code >= 400:
            log.warning("Discord 文字訊息失敗 %s：%s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Discord 文字訊息例外：%s", e)
        return False


def notify_summary(done: int, todo: list[dict], total_usage, seconds: float,
                   stopped: str = "") -> bool:
    """夜跑結束的總結訊息：完成篇數 + 核心/探索分佈 + 整夜 token 用量 + 耗時。"""
    if not configured():
        log.info("未設定 Discord，略過夜跑總結")
        return False
    total = len(todo)
    n_exp = sum(1 for r in todo if (r.get("screen_track") == "explore"))
    n_core = total - n_exp
    mins = int(seconds // 60)
    cost = f"　≈ ${total_usage.cost_usd:.2f}" if total_usage.cost_usd else ""
    head = "🌙 **夜跑結束**" if not stopped else f"⚠️ **夜跑中止（{stopped}）**"
    content = (
        f"{head}　{date.today().isoformat()}\n"
        f"完成 {done}/{total} 篇（🎯 核心 {n_core}／🧭 探索 {n_exp}）\n"
        f"🪙 tokens {_fmt_tokens(total_usage.input_tokens)} in · "
        f"{_fmt_tokens(total_usage.output_tokens)} out{cost}　⏱ {mins} 分　"
        f"（{total_usage.sessions} 個 session）"
    )
    return _post_text(content)
