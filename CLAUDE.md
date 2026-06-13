# paper-reader — 自動論文閱讀系統

每晚自動：找論文 → 排序 → 篩選 → 深讀 → 後處理 → (審稿) → 輸出。
跑在本機 Linux，用 **Claude Agent SDK + Claude Code 訂閱**（不是 API key、不按 token 計費）。

## 執行

```bash
cd /home/ben/CC_Paper_researcher
uv run python main.py                                 # 預設＝完整夜跑：discovery + 逐篇一條龍
uv run python main.py --dry-run                       # 只跑 Discovery（零額度）
uv run python main.py --limit 1 --stages rank,screen,read,enrich   # 手動逐 stage（補跑/除錯）
uv run python main.py --paper <id>                    # 把單篇一條龍跑到 published
uv run python main.py --paper <id> --stages publish --refresh      # 重發某篇 Notion 頁
uv run python main.py --add-pdf ./x.pdf --title "標題"  # 加非 arXiv 論文（本地 PDF）並跑完
uv run python main.py --add-url <pdf_url>             # 加非 arXiv 論文（PDF 連結）並跑完
```

- **非 arXiv 論文**：`main.add_manual_paper()` 以 `manual-<md5[:10]>` 為 id 入庫（source=manual）。`--add-pdf` 把 PDF 複製到 `data/papers/<id>/paper.pdf`（reader 偵測已存在就不下載、skill 直接 Read 本地 PDF）；`--add-url` 設 pdf_url 由 reader 用 httpx 下載（不受 WebFetch 白名單限制）。`process_paper` 已會處理 `queued`。manual 論文不產生 bogus arXiv 連結（reader prompt / notion 連結屬性 / metadata arXiv 行都判斷 `manual-` 前綴）。

- **預設（無 --stages）＝夜跑**：`orchestrator.run_nightly()` 先 rank+screen，再**逐篇**把每篇 read→enrich→review→publish 跑完（`process_paper`，狀態感知可續跑）。單篇失敗只記 error 不擋其他；額度耗盡 graceful stop。
- **--stages＝逐 stage**：沿用 `orchestrator.run()`（手動補跑/除錯）。
- **每晚自動**：`bash deploy/install_systemd.sh` 裝 systemd --user timer（每晚 03:00）。

## Pipeline（stage 順序）

discovery → rank → screen → read → enrich → review → publish

| stage | 實作 | 模型 | 產物 / 狀態 |
|---|---|---|---|
| discovery | `src/pipeline/discovery.py` 純 Python | 無 | arXiv + S2，去重，排除 `exclude.yaml`；status=queued |
| rank | `ranker.py` | haiku | rank_score（recency/citation 由 Python 算，relevance/novelty 由 LLM） |
| screen | `screener.py` | opus | 挑值得深讀的；selected / skipped |
| read | `reader.py` + paper-analyzer skill | sonnet | `report.html`（含 Mermaid 架構/流程圖）；status=analyzed |
| extract | reader 內 `_extract_analysis` | haiku | `analysis.json`（A–H 欄位 + 分數） |
| **enrich** | `enrich.py` 純 Python，零額度 | 無 | 公式轉 **MathML**（移除 KaTeX）+ PyMuPDF 從 PDF 抽圖 **base64 內嵌** + **OpenCC 簡→繁**（report.html + analysis.json）；**idempotent**（以 report.skill.html 為來源、先剝除舊圖表區塊）；status=enriched |
| review | `review.py` + academic-paper-reviewer skill | sonnet | quick 模式 + 銳評 → `review.md` + `review.json`（verdict/sharp_take/strengths/weaknesses/score）；status=reviewed |
| publish | `publish.py` 純 Python，零額度 | 無 | `output/render.py` 把「整體架構總覽」Mermaid **截成 PNG** + `output/report_parse.py` 從 report.html 抽**後設資料**與**指定章節原文**（討論/侷限/研究脈絡/產品落地）→ `output/notion.py` 建頁 + `output/discord.py` 通知（附 report.html）；status=published |

## 關鍵設計與決策

