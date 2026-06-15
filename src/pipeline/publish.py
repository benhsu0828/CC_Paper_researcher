"""Publish 階段（純 Python，零 LLM 額度）：寫 Notion + Discord 通知。

Notion 頁＝銳評 + 後設資料 + 整體架構總覽圖（Mermaid 截圖）+ 指定章節原文
（快速抓重點/對自身研究的幫助/討論/侷限/研究脈絡/產品落地，從 report.html 抽出）。原文表格數字看 arXiv。
完整 report.html 仍由 Discord 夾帶。
成功 → status='published'；Notion 失敗致命（保留可重跑），Discord 失敗只記 warning。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from src.config import PAPERS_DIR, load_config
from src.output import discord, notion, render, report_parse
from src.output.text import convert_json_values
from src.pipeline.reader import safe_id
from src.runner import extract_json
from src.store import queue

log = logging.getLogger("publish")

# 要放進 Notion 的章節（依 h2 標題關鍵字比對），順序即頁面呈現順序
NOTION_SECTIONS = [["快速抓重點"], ["對自身研究的幫助"], ["實驗"],
                   ["討論"], ["侷限"], ["研究脈絡"], ["產品落地"]]


def _load_json(path) -> dict:
    """讀 JSON 並順手轉繁體（防呆：即使 enrich 沒重跑也保證繁體進 Notion）。"""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = extract_json(raw) or {}
    return convert_json_values(data) if isinstance(data, (dict, list)) else data


async def publish_one(row: dict, cfg: dict | None = None, refresh: bool = False) -> bool:
    cfg = cfg or load_config()
    aid = row["arxiv_id"]
    out_dir = PAPERS_DIR / safe_id(aid)
    review = _load_json(out_dir / "review.json")
    analysis = _load_json(out_dir / "analysis.json")
    report = out_dir / "report.html"
    report_path = str(report) if report.exists() else ""
    html = report.read_text(encoding="utf-8") if report.exists() else ""

    # 解析 report.html：後設資料 + 指定章節
    metadata = report_parse.parse_metadata(html, aid) if html else {}
    if aid.startswith("manual-"):  # 非 arXiv：用來源 url（沒有就不顯示連結），不要 bogus arXiv 連結
        metadata["arXiv"] = row.get("url") or ""
    sections = [report_parse.section_items(html, kws) for kws in NOTION_SECTIONS] if html else []
    sections = [s for s in sections if s]

    # 整體架構總覽 Mermaid 截圖（缺則渲染；Playwright 同步 API 不能在 asyncio loop 內跑→工作緒）
    arch = out_dir / "architecture.png"
    if report.exists() and not arch.exists():
        await asyncio.to_thread(render.mermaid_png, str(report), str(arch))
    arch_path = str(arch) if arch.exists() else None

    notion_url = row.get("notion_url") or ""
    if notion.configured():
        if notion_url and not refresh:
            log.info("已有 notion_url，跳過建頁：%s", aid)
        else:
            archive_url = notion_url if (notion_url and refresh) else None
            try:
                notion_url = notion.publish_paper(
                    row, review, metadata, sections,
                    arch_png_path=arch_path, archive_url=archive_url,
                    tags=analysis.get("tags") if isinstance(analysis, dict) else None)
                log.info("Notion 頁面已建立：%s → %s", aid, notion_url)
            except Exception as e:  # noqa: BLE001
                queue.update(aid, status="error", error_msg=f"Notion 失敗：{str(e)[:200]}")
                log.error("Notion 寫入失敗：%s（%s）", aid, e)
                return False
    else:
        log.warning("未設定 Notion（NOTION_TOKEN / PARENT_PAGE_ID），略過 Notion 寫入")

    discord.notify(row, review, notion_url, report_path or None)

    queue.update(aid, status="published", notion_url=notion_url or None,
                 published_at=datetime.now().isoformat(timespec="seconds"),
                 error_msg=None)
    queue.export_csv()
    log.info("Publish 完成：%s", aid)
    return True
