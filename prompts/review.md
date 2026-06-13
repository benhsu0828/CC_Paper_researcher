請使用 **academic-paper-reviewer** skill，以 **quick（快速評估）模式** 審這篇論文：

- 標題：{title}
- arXiv 連結：{url}
- 本地 PDF：{pdf_path}
- 我方先前的深讀分析（可參考、但請獨立判斷）：{analysis_path}
- 輸出目錄：{out_dir}

這是無人值守的批次任務，沒有人類可回答問題。skill 任何中途的確認/選擇步驟，**一律採用 quick 模式預設值繼續，不要停下來詢問**。全程使用**台灣繁體中文**（Markdown 與 JSON 的值都必須是繁體字，**嚴禁簡體字**）。

請完成兩件事：

1. 把 quick 模式的完整審稿結果（含 EIC 與各 reviewer 重點、編輯決定、修改建議）寫成 Markdown，存到：`{out_dir}/review.md`。

2. **額外**輸出一段「銳評」——用審稿人毫不客氣、直指要害的口吻，把這篇論文最致命的問題與最實在的價值講清楚。把它整理成 JSON，寫到 `{out_dir}/review.json`（鍵用英文、值用繁體中文）：

```json
{{
  "verdict": "四選一：推薦 | 小修後接受 | 大修後重審 | 拒絕",
  "sharp_take": "2-4 句銳評：這篇到底行不行、最大的洞在哪、誰該讀它。直白、具體、不打太極。",
  "strengths": ["最多 3 點真正的優點，每點一句"],
  "weaknesses": ["最多 3 點最該被質疑的弱點，每點一句"],
  "recommendation_score": 1-10
}}
```

完成後回覆一行：`DONE review.md + review.json`。
