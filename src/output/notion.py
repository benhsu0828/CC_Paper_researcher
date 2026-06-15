"""Notion 輸出：在 parent page 底下建立 database（首次），每篇論文寫成一頁。

純 Python（httpx REST），零 LLM 額度。NOTION_DATABASE_ID 為空時自動建 DB 並把
id 寫回 .env 供下次重用。需先把該 parent page 分享給 integration。
"""

from __future__ import annotations

import logging
import re
from datetime import date

import httpx

from src.config import PROJECT_ROOT, env
from src.output.text import to_traditional

log = logging.getLogger("notion")

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

VERDICT_COLORS = {
    "推薦": "green",
    "小修後接受": "blue",
    "大修後重審": "yellow",
    "拒絕": "red",
}

# DB 屬性 schema（建 DB 時用）
_DB_PROPERTIES = {
    "Title": {"title": {}},
    "arXiv": {"rich_text": {}},
    "連結": {"url": {}},
    "發表日": {"date": {}},
    "創新分": {"number": {}},
    "相關分": {"number": {}},
    "審稿分": {"number": {}},
    "審稿結論": {"select": {
        "options": [{"name": n, "color": c} for n, c in VERDICT_COLORS.items()]
    }},
    "引用數": {"number": {}},
    "主題": {"rich_text": {}},
    "處理日": {"date": {}},
    # 給使用者手動勾的 check list（pipeline 不寫此欄，refresh 也不碰）
    "已精讀": {"checkbox": {}},
}


def _auth() -> dict:
    return {
        "Authorization": f"Bearer {env('NOTION_TOKEN')}",
        "Notion-Version": NOTION_VERSION,
    }


def _headers() -> dict:
    return {**_auth(), "Content-Type": "application/json"}


def configured() -> bool:
    return bool(env("NOTION_TOKEN")) and bool(env("NOTION_PARENT_PAGE_ID"))


# ---------- DB 建立 / 取得 ----------

