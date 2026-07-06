# 實驗記錄

> 研究過程的決策與觀察紀錄(非論文正文,之後可擇要併入 chapters/)。

## 2026-06-27 — 取消 physical / timing 兩段式推理,改單一 prompt 內部分步 CoT

**觀察:** physical(身體角度)與 timing(節奏+位移)兩個維度,各自單獨餵給模型推理時,
都具有一定的鑑別能力 —— 兩維都能反映出有意義的動作差異。

**問題:** 若把它們拆成「兩次獨立推理,最後再把兩段輸出合併」,推理時間會很長(等於每個 pair、
每顆模型都要跑兩次 LLM,再做一次拼接)。

**決策:** 改成把兩個維度寫進**同一個 prompt**,但在 prompt 內**分步驟**(先看節奏/發力 timing、
再看身體動作 physical、最後整合),讓模型在**單次推理**內做 CoT(chain-of-thought)。
如此一來:
- 效果更好(模型在同一次推理裡能跨維度整合,而非事後機械式拼接);
- 同時省下推理時間(從兩次 LLM 呼叫 + 合併,降為一次)。

**落地:** 移除 `--modes physical,timing` 與 `result_physical.md` / `result_timing.md` 切片視圖;
完整的 `result.md`(同時含角度與節奏+位移)由單一 `coach` prompt(`coach_zh.md`)內部分步 CoT 消化。
保留 `baseline`(Talking Tennis)作為對照。

## 2026-06-27 — 拍攝視角的座標完整度（MediaPipe visibility）與面向方向決定

**腳本：** `scripts/landmark_confidence.py`（掃 `runs/VFI_enhance/*/` 與 `runs/label_detail/*/` 的
`result_landmarks.csv`，看每關節 `visibility`；重點是持拍臂 肩/肘/腕/食指）。

**結果（ben 各視角，arm_ok% = 持拍臂四關節「全部 visibility≥0.5」的幀比例）：**

| 視角 | r_elbow | r_wrist | r_index | arm_ok% |
|---|---|---|---|---|
| 標準側面（hori） | 0.99 | 0.97 | 0.94 | ~100% |
| 45° | 0.91 | 0.93 | 0.89 | ~98% |
| 正面（front） | 0.65 | 0.70 | 0.67 | ~63% |
| 背面（back） | 0.83 | 0.64 | 0.60 | ~61–70% |

**觀察：** 肩部各視角都 ≈1.0，有鑑別力的是遠端（肘/腕/指）；側面與 45° 持拍臂幾乎全程可見，正面/背面
因身體自遮擋，腕/指約 35–40% 的幀掉到 0.5 以下。33 點總平均（overall）被軀幹/臉/腿稀釋、無鑑別力 →
要看 arm_ok%。注意「2D 拍速量得準」≠「座標完整」：正面拍速數字可能看起來正常，但持拍臂遮擋最重。

**方法學決定：** 沿用「**右撇子面向畫面右邊揮拍**（側面/略側，持拍側朝相機）」的拍攝慣例 —— 它給最完整的
持拍臂座標。

**未來計畫（提醒我）：** 即使面向右，姿態仍可能跑掉（偶發遮擋/抖動）。**下次嘗試同時錄「面向左」與
「面向右」各一段揮拍，合併兩視角的結果**以補一邊遺失的關節。下次問到這題時，記得提醒我這個計畫。

## 2026-06-28 — 顯著度門檻改採固定 per-joint MDC（取代 n=2 per-pair std 帶）

**問題：** 「帶」＝顯著度的分母（判斷差異算不算雜訊的門檻）＝ `2·hypot(std學員, std教練)`。但 profile pair
只用 `n_swings=2` 估 std → 帶是極不穩的估計，常塌到趨近 0 → 連「同人、同角度、不同球」的 self 比較都被
判一堆顯著（假陽性）。**這是「帶（門檻分母）爛」，不是 MediaPipe 角度不能比、也不是比較範圍刻意太嚴。**

