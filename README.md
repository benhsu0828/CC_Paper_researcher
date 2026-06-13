# CC_Paper_researcher

每晚自動「找論文 → 排序 → 篩選 → 深讀 → 後處理 → 審稿 → 輸出到 Notion + Discord」的論文閱讀系統。

跑在本機 Linux，用 **Claude Agent SDK + Claude Code 訂閱**（不是 API key、不按 token 計費）。
主程式在 [`paper-reader/`](paper-reader/)。

## 它會做什麼

每晚 03:00（systemd timer）自動：

1. **discovery** — 從 arXiv + Semantic Scholar 找指定主題的論文，去重、排除已讀。
2. **rank / screen** — 粗排（recency/citation + LLM 評分）後，挑出真正值得深讀的幾篇。
3. **read** — 用 `paper-analyzer` skill 深讀，產出含 Mermaid 架構圖、公式、原文圖的 HTML 報告。
4. **enrich** — 公式轉 MathML、PyMuPDF 抽原文圖、OpenCC 轉台灣繁體（純 Python，零額度）。
5. **review** — 用 `academic-paper-reviewer` skill（quick 模式）做多視角審稿 + 一句銳評。
6. **publish** — 在 Notion 建一頁（銳評 + 後設資料 + 整體架構圖 PNG + 討論/侷限/研究脈絡/產品落地原文），並發 Discord 通知（夾帶完整 HTML 報告）。

逐篇一條龍處理：任一篇失敗只記 error、不擋其他；偵測訂閱額度耗盡時 graceful stop，隔晚續跑。

## 設定

需求：Python 3.11+、[uv](https://docs.astral.sh/uv/)、已登入的 Claude Code 訂閱（CLI 在 `~/.local/bin/claude`）。

```bash
cd paper-reader
uv sync                          # 安裝相依
uv run playwright install chromium   # 截整體架構圖 PNG 用（約 150MB，一次性）
cp .env.example .env             # 填入金鑰（見下）
```

`.env` 需要：

| 變數 | 用途 |
|---|---|
| `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` | Notion 整合金鑰 + 要建資料庫的母頁面（需把母頁面分享給整合）。`NOTION_DATABASE_ID` 首次執行會自動建立並寫回 |
| `DISCORD_BOT_TOKEN` / `DISCORD_CHANNEL_ID` | Discord 通知 |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar 搜尋（選用，提高額度） |

研究主題改 [`paper-reader/config.yaml`](paper-reader/config.yaml) 的 `topic` 即可切換領域。

## 執行

```bash
cd paper-reader
uv run python main.py                 # 完整夜跑：discovery + 逐篇一條龍
uv run python main.py --dry-run       # 只跑 discovery（零額度），列候選
uv run python main.py --limit 1       # 限本次 1 篇
uv run python main.py --paper 2501.00001          # 把單篇一條龍跑到 published
uv run python main.py --paper <id> --stages publish --refresh   # 重發某篇的 Notion 頁
```

### 讀「不在 arXiv 上」的論文

把任一篇論文（本地 PDF 或 PDF 連結）加進來並一條龍跑完：

```bash
uv run python main.py --add-pdf ./某論文.pdf --title "論文標題"      # 本地 PDF
uv run python main.py --add-url https://example.com/paper.pdf       # 任意 PDF 連結（非 arXiv 也行）
```

會以 `manual-<hash>` 為 id 入庫、把 PDF 放到 `data/papers/<id>/paper.pdf`，
然後 read→enrich→review→publish。`--title` 建議填（沒填會用檔名/網址當標題）。

## 每晚自動（systemd --user timer）

```bash
cd paper-reader
bash deploy/install_systemd.sh        # 安裝並啟用每晚 03:00 的計時器
systemctl --user start paper-reader.service   # 手動測試一次
journalctl --user -u paper-reader.service -f  # 看日誌
```

## 架構與設計細節

完整的 pipeline 設計、各階段實作、關鍵決策與 `.env` 清單見 [`paper-reader/CLAUDE.md`](paper-reader/CLAUDE.md)。

## 內含的 skills

[`paper-reader/.claude/skills/`](paper-reader/.claude/skills/) 內附兩個 Claude Code skill（已隨專案打包，自包含）：

- **paper-analyzer** — 深讀論文 → 精美 HTML 長文（含 Mermaid 圖、公式）。
- **academic-paper-reviewer** — 多視角學術審稿（EIC + 3 reviewers + Devil's Advocate）。