def _persist_db_id(db_id: str) -> None:
    """把 NOTION_DATABASE_ID 寫回 .env（取代既有空值行），並更新本行程環境變數。"""
    import os

    os.environ["NOTION_DATABASE_ID"] = db_id
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith("NOTION_DATABASE_ID="):
            lines[i] = f"NOTION_DATABASE_ID={db_id}"
            found = True
            break
    if not found:
        lines.append(f"NOTION_DATABASE_ID={db_id}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("已把 NOTION_DATABASE_ID 寫回 .env：%s", db_id)


def ensure_database(client: httpx.Client) -> str:
    """回傳可用的 database_id；若 .env 未設則在 parent page 下建立並持久化。"""
    existing = env("NOTION_DATABASE_ID")
    if existing:
        return existing

    parent = env("NOTION_PARENT_PAGE_ID")
    if not parent:
        raise RuntimeError("缺 NOTION_PARENT_PAGE_ID，無法建立 database")

    body = {
        "parent": {"type": "page_id", "page_id": parent},
        "title": [{"type": "text", "text": {"content": "📚 論文閱讀紀錄"}}],
        "properties": _DB_PROPERTIES,
    }
    r = client.post(f"{API}/databases", headers=_headers(), json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"建立 Notion database 失敗 {r.status_code}：{r.text[:300]}")
    db_id = r.json()["id"]
    _persist_db_id(db_id)
    log.info("已建立 Notion database：%s", db_id)
    return db_id


# ---------- 檔案上傳 / 封存 ----------

# 單檔上限：single-part 20MB；免費方案每檔約 5MB。保守抓 19MB，超過就不傳。
MAX_UPLOAD_BYTES = 19 * 1024 * 1024


def upload_file(client: httpx.Client, path, content_type: str) -> str | None:
    """3 步上傳檔案到 Notion，回傳 file_upload id；失敗回 None（不致命）。"""
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return None
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        log.warning("檔案過大略過上傳（%.1f MB > 上限）：%s", size / 1024 / 1024, path.name)
        return None
    try:
        r1 = client.post(f"{API}/file_uploads", headers=_headers(),
                         json={"filename": path.name, "content_type": content_type})
        if r1.status_code >= 400:
            log.warning("建立 file_upload 失敗 %s：%s", r1.status_code, r1.text[:200])
            return None
        fid = r1.json()["id"]
        files = {"file": (path.name, path.read_bytes(), content_type)}
        r2 = client.post(f"{API}/file_uploads/{fid}/send", headers=_auth(), files=files)
        if r2.status_code >= 400:
            log.warning("上傳檔案內容失敗 %s：%s", r2.status_code, r2.text[:200])
            return None
        log.info("已上傳檔案到 Notion：%s（%.1f KB）", path.name, size / 1024)
        return fid
    except Exception as e:  # noqa: BLE001
        log.warning("上傳檔案例外：%s", e)
        return None


def _page_id_from_url(url: str) -> str:
    return (url or "").rstrip("/").rsplit("-", 1)[-1]


def archive_page(client: httpx.Client, page_url: str) -> None:
    """封存（archived=true）既有頁面，用於 refresh 重發。失敗只記 warning。"""
    pid = _page_id_from_url(page_url)
    if not pid:
        return
    try:
        r = client.patch(f"{API}/pages/{pid}", headers=_headers(),
                         json={"archived": True})
        if r.status_code >= 400:
            log.warning("封存舊頁失敗 %s：%s", r.status_code, r.text[:200])
        else:
            log.info("已封存舊 Notion 頁：%s", pid)
    except Exception as e:  # noqa: BLE001
        log.warning("封存舊頁例外：%s", e)


# ---------- 內容 block 組裝 ----------

# 行內/獨立公式：$$..$$、\[..\]、\(..\)、$..$。單錢號需含 LaTeX 跡象（\ ^ _ {}）才當公式，
# 避免把金額（如 $5）誤判成數學式。
_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|\$([^$\n]+?)\$", re.DOTALL)
_MATH_HINT = re.compile(r"[\\^_{}]")


def _push_text(out: list[dict], s: str) -> None:
    """把純文字切成 ≤1900 字的 text rich_text 片段（Notion 單片段上限 2000）。"""
    for i in range(0, len(s), 1900):
        chunk = s[i:i + 1900]
        if chunk:
            out.append({"type": "text", "text": {"content": chunk}})


def _rich_segments(text: str) -> list[dict]:
    """文字 → Notion rich_text：純文字段用 text，LaTeX 段轉 equation（Notion 以 KaTeX 渲染）。

    支援 $$..$$ / \\[..\\] / \\(..\\) / $..$；公式以原始 LaTeX（去掉錢號）放進 expression。
    公式過長（>800）或為空則退回純文字。順手轉繁體（OpenCC 只動中文，不傷 LaTeX 指令）。
    """
    text = to_traditional(text or "")
    if not text:
        return [{"type": "text", "text": {"content": ""}}]
    out: list[dict] = []
    pos = 0
    for m in _MATH_RE.finditer(text):
        if m.start() > pos:
            _push_text(out, text[pos:m.start()])
        if m.group(4) is not None and not _MATH_HINT.search(m.group(4)):
            _push_text(out, m.group(0))  # 像金額的單錢號 → 維持純文字
        else:
            expr = next(g for g in m.groups() if g is not None).strip()
            if 0 < len(expr) <= 800:
                out.append({"type": "equation", "equation": {"expression": expr}})
            else:
                _push_text(out, m.group(0))
        pos = m.end()
    if pos < len(text):
        _push_text(out, text[pos:])
    return out or [{"type": "text", "text": {"content": ""}}]


def _rt(text: str) -> list[dict]:
    """文字 → rich_text 片段（含 LaTeX→equation；單片段 ≤1900）。"""
    return _rich_segments(text)


def _paras(text: str) -> list[dict]:
    """長文字 → 多個 paragraph block（含 LaTeX→equation；每塊內容 ≤1900 字）。"""
    if not (text or "").strip():
        return []
    segments = _rich_segments(text.strip())
    blocks: list[dict] = []
    cur: list[dict] = []
    cur_len = 0
    for seg in segments:
        slen = (len(seg["text"]["content"]) if seg["type"] == "text"
                else len(seg["equation"]["expression"]))
        if cur and (cur_len + slen > 1900 or len(cur) >= 90):
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": cur}})
            cur, cur_len = [], 0
        cur.append(seg)
        cur_len += slen
    if cur:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": cur}})
    return blocks


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": _rt(text)}}


def _bullets(items: list) -> list[dict]:
    out = []
    for it in items or []:
        s = str(it).strip()
        if not s:
            continue
        out.append({"object": "block", "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _rich_segments(s)}})
    return out


def _callout(text: str, emoji: str = "🔥") -> dict:
    return {"object": "block", "type": "callout", "callout": {
        "rich_text": _rich_segments(text), "icon": {"type": "emoji", "emoji": emoji},
        "color": "gray_background"}}


def _heading3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": _rt(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _image_block(file_upload_id: str) -> dict:
    return {"object": "block", "type": "image",
            "image": {"type": "file_upload", "file_upload": {"id": file_upload_id}}}


def _meta_para(label: str, value: str, url: str | None = None) -> dict:
    """一行後設資料：粗體標籤 + 值（值可帶連結）。"""
    rt = [{"type": "text", "text": {"content": f"{label}："},
           "annotations": {"bold": True}}]
    val = to_traditional(value)[:1900]
    text_obj = {"type": "text", "text": {"content": val}}
    if url:
        text_obj["text"]["link"] = {"url": url}
    rt.append(text_obj)
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}}


def _items_to_blocks(items: list[dict]) -> list[dict]:
    """report_parse 的中間 item → Notion block。"""
    out: list[dict] = []
    for it in items or []:
        k, t = it.get("kind"), it.get("text", "")
        if not t:
            continue
        if k == "h2":
            out.append(_heading(t))
        elif k == "h3":
            out.append(_heading3(t))
        elif k == "p":
            out.extend(_paras(t))
        elif k in ("bullet", "row"):
            out.extend(_bullets([t]))
    return out


def _metadata_blocks(meta: dict) -> list[dict]:
    blocks: list[dict] = []
    for label in ("作者", "通訊作者"):
        if meta.get(label):
            blocks.append(_meta_para(label, meta[label]))
    if meta.get("arXiv"):
        blocks.append(_meta_para("arXiv", meta["arXiv"], url=meta["arXiv"]))
    for label in ("資料集", "程式碼狀態"):
        if meta.get(label):
            blocks.append(_meta_para(label, meta[label]))
    return blocks


def _review_blocks(row: dict, review: dict) -> list[dict]:
    blocks: list[dict] = []
    take = review.get("sharp_take") or row.get("review_take") or ""
    if take:
        blocks.append(_callout(take))
    verdict = review.get("verdict") or row.get("review_verdict") or ""
    if verdict:
        blocks.extend(_paras(f"審稿結論：{verdict}"))
    strengths = review.get("strengths") or []
    weaknesses = review.get("weaknesses") or []
    if strengths:
        blocks.extend(_paras("優點："))
        blocks.extend(_bullets(strengths))
    if weaknesses:
        blocks.extend(_paras("弱點 / 該被質疑處："))
        blocks.extend(_bullets(weaknesses))
    return blocks


def _tags_block(tags: list | None) -> dict | None:
    """把領域標籤組成頁面最上面一行 hashtag（粗體灰字）；無標籤回 None。"""
    items: list[str] = []
    for t in tags or []:
        s = to_traditional(str(t)).strip().replace(" ", "")
        if s:
            items.append("#" + s)
    if not items:
        return None
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": "　".join(items)},
         "annotations": {"bold": True, "color": "gray"}}]}}


