"""Review 階段：academic-paper-reviewer skill（quick 模式）+ 銳評。

產物：review.md（完整審稿）+ review.json（結構化銳評）。
成功 → status='reviewed'，並把 verdict / sharp_take 存進 DB 供 Notion/Discord 用。
若產物已存在則跳過昂貴的 skill，直接收尾（可續跑/補跑）。
"""

from __future__ import annotations

import json
import logging

from src.config import PAPERS_DIR, PROJECT_ROOT, load_config
from src.pipeline import scholar
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


def _load_or_fetch(path, fetch, default_limit: int) -> list[dict]:
    """讀已落地的 json，否則用 fetch() 抓並存檔（idempotent，可續跑）。"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    items = fetch()
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def _evidence_block(row: dict, out_dir, cfg: dict) -> str:
    """組 Evidence Checker 注入區塊：forward 後續引用 + backward 前置文獻（省 token）。"""
    ec = (cfg or {}).get("evidence", {})
    if not ec.get("enabled", True):
        return "（Evidence Checker 已停用。）"

    aid = row["arxiv_id"]
    title = row.get("title") or ""

    cits = _load_or_fetch(
        out_dir / "citations.json",
        lambda: scholar.fetch_citations(aid, title=title, limit=ec.get("fetch_limit", 50)),
        ec.get("fetch_limit", 50),
    )
    # backward 重用 reader 已抓的 references.json；不存在才抓
    rc = (cfg or {}).get("references", {})
    refs = _load_or_fetch(
        out_dir / "references.json",
        lambda: scholar.fetch_references(aid, title=title, limit=rc.get("fetch_limit", 50)),
        rc.get("fetch_limit", 50),
    )

    forward = scholar.format_citations_block(
        cits, max_n=ec.get("max_inject", 12), context_chars=ec.get("context_chars", 180))
    # review 端的 backward 只給標題＋年（abstract_top=0），不重附摘要（reader 已用過）
    backward = scholar.format_references_block(
        refs, max_n=rc.get("max_inject", 10), abstract_top=0)

    return (
        "### 後續引用本論文的文獻（forward，含引用語境）\n" + forward +
        "\n\n### 本論文引用的前置文獻（backward）\n" + backward
    )


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
            evidence=_evidence_block(row, out_dir, cfg),
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
