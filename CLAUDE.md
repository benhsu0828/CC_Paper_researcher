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

- **預設（無 --stages）＝夜跑**：`orchestrator.run_nightly()` 先 rank+screen，再**逐篇**把每篇 read→enrich→review→publish 跑完（`process_paper`，狀態感知可續跑）。**預設不跑 discovery**（一次找完就夠；更新研究後手動 `--dry-run` 補候選）。單篇設計內失敗走 `queue.mark_failed`（累加 retry_count、留原狀態下次續跑，達 `MAX_RETRIES=3` 才設 status='error' 終止，避免壞論文每晚霸佔名額）；額度耗盡 graceful stop。
- **--stages＝逐 stage**：沿用 `orchestrator.run()`（手動補跑/除錯）。`--stages discovery,...` 才會找論文。
- **每天自動**：`bash deploy/install_systemd.sh` 裝 systemd --user timer（每天午夜 00:00）。

## Pipeline（stage 順序）

discovery → rank → screen → read → enrich → review → publish

| stage | 實作 | 模型 | 產物 / 狀態 |
|---|---|---|---|
| discovery | `src/pipeline/discovery.py` 純 Python | 無 | arXiv + S2，去重，排除 `exclude.yaml`；status=queued |
| rank | `ranker.py` | haiku | rank_score（recency/citation 由 Python 算，relevance/novelty 由 LLM） |
| screen | `screener.py` | opus | 挑值得深讀的；selected / skipped |
| read | `reader.py` + paper-analyzer skill | sonnet | `report.html`（含 Mermaid 架構/流程圖）+ `references.json`（S2 被引前置文獻，零額度先抓再注入）；status=analyzed |
| extract | reader 內 `_extract_analysis` | haiku | `analysis.json`（A–H 欄位 + 分數 + **復現難度 repro_difficulty/repro_reasons**） |
| **enrich** | `enrich.py` 純 Python，零額度 | 無 | 公式轉 **MathML**（移除 KaTeX）+ PyMuPDF 從 PDF 抽圖 **base64 內嵌** + **OpenCC 簡→繁**（report.html + analysis.json）；**idempotent**（以 report.skill.html 為來源、先剝除舊圖表區塊）；status=enriched |
| review | `review.py` + academic-paper-reviewer skill | sonnet | quick 模式 + 銳評 + **Evidence Checker** → `review.md` + `review.json`（verdict/sharp_take/strengths/weaknesses/score + **evidence**）+ `citations.json`（S2 後續引用，零額度先抓再注入）；status=reviewed |
| publish | `publish.py` 純 Python，零額度 | 無 | `output/render.py` 把「整體架構總覽」Mermaid **截成 PNG** + `output/report_parse.py` 從 report.html 抽**後設資料**與**指定章節原文**（討論/侷限/研究脈絡/產品落地）→ `output/notion.py` 建頁 + `output/discord.py` 通知（附 report.html）；status=published |

## 關鍵設計與決策

- **研究脈絡感知篩選（research_profile.md）**：使用者可建 `research_profile.md`（自由文字、git 忽略、範本 `research_profile.example.md`）寫目前研究進度/實驗架構/想解決的問題。`config.load_research_profile()` 載入（rank 取前 ~800 字精簡版省 haiku context、screen 取完整版），`research_profile_block()` 包成 prompt 區塊注入 ranker.md/screener.md 的 `{research_profile}`。**rank 新增 `fit` 維度**（對我研究的可用性，0–100）：有 profile 時 `rel=(relevance+novelty+2*fit)/4`、無 profile 或 fit 缺值退回 `(relevance+novelty)/2`（行為不變，不動 priority_mix 外層權重）。**向後相容**：無此檔一切照舊。
- **探索名額（2 核心 + 1 探索）**：screen 把每篇標 `core`（貼近目前進度、可直接用）/`explore`（相關但跳脫進度的創新方向）/`skip`。`config.yaml` 的 `screening.exploration_slots`（預設 1）保留 N 篇 explore，其餘給 core；某軌不足以另一軌依 rank 序回補（不浪費總名額）。選中存 `screen_track` 欄（queue 遷移加）。**需有 research_profile.md 才生效**（否則無從分辨「進度」，自動全當 core）。Discord 標題標 `🎯 核心`/`🧭 探索`。
- **Token 用量可見（夜跑總結 + 每篇 footer）**：SDK `ResultMessage` 帶 `usage`/`total_cost_usd`，`runner.run_stage` 收到後呼叫 `src/store/usage.py`（記憶體內累計，零持久化）的 `record()`。`orchestrator.run_nightly` 開頭 `reset()`、每篇前 `begin_paper(aid)`；每篇 Discord 通知 footer 顯示該篇 token 小計，夜跑結束 `discord.notify_summary()` 發總結（完成數/核心探索分佈/整夜 in·out tokens/估算成本/耗時）。訂閱制不實扣，cost 僅供估量、cache token 折進 input。
- **引用關係補強（`src/pipeline/scholar.py`，純 Python 零額度、不新增 LLM 呼叫）**：read/review 在呼叫 LLM 前先用 Semantic Scholar API 抓引用關係，把結構化清單注入既有 prompt（input only，吃 cache 折扣）。
  - **被引前置文獻（backward / references）**：`fetch_references` 抓本論文引用的前作（標題/摘要/年份/被引數/isInfluential），存 `references.json`，注入 `reader.md` 的 `{references}` → 強化「研究脈絡與前置工作」「方法 prior-method 對比」。
  - **後續引用（forward / citations）+ 引用語境**：`fetch_citations` 抓引用本論文的文獻與 `contexts`，存 `citations.json`，注入 `review.md` 的 `{evidence}` → **Evidence Checker**：依語境分類 支持/反駁/指出侷限/改進（review.json 的 `evidence` 物件）。**新論文常無前向引用** → 退回 backward 角度「本論文相對前作解決/改進了什麼」（`evidence.solves_prior`，由 LLM 從 references 清單判斷聚焦哪幾篇）。
  - **S2 id 解析**：arXiv id→`ARXIV:<id>`、`s2:<pid>`→`<pid>`、`manual-*`/無 id→用標題打 `/paper/search/match`（best-effort）。429 重試比照 `discovery._from_s2`；任何失敗回 `[]`、用中性佔位字串，**reader/review 行為不破**。
  - **省 token**：`config.yaml` 的 `references`/`evidence` 控制 `fetch_limit`（零額度多抓、排序用）與 `max_inject`/`abstract_top`/`abstract_chars`/`context_chars`（注入嚴格截斷，influential→被引數排序）；`enabled:false` 完全跳過、回到現狀 token 量。review 端 backward 用 `abstract_top=0`（只給標題＋年，reader 已用過摘要）。
  - **復現難度**：reader 在 analysis.json 多產 `repro_difficulty`（低/中/高）+ `repro_reasons`（程式碼/資料集/算力/超參數/專有資料），併進既有 read 呼叫、零新增呼叫。
