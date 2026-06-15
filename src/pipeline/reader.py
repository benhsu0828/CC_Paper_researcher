"""Reader 階段：sonnet + paper-analyzer skill → report.html + analysis.json。"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import httpx

from src.config import (
    PAPERS_DIR,
    PROJECT_ROOT,
    load_config,
    load_research_profile,
    research_profile_block,
)
from src.runner import extract_json, run_stage
from src.store import queue

log = logging.getLogger("reader")


def safe_id(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_").replace(":", "_")


def _html_to_text(html: str, limit: int = 16000) -> str:
    """粗略去標籤 + 壓縮空白，供 extract 用。"""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


async def _extract_analysis(out_dir, title: str) -> dict | None:
    """讀 report.html → 用便宜的 extract 階段產 analysis.json。回傳 dict（或 None）。"""
    report = out_dir / "report.html"
    analysis = out_dir / "analysis.json"
    text = _html_to_text(report.read_text(encoding="utf-8"))
    template = (PROJECT_ROOT / "prompts" / "extract.md").read_text(encoding="utf-8")
    prompt = template.format(title=title, report_text=text)

    res = await run_stage("extract", prompt)
    data = extract_json(res.text) or extract_json("\n".join(res.all_text))
    if not isinstance(data, dict):
        log.warning("extract 無法解析 analysis JSON：%s", (res.text or "")[:150])
        return None
    analysis.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已產出 analysis.json：%s", analysis)
    return data


def _download_pdf(pdf_url: str, dest) -> bool:
    if not pdf_url:
        return False
    try:
        with httpx.stream("GET", pdf_url, follow_redirects=True, timeout=60,
                          headers={"User-Agent": "paper-reader/0.1"}) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest.stat().st_size > 1000
    except Exception as e:  # noqa: BLE001
        log.warning("PDF 下載失敗（%s）：%s", pdf_url, e)
        return False


async def read_one(row: dict, cfg: dict | None = None) -> bool:
    """讀一篇。成功 → status='analyzed'；失敗 → status='error'。回傳是否成功。"""
    cfg = cfg or load_config()
    aid = row["arxiv_id"]
    out_dir = PAPERS_DIR / safe_id(aid)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = out_dir / "report.html"
    analysis = out_dir / "analysis.json"
    title = row.get("title") or ""

    # 若 report.html 已存在（前次已跑過 skill），跳過昂貴的 skill，直接續做萃取與收尾
    if report.exists():
        log.info("report.html 已存在，跳過 paper-analyzer，直接萃取：%s", aid)
    else:
        pdf_path = out_dir / "paper.pdf"
        if not pdf_path.exists():
            _download_pdf(row.get("pdf_url") or "", pdf_path)  # 失敗不致命，skill 可改抓 arXiv 全文

        # arXiv 論文給 abs 連結；手動加入（非 arXiv）論文沒有就請 skill 直接讀本地 PDF
        if row.get("url"):
            url = row["url"]
        elif aid.startswith("manual-"):
            url = "（本論文非 arXiv，請直接閱讀上方本地 PDF）"
        else:
            url = f"https://arxiv.org/abs/{aid}"
        # read 用 sonnet，研究脈絡讀完整（比照 screen 不截斷）；缺檔退回中性佔位、行為不破
        profile = research_profile_block(load_research_profile())
        template = (PROJECT_ROOT / "prompts" / "reader.md").read_text(encoding="utf-8")
        prompt = template.format(
            title=title, url=url, pdf_path=str(pdf_path), out_dir=str(out_dir),
            research_profile=profile,
        )
        log.info("開始閱讀：%s（%s）", title[:50], aid)
        res = await run_stage("read", prompt)

        if not report.exists():
            queue.update(aid, status="error",
                         error_msg=f"未產出 report.html；result={(res.sdk_error or res.result_raw)[:200]}")
            log.error("閱讀失敗，未見 report.html：%s", aid)
            return False

    # 萃取 analysis.json（缺才做）
    data: dict = {}
    if analysis.exists():
        try:
            data = json.loads(analysis.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = extract_json(analysis.read_text(encoding="utf-8")) or {}
    else:
        data = await _extract_analysis(out_dir, title) or {}

    fields: dict = {"status": "analyzed", "read_date": date.today().isoformat(),
                    "error_msg": None}
    inv, rel = data.get("innovation_score"), data.get("relevance_score")
    if isinstance(inv, (int, float)):
        fields["innovation_score"] = inv
    if isinstance(rel, (int, float)):
        fields["relevance_score"] = rel

    queue.update(aid, **fields)
    queue.export_csv()
    log.info("閱讀完成：%s → %s", aid, report)
    return True
