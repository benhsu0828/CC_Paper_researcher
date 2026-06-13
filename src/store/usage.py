"""夜跑 token 用量累計（記憶體內，零持久化、零額度）。

SDK 每個 stage 的 ResultMessage 帶 usage（token 數）與 total_cost_usd；
`runner.run_stage` 跑完呼叫 `record()` 累計到「目前論文」與「整夜總計」。
`orchestrator.run_nightly` 開始時 `reset()`、處理每篇前 `begin_paper(aid)` 切換目前論文；
`discord` 的每篇 footer 讀 `paper_totals(aid)`、夜跑總結讀 `grand_total()`。

夜跑是單一 asyncio 程序、同時只處理一篇，故用模組層級狀態即可（不需鎖）。
快取讀寫 token 也折進 input（同樣消耗訂閱額度）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0      # 含 cache read/creation（都吃額度）
    output_tokens: int = 0
    cost_usd: float = 0.0      # SDK 回報的 API 等價成本（訂閱不實扣，僅供估量）
    sessions: int = 0

    def add(self, inp: int, out: int, cost: float) -> None:
        self.input_tokens += inp
        self.output_tokens += out
        self.cost_usd += cost
        self.sessions += 1


_current: str | None = None
_by_paper: dict[str, Usage] = {}
_run = Usage()


def reset() -> None:
    """夜跑開始時清空累計。"""
    global _current, _by_paper, _run
    _current = None
    _by_paper = {}
    _run = Usage()


def begin_paper(aid: str | None) -> None:
    """切換目前正在處理的論文（之後的 record() 都記到它名下）。"""
    global _current
    _current = aid


def record(usage: dict | None, cost_usd: float | None) -> None:
    """記一個 stage 的用量（由 run_stage 在收到 ResultMessage 後呼叫）。"""
    u = usage or {}
    inp = (int(u.get("input_tokens", 0) or 0)
           + int(u.get("cache_read_input_tokens", 0) or 0)
           + int(u.get("cache_creation_input_tokens", 0) or 0))
    out = int(u.get("output_tokens", 0) or 0)
    cost = float(cost_usd or 0.0)
    _run.add(inp, out, cost)
    if _current:
        _by_paper.setdefault(_current, Usage()).add(inp, out, cost)


def paper_totals(aid: str) -> Usage:
    return _by_paper.get(aid, Usage())


def grand_total() -> Usage:
    return _run
