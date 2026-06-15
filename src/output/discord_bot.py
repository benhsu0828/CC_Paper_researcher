"""Discord 指令監聽（輪詢式 REST，不用 gateway / discord.py）：在頻道打 !read 觸發讀論文。

指令（在 DISCORD_CHANNEL_ID 那個頻道輸入）：
  !read              讀庫存中分數最高的 1 篇（不重抓、不重 rank/screen）
  !read 3            讀 3 篇
  !read <arxiv_id>   讀指定論文
  !status            回報佇列數量與目前是否忙碌

常駐執行：`python main.py --serve`（或 systemd 的 paper-reader-bot.service）。
讀取沿用 orchestrator.read_inventory：逐篇 read→enrich→review→publish，並發每篇通知 + 總結。
安全：DISCORD_USER_ID 有設時只接受該使用者的指令；一次只跑一個讀取，忙碌時回覆稍候。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.config import DATA_DIR, env, load_config

log = logging.getLogger("discord_bot")

API = "https://discord.com/api/v10"
POLL_SEC = 20
_LAST_FILE = DATA_DIR / "logs" / "discord_last_id.txt"

_busy = False


def _headers() -> dict:
    return {"Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}"}


def _post(channel: str, content: str) -> None:
    try:
        httpx.post(f"{API}/channels/{channel}/messages",
                   headers={**_headers(), "Content-Type": "application/json"},
                   json={"content": content[:1990]}, timeout=30)
    except Exception as e:  # noqa: BLE001
        log.warning("回覆失敗：%s", e)


def _load_last() -> str | None:
    try:
        return _LAST_FILE.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def _save_last(mid: str) -> None:
    _LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LAST_FILE.write_text(mid, encoding="utf-8")


def _fetch(channel: str, after: str | None) -> list[dict]:
    """抓 after 之後的訊息（舊→新）。after 為空時只抓最新 1 則供初始化 last_id。"""
    url = f"{API}/channels/{channel}/messages?" + (f"limit=30&after={after}" if after else "limit=1")
    try:
        r = httpx.get(url, headers=_headers(), timeout=30)
    except Exception as e:  # noqa: BLE001
        log.warning("讀訊息例外：%s", e)
        return []
    if r.status_code >= 400:
        log.warning("讀訊息失敗 %s：%s", r.status_code, r.text[:200])
        return []
    return list(reversed(r.json()))


def _parse(content: str) -> tuple[str, str] | None:
    """解析指令 → (cmd, arg)。非指令回 None。"""
    c = (content or "").strip()
    if not c.startswith("!"):
        return None
    parts = c[1:].split(maxsplit=1)
    if not parts:
        return None
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


async def _do_read(channel: str, arg: str, cfg: dict) -> None:
    """執行讀取（背景 task）。arg 可為篇數或 arxiv_id。"""
    global _busy
    from src.pipeline import orchestrator
    try:
        if arg and not arg.isdigit():               # 指定論文 id
            _post(channel, f"📖 開始讀指定論文 `{arg}`…")
            done, total = await orchestrator.read_inventory(paper=arg, cfg=cfg)
        else:
            n = int(arg) if arg.isdigit() else 1
            n = max(1, min(n, 5))                    # 上限 5，避免手滑燒爆
            _post(channel, f"📖 開始讀庫存最高分的 {n} 篇…（每篇含 read→審稿→發 Notion）")
            done, total = await orchestrator.read_inventory(n=n, cfg=cfg)
        if total == 0:
            _post(channel, "🤷 庫存沒有可讀的論文（queued 為空）。")
    except Exception as e:  # noqa: BLE001
        log.error("讀取指令失敗：%s", e)
        _post(channel, f"⚠️ 讀取出錯：{str(e)[:300]}")
    finally:
        _busy = False


def _status_text(cfg: dict) -> str:
    from collections import Counter

    from src.store import queue
    rows = queue.fetch()
    c = Counter(r["status"] for r in rows)
    return (f"📊 佇列：queued {c.get('queued', 0)}・published {c.get('published', 0)}"
            f"・error {c.get('error', 0)}　|　{'🔴 讀取中' if _busy else '🟢 閒置'}")


async def serve() -> None:
    """常駐主迴圈：輪詢頻道、解析指令、觸發讀取。"""
    global _busy
    channel = env("DISCORD_CHANNEL_ID")
    if not (channel and env("DISCORD_BOT_TOKEN")):
        raise SystemExit("缺 DISCORD_CHANNEL_ID / DISCORD_BOT_TOKEN，無法啟動監聽")
    only_user = env("DISCORD_USER_ID")  # 選用：只接受此使用者的指令
    cfg = load_config()

    last = _load_last()
    if not last:  # 首次啟動：以目前最新訊息為基準，只回應之後的新指令
        latest = _fetch(channel, None)
        if latest:
            last = latest[-1]["id"]
            _save_last(last)
    log.info("Discord 監聽啟動（channel=%s，from id=%s）", channel, last)
    _post(channel, "🤖 論文讀取 bot 上線。指令：`!read`／`!read 3`／`!read <id>`／`!status`")

    while True:
        for m in _fetch(channel, last):
            last = m["id"]
            _save_last(last)
            author = m.get("author", {})
            if author.get("bot"):
                continue
            if only_user and str(author.get("id")) != str(only_user):
                continue
            parsed = _parse(m.get("content", ""))
            if not parsed:
                continue
            cmd, arg = parsed
            if cmd == "status":
                _post(channel, _status_text(cfg))
            elif cmd == "read":
                if _busy:
                    _post(channel, "⏳ 正在讀上一批，稍候再試（`!status` 看狀態）。")
                    continue
                _busy = True
                asyncio.create_task(_do_read(channel, arg, cfg))
            elif cmd == "help":
                _post(channel, "指令：`!read`（讀1篇）／`!read 3`（讀3篇）／`!read <arxiv_id>`／`!status`")
        await asyncio.sleep(POLL_SEC)