def _build_children(row: dict, review: dict, metadata: dict,
                    sections: list[list[dict]],
                    arch_upload_id: str | None = None,
                    tags: list | None = None) -> list[dict]:
    """組裝 Notion 頁內容：標籤 → 銳評 → 後設資料 → 整體架構總覽圖 → 指定章節原文。"""
    blocks: list[dict] = []

    # 0) 領域標籤 hashtag（頁面最上面一行）
    tag_block = _tags_block(tags)
    if tag_block:
        blocks.append(tag_block)

    # 1) 審稿銳評（快速判斷層）
    blocks.extend(_review_blocks(row, review))
    blocks.append(_divider())

    # 2) 論文後設資料
    blocks.extend(_metadata_blocks(metadata))

    # 3) 整體架構總覽（Mermaid 生成圖）
    if arch_upload_id:
        blocks.append(_heading("整體架構總覽"))
        blocks.append(_image_block(arch_upload_id))

    # 4) 指定章節原文（快速抓重點 / 對自身研究的幫助 / 討論 / 侷限 / 研究脈絡 / 產品落地）
    for items in sections:
        blocks.extend(_items_to_blocks(items))

    return blocks  # 可能 >100，由 publish_paper 分批建頁 + append


def _properties(row: dict, review: dict) -> dict:
    def num(v):
        return float(v) if isinstance(v, (int, float)) else None

    aid = str(row.get("arxiv_id") or "")
    # arXiv 論文補 abs 連結；手動加入（非 arXiv）論文用其來源 url，沒有就不放連結
    link = row.get("url") or (f"https://arxiv.org/abs/{aid}"
                              if aid and not aid.startswith("manual-") else "")
    props: dict = {
        "Title": {"title": _rt((row.get("title") or "(無題)")[:1900])},
        "arXiv": {"rich_text": _rt(aid)},
        "創新分": {"number": num(row.get("innovation_score"))},
        "相關分": {"number": num(row.get("relevance_score"))},
        "審稿分": {"number": num(review.get("recommendation_score"))},
        "引用數": {"number": num(row.get("citation_count"))},
        "主題": {"rich_text": _rt((row.get("topic") or "")[:1900])},
        "處理日": {"date": {"start": date.today().isoformat()}},
    }
    if link:
        props["連結"] = {"url": link}
    pub = (row.get("published") or "")[:10]
    if len(pub) == 10:
        props["發表日"] = {"date": {"start": pub}}
    verdict = review.get("verdict") or row.get("review_verdict")
    if verdict:
        props["審稿結論"] = {"select": {"name": to_traditional(str(verdict))[:90]}}
    return props


