"""Screening 階段：opus 從粗排高分群中，挑出真正值得深讀的論文（上限 limit）。

兩軌名額：core（貼近目前研究進度）+ explore（相關但跳脫進度的創新方向）。
explore 名額由 config 的 screening.exploration_slots 控制，需有 research_profile.md 才生效。
"""

from __future__ import annotations

import logging

from src.config import (
    PROJECT_ROOT,
    load_config,
    load_research_profile,
    research_profile_block,
)
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
    """挑選論文。選中 → status='selected'（含 screen_track=core/explore）；其餘 → 'skipped'。

    名額分配：先填 core_quota 篇 core、再填 exploration_slots 篇 explore；任一軌不足時，
    用另一軌的剩餘候選依 rank 序回補，避免浪費總名額。回傳被選中的 arxiv_id。
    """
    cfg = cfg or load_config()
    limit = limit or cfg.get("papers_per_run", 3)

    profile = load_research_profile()
    exp_slots = int(cfg.get("screening", {}).get("exploration_slots", 0) or 0)
    if not profile:
        exp_slots = 0  # 無研究脈絡無從分辨「跳脫進度」，探索名額失效
    exp_slots = max(0, min(exp_slots, limit))
    core_quota = limit - exp_slots

    pool = queue.fetch(status="queued", order="rank_score DESC")[: limit * POOL_MULTIPLIER]
    if not pool:
        log.info("沒有已排序的 queued 論文可篩選")
        return []

    template = (PROJECT_ROOT / "prompts" / "screener.md").read_text(encoding="utf-8")
    prompt = template.format(
        topic=cfg["topic"],
        research_profile=research_profile_block(profile),
        limit=limit,
        papers=_format_papers(pool),
    )

    res = await run_stage("screen", prompt)
    decisions = extract_json(res.text) or extract_json("\n".join(res.all_text))
    if not isinstance(decisions, list):
        log.error("screener 回傳無法解析為 JSON：%s", res.text[:200])
        return []

    by_index = {int(d["index"]): d for d in decisions if "index" in d}
    # (pool_index, row, decision, reason)；pool 已按 rank 排序，迭代序即 rank 序
    cands = []
    for i, r in enumerate(pool, 1):
        d = by_index.get(i) or {}
        dec = d.get("decision", "skip")
        if dec not in ("core", "explore"):
            dec = "skip"
        cands.append((i, r, dec, d.get("reason", "")))

    core = [c for c in cands if c[2] == "core"]
    explore = [c for c in cands if c[2] == "explore"]

    picked = core[:core_quota] + explore[:exp_slots]
    # 名額未滿（某軌候選不足）→ 用剩餘 core/explore 依 rank 序回補
    if len(picked) < limit:
        chosen = {c[0] for c in picked}
        leftover = sorted(
            (c for c in core[core_quota:] + explore[exp_slots:] if c[0] not in chosen),
            key=lambda c: c[0],
        )
        picked += leftover[: limit - len(picked)]

    picked_idx = {c[0] for c in picked}
    selected: list[str] = []
    for i, r, dec, reason in cands:
        aid = r["arxiv_id"]
        if i in picked_idx:
            track = "explore" if dec == "explore" else "core"
            queue.update(aid, status="selected", screen_decision="read",
                         screen_track=track, screen_reason=reason)
            selected.append(aid)
            log.info("選讀[%s]：%s — %s", track, r["title"][:50], reason)
        else:
            queue.update(aid, status="skipped", screen_decision="skip",
                         screen_reason=reason or "未入選")

    queue.export_csv()
    n_exp = sum(1 for c in picked if c[2] == "explore")
    log.info("Screening 完成，選出 %d 篇深讀（核心 %d / 探索 %d）",
             len(selected), len(selected) - n_exp, n_exp)
    return selected
