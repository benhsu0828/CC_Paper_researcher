"""用 headless Chromium 把 report.html 裡的「整體架構總覽」Mermaid 圖渲染成 PNG。

Notion 無法原生畫 Mermaid，所以只把這張生成圖截成圖片，上傳成 Notion image 區塊；
其餘文字內容（討論/侷限/脈絡/落地）直接以原生 block 呈現，原文表格數字看 arXiv。
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("render")


def mermaid_png(html_path, png_path, index: int = 0, timeout_ms: int = 30000) -> bool:
    """把 report.html 第 index 個 .mermaid 圖（預設第一個＝整體架構總覽）截成 PNG。

    成功回傳 True；任何失敗記 warning 回傳 False（不致命）。
    """
    html_path = Path(html_path)
    png_path = Path(png_path)
    if not html_path.exists():
        log.warning("找不到 HTML：%s", html_path)
        return False

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        log.warning("Playwright 未安裝，跳過截圖：%s", e)
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(device_scale_factor=2)  # 2x 高解析
            page.goto(html_path.resolve().as_uri(),
                      wait_until="networkidle", timeout=timeout_ms)
            # 等 Mermaid 由 CDN JS 渲染出 <svg>
            page.wait_for_selector(".mermaid svg, pre.mermaid svg", timeout=12000)
            page.wait_for_timeout(600)
            nodes = page.query_selector_all(".mermaid")
            if not nodes or index >= len(nodes):
                log.warning("找不到第 %d 個 .mermaid（共 %d）", index, len(nodes))
                browser.close()
                return False
            target = nodes[index]
            target.scroll_into_view_if_needed()
            target.screenshot(path=str(png_path))
            browser.close()
    except Exception as e:  # noqa: BLE001
        log.warning("Mermaid 截圖失敗：%s", e)
        return False

    ok = png_path.exists() and png_path.stat().st_size > 1000
    if ok:
        log.info("已產出架構圖 PNG：%s（%.1f KB）", png_path, png_path.stat().st_size / 1024)
    return ok
