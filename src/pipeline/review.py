"""Review 階段：academic-paper-reviewer skill（quick 模式）+ 銳評。

產物：review.md（完整審稿）+ review.json（結構化銳評）。
成功 → status='reviewed'，並把 verdict / sharp_take 存進 DB 供 Notion/Discord 用。
若產物已存在則跳過昂貴的 skill，直接收尾（可續跑/補跑）。
"""

from __future__ import annotations

import json
import logging

from src.config import PAPERS_DIR, PROJECT_ROOT, load_config
from src.pipeline.reader import safe_id
from src.runner import extract_json, run_stage
from src.store import queue

log = logging.getLogger("review")


def _load_json(path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return extract_json(raw) or {}


async def review_one(row: dict, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    aid = row["arxiv_id"]
    out_dir = PAPERS_DIR / safe_id(aid)
    out_dir.mkdir(parents=True, exist_ok=True)

    review_md = out_dir / "review.md"
    review_json = out_dir / "review.json"
    pdf_path = out_dir / "paper.pdf"
    analysis_path = out_dir / "analysis.json"
    title = row.get("title") or ""

    if review_md.exists() and review_json.exists():
        log.info("review 產物已存在，跳過 reviewer skill，直接收尾：%s", aid)
    else:
        template = (PROJECT_ROOT / "prompts" / "review.md").read_text(encoding="utf-8")
        prompt = template.format(
            title=title,
            url=row.get("url") or f"https://arxiv.org/abs/{aid}",
            pdf_path=str(pdf_path),
            analysis_path=str(analysis_path),
            out_dir=str(out_dir),
        )
        log.info("開始審稿：%s（%s）", title[:50], aid)
        res = await run_stage("review", prompt)

        if not review_json.exists():
            queue.update(aid, status="error",
                         error_msg=f"未產出 review.json；result={(res.sdk_error or res.result_raw)[:200]}")
            log.error("審稿失敗，未見 review.json：%s", aid)
            return False

    data = _load_json(review_json)
    verdict = data.get("verdict") or ""
    take = data.get("sharp_take") or ""

    queue.update(aid, status="reviewed", review_verdict=verdict,
                 review_take=take, error_msg=None)
    queue.export_csv()
    log.info("審稿完成：%s → %s（%s）", aid, verdict or "?", review_md)
    return True