# ---------- 對外：寫一頁 ----------

def publish_paper(row: dict, review: dict, metadata: dict,
                  sections: list[list[dict]], arch_png_path: str | None = None,
                  archive_url: str | None = None, tags: list | None = None) -> str:
    """在 database 建立一頁，回傳 page url。

    metadata：作者/通訊作者/arXiv/資料集/程式碼狀態。
    sections：指定章節的中間 items（每段以 h2 開頭）。
    arch_png_path：整體架構總覽圖，上傳成 image 區塊（失敗不致命）。
    archive_url：refresh 重發時，先封存的舊頁 url。
    tags：領域標籤，組成頁面最上面一行 hashtag。
    """
    with httpx.Client(timeout=120) as client:
        if archive_url:
            archive_page(client, archive_url)
        db_id = ensure_database(client)
        arch_id = upload_file(client, arch_png_path, "image/png") if arch_png_path else None
        blocks = _build_children(row, review, metadata, sections, arch_id, tags)

        # Notion 單次請求 children 上限 100：先建頁帶前 100，其餘分批 append
        body = {
            "parent": {"database_id": db_id},
            "properties": _properties(row, review),
            "children": blocks[:100],
        }
        r = client.post(f"{API}/pages", headers=_headers(), json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"建立 Notion 頁面失敗 {r.status_code}：{r.text[:400]}")
        page = r.json()
        page_id = page.get("id", "")
        for i in range(100, len(blocks), 100):
            chunk = blocks[i:i + 100]
            ra = client.patch(f"{API}/blocks/{page_id}/children",
                              headers=_headers(), json={"children": chunk})
            if ra.status_code >= 400:
                log.warning("append 區塊失敗 %s：%s", ra.status_code, ra.text[:200])
                break
        return page.get("url", "")