- **不用 Gemini 生圖**（key 是 Vertex Express、需開帳單，用戶改方向）。圖改成：架構/流程圖由 paper-analyzer 畫 **Mermaid**；原文圖表用 **PyMuPDF 依圖說(caption)裁切頁面區域**（雙欄論文做欄位偵測，只裁該欄）→ base64。見 `enrich.py` 的 `_caption_crops`。
- **公式必須 MathML**（不用 KaTeX/LaTeX 文字/Unicode）→ `enrich.py` 用 `latex2mathml` 轉 `$$…$$`/`\(…\)`/`\[…\]`，移除 KaTeX CDN。
- **Notion 不吃完整 HTML**（report.html 含 base64 圖達 256KB）→ `output/notion.py` 改用「結構化 block」：properties（標題/arXiv/連結/發表日/創新分/相關分/審稿分/審稿結論 select/引用數/主題/處理日）+ 內文 block（銳評 callout、analysis 各節 heading+paragraph、審稿優缺點 bullets），完整 HTML 留本機並由 Discord 夾帶。Notion 限制：單 rich_text ≤2000 字（切 1900）、單次建頁 children ≤100 block。
- **Notion DB 程式化建立**：`NOTION_DATABASE_ID` 空時 `ensure_database()` 在 parent page 底下建 DB 並把 id 寫回 `.env`（已建：`37dddeca…`）。需先把 parent page 分享給 integration。
- **Discord 用 REST 不用 discord.py**：`output/discord.py` 直接 httpx multipart（`payload_json` + `files[0]`）夾帶 report.html（<7.5MB），Bot token 沿用 AI_searcher。
- **繁體中文用 OpenCC 決定性後處理**（不靠 prompt）：paper-analyzer 對 JSON 仍常吐簡體 → `output/text.py` 的 `to_traditional`（s2twp）。enrich 轉 report.html + analysis.json；notion 的 `_rt`/`_paras` 與 publish 的 `_load_json` 再過一次（idempotent 防呆）。prompt 也加了「禁簡體」但非主防線。
- **Notion 頁＝精選，不是整份報告**（使用者要求）。組成（見 `output/notion.py` 的 `_build_children`）：① 審稿銳評 callout＋verdict＋優缺點 → ② divider → ③ 後設資料（作者/通訊作者/arXiv/資料集/程式碼狀態）→ ④ **整體架構總覽**（Mermaid 截圖 PNG，image block）→ ⑤ 指定章節**原文**（report.html 的「六討論/七侷限/九研究脈絡/十產品落地」）。原文表格數字使用者自己看 arXiv，故**不抽**原文圖表。完整 report.html 仍由 Discord 夾帶。
- **架構圖用 Mermaid 截圖成 PNG 內嵌**（不是整份 PDF）：`output/render.py` `mermaid_png()` 用 Playwright Chromium 等 `.mermaid svg` 渲染完，截**第一個** `.mermaid` 元素（=整體架構總覽）。**Playwright 同步 API 不能在 asyncio loop 內跑** → publish 用 `asyncio.to_thread` 包。PNG 經 Notion **file upload API**（3 步：建 file_upload → `/send` multipart → 用 id 塞 `image` block）。需先 `playwright install chromium`（約 150MB，排程環境也要裝）。
- **report.html → Notion block**：`output/report_parse.py`（BeautifulSoup）。`parse_metadata`（標籤起頭、lookahead 切到下一標籤，避免 inline `<a>` 截斷值）；`section_items` 依 h2 關鍵字切章節、走 sibling 到下個 h2/「原文關鍵圖表」section/頁尾（`本報告由…`）為止（`_is_stop`）；`warning-box` 等只含文字+inline 的 div 整塊當段落、`<table>` 攤平成 `儲存格 ｜ 儲存格` 條列。中間表示（h2/h3/p/bullet/row）由 notion 端轉 block 並過 OpenCC。
- **重發既有頁**：`publish --refresh`（或 `--paper <id> --refresh`）會先 `archived:true` 封存舊頁再重建。`--paper <id>` 現在對所有 stage 都只處理該篇。
- **能抽圖才抽**（向量圖論文靠 caption-crop；點陣圖論文靠 get_images）。
- 主題在 `config.yaml` 的 `topic`（目前：羽球動作分析/姿態估計/AI 教練）。`exclude.yaml` 是已讀清單（標題正規化比對過濾）。
- SDK 在 session 結束會丟假錯誤 `error result: success`；`runner.py` 容忍它，依「實際產物是否存在」判斷成敗。
- reader/enrich 若產物已存在會跳過（可續跑/補跑）。

## 重要檔案

- `src/agent.py` — `build_options(stage)`：模型、工具、安全 hooks（write 限 data/**+/tmp/**、WebFetch 白名單）、`setting_sources=["project"]` 載入 `.claude/skills/`
- `src/runner.py` — `run_stage()` + `extract_json()` + QuotaExhausted
- `src/store/queue.py` — SQLite（`data/queue.db`）+ `papers_log.csv` 匯出
- `src/config.py` — PROJECT_ROOT、load_config、.env（`load_dotenv`）
- `src/output/` — `notion.py`（建 DB/頁/上傳檔/append）、`discord.py`、`render.py`（Mermaid→PNG）、`report_parse.py`（HTML→block）、`text.py`（OpenCC）
- `src/pipeline/orchestrator.py` — `run()`（逐 stage）+ `process_paper()`/`run_nightly()`（逐篇一條龍）
- `deploy/` — systemd `paper-reader.service`/`.timer` + `install_systemd.sh`
- `.claude/skills/` — paper-analyzer、academic-paper-reviewer（**已隨專案打包成實體目錄**，非 symlink）

## 進度（2026-06-13）

- ✅ M1 Discovery / M2 rank+screen+read+extract / M3 enrich（MathML + caption-crop base64）
- ✅ **M4** review + publish（Notion 建頁 + Discord）；**M4.1** 全程台灣繁體（OpenCC s2twp）；**M4.2** Notion 頁精選版（銳評 + 後設資料 + 整體架構圖 PNG + 四章節原文）
- ✅ **M5：逐篇一條龍 + systemd 每晚自動** — `process_paper`/`run_nightly`（狀態感知、單篇失敗不擋其他、額度 graceful stop）；`main.py` 預設＝夜跑、`--paper` 單篇端到端；systemd --user timer 每晚 03:00（`deploy/install_systemd.sh`，已安裝啟用）
- ✅ **code-review + 打包成自包含 GitHub 專案** — 修兩處（process_paper 處理 queued、Notion >100 block 分批 append）；把兩個 skill 從 symlink 改成實體目錄打包進 `.claude/skills/`，刪除上層 `academic-research-skills`/`paper-craft-skills`；加 README + 根 `.gitignore` + `.env.example`；initial commit 已 push 到 `github.com/benhsu0828/CC_Paper_researcher`（main）
- ⬜ 後續可做：跨夜實跑驗證（首夜 03:00）、papers_log.csv 補 review_verdict 欄、長報告 >100 block 的實測

## .env（git 忽略；範本見 `.env.example`）

NOTION_TOKEN / NOTION_PARENT_PAGE_ID（page `37cddeca…`）/ NOTION_DATABASE_ID（已自動建並寫回：`37dddeca…`）/
DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID(1514458061014700143) / SEMANTIC_SCHOLAR_API_KEY

規劃草稿 `5-token-…fluttering-lake.md`（git 忽略，可能含 token）
