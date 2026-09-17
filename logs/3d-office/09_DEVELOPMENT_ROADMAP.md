# 09 — Development Roadmap

Phase 編號依本輪需求（與 `logs/platform/13` 的編號不同；對應關係在 §8）。
每個 Phase 的「Gate」= 全部 Acceptance 通過 + 該 Phase 的 P0 風險有處置。

---

## Phase 0 — Architecture ✅（本資料夾）

- **Goal**：凍結 Event Envelope、Agent Activity State、Realtime 協定、DB delta、Repo 邊界。
- **Gate**：`12_RISKS.md` 的 Open Questions Q1–Q4 有答案。

---

## Phase 1 — Company + Agent Core

- **Goal**：公司與 agent 是可持久化的資料；activity 表存在且可由程式碼寫入並產生事件（尚無 runner）。
- **Features**：Settings/DB bootstrap；companies/goals/policies/agents/agent_activity/projects/budgets/transactions models；FSM 工具；`ActivityService.set(agent_id, state, detail)`（寫表 + emit 事件，同 TX）；Event schema（pydantic）與 `events` 表（含 seq）；schema 產生管線 → `packages/event-schema`。
- **Files**：`autora/db/*`, `autora/runtime/fsm.py`, `autora/runtime/events/{schema,outbox}.py`, `autora/runtime/activity/service.py`, `packages/event-schema/*`, `infra/docker-compose.yml`, `.importlinter`.
- **Database**：Phase 1 表（07 §3）+ `events(seq)`。
- **API**：`GET/POST /api/companies`, `GET /api/companies/{id}/agents`, `GET /api/events?after=&limit=`。
- **Events**：COMPANY_CREATED, AGENT_CREATED, AGENT_PAUSED/RESUMED, 全部 AGENT_* activity 事件（由 ActivityService 產生）。
- **Tests**：FSM 非法轉換；ActivityService 與 events 同 TX（失敗則兩者皆無）；`last_event_seq` 一致；schema 產生物與 pydantic 一致；import-linter 通過。
- **Acceptance**：用腳本把一個 agent 的 activity 走過 8 個狀態，`events` 出現 8 筆 seq 遞增的 AGENT_* 事件，`agent_activity` 與最後一筆一致；TS 端 zod 可解析全部 8 筆。

---

## Phase 2 — Runtime + Task + Event

- **Goal**：真實 Runtime 可以執行 workflow DAG；每步產生真實事件與 activity 更新；含 tool 事件、審批、預算閘門、崩潰恢復。
- **Features**：TaskManager（DB queue + lease）、DAG 實例化與 `unlocks` 計算、AgentRunner（Observe→Think→Act→Evaluate→Repair）、ToolRegistry（含 TOOL_* 事件與 `produced[]`）、ModelGateway + AnthropicProvider + **FakeModelProvider**、CostGuard、PolicyEngine、Approvals、Scheduler、Trace（agent_steps + blob）、EchoWorkflow（3 節點 DAG，含 handoff）。
- **Files**：`autora/runtime/{task_manager,dag,agent_runner,scheduler}.py`, `runtime/tools/*`, `runtime/models/*`, `runtime/policy/*`, `runtime/approvals/*`, `runtime/cost/*`, `runtime/trace/*`, `apps/worker/main.py`.
- **Database**：Phase 2 表。`tasks.required_role/progress/display_name`, `agent_runs.handoff`.
- **API**：`POST /api/companies/{id}/workflows`(啟動 template)、`GET /api/tasks/{id}`、`GET /api/runs/{id}`、`GET /api/runs/{id}/trace`、`GET /api/approvals`、`POST /api/approvals/{id}/decide`.
- **Events**：全部 TASK_*、WORKFLOW_*、TOOL_*、AGENT_RUN_*、APPROVAL_*、POLICY_DENIED、BUDGET_EXHAUSTED、AGENT_STEP_PROGRESS(ephemeral).
- **Tests**：併發 claim 無重複；lease 回收；echo DAG 端到端（fake provider）；`TASK_SUCCEEDED.unlocks` 正確；審批阻塞/恢復；預算耗盡 ABORTED + activity=FAILED；trace 查詢與 steps 對齊；`kill -9` worker 後恢復；真實 Anthropic smoke（`-m integration`）。
- **Acceptance**：啟動 EchoWorkflow（A→B→C，三個不同 role）後，`trace(run)` 顯示 THINKING→WORKING(TOOL_CALLED/COMPLETED)→REVIEWING→COMPLETED，`agent_activity` 依序變化，B 的 activity 在 A 完成前是 WAITING{upstream}。

