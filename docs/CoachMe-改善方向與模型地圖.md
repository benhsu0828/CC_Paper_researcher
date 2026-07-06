# Base on CoachMe：改善方向、成對資料 label 設計、與模型地圖

> 接續 [MotionXperts-開發筆記整理.md](../MotionXperts-開發筆記整理.md)。
> 這份回答三件事：(1) 卡住的「教練 vs 教練誤差太大」要怎麼解；(2) 若請教練標註、要 base on CoachMe，**label 該長怎樣、資料要多少**；(3) 可走的實驗方向與該讀的論文。
> 來源：CoachMe（[arXiv 2509.11698](https://arxiv.org/abs/2509.11698), ACL 2025）、VidDiff（[arXiv 2503.07860](https://arxiv.org/abs/2503.07860), ICLR 2025）全文擷取 + 模型survey（2025-06）。

---

## 0. TL;DR（決策）

- **能 base on CoachMe，而且該這麼做。** CoachMe 整套只訓練 **滑冰 177 / 拳擊 163 支影片**（+ HumanML3D 預訓練 + GPT-4o ×5 改寫）→ **一兩百支羽球標註就夠**，資料量不是牆。
- **請教練「直接看學員揮拍寫建議」（reference-free）會溶掉你卡住的噪音問題**：你現在卡在「學員骨架 − 教練參考骨架」**兩個吵雜訊號相減**；改成教練的判斷當 ground truth，就不再相減。
- **你的資產不丟，反而變成對 CoachMe 的改善點**：相位時序（解它 max-pool 丟時序）、MDC 閘（解它「永遠挑錯」+ 自動產生 error segment）、path_norm 動力鏈（補它缺的領域知識）。
- **VidDiff 是你最強的 motivation**：GPT-4o/Gemini/Qwen2-VL 在細粒度動作比較上**近乎瞎猜（53–58%，二分類）** → 證明「為什麼需要結構化特徵 + 專項模型」。

---

## 1. 你為什麼卡住，CoachMe 範式怎麼解掉它

**卡點**：「教練 vs 教練自己誤差就很大，何況教練 vs 學員」。

**診斷**：這是**數值比對範式獨有**的問題。你做 `學員 − 教練參考`（DTW 角度差），兩個訊號都帶 MediaPipe 量測噪音 → 相減後噪音放大。你的 MDC 其實已擋掉「假顯著」（自比對 0、跨人 13/4），所以剩下的不是誤判，而是**語意鴻溝**：逐關節「right_wrist 角差 8.6°」翻不成教練真正會講的整體診斷（「你在用手臂打、髖沒進去」）。**這才是 DTW+timing 給不出好建議的真因。**

**CoachMe 怎麼解**：它的 label **不是數值比對**，是「教練看著動作直接寫該怎麼改」。沒有減法、沒有參考骨架相減，**教練的眼睛就是 ground truth**。

→ 請教練標註 + base on CoachMe = **用一次性標註成本，換掉「相減噪音」這種結構性、永遠存在的問題**。代價轉成「資料量 + 多教練不一致」，但那比較好處理。

---

## 2. 成對資料的 label 設計（base on CoachMe 的具體規格）

### 2.1 CoachMe 的 label 長怎樣（實證）
- **每支動作 1–3 句、第二人稱、整體式（一次點多個部位）、處方式、不帶數字**。
  例：`Keep your left leg low and tight.`／`Your back foot isn't fully elevated, your hips aren't completely rotating, and your shoulders are a bit stiff.`
- 滑冰：**1 句原始** instruction + **GPT-4o 改寫 5 句**（增強，相似度 0.93）。
- 拳擊：**3 位教練各寫 1 句**（共 3 句）+ 改寫 3 句（相似度 0.95）。
- 滑冰另標 **error interval 時間戳**（449 段訓練 / 64 段測試）；拳擊**沒有**標 error segment。
- **多教練不一致：CoachMe 沒處理**（拳擊三句全用），這是它的坑。

### 2.2 羽球版建議規格
| 欄位 | 建議 | 理由 |
|---|---|---|
| **單位** | 每一次揮拍 | 對齊 CoachMe、對齊教練講話方式（不會逐關節講） |
| **label 本體** | 1–3 句教練處方，第二人稱、整體式、不帶數字 | 同 CoachMe |
| **相位標記** | 每句可選標 架/引/揮 | 幾乎免費，解 CoachMe「對的建議講錯相位」 |
| **error interval** | **不手標**，用你的「相位 + MDC 顯著關節」自動產生 | CoachMe 手標 449 段很貴；你的 MDC 是**自動 error-segment 標註器** ← 改善點 |
| **正面 label** | 揮得好就寫「這球沒大問題」/ 肯定哪裡對 | 從資料源頭解 CoachMe「永遠挑錯」；**MDC 哲學變標註準則**（噪音內不准挑） |
| **reference** | reference-free：教練直接看學員揮拍寫，不做數值比對 | **溶掉你卡住的噪音問題**（§1） |
| **增強** | GPT-4o 改寫 ×3–5，相似度 ≥0.9 | 同 CoachMe；但別過量（CoachMe 教訓：太多→「過於 general」） |
| **多教練** | 先單一可信教練（單一風格），或多教練 + 仲裁準則 | 避開 CoachMe 沒處理的矛盾指令 |
| **規模** | **150–300 支揮拍**起步 | CoachMe ~177/163 支即可；不足再補 |

### 2.3 關鍵工作流：別讓教練從白紙寫
**用你現有 pipeline 先生一版草稿建議 → 教練只做修改/核可**（FineBadminton 的「MLLM 提案 + 人工精修」用在你自己的 teacher pipeline 上）。
- 標註成本大降、格式一致（降不一致）。
- **教練改了多少 = 你 pipeline 品質的量化訊號**（順便回答「我現在的回饋好不好」）。
- 你的結構化特徵（DTW/MDC/path_norm）**從「被相減的對象」變成模型的輸入條件 / grounding**，資產不丟。

---

## 3. CoachMe 複刻事實表（從全文擷取，照抄前回原文核對）

| 項目 | 滑冰 FS | 拳擊 BX |
|---|---|---|
| 訓練/測試影片 | 177 / 40 | 163 / 41 |
| 動作種類 | 4 種跳（Axel/double Axel/Lutz/Loop） | 2 種（Jab/Cross） |
| 原始 instruction | 1 句/片（單一教練） | 3 句/片（3 教練各 1） |
| 增強 | GPT-4o ×5（相似度 0.93） | ×3（相似度 0.95） |
| error segment | 449/64 段（手標時間戳） | 無 |
| 標註者 | 單一滑冰教練，$50/hr | 大學拳擊隊 3 人 |

**架構**：HybrIK（22 關節 SMPL local 3D）→ Human Pose Perception 三支 GCN（PU 位置+方向 / PE 局部部位 / PA 學習式關節關係）→ **CARL（ResNet-50）concept embedding 上做 DTW 對齊** → Transformer encoder 抓 error → **投影層 temporal max-pool + FC 512→512→512→768** → **T5-base（223M）**。
**CoT = 起始 token**：預訓練用 `Motion Description`、微調用 `Motion Instruction`。**預訓練語料 = HumanML3D（23,384 train）**。

**評估**：G-Eval（GPT-4 評審）+ BLEU-1/4 + ROUGE + BertScore + **六項運動效用指標**（偵測錯誤 / 抓時機 / 認部位 / 因果關係 / 解釋怎麼改 / 描述部位協調）+ 人評。
**結果**：G-Eval 比 GPT-4o 高 **31.6%（FS）/ 58.3%（BX）**；error segment 辨識 **76.14%**；人評「Good」FS 26.6% / BX 56.0%。

**Limitation（它自承，全是你的改善點）**：只適合初/中階；**只學單一教練風格**；HumanML3D 無滑冰/拳擊 → 動作分類錯；HybrIK 推論 21–35 秒是瓶頸（你用 MediaPipe 快很多）。

---

## 4. Base on CoachMe：六個改善點（每條對應你已有資產）

| # | CoachMe 弱點 | 改善 | 你的資產 | 新穎性 |
|---|---|---|---|---|
| 1 | 時序被 **max-pool 成單一最糟瞬間** | 逐相位 / 每相位 top-k 關鍵幀表示 | 4 相位切分 + 每相位 DTW/timing | ★★ |
| 2 | **「有輸入就一定有錯」** 永遠挑錯 | error head 加**量測雜訊閘**；超門檻才糾錯，否則肯定 | **MDC 門檻已實作** | ★★★ 端到端範式沒人做 |
| 3 | error segment **手標很貴**（449 段） | 用相位 + MDC **自動產生 error interval** | MDC 顯著關節 | ★★ 省標註 + 可引用 |
| 4 | **缺領域知識** | 注入動力鏈先驗（近→遠鞭打）grounding | **path_norm 磁量鏈 ICC 0.87–0.97** | ★★ |
| 5 | **只學單一教練風格** | 多教練 + 風格條件（先定仲裁準則） | — | ★★ |
| 6 | **胸口 local 座標算不出位移** | 保留全域位移 / 2D 表示 | 正規化位移已實作 | ★ |

> #2 + #3 是你能對 CoachMe 形成「方法差異」的核心：**用量測誤差感知的 error head，同時(a)不過度糾錯、(b)自動產生 error segment**。這在它的範式裡是空白。

### 4.1 必須先決定的兩個架構岔路
1. **骨架維度**：CoachMe 用 HybrIK **3D SMPL**；你定 **2D MediaPipe**（單 iPhone）。要嘛把 GCN encoder 改吃 2D、要嘛用 MotionBERT 2D→3D lift 再餵 CoachMe 式 GCN。**建議先 2D-GCN**（守住單機位故事）。
2. **對齊在哪一層**：CoachMe DTW 在 **CARL 的 RGB 學習式 embedding** 上；你 DTW 在**骨架角度**上。你的更可解釋、零訓練；可當 CoachMe alignment 的輕量替代，寫成消融。

---

## 5. 模型 / 論文地圖（姿態理解模型 / 運動 VLM）

按你 pipeline 三個可換零件 + 兩篇「把任務定義好」的論文分：

**A. 骨架/姿態理解**
- **MotionBERT**（ICCV 2023, [arXiv 2210.06551](https://arxiv.org/abs/2210.06551)）：2D→3D lift + 動作表示預訓練，比 MediaPipe 原生 z 穩；可當遮擋/抖動修正或凍結特徵抽取器。
- **SportsCap**（[arXiv 2104.11452](https://arxiv.org/abs/2104.11452)）：單目 3D + 運動細粒度理解（老牌參考）。

**B. 學習式動作表示 / 動作→文字（取代手刻純量 + T5）**
- **MotionGPT**（NeurIPS 2023, [OpenMotionLab](https://github.com/OpenMotionLab/MotionGPT)）：動作 VQ-VAE 成 token，T5 統一 captioning/generation → 比 CoachMe T5-base 更成熟的 backbone。
- **MotionGPT-2**（[arXiv 2410.21747](https://arxiv.org/abs/2410.21747)）：Part-Aware VQ-VAE，body+hand 細粒度（對手腕/手指發力更貼）。
- **MG-MotionLLM**（[arXiv 2504.02478](https://arxiv.org/abs/2504.02478)）：跨多粒度理解+生成（對應你相位/整段多層級）。
- ⚠️ 都在 HumanML3D 等**日常動作**預訓練，**無羽球高速揮拍** → domain gap，要 fine-tune（同 CoachMe 的 HumanML3D 限制）。

**C. 把「學員 vs 教練」當定義好的任務（最該讀）**
- **VidDiff**（ICLR 2025, [arXiv 2503.07860](https://arxiv.org/abs/2503.07860), [code](https://github.com/jmhb0/viddiff)）：你 DTW 比對的「任務化」版。見 §6。
- **FineBadminton**（ACM MM 2025, [arXiv 2508.07554](https://arxiv.org/abs/2508.07554), [project](https://finebadminton.github.io/FineBadminton/)）：羽球專屬多層級資料集（3,215 rally / 33,325 stroke）+ FBBench。偏**戰術/決策層**（非揮拍生物力學教學），但是羽球 MLLM 定位標竿（必引）+ 它的「MLLM 提案+人工精修」標註法可借。

**D. 解資料瓶頸的範本**
- **Domain Adaptation of VLM for Soccer**（[arXiv 2505.13860](https://arxiv.org/abs/2505.13860)）：合成 instruction 資料 + 多階段 fine-tune 把通用 VLM 遷到單一運動。
- **Fine-Grained Human Motion Video Captioning**（COLING 2025, [aclanthology 2025.coling-main.351](https://aclanthology.org/2025.coling-main.351.pdf)）：用 3D pose 提升細粒度描述。

---

## 6. VidDiff 重點（你最強的 motivation 彈藥）

- **任務**：給兩支同動作影片 → 輸出細粒度差異句（closed-set：判每句偏 A/B；open-set：自己生差異句）+ 時間戳。**這就是「教練 vs 學員比對」的學習版。**
- **VidDiffBench**：549 對 / 18 種活動 / 4,469 差異標註 / 2,075 時間戳。涵蓋健身、籃球足球、手術、音樂、跳水——**沒有羽球/拍類** ← 你的空白。
- **SOTA 近乎瞎猜**（closed-set 二分類準確率）：GPT-4o 53.5%、Gemini-1.5-Pro 57.7%、Claude-3.5 53.4%、Qwen2-VL-7B 50.4%。open-set recall：GPT-4o 41.7%、Gemini 28.3%。→ **通用 VLM 做不了細粒度動作比較** = 你「為什麼需要結構化特徵 + 專項」的鐵證。
- **三段 agent**：① GPT-4o 提差異候選 → ② CLIP embedding + Viterbi 對齊定位關鍵幀（定位讓 easy 從 57.4%→62.7%）→ ③ GPT-4o 在定位幀上驗證偏 A/B。**這套你可直接借，把你的結構化特徵當裡面的「專項 foundation model」。**

---

## 7. 可走的實驗方向（排序）

1. **【主推・零訓練】VidDiff 式三段 agent + CoachMe 弱點補丁**：提案（你的 top 偏差關節）→ 定位（相位 + DTW）→ 驗證（**MDC 閘 + per-claim 忠實度**）。賣點：「在 GPT-4o/Gemini 近乎瞎猜的細粒度比較上，用生物力學特徵 + 量測誤差閘，做可驗證、不過度糾錯的羽球教練回饋。」保留全資產、打中 VidDiff 痛點。
2. **【中期・要標註】pipeline 當老師生草稿 → 教練精修 150–300 支 → base on CoachMe 微調**：改 #1 相位時序 + #2/#3 MDC 閘 + 羽球 domain；backbone 可換 MotionGPT-2 / MG-MotionLLM 對照 T5。
3. **【消融彈藥】** 照 CoachMe 六消融 + 「有/無 MDC 閘」「有/無 path_norm」「結構化特徵 vs 純 Qwen2-VL（靠 VidDiffBench 結論）」。

---

## 8. 讀論文清單

**今晚已讀（本檔已併入重點）**
- ✅ CoachMe（[2509.11698](https://arxiv.org/abs/2509.11698)）→ §3 複刻事實 + §4 改善點。
- ✅ VidDiff（[2503.07860](https://arxiv.org/abs/2503.07860)）→ §6。

**已讀後判決（2025-06）**
| 論文 | 判決 | 關鍵事實 |
|---|---|---|
| **FineBadminton**（[2508.07554](https://arxiv.org/abs/2508.07554)） | ❌ **不可當訓練資料** | 3,215 rally/33,325 stroke 來自 **BWF YouTube 轉播、後上方俯視、25fps**；三層全是選球分類+戰術+決策，**零生物力學/揮拍教學**。只用於 related work 定位 + 借標註法（人工+MLLM 提案+GUI 精修）。FBBench MLLM 差（Gemini2.5Pro 38.6%/Qwen2.5VL-FT 42%） |
| **MG-MotionLLM**（[2504.02478](https://arxiv.org/abs/2504.02478)） | ✅ **首選 backbone** | 多粒度：細粒度含**片段定位（找動作起訖）+ 時段 captioning + 部位級描述** → 正對你相位 + error interval。T5 60M/220M/770M；訓 HumanML3D+FineMotion(42萬片段)；M2T BERTScore 36.7 vs MotionGPT 32.4 |
| **MotionGPT-2**（[2410.21747](https://arxiv.org/abs/2410.21747)） | ✅ 手腕/手指最細 | LLaMA-3.1-8B+LoRA，**Part-Aware VQ-VAE 拆 body/hand**，SMPL-X 3D；BLEU-4 比 MotionGPT +17%。較重、需 3D 全身 |
| **Soccer VLM 域遷移**（[2505.13860](https://arxiv.org/abs/2505.13860)） | ✅ 資料 bootstrap 範本 | Claude 3.5 從事件標籤+8幀生合成指令、5 類 QA、三階段微調（概念對齊→QA tuning→下游）；~20k 合成+3.3k 真實 → VQA 60→82.8、動作分類 11.8%→63.5%；LLaVA-NeXT-Video+LoRA |
| **MotionBERT**（[2210.06551](https://arxiv.org/abs/2210.06551)） | ◐ 去噪前端（選用） | DSTformer，2D→3D lifting 預訓練，對 noisy/遮擋 2D 強健 → 可當 MediaPipe 去抖/補遮擋前端或凍結特徵；守 2D 公制故不主張深度 |

**綜合**：base on CoachMe 的 backbone 首選 **MG-MotionLLM**（原生片段定位+時段描述，接上相位結構 + 解 CoachMe max-pool 缺陷 #1），手腕細節參考 MotionGPT-2 Part-Aware；資料照 **Soccer VLM 三階段配方**用 pipeline 生合成指令。**FineBadminton 不可當資料源（轉播戰術，非揮拍生物力學）**。

**夜跑現況（2026-06-29 查）**：systemd timer **正常**（每晚 04:00 觸發），但 discovery 找到的 73 篇全已處理（published 24/skipped 46/error 3）→ 0 新 → 3 秒空轉。**非故障，是 `config.yaml` topic 題庫見底** → 要拓寬 topic 到本檔新方向（motion-language / AQA / 評估方法學 / action differencing）discovery 才有新料。
