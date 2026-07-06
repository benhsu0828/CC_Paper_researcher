# MotionXperts / CoachMe 開發筆記整理 — 給羽球遷移用

> **來源**:作者 Wei-Hsin Yeh 的開發 log <https://hackmd.io/@weihsinyeh/MotionXperts>
> **對應論文**:CoachMe (ACL 2025) — [arXiv 2509.11698](https://arxiv.org/abs/2509.11698) ｜ [專案頁](https://motionxperts.github.io/)
> **整理目的**:抽出「作者遇到的困難 / 他怎麼推理 / 試過什麼、哪些該避開」,做為把架構改成**羽球揮拍**的參考。
> ⚠️ **注意**:作者的筆記做的是 **花式滑冰(Skating)+ 拳擊(Boxing)**,不是羽球。下面的事實都來自他的 log,遷移判斷是本文的延伸,標 🟦。重要技術細節建議回原文核對(本整理經過摘要壓縮)。

---

## 0. 一頁速覽:作者的整條 pipeline

```
影片
 └─(HybrIK)→ 骨架 24×3 → 砍成 22×3(對齊 HumanML3D 預訓練)
     └─ 建 channel:joint(3) + bone(3) = 6 × seq_len × 22
         └─(STAGCN encoder,三分支)→ Feature / Attention / Perception
             └─ PA_embedding 11264 → 5632 → 768(投影到 T5 維度)
                 └─(對齊模組:CARL concept + DTW)學員↔教練對齊、抓 error segment
                     └─(T5-base decoder)→ 生成文字
                         · pretrain 用「描述語(Motion Description)」起始 token
                         · finetune 用「指導語(Motion Instruction)」起始 token  ← 這就是他的 CoT 做法
```

對應到你 [研究方向與論文清單.md](研究方向與論文清單.md):CARL = 清單 #7、DTW/Soft-DTW = #6、STAGCN 思路接近 #8 的骨架 AQA。

---

## 1. 模型架構(逐層,含設計決策)

| 層 | 做法 | 作者的設計理由 / 細節 |
|---|---|---|
| **骨架抽取** | HybrIK([arXiv 2011.14672](https://arxiv.org/abs/2011.14672)) | 30 fps、SMPL 格式、**以胸口為中心的 local 座標**。把 24×3 砍成 **22×3**,丟掉的是「兩隻手末端」,目的是對齊 HumanML3D 預訓練資料 |
| **特徵 channel** | joint + bone | bone = 關節相減的向量,方向「**從骨盆往外**」;合成 `6 × seq_len × 22` |
| **圖結構** | SMPL layout,`strategy='spatial'`,`hop=3` | 產生 7 個鄰接矩陣 M₀–M₆,分成 Root / 向心(Centripetal)/ 離心(Centrifugal),中心 = 骨盆(joint 0)當「骨架重心」 |
| **STAGCN encoder** | Spatial GCN(3→32 channel)+ Temporal Conv(`t_kernel=9, dropout=0.5`, 殘差) | 三分支:**Feature Extractor**(→128)、**Attention Branch**(→256,產 4 個 attention 矩陣 + attention node)、**Perception Branch** |
| **學習式 attention** | M₇–M₁₀ | 跟 M₀–M₆ 不同:**不受 hop≤3 限制、值落在 0–1、每支影片都不一樣** ← 即「可學的鄰接關係」 |
| **對齊模組** | CARL concept + **DTW**(`use_dtw=True`) | `align_videos(兩支影片)` 對齊學員↔參考,回傳對齊後序列 + index 對應,並抓 error segment(SOE/EOE) |
| **投影** | `11264 → 5632 → 768` | 把 PA_embedding 投到 T5-base 輸入維度 |
| **解碼** | T5-base(223M) | `att_node`、`att_A` 留作評估/視覺化用 |
| **任務切換** | 起始 token | pretrain=「描述語」、finetune=「指導語」,**用起始 token 區分任務 = 他的 Chain-of-Thought 實作** |

---

## 2. 作者遇到的困難(分五類)

### A. 模型輸出本身的毛病(最該記住)
1. **轉圈數算不準**:因為骨架被「固定在原地」(local 座標、原點不動),模型**看不出何時落地** → 旋轉圈數判斷錯。
2. **負面偏差「有輸入就一定有錯」**:訓練時教練指導語**全是糾錯**,模型隱含假設「**只要有影片就一定有錯**」→ **就算跳得好也沒有機制肯定「已達標」,一味吹毛求疵**。(此點見**開發筆記**「模型的問題」第 2 點,論文 Limitation 未列。)🟦 **你的勝利點**:prompt「誤差不大不給建議」+ **MDC 門檻**正解這個(詳見第二部分 §1.3 #7、2.3)。
3. **對的建議講在錯的時間點**:例如在「第一跳」的區間講「第二跳」的問題(`錯誤區間錯`)。
4. **有些指導語需要該運動的專業知識**才講得出來(模型缺領域知識)。
5. **動作分類對、但時間切分錯**:classification 正確不代表 segmentation 正確。

### B. 對齊 / 時間層面
- **Boxing 的 GT 對齊不穩**:視覺化後發現「ground truth 尋找時**跳來跳去**」(ClosestSimGT 在四個 GT 間亂跳)。
- **cross attention 看起來「全連接」**、不可解釋,懷疑是「**做 maxpool 時又同時交換 seq_len 與 22 joints 兩個維度**」造成的。

### C. 訓練 / 實作層面
- **LoRA 無法 reproduce**:Method A 修不好,Method B/C 才修掉。
- **T5 attention 視覺化型態錯誤**:encoder/decoder/cross attention 丟進 `model_view()`/`head_view()` 出現「tuple 與 tensor 型態轉換錯誤」(查 HF 論壇 + commit `066710` 才解)。

### D. 資料層面
- **一支影片對應多份 GT(資料增強)**(dev notes 為 4 句 A-1~A-4;論文 FS 為 1 原始 + 5 augmented = 6 句)→ 帶來「多樣性 vs 過於通用」、**標註者偏見**、**互相矛盾的指導語** 等問題。
- 有明確標記「**有錯的資料**」清單;移除了兩位教練的資料與某些片段。

### E. 評估層面
- **BLEU/ROUGE 對這任務不準**:BLEU-4 只有 2.3–6.8,因為表面字串重疊抓不到教練指導語的品質 → 只能靠 **G-eval + 人評**。
- 擔心 augment/串接 GT 會讓輸出「**過於 general**」(G-eval 變差),即使多樣性上升。

---

## 3. 作者怎麼想(關鍵推理與取捨)

- **量化「換句話說」的程度**:把生成文字分類 → 對映到影片類別(text class A → video class B/C/D),用來量「模型到底是換句話說還是真的講對」。
- **回饋分流**:關節關係的發現回報給「建模的人」、身體部位的觀察回報給「做資料的人」—— 把問題歸因到正確的負責環節。
- **joints 當 token 的取捨**:把 22 joints 放到 token 維度,T5 的 22 個 token「**不含前後關係**」→ 浪費 positional encoding;因此考慮改成 **「22 × times」** 架構(保留時間順序)。
- **投影層救不了時序,最終用「時間濃縮」妥協(解你之前的疑問)**:投影層只做**維度轉換**(把 GCN 特徵投到 T5 維度,見第 1 節),**無法把被「維度交換」破壞掉的時間順序無中生有還原**。發表的論文最終**沒有**把完整連續時序餵給 LLM,而是沿時間做 **max pooling**,只抓「**動作落差最大的那一瞬間(the most critical moment,論文 Eq.8)**」→ LLM 收到的是**高度壓縮的「關鍵錯誤快照」**,沒有連續時間長度。這是為配合輕量 T5 的**工程妥協**(dev notes 一度想換掉 maxpool,但論文最終仍保留)。🟦 **對照你的優勢**:你的**每相位 timing** 保留了時間結構,沒被壓成單一快照。
- **finetune 風格做成可選參數**:用 `Args` 讓訓練者選「**prompt 方式 vs 放 token(CoT)方式**」。

---

## 4. 試過什麼 + 哪些可避開(踩雷地圖)

| 試過的東西 | 結果 | 🟦 給羽球的啟示 |
|---|---|---|
| **Method A — PerGT**(對多句 GT 各算一次 loss):原始版 | **沒修好 LoRA 無法 reproduce**(dev notes 標 `bad lora`) | reproducibility 一開始就要顧 |
| **Method B — ClosestSimGT**(每次用 cosine 相似度挑「最像的一句 GT」算 loss) | reproduce 已修好;但 Boxing(異質資料)視覺化時 **GT 選擇「跳來跳去」、標 `bad`** | 避開:多 GT「選最相似」在異質資料上 GT 選擇不穩 |
| **Method C — PerGT + 修好 reproduce(dev notes「Change 1」)** | reproduce 解決;Skating `better` | **reproduce 的修法是獨立改動,不是 loss 種類決定的**(別把兩者因果綁一起) |
| **maxpool + 同時交換 seq/joint 維度** | cross attention 變「全連接」不可解釋 | **避開**:pool 與維度交換別混在一起;TODO 是「換掉 maxpool」 |
| **joints 當 T5 token** | 丟失時間順序 | **避開**:羽球揮拍是高速時序,**務必保留時間維度**(走 22×times) |
| **梯度回傳找最重要的 attention 矩陣** | 限制:「**只能跟整句話建立關係,無法跟單一文字 token 對應**」 | 可解釋性有上限,別過度依賴 |
| **augment 成 4 GT** | 多樣性↑但「過於 general」、標註者偏見、矛盾指令 | 🟦 羽球若請多位教練標,要先對齊標註準則 |
| **固定原點的 local 座標** | 算不出落地/位移類事件 | 🟦 羽球**腳步/重心移動、擊球瞬間**會同樣受害 → 見下節 |

作者自列的 **TODO**:① 把 Adjacency Matrix 改成可學的;② 用別的操作取代 maxpool。

---

## 5. 六個實驗(他想驗證什麼 → 你可照抄消融設計)

| 實驗 | 驗證的問題 | 比較組 |
|---|---|---|
| 實驗一 | 對齊有沒有用?RGB 有沒有用?skeleton pooling 有沒有用? | 2 vs 4 vs 6 |
| 實驗二 | 對齊能否提升描述品質(自動指標) | A vs C |
| 實驗三 | pretrain 與 finetune 是不是同一個任務(人評) | config 3 vs 4(差在對齊 + transform 模組) |
| 實驗四 | pooling 類型的影響(自動指標) | B vs C |
| 實驗五 | 只有 generator 夠不夠?現在的 generator 有沒有加分(人評) | 1 vs 4 |
| 實驗六 | **對齊模組的好壞會不會影響生成** | 6 vs 7 |

🟦 這套消融骨架(對齊 on/off、RGB on/off、pooling 種類、pretrain=finetune?)可直接搬到羽球論文當實驗章節。

---

## 6. 🟦 遷移到羽球的建議

### 6.1 可直接沿用
- **整條 pipeline 骨幹**:骨架 → 圖卷積 encoder → 對齊(CARL+DTW)→ 投影 → T5 生成。
- **CoT = 起始 token 切任務**(描述語 pretrain → 指導語 finetune)的做法。
- **評估**:別只看 BLEU/ROUGE,**以 G-eval + 人評為主**。
- **消融設計**(第 5 節那六個)。

### 6.2 一定要改
1. **座標固定問題 → 羽球更嚴重**:作者因「骨架固定原地」算不出落地時間。羽球的**步法移動、起跳殺球、擊球瞬間 timing** 全是位移/時間事件 → **不要只用胸口 local 座標**,要保留全域位移或另開「擊球時刻」偵測頭(對應你清單 #1–#4 的揮拍切分)。
2. **保留時間維度**:別把 joints 當 token、犧牲前後關係。羽球揮拍是高速序列,走作者最後的「**22 × times**」方向,positional encoding 要用上。
3. **遮擋/雜訊**:單鏡頭羽球非持拍臂被遮擋(你清單 #8 QAQA 的痛點),對齊前先做異常幀降權,呼應作者「對齊不穩」的教訓。
4. **正面回饋機制**:作者模型「只會挑毛病」。羽球教練回饋也需要「**做對就肯定**」,訓練語料要含正面語句,否則同樣會一味 nitpick。

### 6.3 一定要避開(直接吃作者的虧)
- ❌ maxpool 同時交換 seq/joint 維度 → 注意力不可解釋。
- ❌ 重蹈 Method A(PerGT 原始版)卡在 LoRA 無法 reproduce —— reproducibility 要靠獨立修正(dev notes「Change 1」)一開始就顧。
- ❌ 多 GT「選最相似」在**異質資料**(多教練/多視角/多動作)下會跳動不穩 —— 羽球若多人標註,先統一標註準則,或改回 PerGT。
- ❌ 用表面重疊指標(BLEU)判斷好壞。

### 6.4 與你既有研究的銜接
- 揮拍切分(落地/擊球 timing)→ 清單 **#1–#4**(MS-TCN / ASRF / ASFormer / DeST)。
- 對齊與雜訊降權 → 清單 **#6 Soft-DTW、#7 CARL、#8 QADTW**。
- LLM 生成端的幻覺控制 → 「結構化輸入 + 自然輸出 + 幕後忠實度檢查」,補作者沒處理的「指導語幻覺/講錯時間點」。

---

## 待核對(摘要壓縮可能遺漏)
1. 對齊模組到底是用 CARL embedding 上跑 DTW,還是 DTW 直接在原始關節上 —— 影響你雜訊降權要插哪裡。
2. ~~「22 × times」架構作者最後有沒有實作~~ → **已解**:作者最終**未採用**完整時序,改沿時間 **max pooling** 濃縮成「最糟一瞬間」(見第 3 節)。
3. G-eval 的 prompt 與評分 rubric 細節(要照抄評估就得回原文)。
4. RGB 分支(實驗一的 RGB)實際接法 —— 羽球若要加球拍/球的視覺資訊會用到。

---
---

# 我的論文:定位、進度、與參考來源

> 取代先前分散的附錄一~四,收成三段。
> **確認的現況**:本地開源 LLM(Qwen/Llama)；做**長球(clear)**；**手動標** 4 相位；每相位 DTW(身體角度,三角函數算)+ 每相位 timing(原始座標的正規化位移);取 3 次平均去抖;評估靠「自己會打球、自己看」。

---

## 第一部分:定位 — 我 vs 其他論文,我的優勢

### 1.1 兩大範式(先確認你站哪)
- **端到端微調流派(CoachMe)**:骨架 → 學習式 encoder → 小型 LM(T5 223M)整條一起訓,需成對(動作,指導語)資料。
- **特徵萃取 + 通用 LLM 流派(你 / Talking Tennis / SportsGPT / AgentCoach)**:算物理特徵 → prompt 驅動通用 LLM,**不訓練 motion encoder**。

→ **你在後者。** 重要原則:**跨範式(你 vs CoachMe)只能比「輸出」,且要附 caveat;真正的「方法優劣」要在自己範式內用消融證**(因為跨範式同時差太多變數)。

### 1.2 對手定位表
| 系統 | 運動 | 生成端 | 特徵 / 接地方式 | 公開 | 量幻覺? | timing 粒度 |
|---|---|---|---|---|---|---|
| **CoachMe** | 滑冰/拳擊 | T5-base 端到端 | 學習式 token | code 公開,資料 conditional | 否(G-eval+人評) | error segment |
| **SportsGPT** | 田徑 | Qwen3 + RAG(6B-token KB) | KISMAM(知識規則) | 全無 | 否(Likert+IoU) | 角速度/加速度 |
| **Talking Tennis** | 網球 | 特徵字典 + LLM 短字串 | REFERENCE_RANGES(rule base) | 無 code,用公開 THETIS | 否(只宣稱) | 單一 swing duration |
| **AgentCoach (CHI'26)** | 動作學習 | 教練要點(CP)→評估器 + LLM | CP→可量測參數 | 付費牆 | 否 | CP 對應 |
| **你** | **羽球長球** | 本地 Qwen/Llama + prompt | **每相位 DTW(角度)+ 正規化位移 timing** | — | **規劃:幕後忠實度檢查** | **每相位(架/引/揮)細粒度** |

> ⚠️ 「量幻覺?=否」精確意思:他們都**緩解**幻覺、也都**評品質**;但**沒人量「最終回饋文字 ↔ 輸入數值」的 per-claim 編造率**。SportsGPT 的 IoU 是在評估指標層比對專家規則,不是在 NL 回饋層。對外別講成「沒人做接地/沒人評」。

### 1.3 我的優勢(誠實版)
1. **每相位結構化比對 + 保留時序**:4 相位各自 DTW(身體角度)+ 各相位 timing(正規化位移)—— **比 Talking Tennis 單一 swing duration 細、比 SportsGPT 通用 keyframe 更貼羽球結構**;而且 **CoachMe 最終把時序 max-pool 成單一「最糟快照」,你反而保留了時間結構**。**已實作 = 真資產**。
2. **量測誤差感知門檻(MDC,~~規劃中~~已實作)**:**沒有任何對手處理 MediaPipe 噪音**;你針對「自己 vs 自己也跳建議」擋掉 → **空白地帶**。✅ **更新(2026-06)**:已實作的「自變異 2σ 帶」實證上**就等於 MDC₉₅**(常數差 2%、決策一致;見 [EXPERIMENT_LOG §7.1](EXPERIMENT_LOG.md))。所以這條優勢**已成立**,論文只需把門檻寫成 MDC₉₅(Weir 2005)+ 報 per-feature ICC,**不是再加新機制**。
3. **動力鏈「磁量鏈」(已實作、可信)** ~~「順序/相對時序」~~:
   ⚠️ **重大修正(2026-06 實測)**:動力鏈分兩種,**只有一種成立**——
   - ✅ **磁量鏈(path_norm,各環節走多遠)**:近→遠單調遞增(肩<肘<腕<拍)在 ben 與學員皆成立、ICC 0.87–0.97、學員整條只走教練 ~50–60% 且全顯著 → **這是真資產,論文用這個**。
   - ❌ **順序/相對時序(peak-velocity timing 或 onset)**:近→遠峰值順序 ben 僅 1/6、學員 0/11 球成立(肩部 2D 多為深度方向,峰時刻不可信)→ **單目 2D + 相位切片下不成立**。**論文不要宣稱 kinematic sequence timing**,會被打穿;要做需 2D→3D lifting(清單 #9)或多機位。詳見 [EXPERIMENT_LOG §1、§2](EXPERIMENT_LOG.md)。
4. **本地開源 LLM + 免 RAG**:可控、可部署、可蒸餾;**輕量故事比用 GPT 可信**(vs CoachMe 的模型大小落差也較溫和)。
5. **羽球長球**:對手都是網球/田徑/滑冰/拳擊,**沒人做羽球**。
6. **你會打球**:可當領域評審 —— 但**評估必須去循環**(見 2.2 #1)。
7. **正面回饋 / 不過度糾錯**:CoachMe 的「**有輸入就一定有錯**」缺陷(見開發筆記)讓體驗挫折。你 prompt「誤差不大不給建議」+ **MDC 門檻**解了「**不過度糾錯**」那半;若再補「**達標就主動肯定**」那半(「不講話」≠「稱讚」),就完整打贏 → 勝利點。

### 1.4 定位紅線(別被口委打)
- **vs CoachMe**:只比輸出 + 附「我 Qwen 較大、它主打輕量 T5-223M」caveat → 定位 **tradeoff,非勝負**。
- **vs SportsGPT / Talking Tennis**:沒釋出 → 無法直接比 → 只能引用其論文數字 + **忠實重現等價 baseline**(不准弄笨)。
- **「相對 vs 絕對參考」**:CoRe(清單 #5)已證相對較佳 → **別當原創發現**,定位成「在羽球上驗證」。
- **別把「可解釋」當賣點**(TT、SportsGPT 都喊了)→ 改用「**可量測的忠實度 + MDC 門檻**」。

---

## 第二部分:我的進度 / 可改進 / 缺漏

### 2.1 現在做到哪(confirmed)
- MediaPipe → **三角函數算身體角度** + **原始座標算正規化位移**(已處理人物離鏡頭遠近)。
- **手動標** 4 相位:架拍 / 引拍 / 揮拍 / 非揮拍。
- **每相位 DTW(身體角度)** + **每相位 timing(正規化位移)** 比對學員 vs 教練。
- **取 3 次平均**去抖。
- **本地 Qwen/Llama + prompt** 生繁中回饋。
- **評估**:自己打球、自己看(判斷幻覺/不精準),prompt 也這樣調。

### 2.2 缺漏與可改進(依優先序)
1. **【最高優先】沒有系統化評估,而且是循環的**:你**自己調 prompt、又自己 judge** = 單人、非盲、確認偏誤。**這是擋住「成為論文」的第一關。** 改:固定 rubric(自然/正確/有用/幻覺)+ **教練盲測**(你可當其中一位,但要**盲、最好多人**)。沒這個 → 整本懸空、所有「變好」都是感覺。
2. **MediaPipe 噪音沒嚴格擋 → 加 MDC**:你現在只「取平均」解決了**第一層(單次內亂跳)**;沒擋你抱怨的**第二層(不同次揮拍間波動 → 自己 vs 自己也跳建議)**。改:用同人重複資料逐特徵算 `SEM = SD×√(1−ICC)`、`MDC₉₅ = 1.96×√2×SEM`,**只有 |學員−教練| > MDC 才回饋**。
3. **手動切分 → 不可擴展**:目前手動標相位,沒法 scale,且是論文 limitation。短期當 proof 可接受,但要**寫成 limitation + future**,並指向自動切分(MS-TCN/ASRF/ASFormer/DeST,清單 #1–4)。
4. **特徵已加動力鏈磁量鏈**:現有「角度 + timing」是這領域**標配,不算優勢**;~~加「峰值順序 + 節間相對時序」~~ → **峰值順序/時序在單目 2D 不成立(2026-06 實測,見 §1.3 #3)**;改用**已實作的磁量鏈(path_norm,近→遠走多遠)**,可信(ICC 0.87–0.97)且有羽球專屬區辨力。
5. **輸出/幻覺未量,且可能太機械**:加**幕後忠實度 sanity check**(不影響輸出措辭);輸出走**自然教練語**,用「CP→評估器」當數字與自然語言的橋(借 AgentCoach)。
6. **與對手可比性**:重現一個 Talking Tennis 等價 baseline;沿用 CoachMe 公開的 G-eval 模板(讓數字可比)。

### 2.3 兩層噪音處理(別搞混,常被問)
- **第一層 — 單次揮拍內亂跳**:時間平滑(One-Euro / Savitzky–Golay)+ 關鍵幀取視窗中位數;取平均也屬這層(平均 k 次降 SEM √k 倍)。
- **第二層 — 不同次揮拍間波動(你的痛點)**:**MDC 門檻**。自己 vs 自己的差依定義 95% 落在 MDC 內 → 不觸發。⚠️ 還有一道 **MCID(最小有意義差異)** 要教練定:門檻要在 MDC 之上、但別高到把真正該講的也濾掉。

### 2.4 乾淨實驗設計(一次只動一個變數)
- **消融**:有/無 **MDC 門檻**(=自變異帶) → 量「無意義/自我矛盾建議率」降多少;有/無 **動力鏈磁量特徵**(path_norm,非順序/時序) → 量教練評分提升。
- **baseline**:忠實重現 Talking Tennis 等價系統(同 LLM、同等 prompt,**不准弄笨**)。
- **評估三層**:教練盲測 Likert(主)+ per-claim 忠實度幕後檢查(輔)+ G-eval(沿用 CoachMe 模板,當查證器、附偏誤說明)。
- **結論措辭**:報 delta + 顯著性(「加 X 使編造率 a%→b%」),**不喊**「無懈可擊 / 屌打 / 比誰快」。

---

## 第三部分:各技術的參考來源

| 技術 | 參考論文 / 來源 | 對你的用途 |
|---|---|---|
| **MDC / 信度公式** | **Weir JP (2005)**, *Quantifying test–retest reliability using the ICC and the SEM*, J Strength Cond Res 19(1):231–240 | MDC/SEM 公式**必引** |
| markerless 信度實證 | [PMC11783685](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11783685/)(髖膝 ICC 0.74–0.93)、[MDPI 16:1202](https://www.mdpi.com/2076-3417/16/3/1202)(上肢極端角 RMSE 16–19°)、[Heliyon](https://www.cell.com/heliyon/fulltext/S2405-8440(24)12369-9)、[JMIR 102399](https://preprints.jmir.org/preprint/102399) | 證「為何需要 MDC」(MediaPipe 真的會抖) |
| 單目關節角誤差地板 | 清單 #10 Medrano-Paredes 2025(RMSE ~9°) | 門檻下限依據 |
| 降抖動(第一層) | One-Euro filter、Savitzky–Golay(清單待補 #2) | 平滑 + 微分前處理 |
| **自動揮拍切分** | MS-TCN(#1)、ASRF(#2)、ASFormer(#3)、DeST(#4) | 取代你現在的**手動標** |
| 對齊 / 降噪 | Soft-DTW(#6)、CARL·TCC(#7)、QAQA 異常感知 DTW(#8) | 強化你的 DTW + 異常幀降權 |
| **動力鏈(生物力學,what)** | [TPI Kinematic Sequence](https://www.mytpi.com/articles/biomechanics/kinematic-sequence-revisited)、[AAU 羽球 Clear](https://projekter.aau.dk/projekter/files/42678547/articledone.pdf)、[Rusdiana](http://www.sportmont.ucg.ac.me/clanci/SM_October_2021_Rusdiana.pdf)、Marshall & Elliott 2000、[PMC9598458](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9598458/) | 「峰值順序/相對時序」特徵依據 |
| AQA 特徵表示(how) | Pan 2019 Joint Relation Graphs、[FineCausal](https://arxiv.org/pdf/2503.23911)、[TGST](https://link.springer.com/article/10.1007/s00371-025-04101-6) | 關節「關係」/因果式可解釋表示 |
| 相對 vs 絕對評分 | CoRe(#5) | 相對較佳已證(別當原創) |
| **對手 LLM 教練** | CoachMe、[SportsGPT](https://arxiv.org/abs/2512.14121)、[Talking Tennis](https://arxiv.org/abs/2510.03921)、[AgentCoach](https://doi.org/10.1145/3772318.3791652) | related work + baseline |
| 2D→3D(若要遠端速度) | VideoPose3D / MotionBERT(#9) | lifting(動力鏈絕對速度版才需要) |
| prompt 降幻覺 | 結構化輸入 + CoVe(arXiv:2309.11495)/ Self-Refine(arXiv:2303.17651) | 幕後忠實度檢查 |

> 「清單 #N」指 [研究方向與論文清單.md](研究方向與論文清單.md) 的編號。

---

## 下一步(到實驗機後,投產比排序)
- [ ] **建評估**:固定 rubric + 教練盲測(去掉「自己調自己評」的循環)→ 拿 baseline 數字。
- [ ] 用同人重複資料逐特徵算 **ICC / SEM / MDC₉₅**(關節角 + 各相位 timing)。
- [ ] 加 **MDC 門檻**,跑「有/無門檻」消融 → 量無意義建議率下降。
- [ ] 實作**動力鏈「峰值順序 + 節間時序」**(含平滑),跑「有/無」消融。
- [ ] 定 4–6 個長球 **CP→評估器**,輸出走自然教練語。
- [ ] 重現 **Talking Tennis 等價 baseline**,設計教練盲測。