---

## Phase 3 — WebSocket + Dashboard

- **Goal**：瀏覽器可即時看到 Runtime；重連正確；Dashboard 顯示真實數字。
- **Features**：`realtime/projection.py` + snapshot 端點；WS Gateway（LISTEN、backlog、SNAPSHOT_REQUIRED、heartbeat、ephemeral）；前端 `RealtimeClient`、reducer、hydrate、gap-fill；`stores/realtime`、`stores/ui`；Dashboard（Cash/Revenue/Expenses/Active Agents/Active Tasks/Published/Today's Goal）；Agent list（2D 卡）；Agent Detail Panel（即時 + REST）；Trace Viewer；Event Timeline；Approval Inbox；Reporting 最小版（KPI 由 transactions/tasks 計算）。
- **Files**：`autora/realtime/*`, `apps/api/ws.py`, `apps/web/src/{realtime,stores,features/dashboard,features/agent-panel,features/trace-viewer,features/approvals}`.
- **Database**：kpi_snapshots、cycles（最小）；events 索引。
- **API**：`GET /api/companies/{id}/realtime/snapshot`、`WS /ws/companies/{id}`、`GET /api/companies/{id}/kpis`、`GET /api/events?after&until`.
- **Events**：無新增；KPI_SNAPSHOT_CREATED.
- **Tests**：gateway backlog/snapshot/overflow/poll-fallback；投影契約測試；reducer ordering/dup/gap；client reconnect（mock WS）；Playwright：斷線 30s 內恢復且事件不重複。
- **Acceptance**：兩個瀏覽器分頁開 Dashboard，啟動 EchoWorkflow，兩者的 agent 卡片同步變化；關閉 API 20 秒再啟動，分頁自動恢復，`last_seq` 與 server head 一致，Trace 無缺漏。

---

## Phase 4 — 3D Office

- **Goal**：同一個 store 以 3D 呈現；點擊、handoff 動畫、狀態燈、fallback。
- **Features**：OfficeCanvas（WebGL 偵測、dynamic import）、Room/Zone/layout、AgentAvatar（GLB、6 clips、transient subscribe）、Screen/StatusIndicator、`visual/mapping.ts`、`visual/director.ts` + CueRunner、Courier（walk）、CameraRig + focusOn、picking → uiStore、OfficeBoard2D fallback、效能設定（DPR、hidden 節流、ring buffer）。
- **Files**：`apps/web/src/office3d/**`, `public/models/*.glb`.
- **Database**：無。
- **API**：無新增。
- **Events**：無新增。
- **Tests**：mapping/director/layout 單元測試；test-renderer smoke；Playwright：`/office` 在 simulation 下 6 個 badge 依序 Working→Done，handoff 動畫出現（以 DOM badge 與 store 斷言，不以像素）；無 WebGL 時渲染 2D 板；lint 邊界（office3d 不 import api/realtime client）。
- **Acceptance**：連續開啟 2 小時（simulation 每 5 分鐘一輪），記憶體不持續成長（heap 差 < 50MB）、FPS ≥ 30；點任一 avatar 右側面板 300ms 內顯示即時狀態。

---

## Phase 5 — Newsroom

