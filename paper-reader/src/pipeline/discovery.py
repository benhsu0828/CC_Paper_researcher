"""Discovery：純 Python 從 arXiv + Semantic Scholar 取候選論文（零 LLM 額度）。

- arXiv API：Atom XML（feedparser 解析），可依 submittedDate 篩最近 N 天
- Semantic Scholar：JSON，提供 citationCount（排序用），無金鑰但有速率限制
- 以 arxiv_id 去重；兩來源都有同一篇時，把 S2 的引用數併入
- 任一來源失敗（網路/429）會印警告並降級，用另一來源繼續
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
import yaml

from src.config import PROJECT_ROOT, env

log = logging.getLogger("discovery")

ARXIV_API = "https://export.arxiv.org/api/query"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"

UA = {"User-Agent": "paper-reader/0.1 (research assistant)"}

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def _arxiv_id(raw: str) -> str | None:
    m = _ARXIV_ID_RE.search(raw or "")
    return m.group(1) if m else None


def _norm_title(t: str) -> str:
    """標題正規化：轉小寫、去除標點與空白（保留中英文與數字）供比對。"""
    return re.sub(r"[^\w]", "", (t or "").lower())


def _load_excluded() -> list[str]:
    """讀 exclude.yaml 的已讀清單，回傳正規化後的標題。"""
    path = PROJECT_ROOT / "exclude.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [_norm_title(t) for t in data.get("already_read", []) if t]


def _is_excluded(title: str, excluded: list[str]) -> bool:
    nt = _norm_title(title)
    if not nt:
        return False
    return any(ex and (ex in nt or nt in ex) for ex in excluded)


def _from_arxiv(query: str, topic: str, candidates: int, recent_days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=recent_days)).strftime("%Y%m%d%H%M%S")
    until = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    search = f'all:{query} AND submittedDate:[{since} TO {until}]'
    params = {
        "search_query": search,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(candidates),
    }
    try:
        resp = httpx.get(ARXIV_API, params=params, headers=UA, timeout=30,
                         follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("arXiv 取得失敗，略過此來源：%s", e)
        return []

    feed = feedparser.parse(resp.content)
    out: list[dict] = []
    for e in feed.entries:
        aid = _arxiv_id(e.get("id", ""))
        if not aid:
            continue
        pdf = next(
            (l.href for l in e.get("links", []) if l.get("type") == "application/pdf"),
            f"https://arxiv.org/pdf/{aid}",
        )
        authors = ", ".join(a.get("name", "") for a in e.get("authors", []))
        out.append({
            "arxiv_id": aid,
            "title": (e.get("title") or "").strip().replace("\n", " "),
            "authors": authors,
            "published": (e.get("published") or "")[:10],
            "source": "arxiv",
            "url": f"https://arxiv.org/abs/{aid}",
            "pdf_url": pdf,
            "abstract": (e.get("summary") or "").strip().replace("\n", " "),
            "citation_count": 0,
            "topic": topic,
        })
    log.info("arXiv：取得 %d 篇", len(out))
    return out


def _from_s2(query: str, topic: str, candidates: int, min_year: int) -> list[dict]:
    params = {
        "query": query,
        "limit": str(min(candidates, 100)),
        "year": f"{min_year}-",
        "fields": "title,abstract,year,authors,citationCount,externalIds,openAccessPdf,url,publicationDate",
    }
    headers = dict(UA)
    s2_key = env("SEMANTIC_SCHOLAR_API_KEY")
    if s2_key:
        headers["x-api-key"] = s2_key

    data = None
    for attempt in range(3):
        try:
            resp = httpx.get(S2_SEARCH, params=params, headers=headers, timeout=30,
                             follow_redirects=True)
            if resp.status_code == 429:
                wait = 3 * (attempt + 1)
                log.info("Semantic Scholar 429，%ds 後重試（%d/3）", wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json().get("data", []) or []
            break
        except Exception as e:  # noqa: BLE001
            log.warning("Semantic Scholar 取得失敗：%s", e)
            break
    if data is None:
        log.warning("Semantic Scholar 多次 429，略過此來源")
        return []

    out: list[dict] = []
    for p in data:
        ext = p.get("externalIds") or {}
        aid = _arxiv_id(ext.get("ArXiv", "")) if ext.get("ArXiv") else None
        key = aid or f"s2:{p.get('paperId')}"
        pdf = (p.get("openAccessPdf") or {}).get("url") or (
            f"https://arxiv.org/pdf/{aid}" if aid else ""
        )
        authors = ", ".join(a.get("name", "") for a in (p.get("authors") or []))
        out.append({
            "arxiv_id": key,
            "title": (p.get("title") or "").strip(),
            "authors": authors,
            "published": p.get("publicationDate") or (str(p["year"]) if p.get("year") else ""),
            "source": "s2",
            "url": p.get("url") or "",
            "pdf_url": pdf,
            "abstract": (p.get("abstract") or "").strip(),
            "citation_count": p.get("citationCount") or 0,
            "topic": topic,
        })
    log.info("Semantic Scholar：取得 %d 篇", len(out))
    return out


def discover(cfg: dict) -> list[dict]:
    """回傳去重後的候選論文清單（純資料，未排序、未呼叫 LLM）。

    會過濾掉 exclude.yaml 列出的已讀論文。
    """
    topic = cfg["topic"]
    dc = cfg.get("discovery", {})
    candidates = dc.get("candidates", 40)
    sources = cfg.get("sources", {})
    arxiv_query = dc.get("arxiv_query") or topic
    s2_query = dc.get("s2_query") or topic

    pool: dict[str, dict] = {}

    if sources.get("arxiv", True):
        for p in _from_arxiv(arxiv_query, topic, candidates, dc.get("recent_days", 30)):
            pool[p["arxiv_id"]] = p

    if sources.get("semantic_scholar", True):
        for p in _from_s2(s2_query, topic, candidates, dc.get("min_year", 2023)):
            existing = pool.get(p["arxiv_id"])
            if existing:
                # 同一篇：把 S2 的引用數併進 arXiv 條目
                existing["citation_count"] = max(
                    existing.get("citation_count", 0), p["citation_count"]
                )
            else:
                pool[p["arxiv_id"]] = p

    excluded = _load_excluded()
    result = [p for p in pool.values() if not _is_excluded(p["title"], excluded)]
    dropped = len(pool) - len(result)
    log.info("Discovery 去重後 %d 篇；排除已讀 %d 篇 → %d 篇候選",
             len(pool), dropped, len(result))
    return result