- **同篇只分析一次（快取續跑）**：`report.html`/`analysis.json`/`review.*`/`references.json`/`citations.json` 都落地，已存在就跳過昂貴步驟（reader/review skill 與 S2 抓取皆 idempotent）。
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
- `src/config.py` — PROJECT_ROOT、load_config、.env（`load_dotenv`）、`load_research_profile()`/`research_profile_block()`
- `src/store/usage.py` — 夜跑 token 用量記憶體累計（reset/begin_paper/record/paper_totals/grand_total）
- `research_profile.example.md` — 研究脈絡範本（複製成 `research_profile.md` 填寫，後者 git 忽略）
- `src/output/` — `notion.py`（建 DB/頁/上傳檔/append）、`discord.py`（每篇通知 + `notify_summary` 夜跑總結）、`render.py`（Mermaid→PNG）、`report_parse.py`（HTML→block）、`text.py`（OpenCC）
- `src/pipeline/scholar.py` — Semantic Scholar 引用關係（純 Python 零額度）：`fetch_references`（backward）/`fetch_citations`（forward + 語境）+ `format_references_block`/`format_citations_block`（注入截斷）+ `_resolve_s2_id`（arXiv/s2/標題 match）
- `src/pipeline/orchestrator.py` — `run()`（逐 stage）+ `process_paper()`/`run_nightly()`（逐篇一條龍）
- `deploy/` — systemd `paper-reader.service`/`.timer` + `install_systemd.sh`
- `.claude/skills/` — paper-analyzer、academic-paper-reviewer（**已隨專案打包成實體目錄**，非 symlink）

## 進度（2026-06-13）

- ✅ M1 Discovery / M2 rank+screen+read+extract / M3 enrich（MathML + caption-crop base64）
- ✅ **M4** review + publish（Notion 建頁 + Discord）；**M4.1** 全程台灣繁體（OpenCC s2twp）；**M4.2** Notion 頁精選版（銳評 + 後設資料 + 整體架構圖 PNG + 四章節原文）
- ✅ **M5：逐篇一條龍 + systemd 每晚自動** — `process_paper`/`run_nightly`（狀態感知、單篇失敗不擋其他、額度 graceful stop）；`main.py` 預設＝夜跑、`--paper` 單篇端到端；systemd --user timer 每晚 03:00（`deploy/install_systemd.sh`，已安裝啟用）
- ✅ **code-review + 打包成自包含 GitHub 專案** — 修兩處（process_paper 處理 queued、Notion >100 block 分批 append）；把兩個 skill 從 symlink 改成實體目錄打包進 `.claude/skills/`，刪除上層 `academic-research-skills`/`paper-craft-skills`；加 README + 根 `.gitignore` + `.env.example`；initial commit 已 push 到 `github.com/benhsu0828/CC_Paper_researcher`（main）
- ✅ **M6：研究脈絡感知 + 探索名額 + token 可見** — `research_profile.md`（rank/screen 注入 + fit 維度）；`screening.exploration_slots` 兩軌篩選（核心/探索，`screen_track`）；`src/store/usage.py` token 累計 → 每篇 Discord footer + 夜跑總結（`notify_summary`）
- ✅ **M7：引用脈絡補強 + 復現難度 + Evidence Checker（皆零新增 LLM 呼叫）** — `src/pipeline/scholar.py`（S2 references/citations 零額度）；reader 注入被引前置文獻（`{references}`）+ analysis.json 加 `repro_difficulty`/`repro_reasons`；review 折進 Evidence Checker（`{evidence}`：forward 分類支持/反駁/侷限/改進；新論文退回 `solves_prior`）；`config.yaml` 加 `references`/`evidence`（嚴格截斷省 token、可關閉）。skill 審核結論：不裝 pdf skill、deep-research 不進 pipeline、paper-analyzer 已是更強的「整理重點」skill
- ⬜ 後續可做：跨夜實跑驗證（首夜 03:00）、papers_log.csv 補 review_verdict 欄、長報告 >100 block 的實測、（選用）publish 把復現難度/evidence 摘要帶進 Notion

## .env（git 忽略；範本見 `.env.example`）

NOTION_TOKEN / NOTION_PARENT_PAGE_ID（page `37cddeca…`）/ NOTION_DATABASE_ID（已自動建並寫回：`37dddeca…`）/
DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID(1514458061014700143) / SEMANTIC_SCHOLAR_API_KEY

規劃草稿 `5-token-…fluttering-lake.md`（git 忽略，可能含 token）
