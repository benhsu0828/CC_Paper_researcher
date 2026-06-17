# CC_Paper_researcher

每晚自動「找論文 → 排序 → 篩選 → 深讀 → 後處理 → 審稿 → 輸出到 Notion + Discord」的論文閱讀系統。

跑在本機 Linux，用 **Claude Agent SDK + Claude Code 訂閱**（不是 API key、不按 token 計費）。
入口為本目錄的 [`main.py`](main.py)。

## 它會做什麼

每晚 03:00（systemd timer）自動：

1. **discovery** — 從 arXiv + Semantic Scholar 找指定主題的論文，去重、排除已讀。
2. **rank / screen** — 粗排（recency/citation + LLM 評分）後，挑出真正值得深讀的幾篇。
3. **read** — 用 `paper-analyzer` skill 深讀，產出含 Mermaid 架構圖、公式、原文圖的 HTML 報告；並用 Semantic Scholar 自動抓**被引前置文獻**補強「研究脈絡與前置工作」、評估**復現難度**。
4. **enrich** — 公式轉 MathML、PyMuPDF 抽原文圖、OpenCC 轉台灣繁體（純 Python，零額度）。
5. **review** — 用 `academic-paper-reviewer` skill（quick 模式）做多視角審稿 + 一句銳評，並做 **Evidence Checker**（追蹤後續引用本論文的文獻 → 分類支持/反駁/指出侷限/改進；新論文無後續引用時改答「相對前作解決了什麼」）。
6. **publish** — 在 Notion 建一頁（銳評 + 後設資料 + 整體架構圖 PNG + 討論/侷限/研究脈絡/產品落地原文），並發 Discord 通知（夾帶完整 HTML 報告）。

逐篇一條龍處理：任一篇失敗只記 error、不擋其他；偵測訂閱額度耗盡時 graceful stop，隔晚續跑。

## 設定

