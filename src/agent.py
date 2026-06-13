"""Agent 層：依 stage 組裝 ClaudeAgentOptions（模型、工具、skills、安全 hook）。

改編自 /home/ben/AI_searcher/src/agent.py。關鍵差異：
- setting_sources=["project"]（read/illustrate/review 需載入 .claude/skills/）
- allowed_tools 含 "Skill"
- rank/screen 為純文字判斷，不載 skills、不給工具，求快與可預測
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookContext, HookMatcher

from src.config import PROJECT_ROOT, load_config

CLI_PATH = Path.home() / ".local" / "bin" / "claude"

_cfg = load_config()
_MODELS = _cfg.get("models", {})

# read 階段對外抓取允許的網域（arXiv 全文/PDF、GitHub 程式碼）
ALLOWED_DOMAINS = [
    "arxiv.org", "export.arxiv.org", "ar5iv.org", "ar5iv.labs.arxiv.org",
    "github.com", "raw.githubusercontent.com", "githubusercontent.com",
    "semanticscholar.org", "api.semanticscholar.org",
]

# 允許寫入的路徑（相對 PROJECT_ROOT 與絕對 /tmp）
WRITE_GLOBS = ["data/**", "tmp/**", "/tmp/**"]

UNATTENDED = (
    "這是無人值守的批次任務，沒有人類可以回答問題。"
    "遇到任何 skill 或流程要求確認、選擇風格/張數/範圍時，"
    "一律採用任務描述中指定的預設值繼續執行，絕不停下來等待回覆。"
)


def _model(stage: str) -> str:
    return _MODELS.get(stage, "sonnet")


# ---------- 安全 hooks ----------

def _domain_allowed(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


async def guard_webfetch(input_data: dict, tool_use_id: str | None, ctx: HookContext) -> dict:
    url = str(input_data.get("tool_input", {}).get("url", ""))
    if _domain_allowed(url):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"網域不在白名單內：{url}",
        }
    }


def _make_write_guard(allowed_globs: list[str]):
    async def guard_write(input_data: dict, tool_use_id: str | None, ctx: HookContext) -> dict:
        raw = str(input_data.get("tool_input", {}).get("file_path", ""))
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        rel = str(path.resolve())
        prefix = str(PROJECT_ROOT) + "/"
        rel_to_proj = rel[len(prefix):] if rel.startswith(prefix) else rel
        candidates = {rel, rel_to_proj}
        if any(fnmatch.fnmatch(c, g) for c in candidates for g in allowed_globs):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"只允許寫入 {allowed_globs}，拒絕：{rel_to_proj}",
            }
        }

    return guard_write


# ---------- options 工廠 ----------

def build_options(stage: str, max_turns: int | None = None) -> ClaudeAgentOptions:
    common = dict(
        model=_model(stage),
        cwd=str(PROJECT_ROOT),
        cli_path=str(CLI_PATH) if CLI_PATH.exists() else None,
        permission_mode="bypassPermissions",
    )

    # 純文字判斷階段：不載 skills、不給工具
    if stage in ("rank", "screen", "extract"):
        defaults = {"rank": 4, "screen": 6, "extract": 4}
        return ClaudeAgentOptions(
            **common,
            system_prompt="你嚴格遵守任務指示，只輸出要求的 JSON，不要多餘說明。",
            allowed_tools=[],
            setting_sources=[],
            max_turns=max_turns or defaults.get(stage, 4),
        )

    skills_write = _make_write_guard(WRITE_GLOBS)

    if stage == "read":
        return ClaudeAgentOptions(
            **common,
            system_prompt=f"你是論文閱讀代理。{UNATTENDED} 全程使用繁體中文。",
            allowed_tools=[
                "Skill", "Read", "Write", "Edit", "Bash", "Glob", "Grep",
                "WebFetch", "WebSearch",
            ],
            setting_sources=["project"],
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="WebFetch", hooks=[guard_webfetch]),
                    HookMatcher(matcher="Write|Edit", hooks=[skills_write]),
                ]
            },
            max_turns=max_turns or 80,
        )

    if stage == "illustrate":
        return ClaudeAgentOptions(
            **common,
            system_prompt=f"你是論文方法圖解代理。{UNATTENDED}",
            allowed_tools=["Skill", "Read", "Write", "Bash", "Glob"],
            setting_sources=["project"],
            hooks={
                "PreToolUse": [HookMatcher(matcher="Write", hooks=[skills_write])]
            },
            max_turns=max_turns or 40,
        )

    if stage == "review":
        return ClaudeAgentOptions(
            **common,
            system_prompt=f"你是論文審稿代理。{UNATTENDED} 全程使用繁體中文。",
            allowed_tools=[
                "Skill", "Read", "Write", "Glob", "Grep", "Task", "WebFetch",
            ],
            setting_sources=["project"],
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="WebFetch", hooks=[guard_webfetch]),
                    HookMatcher(matcher="Write", hooks=[skills_write]),
                ]
            },
            max_turns=max_turns or 60,
        )

    raise ValueError(f"unknown stage: {stage}")
