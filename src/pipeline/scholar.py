"""Semantic Scholar 引用關係客戶端（純 Python，零 LLM 額度）。

兩個方向：
- references（backward）：本論文「引用」的前置文獻 → 強化 reader 的「研究脈絡與前置工作」。
- citations（forward）：後續「引用本論文」的文獻 + 引用語境（contexts）→ 給 review 做
  Evidence Checker（支持/反駁/指出侷限/改進）；新論文無前向引用時退回 backward 角度。

抓取沿用 discovery._from_s2 的模式：httpx + 選用 API key + 429 重試；任何失敗印警告回 []。
不耗 token：在 LLM 呼叫前以 httpx 取得結構化資料，落地成 json 供 prompt 注入。
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from src.config import env

log = logging.getLogger("scholar")

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_MATCH = f"{S2_BASE}/paper/search/match"

UA = {"User-Agent": "paper-reader/0.1 (research assistant)"}

_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

_REF_FIELDS = "title,abstract,year,citationCount,isInfluential,externalIds"
_CIT_FIELDS = "title,abstract,year,citationCount,contexts,intents,isInfluential,externalIds"


def _headers() -> dict:
    h = dict(UA)
    key = env("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        h["x-api-key"] = key
    return h


def _get(url: str, params: dict) -> dict | None:
    """GET + 429 重試（最多 3 次）。回傳 JSON dict 或 None。"""
    for attempt in range(3):
        try:
            resp = httpx.get(url, params=params, headers=_headers(), timeout=30,
                             follow_redirects=True)
            if resp.status_code == 429:
                wait = 3 * (attempt + 1)
                log.info("S2 429，%ds 後重試（%d/3）：%s", wait, attempt + 1, url)
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("S2 取得失敗（%s）：%s", url, e)
            return None
    log.warning("S2 多次 429，放棄：%s", url)
    return None


def _resolve_s2_id(paper_key: str, title: str = "") -> str | None:
    """把 queue 的 arxiv_id 欄轉成 S2 可查的 paper id。

    - arXiv id（2410.12345）→ ARXIV:2410.12345
    - s2:<paperId>          → <paperId>
    - manual-* / 其他       → 用標題向 /paper/search/match 解析（best-effort）
    """
    key = (paper_key or "").strip()
    if _ARXIV_ID_RE.match(key):
        return f"ARXIV:{key.split('v')[0]}"
    if key.startswith("s2:"):
        return key[3:]
    if title:
        data = _get(S2_MATCH, {"query": title, "fields": "title"})
        items = (data or {}).get("data") or []
        if items and items[0].get("paperId"):
            return items[0]["paperId"]
    return None


def _arxiv_of(ext: dict | None) -> str:
    return (ext or {}).get("ArXiv") or ""


def _flatten(paper: dict, *, is_influential: bool, contexts: list | None = None) -> dict:
    out = {
        "title": (paper.get("title") or "").strip(),
        "abstract": (paper.get("abstract") or "").strip(),
        "year": paper.get("year"),
        "citation_count": paper.get("citationCount") or 0,
        "is_influential": bool(is_influential),
        "arxiv_id": _arxiv_of(paper.get("externalIds")),
    }
    if contexts is not None:
        out["contexts"] = [c.strip() for c in contexts if c and c.strip()]
    return out


def _fetch_edges(s2_id: str, *, kind: str, limit: int) -> list[dict]:
    """kind='references' → citedPaper；kind='citations' → citingPaper。"""
    nested = "citedPaper" if kind == "references" else "citingPaper"
    fields = _REF_FIELDS if kind == "references" else _CIT_FIELDS
    data = _get(f"{S2_BASE}/paper/{s2_id}/{kind}", {"fields": fields, "limit": str(min(limit, 1000))})
    rows = (data or {}).get("data") or []
    out: list[dict] = []
    for item in rows:
        paper = item.get(nested) or {}
        if not paper.get("title"):
            continue
        out.append(_flatten(
            paper,
            is_influential=item.get("isInfluential", False),
            contexts=item.get("contexts") if kind == "citations" else None,
        ))
    return out


def fetch_references(paper_key: str, *, title: str = "", limit: int = 50) -> list[dict]:
    """本論文引用的前置文獻（backward）。失敗或無法解析 id → []。"""
    s2_id = _resolve_s2_id(paper_key, title)
    if not s2_id:
        log.info("無法解析 S2 id，略過 references：%s", paper_key)
        return []
    refs = _fetch_edges(s2_id, kind="references", limit=limit)
    log.info("references：%s → %d 篇前置文獻", paper_key, len(refs))
    return refs


def fetch_citations(paper_key: str, *, title: str = "", limit: int = 50) -> list[dict]:
    """後續引用本論文的文獻 + 引用語境（forward）。失敗或無法解析 id → []。"""
    s2_id = _resolve_s2_id(paper_key, title)
    if not s2_id:
        log.info("無法解析 S2 id，略過 citations：%s", paper_key)
        return []
    cits = _fetch_edges(s2_id, kind="citations", limit=limit)
    log.info("citations：%s → %d 篇後續引用", paper_key, len(cits))
    return cits


# ---------- 格式化成 prompt 注入區塊（嚴格截斷以省 token） ----------

def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda x: (x.get("is_influential", False),
                                        x.get("citation_count", 0)), reverse=True)


def _trunc(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n].rstrip() + "…" if len(text) > n else text


def format_references_block(refs: list[dict], *, max_n: int = 10,
                            abstract_top: int = 4, abstract_chars: int = 160) -> str:
    """前置文獻清單。僅前 abstract_top 筆附摘要，其餘僅標題＋年＋引用數，以省 token。"""
    if not refs:
        return "（無法取得本論文的被引前置文獻，請僅依論文原文的 related work 書寫。）"
    lines: list[str] = []
    for i, r in enumerate(_sorted(refs)[:max_n]):
        year = r.get("year") or "?"
        cit = r.get("citation_count", 0)
        flag = "，influential" if r.get("is_influential") else ""
        line = f"- [{year}] {r['title']}（被引 {cit}{flag}）"
        if i < abstract_top and r.get("abstract"):
            line += f"\n  摘要：{_trunc(r['abstract'], abstract_chars)}"
        lines.append(line)
    return "\n".join(lines)


def format_citations_block(cits: list[dict], *, max_n: int = 12,
                           context_chars: int = 180) -> str:
    """後續引用清單。每筆僅標題/年 + 最相關 1 句引用語境（不附 abstract），以省 token。"""
    if not cits:
        return "（本論文較新，尚無後續引用文獻。）"
    lines: list[str] = []
    for c in _sorted(cits)[:max_n]:
        year = c.get("year") or "?"
        line = f"- [{year}] {c['title']}（被引 {c.get('citation_count', 0)}）"
        ctxs = c.get("contexts") or []
        if ctxs:
            line += f"\n  引用語境：「{_trunc(ctxs[0], context_chars)}」"
        lines.append(line)
    return "\n".join(lines)