需求：Python 3.11+、[uv](https://docs.astral.sh/uv/)、已登入的 Claude Code 訂閱（CLI 在 `~/.local/bin/claude`）。

```bash
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

研究主題改 [`config.yaml`](config.yaml) 的 `topic` 即可切換領域。想讓篩選更貼近自己的研究，
複製研究脈絡範本並填入你目前的進度與實驗架構（選用，不建也能跑）：

```bash
cp research_profile.example.md research_profile.md   # 編輯成你的真實研究內容
```

## 執行

```bash
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
bash deploy/install_systemd.sh        # 安裝並啟用每晚 03:00 的計時器
systemctl --user start paper-reader.service   # 手動測試一次
journalctl --user -u paper-reader.service -f  # 看日誌
```

## 架構

### Pipeline（stage 順序與資料流）

每篇論文是 SQLite（[`data/queue.db`](data/)）裡的一列，靠 `status` 欄推進；逐 stage 改寫狀態，可中斷續跑。

```
discovery → rank → screen → read → enrich → review → publish
 queued     (評分)  selected  analyzed enriched reviewed published
                    /skipped
```

| stage | 實作 | 模型 | 產物 / 做的事 |
|---|---|---|---|
| discovery | [`src/pipeline/discovery.py`](src/pipeline/discovery.py) | 無 | arXiv + S2 搜尋、去重、排除已讀 → `status=queued` |
| rank | [`src/pipeline/ranker.py`](src/pipeline/ranker.py) | haiku | recency/citation（Python 算）+ relevance/novelty/**fit**（LLM 評）→ `rank_score` |
| screen | [`src/pipeline/screener.py`](src/pipeline/screener.py) | opus | 挑值得深讀的，分**核心/探索**兩軌 → `selected`（含 `screen_track`）/ `skipped` |
| read | [`src/pipeline/reader.py`](src/pipeline/reader.py) + `paper-analyzer` skill | sonnet | `report.html`（Mermaid 架構圖、公式）+ `analysis.json`（含復現難度）+ `references.json`（S2 被引文獻，零額度）→ `analyzed` |
| enrich | [`src/pipeline/enrich.py`](src/pipeline/enrich.py) | 無 | 公式轉 MathML、PyMuPDF 抽原文圖 base64、OpenCC 轉繁 → `enriched` |
| review | [`src/pipeline/review.py`](src/pipeline/review.py) + `academic-paper-reviewer` skill | sonnet | `review.md` + `review.json`（verdict / 銳評 / 優缺點 / **evidence**）+ `citations.json`（S2 後續引用，零額度）→ `reviewed` |
| publish | [`src/pipeline/publish.py`](src/pipeline/publish.py) | 無 | 建 Notion 頁 + Discord 通知（夾帶完整 HTML）→ `published` |

[`src/pipeline/orchestrator.py`](src/pipeline/orchestrator.py) 是總指揮：`run_nightly()` 先 rank+screen，再**逐篇**把 read→…→publish 跑完（單篇失敗不擋其他、額度耗盡 graceful stop）；`run()` 則供 `--stages` 手動逐 stage 補跑。

### 目錄結構

```
main.py                 入口：解析參數 → orchestrator
config.yaml             主題、篇數、探索名額、各 stage 模型、權重
exclude.yaml            已讀清單（標題正規化比對過濾）
research_profile.md     你的研究脈絡（git 忽略；範本 research_profile.example.md）

src/
  agent.py              build_options(stage)：模型、工具、安全 hooks、載入 .claude/skills
  runner.py             run_stage()：跑 SDK query、解析 JSON、偵測額度耗盡、記 token 用量
  config.py             PROJECT_ROOT / load_config / .env / load_research_profile
  pipeline/            discovery, ranker, screener, reader, enrich, review, publish, orchestrator
    scholar.py          Semantic Scholar 引用關係（被引前置文獻 / 後續引用 + 語境），純 Python 零額度
  store/
    queue.py            SQLite 佇列 + papers_log.csv 匯出
    usage.py            夜跑 token 用量記憶體累計（→ Discord footer / 夜跑總結）
  output/
    notion.py           建 DB / 建頁 / 上傳圖；文字含 LaTeX 自動轉 Notion equation（KaTeX 渲染）
    discord.py          每篇通知（附 HTML、token 小計）+ 夜跑總結
    render.py           Mermaid → PNG（Playwright Chromium）
    report_parse.py     report.html → 中間 block（後設資料 + 指定章節）
    text.py             OpenCC 簡→繁（s2twp）

prompts/                各 stage 的 prompt 模板（ranker / screener / reader / extract / review）
.claude/skills/         paper-analyzer、academic-paper-reviewer（隨專案打包）
deploy/                 systemd --user service / timer + 安裝腳本
data/                   queue.db、papers/<id>/（PDF、report.html、analysis.json、review.*、圖）
```

### 個人化篩選與用量可見

- **研究脈絡感知**：建 `research_profile.md`（複製 `research_profile.example.md` 來改）寫下你目前的研究進度與實驗架構，rank/screen 會據此評分，rank 多一個 **fit**（對你研究的可用性）維度。沒建此檔則退回只用 `topic` 判斷，行為不變。
- **探索名額**：`config.yaml` 的 `screening.exploration_slots`（預設 1）保留 N 篇「主題相關但跳脫你目前進度」的創新方向，其餘給貼近進度的核心論文（需有 `research_profile.md` 才生效）。
- **token 用量**：每篇 Discord 通知附該篇 token 小計，夜跑結束發一則總結（完成數、核心/探索分佈、整夜 token 與估算成本、耗時）。訂閱制不實扣，成本僅供估量。
- **引用脈絡補強（零額度、不增 LLM 呼叫）**：read/review 前先用 Semantic Scholar API 抓引用關係——**被引前置文獻**（backward，強化研究脈絡）與**後續引用 + 引用語境**（forward，做 Evidence Checker）。抓取是純 httpx、零 token，分類則折進既有的 read/review 呼叫；注入內容嚴格截斷（influential 優先、分層附摘要）以省 token。可在 `config.yaml` 的 `references` / `evidence` 區塊調整或關閉。
- **同篇只分析一次（快取續跑）**：每篇產物（`report.html` / `analysis.json` / `review.*` / `references.json` / `citations.json`）都會落地，已存在就跳過昂貴步驟，可中斷後續跑、補跑單一 stage 不重花額度。

更深入的關鍵決策（為何不用 Gemini 生圖、Notion 結構化 block、OpenCC 後處理…）與 `.env` 細節見 [`CLAUDE.md`](CLAUDE.md)。

## 內含的 skills

[`.claude/skills/`](.claude/skills/) 內附兩個 Claude Code skill（已隨專案打包，自包含）：

- **paper-analyzer** — 深讀論文 → 精美 HTML 長文（含 Mermaid 圖、公式）。
- **academic-paper-reviewer** — 多視角學術審稿（EIC + 3 reviewers + Devil's Advocate）。