**決定（文獻標準做法）：** 改用一張**固定的 per-joint 量測門檻**，從**高水準參考集（教練 + 少數高手，
每人 ≥5–10 球、同朝向）**算 per-joint **ICC₂,₁ + SEM + MDC₉₅**（對 max/min/ROM 各算），套用到所有學員。
- ICC 的變異拆解把「**受測者內（誤差）變異**」與「**受測者間變異**」分開 → 用**穩定/高水準**受測者估，
  within-subject 才接近純量測誤差；**不可把受測學員自己、或動作很亂的人 pool 進門檻**（會撐大 SEM、漏掉真缺點）。
- 學員自己的球間變異 → 另當「動作穩定度」回饋輸出，不塞進「跟教練差多少」的門檻。

**文獻來源：**
- **ICC₂,₁ 模型（受測者內/間變異拆解）：** Shrout, P. E., & Fleiss, J. L. (1979). Intraclass correlations:
  uses in assessing rater reliability. *Psychological Bulletin, 86*(2), 420–428.
- **SEM / MDC₉₅ 公式：** Weir, J. P. (2005). *J Strength Cond Res, 19*(1), 231–240
  （`MDC₉₅ = 1.96·√2·SEM`、`SEM = SD·√(1−ICC)`；本檔 §7.1 已引）。
