以下是一篇論文的「學術深度解讀」報告內文（已從 HTML 轉為純文字）。
請依下列模板，把內容整理成結構化 JSON。鍵用英文、值用繁體中文，內容忠於報告、精煉不灌水。
分數（innovation_score / relevance_score）為 1–10 整數，依報告內容合理評估。

只輸出 JSON 物件，不要任何其他文字：

```json
{{
  "summary": "核心問題與價值（Gap）、What/Why/How",
  "contributions": "關鍵創新點（Novelty）：新的部分 vs 整合既有；移除創新點後還剩什麼",
  "methodology": "方法流程 Input→Process→Output",
  "assumptions": "隱含假設：哪些條件成立、哪些情境會失效",
  "reproducibility": "重現必要條件：資料集、算力、超參數",
  "experiment_fairness": "實驗可信度：被淡化處、Baseline 是否合理、成本效益",
  "next_steps": "短/中/長期改進與後續研究方向",
  "figure_notes": "關鍵圖表解讀，哪張最能代表核心主張",
  "field_positioning": "研究脈絡、關鍵前置工作、與同期論文的相對位置",
  "production_readiness": "真實產品落地還需哪些工程、主要瓶頸與適用規模",
  "innovation_score": 1-10,
  "relevance_score": 1-10
}}
```

論文標題：{title}

報告內文：
{report_text}
