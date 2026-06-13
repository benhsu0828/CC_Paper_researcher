"""文字工具：簡體 → 台灣繁體（OpenCC s2twp）。

paper-analyzer skill 對 JSON 輸出常吐簡體，光靠 prompt 不可靠，這裡做決定性後處理。
s2twp 對「已是繁體」的字串近乎 identity，可安全重複套用（idempotent-ish）。
"""

from __future__ import annotations

import logging

log = logging.getLogger("text")

_CC = None


def _converter():
    global _CC
    if _CC is None:
        import opencc
        _CC = opencc.OpenCC("s2twp")
    return _CC


def to_traditional(s: str) -> str:
    if not s or not isinstance(s, str):
        return s
    try:
        return _converter().convert(s)
    except Exception as e:  # noqa: BLE001
        log.warning("OpenCC 轉換失敗，原樣返回：%s", e)
        return s


def convert_json_values(obj):
    """遞迴把 dict/list 內所有字串值轉繁體（鍵不動）。回傳新結構。"""
    if isinstance(obj, str):
        return to_traditional(obj)
    if isinstance(obj, list):
        return [convert_json_values(x) for x in obj]
    if isinstance(obj, dict):
        return {k: convert_json_values(v) for k, v in obj.items()}
    return obj
