"""Ranking 階段：haiku 一次評分（relevance/novelty），Python 併入 recency/citation 算總分。"""

from __future__ import annotations

import logging
from datetime import date

from src.config import (
    PROJECT_ROOT,
    load_config,
    load_research_profile,
    research_profile_block,
)
from src.runner import extract_json, run_stage
from src.store import queue

log = logging.getLogger("ranker")

# rank 用 haiku、要評整池論文，研究脈絡只取精簡前段以省 context
RANK_PROFILE_CHARS = 800


def _recency_score(published: str, recent_days: int) -> float:
    """越新越高，0..1。無法解析日期則給中間值 0.5。"""
    try:
        y, m, d = (int(x) for x in published[:10].split("-"))
        age = (date.today() - date(y, m, d)).days
        return max(0.0, min(1.0, 1 - age / max(recent_days, 1)))
    except Exception:  # noqa: BLE001
        return 0.5


def _format_papers(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        abstract = (r.get("abstract") or "")[:400]
        lines.append(f"[{i}] {r.get('title')}\n    摘要：{abstract}")
    return "\n".join(lines)


async def rank(cfg: dict | None = None) -> int:
    """對 status='queued' 的論文評分，寫回 rank_score。回傳評分篇數。"""
    cfg = cfg or load_config()
    rows = queue.fetch(status="queued", order="discovered_date DESC")
    if not rows:
        log.info("沒有 queued 論文可排序")
        return 0

    profile = load_research_profile(max_chars=RANK_PROFILE_CHARS)
    has_profile = bool(profile)
    template = (PROJECT_ROOT / "prompts" / "ranker.md").read_text(encoding="utf-8")
    prompt = template.format(
        topic=cfg["topic"],
        research_profile=research_profile_block(profile),
        papers=_format_papers(rows),
    )

    res = await run_stage("rank", prompt)
    scores = extract_json(res.text) or extract_json("\n".join(res.all_text))
    if not isinstance(scores, list):
        log.error("ranker 回傳無法解析為 JSON 陣列：%s", res.text[:200])
        return 0

    by_index = {int(s["index"]): s for s in scores if "index" in s}
    pm = cfg.get("priority_mix", {})
    w_rec = pm.get("recency_weight", 0.4)
    w_cit = pm.get("citation_weight", 0.4)
    w_rel = pm.get("relevance_weight", 0.2)
    recent_days = cfg.get("discovery", {}).get("recent_days", 365)
    cmax = max((r.get("citation_count") or 0) for r in rows) or 1

    ranked = 0
    for i, r in enumerate(rows, 1):
        s = by_index.get(i)
        if not s:
            continue
        relq = s.get("relevance", 0)
        novq = s.get("novelty", 0)
        fitq = s.get("fit")
        # 有研究脈絡時，fit（對我研究的可用性）加權主導；無脈絡或 fit 缺值退回二維平均（行為不變）
        if has_profile and isinstance(fitq, (int, float)):
            rel = (relq + novq + 2 * fitq) / 4 / 100.0
        else:
            rel = (relq + novq) / 2 / 100.0
        rec = _recency_score(r.get("published") or "", recent_days)
        cit = (r.get("citation_count") or 0) / cmax
        final = 100 * (w_rec * rec + w_cit * cit + w_rel * rel)
        queue.update(r["arxiv_id"], rank_score=round(final, 2))
        ranked += 1

    queue.export_csv()
    log.info("Ranking 完成，評分 %d 篇", ranked)
    return ranked
