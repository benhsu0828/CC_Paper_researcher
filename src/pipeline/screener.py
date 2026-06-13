"""Screening 階段：opus 從粗排高分群中，挑出真正值得深讀的論文（上限 limit）。"""

from __future__ import annotations

import logging

from src.config import PROJECT_ROOT, load_config
from src.runner import extract_json, run_stage
from src.store import queue

log = logging.getLogger("screener")

POOL_MULTIPLIER = 4   # 從 rank 前 limit*4 篇裡挑


def _format_papers(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        abstract = (r.get("abstract") or "")[:400]
        lines.append(
            f"[{i}] (rank={r.get('rank_score')}) {r.get('title')}\n    摘要：{abstract}"
        )
    return "\n".join(lines)


async def screen(cfg: dict | None = None, limit: int | None = None) -> list[str]:
    """挑選論文。read → status='selected'；其餘 → status='skipped'。回傳被選中的 arxiv_id。"""
    cfg = cfg or load_config()
    limit = limit or cfg.get("papers_per_run", 3)

    pool = queue.fetch(status="queued", order="rank_score DESC")[: limit * POOL_MULTIPLIER]
    if not pool:
        log.info("沒有已排序的 queued 論文可篩選")
        return []

    template = (PROJECT_ROOT / "prompts" / "screener.md").read_text(encoding="utf-8")
    prompt = template.format(topic=cfg["topic"], limit=limit, papers=_format_papers(pool))

    res = await run_stage("screen", prompt)
    decisions = extract_json(res.text) or extract_json("\n".join(res.all_text))
    if not isinstance(decisions, list):
        log.error("screener 回傳無法解析為 JSON：%s", res.text[:200])
        return []

    selected: list[str] = []
    by_index = {int(d["index"]): d for d in decisions if "index" in d}
    # 先處理 read，受 limit 限制（按 rank 順序，pool 已排序）
    for i, r in enumerate(pool, 1):
        d = by_index.get(i)
        decision = (d or {}).get("decision", "skip")
        reason = (d or {}).get("reason", "")
        if decision == "read" and len(selected) < limit:
            queue.update(r["arxiv_id"], status="selected",
                         screen_decision="read", screen_reason=reason)
            selected.append(r["arxiv_id"])
            log.info("選讀：%s — %s", r["title"][:50], reason)
        else:
            queue.update(r["arxiv_id"], status="skipped",
                         screen_decision="skip", screen_reason=reason or "未入選")

    queue.export_csv()
    log.info("Screening 完成，選出 %d 篇深讀", len(selected))
    return selected
