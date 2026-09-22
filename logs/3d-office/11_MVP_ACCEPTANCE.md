# 11 — MVP Acceptance Criteria

MVP = Phase 1–5（3D Office + Newsroom，workflow 由人啟動或 simulation 觸發）。
Phase 6（自主 Cycle）的 AC-11~14 列於末尾，作為「完整 MVP」的定義。

每條 AC 都有自動化測試對應（`backend/tests/**` 與 `frontend/web/e2e/*.spec.ts`；階段 5 的對照表見 `logs/devlog/05_PHASE_5.md` 的驗收一節），且**在 `MODEL_PROVIDER=fake` 與真模型下都必須通過**（真模型只跑 AC-2、AC-5 smoke）。

時間上限（AC-1 的 3 秒、AC-2 的 5 秒、AC-3 / AC-10 的 1 秒）在本機量測；CI 用軟體算圖，一幀可能要數百毫秒，所以 CI 上放寬成只防當掉（AC-1、AC-5 的測試都照這個寫法）。

---

## 使用者故事層

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-1** 看 3D Office | 開啟 `/office` | 3 秒內顯示公司的每個代理，各自在對應區域；連線指示為 LIVE；無 WebGL 時顯示 2D 板且資訊相同。**CEO 的座位已經在場景裡，但 CEO 代理屬於階段 6**（AC-11），階段 5 的公司是 5 個角色（研究員、分析師、寫手、編輯、行銷） |
| **AC-2** 啟動 Research Task | 在 UI 按「Start research」（或 API `POST /workflows`） | 5 秒內 Researcher badge 變 Thinking→Working；Analyst 顯示 Waiting for research；事件 timeline 出現 WORKFLOW_RUN_CREATED、每個節點一筆 TASK_CREATED（`story_to_article_v2` 有 7 個節點）、TASK_READY、AGENT_RUN_STARTED |
| **AC-3** Researcher 完成 → Analyst 自動開始 | 等待 research task 結束 | 出現 TASK_SUCCEEDED{unlocks:[analysis]}；Researcher 走到 Analyst 桌的動畫播放；Analyst 在 AGENT_RUN_STARTED 後 ≤1 秒變 Working；Researcher 回座 IDLE（COMPLETED 顯示期後） |
| **AC-4** Writer → Editor → Published | 等待整條線 | Writer 完成 → Editor Working → (若 policy=human) Editor 走到 Approval Desk、桌燈 amber、Inbox 出現 approval → 核准後 ARTICLE_PUBLISHED；公開站 `/news/zh-TW/articles/{slug}` 與 `/news/en/…` 皆可讀（D-015）；Marketing 隨後 Working → Distribution 記錄出現 |
| **AC-5** 點 Agent 看 Current Task | 點 Researcher（工作中） | 右側面板 300ms 內顯示：Status=Working、Current Task 名稱、tool（如 fetch_url）、Sources 計數 = 已發出的 `TOOL_COMPLETED.produced(evidence)` 數、Next Step = "analysis (Analyst)"、連結到 Story 頁 |
| **AC-6** 看 Trace | 面板按 View Trace | 每一行對應 `events WHERE run_id` 的一筆（測試逐行比對 DB）；無前端合成行；包含 TOOL_CALLED/COMPLETED 與 AGENT_* |
| **AC-7** 看 Article | 點 Writer → Draft 連結 | 顯示雙語 version；每段落標示 claim；每個 claim 可展開 evidence quote，quote 在 evidence.extracted_text 中可定位 |
| **AC-8** 看 Event Timeline | 開 timeline | 事件依 seq 排序；暫停/篩選有效；長度上限 500 |
| **AC-9** 失敗可見 | simulation 的 `demo.editor_fails`：編輯每次都回報自己沒做的決定，驗證器每次都擋下 | Editor 桌燈紅、badge Failed、面板顯示 error_class；task FAILED（用完重試）；下游 CANCELLED。人可從 Inbox 重新啟動失敗的 workflow（階段 6 補上：`GET /workflows/failed` + `POST /workflows/{id}/restart`，走命令管線） |
| **AC-10** 等待審批可見 | policy=human | Editor 為 WAITING{approval}；Approval Desk 燈 amber；Dashboard「Pending approvals」= 1；核准後 ≤1 秒恢復 |

## 系統層

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-S1** 重連 | 執行中關閉 API 20 秒 | 前端顯示 reconnecting；恢復後 `store.last_seq == server head_seq`；事件無重複、無缺漏（比對 DB） |
| **AC-S2** 大 gap | 前端離線 > BACKLOG_MAX 事件 | 收到 SNAPSHOT_REQUIRED，rehydrate 後投影 == server snapshot |
| **AC-S3** 投影契約 | 隨機事件序列（目前 8 組種子 × 120 步，每組多個快照點） | Python reducer、TS reducer、snapshot 三者一致 |
| **AC-S4** 崩潰恢復 | Writer 執行中 `kill -9` worker | 重啟後 lease 回收、新 attempt 完成、只有一組 draft、3D 中 Writer 從 Failed/Working 正確恢復 |
| **AC-S5** 邊界 | `lint-imports`、ESLint | `office3d/**` 無 fetch/WS import；`runtime` 不 import `domains`；刪除 `domains/newsroom` 後 runtime/company/realtime 測試全綠 |
| **AC-S6** 無假資料 | 靜態掃描 + review | 前端無 `setTimeout` 驅動的狀態變更；無硬編碼 agent 列表；simulation 的 trace 中每筆 TOOL_CALLED 都對應真實 tool 執行 |
| **AC-S7** 效能 | 2 小時 soak（simulation 每 5 分鐘一輪，每分鐘進出一個部門） | FPS ≥ 30（桌機）；heap 增長 < 50MB；WS 事件處理延遲 p95 < 100ms。**2026-09-22 在 T-600 之後的場景（部門分區、事業色帶、房間隔離）重量：通過**——25 輪、走路 50 趟、進出房間 60 次（六個房間都去過）；回收後 heap 15.6 → 18.3 MB（**+2.7**）；FPS 最低 58、第 10 百分位與中位數 60；WS p95 **0.7 毫秒**（1051 則）；DOM 節點與監聽數四等分平均持平（節點 610→595、監聽 3207→3144）；無頁面錯誤。完整取樣：`logs/perf/2026-09-22-office-soak-120min.json` |
| **AC-S8** 成本歸屬 | 真模型跑一輪 | 每篇文章可查總成本 = Σ model_calls(run ∈ workflow)；Dashboard Expenses 含此值 |

## Phase 6（完整 MVP）

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-11** 冷啟動自主 | 建公司 + sources + project + 預算 | 下一個排程點 Cycle 自動建立；CEO THINKING；CyclePlan 實例化 ≤ max_workflows 個 workflow；無人介入 |
| **AC-12** Today's Goal 真實 | Dashboard | 「Publish N bilingual articles」來自 `cycles.plan`，「3 / 5」來自真實 published 計數 |
| **AC-13** 預算閘門 | project 預算設極低 | 下一次 model call 被拒 → run ABORTED → agent FAILED{BudgetExceeded} → BUDGET_EXHAUSTED 在 timeline；加預算後恢復 |
| **AC-14** 7 cycle 無人修復 | 加速時鐘 | 7 個 cycle DONE；每個 cycle 有 plan 與 review；auto-pause 規則至少被評估一次並記錄 |
