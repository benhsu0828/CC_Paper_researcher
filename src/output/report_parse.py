"""從 report.html 抽出 Notion 頁要用的內容：論文後設資料 + 指定章節。

回傳「中間表示」（list of item dict），由 notion.py 轉成實際 Notion block，
避免與 notion.py 互相 import。所有文字在 notion 端會再過 OpenCC 轉繁體。
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger("report_parse")

META_LABELS = ["作者", "通訊作者", "資料集", "程式碼狀態"]


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_metadata(html: str, arxiv_id: str = "") -> dict:
    """抽出 作者 / 通訊作者 / arXiv / 資料集 / 程式碼狀態。缺的就略過。"""
    soup = _soup(html)
    h1 = soup.find("h1")
    # 蒐集 h1 之後、第一個 h2 之前的文字（後設資料區）。每個元素用空白接合，
    # 讓行內 <a>OpenTAD</a> 等保持同一行（避免值被截斷）。
    lines: list[str] = []
    node = h1.next_sibling if h1 else None
    while node is not None:
        if getattr(node, "name", None) == "h2":
            break
        if hasattr(node, "get_text"):
            t = node.get_text(" ", strip=True)
            if t:
                lines.append(re.sub(r"\s+", " ", t))
        node = node.next_sibling
    blob = "\n".join(lines)

    boundary = r"(?=(?:通訊作者|作者|資料集|程式碼狀態|arXiv)[：:]|\n|$)"
    meta: dict[str, str] = {}
    for label in META_LABELS:
        m = re.search(re.escape(label) + r"[：:]\s*(.+?)\s*" + boundary, blob, re.S)
        if m and m.group(1).strip():
            meta[label] = m.group(1).strip()
    meta["arXiv"] = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
    return meta


def _node_items(node) -> list[dict]:
    """把一個 HTML 元素轉成中間 item（段落 / 子標題 / 條列 / 表格列）。"""
    items: list[dict] = []
    name = getattr(node, "name", None)
    if name is None:
        return items
    if name == "pre":
        # 跳過 Mermaid 流程圖與程式碼區塊：原始碼塞進 Notion 只會是一坨噪音
        # （整體架構圖另由 publish 截成 PNG 呈現）
        return items
    if name in ("h3", "h4"):
        txt = node.get_text(" ", strip=True)
        if txt:
            items.append({"kind": "h3", "text": txt})
    elif name == "p":
        txt = node.get_text(" ", strip=True)
        if txt:
            items.append({"kind": "p", "text": txt})
    elif name in ("ul", "ol"):
        for li in node.find_all("li", recursive=False):
            txt = li.get_text(" ", strip=True)
            if txt:
                items.append({"kind": "bullet", "text": txt})
    elif name == "table":
        for tr in node.find_all("tr"):
            cells = [td.get_text(" ", strip=True)
                     for td in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]
            if cells:
                items.append({"kind": "row", "text": " ｜ ".join(cells)})
    elif name in ("div", "blockquote", "section", "article"):
        # 容器：有區塊級子元素就遞迴；否則（如 warning-box 只含文字+inline）整塊當段落
        if node.find(["p", "ul", "ol", "table", "h3", "h4", "div"], recursive=False):
            for child in node.children:
                items.extend(_node_items(child))
        else:
            txt = node.get_text(" ", strip=True)
            if txt:
                items.append({"kind": "p", "text": re.sub(r"\s+", " ", txt)})
    else:
        # 其他：往下找已知元素
        for child in getattr(node, "children", []):
            items.extend(_node_items(child))
    return items


def _is_stop(node) -> bool:
    """章節邊界：下一個 h2、enrich 加的「原文關鍵圖表」區塊、或報告頁尾。"""
    name = getattr(node, "name", None)
    if name == "h2":
        return True
    if name == "section" and "extracted-figures" in (node.get("class") or []):
        return True
    if name in ("p", "div", "footer") and node.get_text(" ", strip=True).startswith("本報告由"):
        return True
    return False


def section_items(html: str, keywords: list[str]) -> list[dict]:
    """找 h2 標題含任一 keyword 的章節，回傳該章節（到下一個 h2 前）的中間 items。

    第一個 item 為該章節標題（kind=h2）。找不到回傳空 list。
    """
    soup = _soup(html)
    for h2 in soup.find_all("h2"):
        htext = h2.get_text(" ", strip=True)
        if not any(k in htext for k in keywords):
            continue
        items: list[dict] = [{"kind": "h2", "text": htext}]
        node = h2.next_sibling
        while node is not None:
            if _is_stop(node):
                break
            items.extend(_node_items(node))
            node = node.next_sibling
        return items
    log.warning("找不到含 %s 的章節", keywords)
    return []
