"""執行 Claude Agent SDK query 的共用 runner + JSON 解析 + 額度偵測。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

from src.agent import build_options
from src.store import usage as usage_store

log = logging.getLogger("runner")

# 訂閱額度耗盡時 result 文字常見關鍵字
_LIMIT_MARKERS = ("usage limit", "rate limit", "quota", "額度", "用量上限", "5-hour",
                  "limit reached", "session limit", "hit your session")
# 只有 Claude Code 介面才會吐的特定句子（用來掃模型「正常文字」輸出，避免誤判論文內文）
_HARNESS_LIMIT_PHRASES = ("hit your session limit", "you've hit your", "usage limit reached",
                          "session limit · resets", "claude usage limit")


class QuotaExhausted(Exception):
    """偵測到訂閱額度耗盡，由 orchestrator 捕捉做 graceful stop。"""


@dataclass
class RunResult:
    text: str = ""                       # 最後一段 assistant 文字（總結）
    all_text: list[str] = field(default_factory=list)
    is_error: bool = False
    session_id: str | None = None
    result_raw: str = ""
    sdk_error: str | None = None         # SDK 在 session 結束時丟出的錯誤（多半無害）
    usage: dict | None = None            # token 用量（input/output/cache…）
    cost_usd: float | None = None        # API 等價成本（訂閱不實扣，僅供估量）
    num_turns: int = 0


async def run_stage(stage: str, prompt: str, max_turns: int | None = None) -> RunResult:
    """跑一個 stage 的 query()，回傳 RunResult。

    偵測額度耗盡時丟 QuotaExhausted。其餘 SDK 結束錯誤（如 CLI 以非零碼退出、
    "error result: success" 之類）視為無害：記在 sdk_error，仍回傳已收到的輸出，
    交由呼叫端依「實際產物是否存在」判斷成敗。
    """
    options = build_options(stage, max_turns=max_turns)
    out = RunResult()
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        out.all_text.append(block.text)
            elif isinstance(msg, ResultMessage):
                out.session_id = msg.session_id
                out.result_raw = msg.result or ""
                out.is_error = bool(getattr(msg, "is_error", False))
                out.usage = getattr(msg, "usage", None)
                out.cost_usd = getattr(msg, "total_cost_usd", None)
                out.num_turns = getattr(msg, "num_turns", 0) or 0
                usage_store.record(out.usage, out.cost_usd)  # 累計到目前論文/整夜總計
    except Exception as e:  # noqa: BLE001
        text = str(e)
        if any(m in text.lower() for m in _LIMIT_MARKERS):
            raise QuotaExhausted(text[:300]) from e
        out.sdk_error = text
        log.warning("SDK 結束時報錯（多半無害，已收到輸出）：%s", text[:200])

    out.text = out.all_text[-1] if out.all_text else ""
    # ① is_error 的 result_raw 用較寬的 markers；② 模型把限額訊息當普通文字吐回時，
    #    只用「介面專屬句子」掃 all_text，避免誤判含 rate limit/quota 字樣的論文內文
    if out.is_error and any(m in out.result_raw.lower() for m in _LIMIT_MARKERS):
        raise QuotaExhausted(out.result_raw[:300])
    blob = (out.result_raw + " " + " ".join(out.all_text)).lower()
    if any(p in blob for p in _HARNESS_LIMIT_PHRASES):
        raise QuotaExhausted((out.result_raw or out.text)[:300])
    return out


def extract_json(text: str):
    """從模型輸出中抽出第一個 JSON 物件或陣列（容忍 ```json 圍欄與前後雜訊）。"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 退而求其次：掃描第一個平衡的 [...] 或 {...}
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None