- **markerless 姿態關節角信度實證（門檻量級參考）：** 上肢肘 ICC≈0.91、肩≈0.94
  （[MDPI Appl. Sci. 16:1202](https://www.mdpi.com/2076-3417/16/3/1202)）；髖膝 ICC>0.75、MDC<3°
  （[PMC11783685](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11783685/)）；OpenCap 跳落 ICC₂,k 0.70–0.97、
  MDC 1.89–11.62°（[Nat. Sci. Rep. s41598-024-79707-2](https://www.nature.com/articles/s41598-024-79707-2)）。

> 對照本檔 §7.1：自變異 2σ 帶在數值上≈MDC₉₅，但現行是 per-pair（n=2）即時估，**改成固定參考集門檻才穩、可引用**。

## 2026-06-29 — 固定教練 MDC 門檻已實作並接上（取代 per-pair std）

`src/MotionDTW/mdc_threshold.py`：掃 9 位教練（每人 10 球，3 位左手已鏡像成右手 canonical）→ 每位
`player_profile` → 對每 (phase, feature) 做 one-way ANOVA 算 **ICC₂,₁ / SEM / MDC95**，寫
`runs/motion_dtw/_self_variation/coach_mdc.json`。`compare_profiles(mdc=...)` 改用此固定門檻，
**不再退回 per-pair std**（無 mdc 時顯著度從缺，不假裝）。`python -m LLMCoach` profile 模式自動載入。

**驗證（直接打中本研究的痛點）：**
| 比較 | per-pair std（舊） | 教練 MDC（新） |
|---|---|---|
| 自比對（同教練 前5 vs 後5） | 角度顯著 1–3 | **0** |
| 跨人（兩位不同教練） | — | 角度 13 / 位移 4 |

→ 自己比自己的假顯著消失，跨人真差異仍抓得到。揮拍 wrist 角 MDC≈8.6°（合 markerless ~9° 地板）、
位移 wrist/index ICC≈0.80。重建門檻：`PYTHONPATH=src/LLMCoach:src python src/MotionDTW/mdc_threshold.py`。

## 2026-06-28 — 單支 iPhone 是否改用 3D：結論先留 2D

只用 iPhone 一般 RGB 錄影（不用 RGBD/LiDAR）。MediaPipe 雖有輸出 `_z` / world landmarks（公制 3D），但那是
**單目深度的學習估計，深度方向最不準**——對「往返鏡頭」的動作（揮拍手臂的深度位移）尤其不可靠，拿來算精細關節角
差會**幫倒忙**。單目 2D→3D lifting（VideoPose3D / MotionBERT）比 MediaPipe 原生 z 好，但**單視角仍有深度歧義**
（會「腦補」合理但非真值的深度）。**決定：維持 2D + 固定側向視角（讓揮拍平面落在影像平面）**；多面向（面向左/右）
只拿來**補遮擋關節**，不拿來取得公制深度。要真 3D 角度需同步多機位或深度感測，超出單 iPhone 範圍。

---

> 以下方法學與拍攝指南原為 `docs/CAPTURE_AND_METRICS.md`，已併入本檔。

# 揮拍動力鏈量測：方法學與拍攝指南

本文統整「揮拍動力鏈」分析從 **onset 時序 → 座標位移量** 的轉折、各項實驗結論，以及
未來拍攝的方法學決定。對應程式碼：[`src/LLMCoach/chain_displacement.py`](../src/LLMCoach/chain_displacement.py)、
[`src/LLMCoach/compare_swing.py`](../src/LLMCoach/compare_swing.py)。

---

## 1. 為什麼放棄 onset/lag 時序

原本想用「動力鏈時序」描述鞭打：量髖→肩→肘→腕**各環節開始動（onset）的時刻**與相鄰環節
的銜接間隔（lag），看「手肘先出、手腕接力」的傳導順序。

**失敗原因（窗口問題，非解析度問題）：** onset 的定義需要一個「靜止基準」當起點，但**每個
phase 切片的第一幀，關節速度就已經不是 0**（關節在進入該 phase 時早就在動）。因此 onset 偵測
塌成第 0 幀——尤其揮拍（揮拍本身就是全動作最快的段，切片頭就在飛）。

提高 fps **救不了**：30→60→120fps 只讓 lag 的量化解析度變細（33ms→16.7ms→8.3ms），
但「揮拍切片無靜止基準」是窗口問題，120fps 的揮拍 onset 仍常見 0.0ms。
（背景見 `runs/motion_dtw/compare_result_lag_detect/README.md`。）

→ onset 程式碼保留休眠（`compare_swing.render_timing`，`with_timing` 預設 False，僅供實驗），
正式報告改用下面的磁量法。

**補充（2026-06 實測）：峰值速度「順序」也救不回時序。** 除 onset 外，另測了動力鏈的
**峰值速度時刻順序**（各關節在揮拍內速度峰出現的先後，此法不需靜止基準）。近端→遠端的順序在
ben 僅 **1/6** 球成立、學員（VFI_41090A025）**0/11** 球成立（肩部峰值甚至最晚出現），平均序完全
打亂。原因同 onset：揮拍窗太短、單目 2D 對近遠端 ~10–30ms 的落差解析不出、且**肩部運動主要在
朝相機的深度方向**（2D 速度峰時刻不可信）。**結論：時序/順序這一族（onset 與 peak-timing）在
單目 2D ＋ 相位切片下都不成立，只有「磁量」（走多遠，§2）版本可信。** 換相機朝向無法根治——任一
單一視角總有關節落在深度方向；要做真正的 kinematic sequence 需 2D→3D lifting（VideoPose3D /
MotionBERT，清單 #9）或多機位。近→遠序列的生物力學依據見 Marshall & Elliott (2000)、TPI
Kinematic Sequence、AAU 羽球 Clear（[參考文獻](#參考文獻)）。

---

## 2. 磁量法：path_norm + speed（誰在做事）

對每個 phase、每個持拍側關節，量它在這區間**走的 2D 路徑長**（每幀位移量加總），
除以 **body_scale（軀幹長：肩中點→髖中點，取錄影起始靜止段的中位數）** 正規化：

- **path_norm** ＝ 路徑長 ÷ 軀幹長（body-length 倍數，**尺度/距離無關**）。
- **speed** ＝ path_norm ÷ phase 秒數（body-length/秒）。

因為是「整段的磁量」，**不需要靜止起點，所以不會像 onset 一樣塌掉**。
報告同時列 path 與 speed：學員揮拍較慢時會「path 一樣大但 speed 較低」，這正是要抓的
**節奏／爆發力差異**；只看 path 會把「走得遠」和「走得快」混在一起。

關節（近→遠）：肩 → 肘 → 腕 → 拍(食指)，加下肢 髖 → 膝 → 踝。

> **生物力學依據**：羽球高遠球的鞭打是近端→遠端的動能傳遞，遠端關節因此走得更遠、更快
> （Marshall & Elliott 2000；Rusdiana 2021；AAU 羽球 Clear；PMC9598458）。本法量的是這條鏈的
> **空間後果（路徑長）**，而非角速度峰；與「峰值速度時序」是不同量（後者單目 2D 不可信，見 §1）。
> 實測近→遠 path_norm 單調遞增（肩<肘<腕<拍）在 ben 與學員皆成立——詳見 §3 與下方驗證。

---

## 3. 各區間可信度（ben 6 球自比對，path_norm mean±std）

| 區間 | 代表值（腕） | 穩定度 | 處置 |
|---|---|---|---|
| **揮拍** | 5.41 ± 0.16 | **高**（近→遠單調遞增 肩<肘<腕<拍） | 主力區間，值得渲染／餵 LLM |
| **架拍** | 2.64 ± 0.19（120fps） | 中（60fps std 偏大） | 小幅準備動作，別過度解讀 |
| **引拍** | 3.66 ± **3.81** | **低**（std≈mean，逐球差到 ~6×） | **標「不可信」**，僅供參考 |

引拍不可信的原因：它是很短的過渡段，邊界（架拍結束／揮拍開始）切點每球不同，**切點主導了
路徑長**而非穩定的物理量——磁量版的 onset 問題。下肢（髖/膝/踝）三區間都小且平（~0.4–0.9），
不是主導關節。

> **為何要量「可信度／自變異」**：markerless（MediaPipe）姿態本就有抖動與系統誤差——髖膝
> ICC 0.74–0.93、上肢極端角 RMSE 16–19°、單目關節角誤差地板 ~9°（PMC11783685；MDPI 16:1202；
> Medrano-Paredes 2025）。所以「學員 vs 教練的差」必須先扣掉這層量測噪音才算數，這就是 §7 的
> MDC₉₅ 門檻（Weir 2005）。

---

## 4. fps 對量測的影響（抽幀實驗）

把 ben **真 120fps** 抽幀模擬 60/30fps（同一批球，純 fps 效果），揮拍：

| 關節 | speed@120 | speed@60 | speed@30 | 損失@60 | 損失@30 |
|---|---|---|---|---|---|
| 肘 | 5.81 | 5.34 | 4.86 | −8% | **−16%** |
| 腕 | 10.23 | 9.93 | 9.65 | −3% | −6% |
| 拍 | 11.70 | 11.37 | 11.03 | −3% | −6% |

**關鍵：偏差是系統性且單調的**（弧小的肘受害較重；大動作的腕/拍幾乎不受影響；60fps 損失約為 30fps 的一半，
關節排序在各 fps 都不變 → 是可預測的縮放，不是雜訊）。所以——
- **學員與教練同 fps 時，偏差會在「學員/教練比值」中抵消**。
- 真正危險的是**跨 fps 比較**（學員@30 vs 教練@120，偏差污染落差）。
- **VFI 內插的假影格不得用於任何 speed/timing 宣稱**（內插可能抹平或捏造瞬間動態）。

---

## 5. 方法學決定

1. **拍攝 fps**：預設 **60fps 真實拍攝**；鐵則是**學員與教練同 fps**（比值抵消 fps 偏差），
   比絕對數字重要。30fps 僅在教練參考也是 30fps 時可用。最終 60-vs-30 待真 30fps 資料比對後拍板。
2. **拍攝距離**：**不訂死公尺數**——指標皆尺度正規化（path ÷ 軀幹長、角度與距離無關）。
   規則：**全身入框 + 四周留 ~10–15% margin + 機位/變焦/朝向跨片段固定**。
   機位朝向比距離更關鍵（path_norm 是影像平面 2D，深度方向的動作拍不到）。
3. **切 swing 必須保留原始錄影 frame_no**（否則對不到 landmarks，見下節）。

---

## 6. 資料對齊（split ↔ landmarks）

`chain_displacement` 需要 split 的 frame 能索引進 `result_landmarks.csv`。目前有兩種 split 慣例：

- **cutSwing 切的 split**（41090A025 / CCE / 41093A074）：frame_no **每球從 0 重編**（設計如此，
  對齊切出來的影片，見 `cutSwing.py:172`）。用 `splits/swing_cut_summary.csv` 的 `actual_start`
  補回：`錄影 frame = actual_start + split_frame_no`。
- **CSV-only 切的 split**（ben_120fps / ben_60fps / VFI_41090A025）：無 summary，frame_no 已是
  原始錄影索引 → offset 0。

`compare_swing.frame_offset_for_split()` 自動判別（有 summary 用 actual_start，否則 0）；
對不到時 `compute_displacement()` 回傳 None、報告標「缺座標資料」，**不輸出假的 ~0 值**。

**Landmark 檔對照**（`runs/VFI_enhance/`，皆 gitignored）：
`original_41090A025` ＝ 真實 866 幀；`mediapipe_41090A025` ＝ VFI-4x（3461≈4×866）；
`mediapipe_ben_120fps`/`mediapipe_ben_60fps` ＝ ben 真實。

**目前可跑位移的對象**：ben_120fps、ben_60fps、VFI_41090A025；41090A025 真實需 actual_start offset。
**首個真實比較對**：VFI_41090A025（學員）vs ben_120fps（教練），均 ~120fps。
實測揮拍 學員/教練(路徑) 比值 ≈ 0.46（肩）/0.64（肘）/0.60（腕）/0.59（拍）——學員約只走教練的六成，
真有「沒把拍甩出去」的落差。

---

## 7. 顯著度：個人去噪代表 profile + 自變異帶（取代 pooled）

要判斷「學員 vs 教練的差算不算大」，需要一條「正常變異」的尺。舊版用 **pooled**（所有人混）的
角度帶，且**位移完全沒有帶** → self-vs-self 也被當成有差、模型亂給建議。

新版（`src/MotionDTW/self_variation.py` 的 `player_profile`）：每人取**前 3 球**，建一條**去噪代表
動作**，並用**本人球間 std 當帶**（不再 pooled）。為什麼前 3 球：使用者不想上傳太多球。

**去噪 + 平均（每 phase）：**
1. 每球該 phase 線性**重取樣**到共同長度（把三球的幀對齊，才能跨球同幀比）。
2. **5 幀 Hampel** 去跳點（中位數 + MAD，K=3）：中間幀離前後太多 = MediaPipe 跳點 → 標掉。
3. **跨球同幀補幀**：壞幀用其他球同一幀補；三球全壞 → 用各球自己前後幀內插。
4. **平均**成代表訊號。
5. 純量：形狀類（角度 mean/ROM、位移 path）從代表算；速率類（角速度/加速度/speed）**逐球用原始
   時間算再平均**（正規化會破壞 dt）；節奏佔比/時長用原始幀數。
6. **帶 = 球間 std**，用各球**自己（補幀前）的資料**算——若用補幀後的會讓球互相靠近、帶縮小、
   過度標顯著（這點在 code review 修掉）。

**比較**（`compare_swing.compare_profiles`）：學員 profile vs 教練 profile，每關節/phase
**顯著度 = |學員−教練| ÷ 合併帶**，合併帶 = `2·hypot(std_學員, std_教練)`（≈2σ；顯著≥1×
代表明顯超出自然球間變異）。**角度、節奏、位移三類都標**。

**限制**：n=3 的 std 很粗，當粗略門檻看；「個體差異 vs 真缺點」要更多受測者才判得出，現階段只能
說「超不超過雜訊」。驗證：ben 前3 vs 後3（null）→ 多落正常範圍；VFI vs ben（real）→ 揮拍顯著。

> std「能很好回答是不是雜訊，回答不了是不是個體差異」——後者需多人資料；timing/位移以前沒 std，
> 是沒算不是不能算，現已補上。

---

## 7.1 自變異帶 ≡ MDC₉₅（Weir 2005）：同一個門檻，可引用的版本

把上面的 `合併帶 = 2·hypot(std_學員, std_教練)` 對照復健/運動科學的標準
**最小可偵測變化量 MDC₉₅**（Weir 2005）：

- **MDC₉₅ = 1.96·√2·SEM**，其中 **SEM**（量測標準誤）以受測者重複測量的組內變異估計，
  直接法即「球間 within-subject SD」（與 Weir 的 SEM = SD·√(1−ICC) 為同一恆等式）。
- 本帶 = **2·hypot(σ) ≈ 2·√2·SD**（兩人 σ 相近時）。
- 兩者常數 **2·√2 = 2.83 vs 1.96·√2 = 2.77**，差約 **2%**。

**實證（用 ben/VFI 學員/CCE/41093A074 的重複球算 ICC、SEM、MDC₉₅，再比兩種門檻）：**

| 測試 | 特徵數 | 現行 2σ 帶 觸發 | MDC₉₅ 觸發 |
|---|---|---|---|
| **自比對**（ben 前3 vs 後3，應≈0） | 66 | 3（4.5%） | 1（1.5%） |
| **真實**（VFI 學員 vs ben 教練，應揮拍亮） | 66 | 33 | 33 |

→ **結論：本專案的自變異帶在數值上就是 MDC₉₅，決策完全一致**（真實對 33=33、自比對皆 ~1–3/66
≈ 95% 門檻的名目誤報率）。所以「加 MDC」不是換演算法、不會再降誤差——它的價值是
**(a) 可引用**（門檻寫成 MDC₉₅，Weir 2005，口委無從攻擊）、**(b) 強制報 per-feature ICC**。

**ICC 可信度（同批資料，球=重複測量）→ 用來決定哪些特徵可信：**

| 群組 | ICC | 處置 |
|---|---|---|
| 位移鏈（肩/肘/腕/拍） | 0.87–0.97 | **可信，主力** |
| 右側（持拍）角度：髖/膝/踝 0.98、肘 0.83、肩 ~0.85 | 0.83–0.99 | 可信 |
| 左側（非持拍、被遮擋）：left_wrist 0.25、left_shoulder/hip ~0.66 | 0.25–0.67 | **雜訊主導 → 降權/排除** |

> ⚠️ **誠實 caveat（寫論文必附）**：教科書 MDC 的「噪音」指**同一動作的量測誤差**；本專案的球間 SD
> ＝量測誤差 ＋ **真實動作變異**。對「球間誤報」這個問題，球間 SD 正是對的雜訊模型，但須如實描述，
> 不可宣稱已隔離出純量測誤差（那需同一次動作的 test–retest，動作無法重來）。對應程式：
> `compare_swing._combined_band` / `_band_significance`。

---

## 8. 報告與執行

- 報告每 phase：角度表（含顯著度）、節奏（佔比 + 顯著度）、位移（path/speed/比值 + 顯著度）；
  **架拍只看姿勢、不評節奏**；引拍位移標不可信。
- LLM prompts（`src/LLMCoach/prompts/`）：`talking_tennis_zh.md`（**Talking Tennis baseline**，
  非最小控制組——忠實重現 arXiv 2510.03921 的餵 LLM 法：學員整段純量 vs 專家參考範圍、評分 X/10 +
  三點修正，刻意不分相位/不比教練/無 MDC；見 `src/LLMCoach/talking_tennis_baseline.py`）、
  `coach_zh.md`（**單次 CoT 整合**，吃完整報告，內部 timing→身體→整合；原 `coach_zh_physical.md`／`coach_zh_timing.md` 兩段式已移除，見開頭 2026-06-27 記錄）。`coach*` 共同鐵則：直接對學員講、
  不提教練、不露內部數字（佔比%/比值/顯著度×）；**注意 baseline 反而會露數字+評分（TT 原法如此，刻意保留）**。
- 批次（**需手動執行**）：`scripts/rerun_displacement.sh`（profile 比較 + LLM，目前
  `--modes baseline,coach`）、`scripts/compare_prompt_versions.sh`（單對、三版對照）。

---

## 9. Talking Tennis baseline（同範式對手 baseline）

`baseline` 模式已從「最小 prompt 控制組」**換成 Talking Tennis 的忠實重現**
（[arXiv 2510.03921](https://arxiv.org/abs/2510.03921) §3.2/§3.3）。目的是做乾淨消融：用**同一組輸入特徵**，
比較「TT 的絕對參考範圍 + 扁平 prompt」vs.「本專案的每相位 + 教練 DTW + MDC」，讓差異歸因於**方法**而非特徵。
程式 [`src/LLMCoach/talking_tennis_baseline.py`](../src/LLMCoach/talking_tennis_baseline.py)、
prompt `prompts/talking_tennis_zh.md`、範圍 `talking_tennis_ranges.json`。

### 9.1 TT 到底餵 LLM 什麼（只餵這些純量，不餵逐關節角度表）
§3.2 算了一堆特徵，但 §3.3 進 LLM 的只有**一球一個 dict**：`predicted_stroke` + 拍速、爆發力、
旋轉幅度(度)、揮拍時長、尖峰角速度、觸球時機(%)。grounding = 常數 `REFERENCE_RANGES`（每球種一組
`{特徵:(lo,hi)}`），`compare_to_reference` 算相對 % 偏差
（`v<lo → (lo−v)/(hi−lo+ε)·100`，`ε=1e-9`），組成文字後 prompt 強制
**`總評分:X/10` + 2–3 句診斷 + 剛好三點修正**、不得捏造數字。

### 9.2 羽球版的對映（whole-swing，不分相位）

| TT 特徵 | 本專案來源（重用既有程式） | 進範圍比較? | 範圍來源 |
|---|---|---|---|
| `predicted_stroke` | 固定「長球(clear)」（無分類器） | 否（raw-list） | — |
| `racket_velocity`（座標） | `chain_displacement` 的 `right_index` 整段 speed（軀幹長/秒） | 是 | **ben** |
| `rotate_range_deg`（角度） | `descriptive_stats` 的 `right_shoulder_horizontal_2d` ROM（替代 TT 軀幹 θ） | 是 | CCE+ben |
| `peak_angular_velocity`（角度） | 持拍臂角度欄 `ang_vel_peak` 最大值（度/秒） | 是 | CCE+ben |
| `stroke_duration_s` | 整段幀數÷fps（秒） | 是 | CCE+ben |
| `peak_power`（座標） | ½·`racket_velocity`²（TT 的 KE，單位質量） | 否（raw-list） | — |
| `impact_timing_pct`（角度） | 持拍臂角度峰值的相對位置×100 | 否（raw-list） | — |

只有 TT 驗證過的 4 個（拍速/旋轉幅度/角速度/時長）進 `REFERENCE_RANGES` 比較；其餘 raw-list（同 TT）。
範圍重建一次：`python -m LLMCoach.talking_tennis_baseline build-ranges`（座標類用 ben，因 CCE 無 landmarks；
角度/時長類用 CCE+ben 合併，p10–p90 帶）。

### 9.3 忠實度 caveat（寫論文必附）
- **無揮拍分類**（固定長球）、**無球拍偵測** → 拍速/觸球時機用**手掌**（`right_index`）代理；
  TT 的「軀幹旋轉 arctan2(肩線)」以本專案**肩部關節角度**替代（因要「用我的身體角度特徵」）。
- **單位不同於 TT**：拍速＝軀幹長/秒（非 m/s）、角速度＝度/秒（非 rad/s）、時長＝秒（非 frames，
  改用秒才能跨 CCE@30/ben@120 不同 fps 合併）。
- **角速度範圍混 fps**：CCE@30 會低估峰值 → 範圍偏寬（「角度類用 CCE+ben」的必然結果，不可宣稱已校正）。
- **刻意保留 TT 的限制**：單球、絕對參考、不比教練、不分相位、無 DTW/MDC；輸出**會露數字+評分**（TT 原法如此）。
- `num_predict` 用 2048（非 TT 的 120；256 也不夠——qwen3 等推理模型的 thinking 會吃光 budget 導致 content 空，見開頭 2026-06-27 記錄），可在 `experiment.MODE_DECODING` / `talking_tennis_baseline.DEFAULT_NUM_PREDICT` 調。

### 9.4 執行
- 資料流：`__main__`（CSV 模式）或批次腳本產生 `result_baseline.md` → `baseline` 模式吃它。
  **報告模式若缺 `result_baseline.md`，baseline 記為 error，不會誤吃教練報告**（已加保護）。
- `python -m LLMCoach --exp --coach-csv … --student-csv … --student-landmarks … --fps 120 --models big`
  → 一次產出 baseline + coach，並在 `llm/_index.md` 列 **baseline vs coach 生成時間對照**。
- `scripts/run_tt_baseline.sh [models]`（**需手動執行**）：對 mediapipe_41090A025 與 hori_ben_60fps_1
  的 swing 01/02 跑 baseline（後者從 `*_Dlabel.csv` 即時切球、保留原始 frame_no 對齊 landmarks）。

---

## 參考文獻

> 引用對應 [`docs/MotionXperts-開發筆記整理.md`](MotionXperts-開發筆記整理.md) 第三部分的來源表，編號 `#N` 指
> `研究方向與論文清單.md`。寫論文時請回原文核對頁碼/數字。

**信度與最小可偵測變化量（MDC / SEM / ICC）**
- **Weir, J. P. (2005).** Quantifying test–retest reliability using the intraclass correlation
  coefficient and the SEM. *J Strength Cond Res, 19*(1), 231–240. — **MDC₉₅ / SEM 公式必引（§7.1）。**
- markerless（MediaPipe）信度實證：[PMC11783685](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11783685/)
  （髖膝 ICC 0.74–0.93）；[MDPI Appl. Sci. 16:1202](https://www.mdpi.com/2076-3417/16/3/1202)
  （上肢極端角 RMSE 16–19°）。
- 單目關節角誤差地板：Medrano-Paredes et al. (2025)（RMSE ~9°，清單 #10）。

**動力鏈（生物力學，近→遠序列）**
- **Marshall, R. N., & Elliott, B. C. (2000).** Long-axis rotation: the missing link in
  proximal-to-distal segmental sequencing. *J Sports Sci, 18*(4), 247–254.
- TPI [Kinematic Sequence Revisited](https://www.mytpi.com/articles/biomechanics/kinematic-sequence-revisited)；
  Rusdiana (2021, *Sport Mont*)；[AAU 羽球 Clear](https://projekter.aau.dk/projekter/files/42678547/articledone.pdf)；
  [PMC9598458](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9598458/)。

**降抖動 / 平滑（第一層噪音）**
- One-Euro filter；Savitzky–Golay（`compare_swing.smooth_sequence`，window=7, polyorder=2）。

**時序所需 2D→3D lifting（若未來要做 kinematic sequence）**
- VideoPose3D / MotionBERT（清單 #9）。
