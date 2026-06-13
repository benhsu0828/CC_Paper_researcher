"""Enrich 階段（純 Python，零 LLM 額度）：後處理 report.html。

1. 公式：把 HTML 內的 LaTeX（$$…$$ / \\[…\\] / \\(…\\)）轉成 MathML，移除 KaTeX CDN
2. 圖表：用 PyMuPDF 從 paper.pdf 抽取關鍵圖表，base64 內嵌成「原文關鍵圖表」一節
3. Mermaid 架構/流程圖：原樣保留

原始 skill HTML 備份成 report.skill.html，產物覆寫 report.html。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re

import fitz
from latex2mathml.converter import convert as latex_to_mathml

from src.config import PAPERS_DIR, load_config
from src.output.text import convert_json_values, to_traditional
from src.pipeline.reader import safe_id
from src.store import queue

log = logging.getLogger("enrich")


# ---------- 公式：LaTeX → MathML ----------

def _strip_katex(html: str) -> str:
    html = re.sub(r"<link[^>]*katex[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<script[^>]*katex[^>]*>.*?</script>", "", html, flags=re.I | re.S)
    html = re.sub(r"<script[^>]*>[^<]*renderMathInElement[^<]*</script>", "", html,
                  flags=re.I | re.S)
    return html


def _one_formula(latex: str, block: bool) -> tuple[str, bool]:
    tag_m = re.search(r"\\tag\{([^}]*)\}", latex)
    tag = tag_m.group(1) if tag_m else None
    clean = re.sub(r"\\tag\{[^}]*\}", "", latex).strip()
    try:
        mml = latex_to_mathml(clean)
        ok = True
    except Exception:  # noqa: BLE001
        safe = (clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        mml = (f'<math xmlns="http://www.w3.org/1998/Math/MathML"><merror>'
               f'<mtext>{safe}</mtext></merror></math>')
        ok = False
    if block:
        mml = mml.replace('display="inline"', 'display="block"', 1)
        tag_html = f'<span class="eq-tag">({tag})</span>' if tag else ""
        return f'<div class="eq">{mml}{tag_html}</div>', ok
    return mml, ok


def latex_to_mathml_html(html: str) -> tuple[str, int, int]:
    """轉換 HTML 內所有 LaTeX 公式為 MathML。回傳 (html, 成功數, 失敗數)。"""
    html = _strip_katex(html)
    stats = {"ok": 0, "fail": 0}

    def repl(block: bool):
        def _f(m):
            out, ok = _one_formula(m.group(1), block)
            stats["ok" if ok else "fail"] += 1
            return out
        return _f

    html = re.sub(r"\$\$(.+?)\$\$", repl(True), html, flags=re.S)
    html = re.sub(r"\\\[(.+?)\\\]", repl(True), html, flags=re.S)
    html = re.sub(r"\\\((.+?)\\\)", repl(False), html, flags=re.S)
    return html, stats["ok"], stats["fail"]


# ---------- 圖表：PyMuPDF 抽取 → base64 ----------

_CAP_RE = re.compile(r"^\s*(figure|fig\.?|table|圖|图|表)\s*\d+", re.I)


def _embedded_images(doc, max_figs: int) -> list[tuple[int, bytes, str]]:
    """抓嵌入的點陣圖（過濾過小/長條狀/重複）。"""
    out: list[tuple[int, bytes, str]] = []
    seen_xref: set[int] = set()
    seen_hash: set[str] = set()
    for pno in range(len(doc)):
        for img in doc[pno].get_images(full=True):
            xref = img[0]
            if xref in seen_xref:
                continue
            seen_xref.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.width < 200 or pix.height < 150:
                    continue
                ar = pix.width / pix.height
                if ar > 6 or ar < 0.16:
                    continue
                if pix.n - pix.alpha >= 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                png = pix.tobytes("png")
                h = hashlib.md5(png).hexdigest()
                if h in seen_hash:
                    continue
                seen_hash.add(h)
                out.append((pno + 1, png, "嵌入圖"))
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= max_figs:
                return out
    return out


def _caption_crops(doc, max_figs: int) -> list[tuple[int, bytes, str]]:
    """依圖說（Figure/Table/圖/表 N）定位，把圖說上方的頁面區域渲染成點陣圖。

    適用向量圖（arXiv 常見 TikZ/matplotlib），get_images 抓不到的情況。
    """
    out: list[tuple[int, bytes, str]] = []
    for pno in range(len(doc)):
        page = doc[pno]
        pr = page.rect
        blocks = page.get_text("dict")["blocks"]
        # 找出本頁所有圖說區塊（取其文字與 bbox）
        caps = []
        for b in blocks:
            if "lines" not in b:
                continue
            text = " ".join(s["text"] for ln in b["lines"] for s in ln["spans"]).strip()
            if _CAP_RE.match(text):
                caps.append((fitz.Rect(b["bbox"]), text[:60]))
        if not caps:
            continue
        caps.sort(key=lambda c: c[0].y0)
        center = (pr.x0 + pr.x1) / 2
        for cap_rect, label in caps:
            # 依圖說所在欄位決定水平裁切範圍（雙欄論文避免裁到鄰欄內文）
            if cap_rect.x1 < center + 5:          # 左欄
                x0, x1 = pr.x0 + 24, center - 6
            elif cap_rect.x0 > center - 5:         # 右欄
                x0, x1 = center + 6, pr.x1 - 24
            else:                                  # 跨欄（全寬圖）
                x0, x1 = pr.x0 + 24, pr.x1 - 24
            # 垂直：往上到同欄上一個圖說下緣，並限制最高約 55% 頁高，避免裁進大段內文
            top = max(pr.y0 + 36, cap_rect.y0 - pr.height * 0.55)
            for prev_rect, _ in caps:
                same_col = not (prev_rect.x1 < x0 or prev_rect.x0 > x1)
                if same_col and top < prev_rect.y1 <= cap_rect.y0:
                    top = prev_rect.y1 + 4
            clip = fitz.Rect(x0, top, x1, cap_rect.y0 - 2)
            if clip.height < 60 or clip.width < 80:
                continue
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)  # 2x 解析度
                if pix.width < 160 or pix.height < 90:
                    continue
                out.append((pno + 1, pix.tobytes("png"), label))
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= max_figs:
                return out
    return out


def extract_figures(pdf_path, max_figs: int = 12) -> list[tuple[int, bytes, str]]:
    """回傳 [(頁碼, png bytes, 標籤)]。優先用嵌入點陣圖；不足則用圖說裁切補上。"""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:  # noqa: BLE001
        log.warning("無法開啟 PDF：%s", e)
        return []

    embedded = _embedded_images(doc, max_figs)
    if len(embedded) >= 2:
        return embedded
    # 嵌入圖太少（多為向量圖論文）→ 改用圖說裁切
    crops = _caption_crops(doc, max_figs)
    if crops:
        return crops
    return embedded


def _figures_section(figs: list[tuple[int, bytes, str]]) -> str:
    if not figs:
        return ""
    items = []
    for i, (page, png, label) in enumerate(figs, 1):
        b64 = base64.b64encode(png).decode("ascii")
        cap = label or f"原文圖 {i}"
        items.append(
            f'<figure style="margin:1.5em 0;text-align:center">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="max-width:100%;height:auto;border:1px solid #ddd;border-radius:6px"/>'
            f'<figcaption style="color:#666;font-size:.9em;margin-top:.4em">'
            f'{cap}（p.{page}）</figcaption></figure>'
        )
    return ('<section class="extracted-figures"><h2>原文關鍵圖表</h2>'
            '<p style="color:#666">以下圖表自論文 PDF 抽取並內嵌（base64）。</p>'
            + "".join(items) + "</section>")


# ---------- 主流程 ----------

async def enrich_one(row: dict, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    aid = row["arxiv_id"]
    out_dir = PAPERS_DIR / safe_id(aid)
    report = out_dir / "report.html"
    if not report.exists():
        log.warning("無 report.html，跳過 enrich：%s", aid)
        return False

    # 從原始 skill 產物 enrich，確保可重複執行（idempotent）：
    # 首次把 report.html 備份成 report.skill.html；之後一律以此備份為來源，
    # 避免重跑時把已加工的 report.html 當輸入（造成圖表區塊重複、備份被覆蓋）。
    skill_backup = out_dir / "report.skill.html"
    if not skill_backup.exists():
        skill_backup.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
    raw = skill_backup.read_text(encoding="utf-8")

    # 移除任何既有的「原文關鍵圖表」區塊，等下重新加上（重跑不重複）
    raw = re.sub(r'<section class="extracted-figures">.*?</section>', "", raw, flags=re.S)

    html, ok, fail = latex_to_mathml_html(raw)
    log.info("公式轉 MathML：成功 %d、失敗 %d（%s）", ok, fail, aid)

    # 簡體 → 台灣繁體（決定性後處理；OpenCC 只動中文字，tag/base64/MathML 不受影響）
    html = to_traditional(html)

    # analysis.json 一併轉繁體（Notion 結構化區塊的來源）
    analysis = out_dir / "analysis.json"
    if analysis.exists():
        try:
            data = json.loads(analysis.read_text(encoding="utf-8"))
            analysis.write_text(
                json.dumps(convert_json_values(data), ensure_ascii=False, indent=2),
                encoding="utf-8")
            log.info("analysis.json 已轉繁體（%s）", aid)
        except json.JSONDecodeError:
            log.warning("analysis.json 非合法 JSON，略過繁體轉換（%s）", aid)

    figs = extract_figures(out_dir / "paper.pdf",
                           max_figs=cfg.get("figures", {}).get("max_extract", 12))
    if figs:
        section = _figures_section(figs)
        html = html.replace("</body>", section + "</body>", 1) if "</body>" in html \
            else html + section
        fig_dir = out_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        paths = []
        for i, (page, png, _label) in enumerate(figs, 1):
            p = fig_dir / f"fig_{i:02d}_p{page}.png"
            p.write_bytes(png)
            paths.append(str(p))
    else:
        paths = []
    log.info("抽取圖表 %d 張（%s）", len(figs), aid)

    report.write_text(html, encoding="utf-8")
    queue.update(aid, status="enriched",
                 figures=json.dumps(paths, ensure_ascii=False))
    queue.export_csv()
    log.info("Enrich 完成：%s（公式 %d、圖 %d）", aid, ok, len(figs))
    return True
