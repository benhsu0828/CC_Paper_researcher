"""Orchestrator：協調各 stage。逐篇完整處理，偵測額度耗盡時 graceful stop。"""

from __future__ import annotations

import logging

from src.config import load_config
from src.pipeline import ranker, screener
from src.runner import QuotaExhausted
from src.store import queue

log = logging.getLogger("orchestrator")


async def run(stages: list[str], limit: int | None = None,
              paper: str | None = None, refresh: bool = False) -> None:
    cfg = load_config()
    limit = limit or cfg.get("papers_per_run", 3)

    def _select(status: str) -> list[dict]:
        """挑要處理的論文：指定 --paper 就只處理該篇（不限 status），否則依 status 取前 limit。"""
        if paper:
            row = queue.get(paper)
            return [row] if row else []
        return queue.fetch(status=status, order="rank_score DESC")[:limit]

    try:
        if "rank" in stages:
            await ranker.rank(cfg)

        if "screen" in stages:
            await screener.screen(cfg, limit)

        if "read" in stages:
            from src.pipeline import reader  # lazy：read 階段才載入
            rows = _select("selected")
            if not rows:
                log.info("沒有可閱讀的論文")
            for r in rows:
                await reader.read_one(r, cfg)

        if "enrich" in stages:
            from src.pipeline import enrich  # 純 Python：MathML + 抽圖 base64
            rows = _select("analyzed")
            if not rows:
                log.info("沒有可 enrich 的論文")
            for r in rows:
                await enrich.enrich_one(r, cfg)

        if "review" in stages:
            from src.pipeline import review  # sonnet + academic-paper-reviewer skill
            rows = _select("enriched")
            if not rows:
                log.info("沒有可 review 的論文")
            for r in rows:
                await review.review_one(r, cfg)

        if "publish" in stages:
            from src.pipeline import publish  # 純 Python：Notion + Discord
            rows = _select("reviewed")
            if not rows:
                log.info("沒有可 publish 的論文")
            for r in rows:
                await publish.publish_one(r, cfg, refresh=refresh)

    except QuotaExhausted as e:
        log.warning("訂閱額度耗盡，graceful stop：%s", e)
        queue.export_csv()
        return

    queue.export_csv()


# ---------- 逐篇一條龍（M5 夜跑主流程）----------

async def process_paper(row: dict, cfg: dict) -> bool:
    """把一篇論文從目前狀態推到 published：read→enrich→review→publish。

    狀態感知、可續跑（各 *_one 產物已存在會跳過）。QuotaExhausted 往上拋由呼叫端
    做 graceful stop；回傳是否成功跑到 published。
    """
    from src.pipeline import enrich, publish, reader, review

    aid = row["arxiv_id"]

    # queued（手動 --paper 補跑、尚未經 screen）也直接讀
    if row["status"] in ("queued", "selected"):
        if not await reader.read_one(row, cfg):
            return False
        row = queue.get(aid)

    if row["status"] == "analyzed":
        await enrich.enrich_one(row, cfg)
        row = queue.get(aid)

    if row["status"] == "enriched":
        if not await review.review_one(row, cfg):
            return False
        row = queue.get(aid)

    if row["status"] == "reviewed":
        if not await publish.publish_one(row, cfg):
            return False
        row = queue.get(aid)

    return row["status"] == "published"


async def run_nightly(limit: int | None = None, paper: str | None = None) -> None:
    """夜跑：rank+screen 後，逐篇把論文一條龍跑到 published。

    單篇失敗只記 error 並續跑下一篇；偵測額度耗盡時 graceful stop（保留進度下次續跑）。
    """
    cfg = load_config()
    limit = limit or cfg.get("papers_per_run", 3)

    if paper:  # 手動指定單篇：直接一條龍
        row = queue.get(paper)
        if not row:
            log.warning("找不到論文：%s", paper)
            return
        try:
            await process_paper(row, cfg)
        except QuotaExhausted as e:
            log.warning("額度耗盡，graceful stop：%s", e)
        queue.export_csv()
        return

    try:
        await ranker.rank(cfg)
        await screener.screen(cfg, limit)
    except QuotaExhausted as e:
        log.warning("rank/screen 階段額度耗盡，graceful stop：%s", e)
        queue.export_csv()
        return

    # 待處理：進行中（含前次中斷）的優先，依 rank_score 排序，本次最多處理 limit 篇
    todo = [r for r in queue.fetch_active() if r["status"] != "deferred"][:limit]
    if not todo:
        log.info("沒有待處理的論文")
        queue.export_csv()
        return

    done = 0
    for row in todo:
        aid = row["arxiv_id"]
        try:
            if await process_paper(row, cfg):
                done += 1
                log.info("✅ 完成一篇（%d/%d）：%s", done, len(todo), aid)
        except QuotaExhausted as e:
            log.warning("額度耗盡，graceful stop（已完成 %d 篇）：%s", done, e)
            queue.export_csv()
            return
        except Exception as e:  # noqa: BLE001 單篇失敗不擋其他
            log.error("論文 %s 處理失敗，跳過：%s", aid, e)
            queue.update(aid, status="error", error_msg=str(e)[:200])
            continue

    queue.export_csv()
    log.info("夜跑結束：成功 %d / 待處理 %d", done, len(todo))
