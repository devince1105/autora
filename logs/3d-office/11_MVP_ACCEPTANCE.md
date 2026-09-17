# 11 — MVP Acceptance Criteria

MVP = Phase 1–5（3D Office + Newsroom，workflow 由人啟動或 simulation 觸發）。
Phase 6（自主 Cycle）的 AC-11~14 列於末尾，作為「完整 MVP」的定義。

每條 AC 都有自動化測試對應（`tests/acceptance/` 或 `apps/web/e2e/`），且**在 `MODEL_PROVIDER=fake` 與真模型下都必須通過**（真模型只跑 AC-2、AC-5 smoke）。

---

## 使用者故事層

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-1** 看 3D Office | 開啟 `/office` | 3 秒內顯示 6 個 avatar（CEO、Researcher、Analyst、Writer、Editor、Marketing），各自在對應區域；連線指示為 LIVE；無 WebGL 時顯示 2D 板且資訊相同 |
| **AC-2** 啟動 Research Task | 在 UI 按「Start research」（或 API `POST /workflows`） | 5 秒內 Researcher badge 變 Thinking→Working；Analyst 顯示 Waiting for research；事件 timeline 出現 WORKFLOW_RUN_CREATED、TASK_CREATED×6、TASK_READY、AGENT_RUN_STARTED |
| **AC-3** Researcher 完成 → Analyst 自動開始 | 等待 research task 結束 | 出現 TASK_SUCCEEDED{unlocks:[analysis]}；Researcher 走到 Analyst 桌的動畫播放；Analyst 在 AGENT_RUN_STARTED 後 ≤1 秒變 Working；Researcher 回座 IDLE（COMPLETED 顯示期後） |
| **AC-4** Writer → Editor → Published | 等待整條線 | Writer 完成 → Editor Working → (若 policy=human) Editor 走到 Approval Desk、桌燈 amber、Inbox 出現 approval → 核准後 ARTICLE_PUBLISHED；公開站 `/zh-TW/articles/{slug}` 與 `/en/…` 皆可讀；Marketing 隨後 Working → Distribution 記錄出現 |
| **AC-5** 點 Agent 看 Current Task | 點 Researcher（工作中） | 右側面板 300ms 內顯示：Status=Working、Current Task 名稱、tool（如 fetch_url）、Sources 計數 = 已發出的 `TOOL_COMPLETED.produced(evidence)` 數、Next Step = "analysis (Analyst)"、連結到 Story 頁 |
| **AC-6** 看 Trace | 面板按 View Trace | 每一行對應 `events WHERE run_id` 的一筆（測試逐行比對 DB）；無前端合成行；包含 TOOL_CALLED/COMPLETED 與 AGENT_* |
| **AC-7** 看 Article | 點 Writer → Draft 連結 | 顯示雙語 version；每段落標示 claim；每個 claim 可展開 evidence quote，quote 在 evidence.extracted_text 中可定位 |
| **AC-8** 看 Event Timeline | 開 timeline | 事件依 seq 排序；暫停/篩選有效；長度上限 500 |
| **AC-9** 失敗可見 | 在 simulation 中設定 Editor 第 1 次 evaluation 失敗 3 次（超過 repair 上限） | Editor 桌燈紅、badge Failed、面板顯示 error_class；task FAILED；下游 CANCELLED；人可從 Inbox 重新啟動 workflow |
| **AC-10** 等待審批可見 | policy=human | Editor 為 WAITING{approval}；Approval Desk 燈 amber；Dashboard「Pending approvals」= 1；核准後 ≤1 秒恢復 |

## 系統層

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-S1** 重連 | 執行中關閉 API 20 秒 | 前端顯示 reconnecting；恢復後 `store.last_seq == server head_seq`；事件無重複、無缺漏（比對 DB） |
| **AC-S2** 大 gap | 前端離線 > BACKLOG_MAX 事件 | 收到 SNAPSHOT_REQUIRED，rehydrate 後投影 == server snapshot |
| **AC-S3** 投影契約 | 隨機 5000 事件序列 | Python reducer、TS reducer、snapshot 三者一致 |
| **AC-S4** 崩潰恢復 | Writer 執行中 `kill -9` worker | 重啟後 lease 回收、新 attempt 完成、只有一組 draft、3D 中 Writer 從 Failed/Working 正確恢復 |
| **AC-S5** 邊界 | `lint-imports`、ESLint | `office3d/**` 無 fetch/WS import；`runtime` 不 import `domains`；刪除 `domains/newsroom` 後 runtime/company/realtime 測試全綠 |
| **AC-S6** 無假資料 | 靜態掃描 + review | 前端無 `setTimeout` 驅動的狀態變更；無硬編碼 agent 列表；simulation 的 trace 中每筆 TOOL_CALLED 都對應真實 tool 執行 |
| **AC-S7** 效能 | 2 小時 soak（simulation 每 5 分鐘一輪） | FPS ≥ 30（桌機）；heap 增長 < 50MB；WS 事件處理延遲 p95 < 100ms |
| **AC-S8** 成本歸屬 | 真模型跑一輪 | 每篇文章可查總成本 = Σ model_calls(run ∈ workflow)；Dashboard Expenses 含此值 |

## Phase 6（完整 MVP）

| # | 場景 | 通過條件 |
|---|---|---|
| **AC-11** 冷啟動自主 | 建公司 + sources + project + 預算 | 下一個排程點 Cycle 自動建立；CEO THINKING；CyclePlan 實例化 ≤ max_workflows 個 workflow；無人介入 |
| **AC-12** Today's Goal 真實 | Dashboard | 「Publish N bilingual articles」來自 `cycles.plan`，「3 / 5」來自真實 published 計數 |
| **AC-13** 預算閘門 | project 預算設極低 | 下一次 model call 被拒 → run ABORTED → agent FAILED{BudgetExceeded} → BUDGET_EXHAUSTED 在 timeline；加預算後恢復 |
| **AC-14** 7 cycle 無人修復 | 加速時鐘 | 7 個 cycle DONE；每個 cycle 有 plan 與 review；auto-pause 規則至少被評估一次並記錄 |