- **Goal**：6 個 agent 的 `story_to_article_v2` 真實跑通（真模型與 fake 兩種模式），雙語文章發布到公開站，從 3D 可進入每個產物。
- **Features**：sources/poller、fetch_url→Evidence、chunks+embedding、stories+dedup、claims/claim_evidence、Researcher/Analyst/Writer/Editor/Marketing agents（prompts、schemas、validators）、確定性 fact-check、Publisher、Distribution、公開站 + beacon、analytics_daily、`activity_links`、Newsroom 頁面、simulation fixtures。
- **Files**：`autora/domains/newsroom/**`, `apps/web/src/app/(admin)/newsroom/**`, `apps/web/src/app/(site)/**`, `features/newsroom/**`.
- **Database**：Phase 5 表；`article_versions.lang/draft_group_id`.
- **API**：`/api/sources`, `/api/stories`, `/api/articles`, `/api/articles/{id}/versions`, `POST /api/analytics/beacon`(公開), site 讀取 API.
- **Events**：全部 Newsroom 事件（03 §2.5）。
- **Tests**：quote 定位；claim 無 evidence → fact-check 不過；雙語 claim 集合一致 validator；publish 冪等；beacon 去重；agents 的 schema 驗證（fake provider）；真模型 smoke；E2E：從 3D 點 Writer → 連結到 draft 頁 → 內容與 store 中 task 一致。
- **Acceptance**：`11_MVP_ACCEPTANCE.md` 的 AC-1 ~ AC-10。

---

## Phase 6 — Autonomous Company Loop

- **Goal**：不需要人啟動 workflow；Cycle 每日自動 PLANNING→…→REVIEWING；CEO 在 3D 中可見其行為；治理規則自動 pause。
- **Features**：Cycle FSM + stage runner + deadlines + fallback plan；Reporting/CompanySnapshot；CEO agent（CyclePlan/CycleReview）；Command pipeline（instantiate_workflow、pause/kill、allocate_budget）；kill criteria；Ledger 結算 model_calls → expense；agent_memory；Dashboard 的 Cycle 頁與 Today's Goal 來自真實 cycle；每日摘要。
- **Files**：`autora/company/**`（cycle, reporting, snapshot, governance, commands, agents/ceo）.
- **Database**：agent_memory、documents；cycles 全欄位。
- **API**：`/api/cycles`, `/api/cycles/{id}`, `/api/companies/{id}/snapshot`, `/api/projects`, `/api/budgets`.
- **Events**：CYCLE_*、GOAL_*、PROJECT_*、BUDGET_ALLOCATED、EXPENSE_RECORDED、KPI_SNAPSHOT_CREATED.
- **Tests**：cycle 三階段自動推進（加速時鐘）；CEO plan schema 驗證與截斷；預算三層閘門；kill criteria 自動 pause；fallback plan；expense 對帳 = Σ model_calls。
- **Acceptance**：AC-11 ~ AC-14；連續 7 個 cycle（可加速）無人工修復；3D 中 CEO 在 PLANNING/REVIEWING 時 THINKING，其餘 IDLE，且 Today's Goal 來自 CyclePlan。

---

## 7. Gate 總表

| 進入 | 必須完成 |
|---|---|
| Phase 2 | Phase 1 Acceptance + event-schema 產生管線在 CI |
| Phase 3 | Phase 2 Acceptance + 真實 Anthropic smoke 通過一次 |
| Phase 4 | Phase 3 Acceptance + 投影契約測試在 CI |
| Phase 5 | Phase 4 Acceptance + 版權確認的 3D 資產 |
| Phase 6 | Phase 5 AC-1~10 + Open Question Q5（每日預算）有答案 |
| 對外 Demo | Phase 4 完成即可（EchoWorkflow 或 Phase 5 的 simulation） |

---

## 8. 與平台 roadmap 的對應

| 本輪 | platform/13 |
|---|---|
| Phase 1 Company + Agent Core | P1 的 schema 部分 + P2 的 company schema |
| Phase 2 Runtime + Task + Event | P1 Core Runtime |
| Phase 3 WS + Dashboard | 新增 |
| Phase 4 3D Office | 新增 |
| Phase 5 Newsroom | P3 Newsroom |
| Phase 6 Autonomous Loop | P2 Company Engine + P4 Autonomous Loop |
| （Revenue、Multi-company） | P5、P6 — 本輪未排程 |
