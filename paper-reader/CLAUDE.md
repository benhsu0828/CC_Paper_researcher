# paper-reader — 自動論文閱讀系統

每晚自動：找論文 → 排序 → 篩選 → 深讀 → 後處理 → (審稿) → 輸出。
跑在本機 Linux，用 **Claude Agent SDK + Claude Code 訂閱**（不是 API key、不按 token 計費）。

## 執行

```bash
cd /home/ben/CC_Paper_researcher/paper-reader
uv run python main.py --dry-run                      # 只跑 Discovery（零額度）
uv run python main.py --limit 1 --stages rank,screen,read,enrich   # 跑指定階段
uv run python main.py --limit 3                      # 完整夜跑（含所有 stage）
```

`--paper <arxiv_id>` 補跑單篇；`--stages a,b,c` 指定階段。

## Pipeline（stage 順序）

discovery → rank → screen → read → enrich →（review）→（publish）

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
- `.claude/skills/` — paper-analyzer、academic-paper-reviewer（symlink 到上層 repo）

## 進度（2026-06-12）

- ✅ M1 Discovery / M2 rank+screen+read+extract / M3 enrich（MathML + caption-crop base64）
- ✅ **M4：review（reviewer skill quick + 銳評）+ publish（Notion 建頁 + Discord 通知）** — 第一篇 `2605.23355` 已完整跑到 status=published
- ✅ **M4.1：繁體中文（OpenCC s2twp）** — Notion 頁 0 簡體；enrich + notion + publish 三處轉換。
- ✅ **M4.2：Notion 頁改精選版**（使用者要求，取代 M4.1 的整份 PDF）——銳評 + 後設資料 + 整體架構總覽（Mermaid 截圖 PNG，image block）+ 四章節原文（討論/侷限/研究脈絡/產品落地）。已驗證：47 blocks、含 image、0 簡體、無頁尾/圖表外洩。新增 `output/report_parse.py`、`uv add beautifulsoup4`。架構圖 PNG 截圖乾淨（完整 DSTA pipeline）。
- ⬜ **M5（下一步）**：systemd timer 每晚自動 + 逐篇 per-paper 迴圈（目前是逐 stage；改成每篇 read→enrich→review→publish 一條龍，任一篇失敗不擋其他）+ 額度 graceful stop 全程覆蓋。可補：`main.py --paper <id>` 單篇端到端、papers_log.csv 補上 review_verdict 欄。

## .env（已設定，git 忽略）

NOTION_TOKEN / NOTION_PARENT_PAGE_ID（page `37cddeca…`）/ NOTION_DATABASE_ID（M4 已自動建並寫回：`37dddeca…`）/
DISCORD_BOT_TOKEN（沿用 AI_searcher）/ DISCORD_CHANNEL_ID(1514458061014700143) /
SEMANTIC_SCHOLAR_API_KEY（已驗證）/ GEMINI_API_KEY（已棄用，可留）

完整計劃：`../5-token-notion-zotero-notion-dc-token-fluttering-lake.md`
