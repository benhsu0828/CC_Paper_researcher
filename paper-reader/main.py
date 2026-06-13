"""自動論文閱讀系統入口。

用法：
    python main.py                        # 完整夜跑：discovery + 逐篇一條龍（read→enrich→review→publish）
    python main.py --limit 2              # 同上，限本次 2 篇
    python main.py --dry-run              # 只跑 Discovery（零額度），列候選 + 寫 CSV
    python main.py --stages rank,screen   # 手動只跑指定 stage（逐 stage，補跑/除錯用）
    python main.py --paper 2401.12345     # 把單篇一條龍跑到 published
    python main.py --paper 2401.12345 --stages publish --refresh  # 重發某篇的 Notion 頁
    python main.py --add-pdf ./my.pdf --title "論文標題"   # 加一篇非 arXiv 論文（本地 PDF）並跑完
    python main.py --add-url https://example.com/paper.pdf # 加一篇非 arXiv 論文（PDF 連結）並跑完
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import PAPERS_DIR, load_config
from src.pipeline import discovery
from src.pipeline.reader import safe_id
from src.store import queue

log = logging.getLogger("main")

ALL_STAGES = ["discovery", "rank", "screen", "read", "enrich", "review", "publish"]


def _print_candidates(rows: list[dict]) -> None:
    if not rows:
        print("（沒有候選論文）")
        return
    rows = sorted(rows, key=lambda r: (r.get("citation_count") or 0), reverse=True)
    print(f"\n{'#':>3}  {'cites':>5}  {'published':<10}  {'id':<16}  title")
    print("-" * 100)
    for i, r in enumerate(rows, 1):
        title = (r.get("title") or "")[:60]
        print(
            f"{i:>3}  {r.get('citation_count') or 0:>5}  "
            f"{(r.get('published') or '')[:10]:<10}  {r.get('arxiv_id', ''):<16}  {title}"
        )
    print(f"\n共 {len(rows)} 篇候選。")


def run_discovery(cfg: dict) -> int:
    """搜尋 + 去重 + 入庫 + 匯出 CSV。回傳本次新增筆數。"""
    queue.init_db()
    candidates = discovery.discover(cfg)
    known = queue.known_ids()
    fresh = [c for c in candidates if c["arxiv_id"] not in known]
    new = queue.upsert_candidates(candidates)
    queue.export_csv()
    log.info("入庫：候選 %d、其中新增 %d（已存在 %d）",
             len(candidates), new, len(candidates) - len(fresh))
    return new


def add_manual_paper(cfg: dict, *, pdf: str | None = None,
                     url: str | None = None, title: str | None = None) -> str:
    """加一篇非 arXiv 論文到佇列（status=queued），回傳其 id。

    pdf：本地 PDF 路徑（會複製到 data/papers/<id>/paper.pdf，reader 直接讀）。
    url：PDF 連結（reader 用 httpx 下載，不受 WebFetch 白名單限制）。
    """
    key = str(Path(pdf).resolve()) if pdf else (url or "")
    if not key:
        raise SystemExit("--add-pdf 或 --add-url 至少給一個")
    aid = "manual-" + hashlib.md5(key.encode()).hexdigest()[:10]

    display = title or (Path(pdf).stem if pdf else url)
    queue.upsert_candidates([{
        "arxiv_id": aid,
        "title": display,
        "url": url or "",
        "pdf_url": (url or "") if not pdf else "",
        "abstract": "",
        "source": "manual",
        "topic": cfg.get("topic"),
        "citation_count": 0,
        "published": date.today().isoformat(),
    }])

    if pdf:
        src = Path(pdf)
        if not src.exists():
            raise SystemExit(f"找不到 PDF：{pdf}")
        out_dir = PAPERS_DIR / safe_id(aid)
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, out_dir / "paper.pdf")

    log.info("已加入手動論文：%s（%s）", aid, display)
    return aid


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="自動論文閱讀系統")
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 Discovery（零額度），不呼叫任何 LLM")
    parser.add_argument("--limit", type=int, default=None, help="本次處理篇數上限")
    parser.add_argument("--stages", type=str, default=None,
                        help=f"逗號分隔的 stage（{','.join(ALL_STAGES)}）")
    parser.add_argument("--paper", type=str, default=None, help="手動補跑單篇 arxiv_id")
    parser.add_argument("--refresh", action="store_true",
                        help="publish 時若已有 Notion 頁則封存舊頁、重建（用於更新內容）")
    parser.add_argument("--add-pdf", type=str, default=None,
                        help="加一篇非 arXiv 論文（本地 PDF 路徑）並一條龍跑完")
    parser.add_argument("--add-url", type=str, default=None,
                        help="加一篇非 arXiv 論文（PDF 連結）並一條龍跑完")
    parser.add_argument("--title", type=str, default=None,
                        help="搭配 --add-pdf/--add-url 指定論文標題（建議填）")
    args = parser.parse_args()

    cfg = load_config()

    if args.dry_run:
        run_discovery(cfg)
        rows = queue.fetch(order="citation_count DESC")
        _print_candidates(rows)
        print(f"\nCSV 已寫到 {queue.CSV_PATH}")
        return

    from src.pipeline import orchestrator

    if args.add_pdf or args.add_url:
        # 加一篇非 arXiv 論文，然後一條龍跑到 published
        queue.init_db()
        aid = add_manual_paper(cfg, pdf=args.add_pdf, url=args.add_url, title=args.title)
        asyncio.run(orchestrator.run_nightly(paper=aid))
        return

    if args.stages:
        # 手動指定階段（補跑/除錯）：沿用逐 stage 流程
        stages = [s.strip() for s in args.stages.split(",")]
        if "discovery" in stages:
            run_discovery(cfg)
        asyncio.run(orchestrator.run(stages, limit=args.limit,
                                     paper=args.paper, refresh=args.refresh))
    else:
        # 預設＝完整夜跑：discovery + 逐篇一條龍（read→enrich→review→publish）
        if not args.paper:
            run_discovery(cfg)
        asyncio.run(orchestrator.run_nightly(limit=args.limit, paper=args.paper))


if __name__ == "__main__":
    main()
