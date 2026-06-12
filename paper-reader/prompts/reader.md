請使用 **paper-analyzer** skill 深度解析這篇論文：

- 標題：{title}
- arXiv 連結：{url}
- 本地 PDF：{pdf_path}
- 輸出目錄：{out_dir}

這是無人值守的批次任務，沒有人類可回答問題。所有 skill 中途的決策點，一律採用下列預設值，**不要停下來詢問**：

1. **寫作風格固定為 `academic`（學術深度解讀）**，跳過風格選擇步驟，直接進入產出。
2. 全文使用**台灣繁體中文**（HTML 與 JSON 的值都必須是繁體字，**嚴禁簡體字**，例如要寫「網路/實驗/動作/識別」而非「网络/实验/动作/识别」）。
3. HTML 報告寫到：`{out_dir}/report.html`（自包含）。
4. **報告中務必包含 Mermaid 圖：至少一張「方法/實驗架構圖」與一張「整體流程圖」**（用 `<pre class="mermaid">` 或 ```mermaid 區塊），這是讀者最想看的部分。
5. 公式照常用 `$$...$$`（系統會在後處理自動轉成 MathML，你不需處理）。
6. 若需要抓取全文或程式碼，只使用 arXiv 與 GitHub（其餘網域會被擋）。

產出 HTML 後，**額外**依下列「閱讀模板」把分析整理成 JSON，寫到 `{out_dir}/analysis.json`（鍵用英文、值用繁體中文）：

```json
{{
  "summary": "A 核心問題與價值（Gap）、What/Why/How",
  "contributions": "A 關鍵創新點（Novelty）：新的部分 vs 整合既有；移除創新點後還剩什麼",
  "methodology": "A 方法流程 Input→Process→Output",
  "assumptions": "B 隱含假設：哪些條件成立、哪些情境會失效",
  "reproducibility": "B 重現必要條件：資料集、算力、超參數",
  "experiment_fairness": "C 實驗可信度：被淡化處、Baseline 是否合理、成本效益",
  "next_steps": "D 短/中/長期改進與後續研究方向",
  "figure_notes": "F 逐張關鍵圖表解讀，哪張最能代表核心主張",
  "field_positioning": "G 研究脈絡、關鍵前置工作、與同期論文的相對位置",
  "production_readiness": "H 真實產品落地還需哪些工程、主要瓶頸與適用規模",
  "innovation_score": 1-10,
  "relevance_score": 1-10
}}
```

完成後回覆一行：`DONE report.html + analysis.json`。
