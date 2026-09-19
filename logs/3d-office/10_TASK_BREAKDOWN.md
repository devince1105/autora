# 10 — Task Breakdown (for Coding Agents)

每個 Task：單一責任、明確 input/output、files、dependency、acceptance、validation command。
不得修改其他 domain 的檔案；跨層變更拆成多個 task。

格式：**ID · 名稱** — Scope / In / Out / Files / Deps / AC / Validate

---

## Phase 1 — Company + Agent Core

**T-101 · Bootstrap monorepo & infra** — 建立 pnpm workspace、`packages/autora` pyproject、docker-compose(db+api+worker+web)、Makefile、CI 骨架 / In: 無 / Out: 可 `make dev` 起 db；`pytest`、`pnpm test` 空跑通過 / Files: 根目錄、`infra/*`、`apps/*` 入口 / Deps: — / AC: 新 clone 後 `make dev` 10 分鐘內可跑 / Validate: `make dev && make test`

**T-102 · Settings & DB session** — pydantic-settings、SQLAlchemy 2 async engine、Alembic init / In: T-101 / Out: `autora/infra/settings.py`, `autora/db/session.py`, `alembic/` / AC: 缺必要 env 啟動即失敗且訊息明確 / Validate: `alembic upgrade head`

**T-103 · Company & Agent models** — companies, company_goals, company_policies, agents, projects, budgets, transactions / In: T-102 / Out: models + migration + repositories / AC: 欄位如 platform/10 + 07 §2(agents) / Validate: `pytest tests/db/test_company_models.py`

**T-104 · Generic FSM** — `StateMachine(states, transitions, guards)` + `state_transitions` 表 / In: T-102 / Out: `autora/runtime/fsm.py` / AC: 非法轉換拋錯；每次轉換一筆紀錄 / Validate: `pytest tests/runtime/test_fsm.py`

**T-105 · Event schema (pydantic)** — Envelope + 每個 event_type 的 payload class + registry / In: T-102 / Out: `autora/runtime/events/schema.py` / AC: 03 §2 全部 type 有 class；未知 type 註冊即失敗 / Validate: `pytest tests/runtime/events/test_schema.py`

**T-106 · Events table & outbox emit** — `events(seq bigserial, …)` + `emit(session, event)` 同 TX + NOTIFY / In: T-105 / Out: `autora/runtime/events/outbox.py`, migration / AC: rollback 則無事件；seq 遞增；NOTIFY 在 commit 後 / Validate: `pytest tests/runtime/events/test_outbox.py`

**T-107 · agent_activity table & ActivityService** — 表 + `set(agent_id, state, detail, run_id?, task_id?)` 寫表並 emit 對應 AGENT_* 事件 / In: T-103, T-106 / Out: `autora/runtime/activity/service.py` / AC: 表與事件同 TX；`last_event_seq` 一致；detail schema 依 state 驗證 / Validate: `pytest tests/runtime/activity/`

**T-108 · Schema generation pipeline** — pydantic → JSON Schema → `packages/event-schema`(TS types + zod) / In: T-105 / Out: `make gen-schema`, `packages/event-schema/src/index.ts` / AC: CI 檢查產生物一致；TS 可 parse Phase 1 事件 fixture / Validate: `make gen-schema && git diff --exit-code packages/event-schema && pnpm -F event-schema test`

**T-109 · Import boundaries** — `.importlinter` + ESLint restricted imports / In: T-101 / Out: 設定檔 + CI job / AC: 故意違規的測試分支 CI 失敗 / Validate: `lint-imports && pnpm lint`

**T-110 · Minimal REST: companies/agents/events** — / In: T-103, T-106 / Out: `apps/api/routers/{companies,agents,events}.py` / AC: `GET /api/events?after=&limit=` 依 seq / Validate: `pytest tests/api/test_phase1.py`

---

## Phase 2 — Runtime + Task + Event

**T-201 · Task/WorkflowRun/AgentRun/AgentStep models** — 含 `required_role, progress, display_name, handoff` / In: T-103 / Out: models + migration / Validate: `pytest tests/db/test_task_models.py`

**T-202 · TaskManager (queue + lease)** — `claim_next`, `complete`, `fail`, `retry`, `reap_leases`；每轉換 emit TASK_* 與 activity 更新（WAITING{upstream} 在 task PENDING 且 role 空閒時） / In: T-201, T-104, T-107 / Out: `runtime/task_manager.py` / AC: 兩 worker 1000 tasks 無重複；lease 回收 / Validate: `pytest tests/runtime/test_task_manager.py`

**T-203 · DAG instantiate & unlocks** — `WorkflowTemplate` 宣告、`instantiate()`、`on_finished()` 計算 `unlocks` 與下游 CANCELLED / In: T-202 / Out: `runtime/dag.py` / AC: 無環檢查；`TASK_SUCCEEDED.unlocks` 正確 / Validate: `pytest tests/runtime/test_dag.py`

**T-204 · ToolRegistry + TOOL_* events** — decorator 註冊、schema 驗證、side_effect、`produced[]`、timeout / In: T-106 / Out: `runtime/tools/registry.py` / AC: 每次呼叫 TOOL_CALLED/COMPLETED|FAILED 成對 / Validate: `pytest tests/runtime/tools/`

**T-205 · PolicyEngine** — role defaults、公司收緊、irreversible→HUMAN、`policy_decisions`；預設 `newsroom.auto_approve_if_fact_check_passed=false`（D-001） / In: T-204 / Out: `runtime/policy/*` / AC: platform/07 矩陣參數化測試；approve_article 預設 HUMAN / Validate: `pytest tests/runtime/policy/`

**T-206 · Approvals** — 表、request/decide、task/run 暫停恢復、activity WAITING{approval} / In: T-202, T-205 / Out: `runtime/approvals/*`, API `POST /api/approvals/{id}/decide` / Validate: `pytest tests/runtime/approvals/`

**T-207 · ModelGateway + Router + FakeModelProvider** — 統一介面、alias 解析、structured output 驗證、`model_calls` 記錄；Fake provider 依 `(role, task_name, attempt)` 回 fixture / In: T-201 / Out: `runtime/models/*` / AC: 程式碼無 model id 字串；fake 可腳本化延遲與分支 / Validate: `grep -rn "claude-" packages/autora/autora | wc -l` = 0；`pytest tests/runtime/models/`

**T-208 · AnthropicProvider** — complete/count_tokens/price/cache 標記 / In: T-207 / Out: `runtime/models/providers/anthropic.py` / AC: integration smoke 記錄 usage / Validate: `pytest -m integration tests/runtime/models/test_anthropic.py`

**T-209 · CostGuard** — reserve/settle、三層預算、BUDGET_EXHAUSTED / In: T-207, T-103 / Out: `runtime/cost/*` / Validate: `pytest tests/runtime/cost/`

**T-210 · Trace + BlobStore** — agent_steps 寫入、LocalFS/S3 adapter、`GET /api/runs/{id}/trace`（events ⋈ steps） / In: T-201, T-106 / Out: `runtime/trace/*`, `infra/blobstore.py`, router / AC: trace 每列對應真實 event 或 step / Validate: `pytest tests/runtime/trace/ tests/api/test_trace.py`

**T-211 · AgentRunner** — Observe→Think→Act→Evaluate→Repair；每階段呼叫 ActivityService；max_steps；NEEDS_APPROVAL 暫停 / In: T-202, T-204, T-205, T-206, T-207, T-209, T-210 / Out: `runtime/agent_runner.py` / AC: fake provider 下 activity 序列 THINKING→WORKING→REVIEWING→COMPLETED；預算超額 → ABORTED + FAILED activity / Validate: `pytest tests/runtime/test_agent_runner.py`

**T-212 · Scheduler** — `schedules` 表、lease 去重、APScheduler / In: T-106 / Out: `runtime/scheduler.py` / Validate: `pytest tests/runtime/test_scheduler.py`

**T-213 · Worker main + EchoWorkflow** — worker 入口（scheduler + dispatcher + task loop）、`echo` domain（3 節點 DAG、3 roles、echo tools） / In: T-211, T-212, T-203 / Out: `apps/worker/main.py`, `autora/domains/echo/*` / AC: Phase 2 Acceptance / Validate: `pytest tests/e2e/test_echo_workflow.py`

**T-214 · Workflow start API** — `POST /api/companies/{id}/workflows`（Command，human actor） / In: T-203, T-205 / Out: router + command / Validate: `pytest tests/api/test_workflows.py`

**T-215 · Crash recovery test** — `kill -9` worker 中途、重啟後完成、無重複產物 / In: T-213 / Out: `tests/e2e/test_recovery.py` / Validate: `pytest tests/e2e/test_recovery.py`

---

## Phase 3 — WebSocket + Dashboard

**T-301 · Realtime projection & snapshot API** — `projection.py`（agents/tasks/kpis/recent_events/last_seq；COMPLETED 過期→IDLE 規則）、`GET /api/companies/{id}/realtime/snapshot` / In: T-107, T-201 / Out: `autora/realtime/projection.py`, router / AC: 07 §4 查詢 < 50ms（本地） / Validate: `pytest tests/realtime/test_projection.py`

**T-302 · Projection contract test** — 隨機事件序列：`apply(snapshot0, events) == snapshot_n`（Python 版 reducer 作為參考實作） / In: T-301 / Out: `autora/realtime/reducer.py`（參考實作）, `tests/realtime/test_contract.py` / Validate: `pytest tests/realtime/test_contract.py`

**T-303 · WS Gateway** — ConnectionManager、LISTEN + 2s poll fallback、HELLO/backlog/BACKLOG_DONE/SNAPSHOT_REQUIRED/HEARTBEAT、bounded queue、ephemeral 轉送、token 驗證 / In: T-301 / Out: `autora/realtime/gateway.py`, `apps/api/ws.py` / AC: 05 §8 四個 gateway 測試 / Validate: `pytest tests/realtime/test_gateway.py`

**T-304 · Ephemeral progress from runner** — AgentRunner 每秒 ≤1 次 NOTIFY 攜帶 AGENT_STEP_PROGRESS（不落表） / In: T-211, T-303 / Out: runner 修改 + gateway 路徑 / Validate: `pytest tests/realtime/test_ephemeral.py`

**T-305 · Frontend realtime store & reducer** — Zustand `realtime.ts`（domain 投影）、reducer（zod 驗證、ordering、dup、pending buffer） / In: T-108 / Out: `apps/web/src/stores/realtime.ts`, `realtime/reducer.ts` / AC: 與 T-302 相同 fixture 產生相同投影（跨語言契約） / Validate: `pnpm -F web test reducer`

**T-306 · RealtimeClient** — WS 連線、hydrate、since、backoff+jitter、heartbeat 逾時、gap-fill via REST、hidden→rehydrate / In: T-305, T-303 / Out: `realtime/client.ts` / AC: mock WS 測試 reconnect/gap/overflow / Validate: `pnpm -F web test client`

**T-307 · UI store** — selectedAgentId、panelTab、cameraMode / In: — / Out: `stores/ui.ts` / Validate: `pnpm -F web test ui-store`

**T-308 · Typed API client + Query hooks** — openapi-typescript 產生、TanStack Query、`eventToQueryKeys` 失效表 / In: T-110, T-210 / Out: `apps/web/src/api/*` / Validate: `pnpm -F web gen-api && pnpm -F web typecheck`

**T-309 · Dashboard page** — Cash/Revenue/Expenses/Active Agents/Active Tasks/Published/Today's Goal（來自 store.kpis + snapshot）、連線狀態 / In: T-305, T-308 / Out: `features/dashboard/*`, `app/(admin)/dashboard` / AC: 數字來自 store，無硬編碼 / Validate: `pnpm -F web test dashboard`

**T-310 · Agent cards (2D) + Agent Detail Panel** — 卡片（state/task/tool/since）、Panel（即時區 + REST 明細：steps 摘要、tools 統計、produced 計數、Next Step、links[]、Output tab） / In: T-305, T-308, T-307 / Out: `features/agent-panel/*` / AC: 「Sources 37」來自 produced 計數；progress 只在 task.progress 存在時顯示 / Validate: `pnpm -F web test agent-panel`

**T-311 · Trace Viewer** — `GET /runs/{id}/trace` 渲染；可由 task/article(correlation) 進入；raw 顯示未知 type / In: T-308 / Out: `features/trace-viewer/*`, `app/(admin)/trace/[runId]` / Validate: `pnpm -F web test trace-viewer`

**T-312 · Event Timeline** — recentEvents ring buffer 的列表，可暫停、篩選 agent/type / In: T-305 / Out: `features/timeline/*` / Validate: `pnpm -F web test timeline`

**T-313 · Approval Inbox** — 列表 + decide（REST）；決議後由事件更新 / In: T-206, T-308 / Out: `features/approvals/*` / Validate: `pnpm -F web test approvals`

**T-314 · Minimal KPI reporting** — 由 transactions/tasks/articles 計算 store.kpis 所需欄位（Phase 6 前的簡版） / In: T-103, T-201 / Out: `company/reporting_min.py` / Validate: `pytest tests/company/test_reporting_min.py`

**T-315 · Phase 3 E2E** — Playwright：兩分頁同步、API 重啟恢復、無重複 / In: T-306, T-309, T-310 / Out: `apps/web/e2e/realtime.spec.ts` / Validate: `pnpm -F web e2e realtime`

---

## Phase 4 — 3D Office

**T-401 · OfficeCanvas + WebGL detection + dynamic import** — DPR、frameloop、hidden 節流、context-lost overlay、fallback 切換 / In: T-307 / Out: `office3d/OfficeCanvas.tsx`, `app/(admin)/office` / Validate: `pnpm -F web test office-canvas`

**T-402 · layout.ts + Room/Zone** — role → seat 表、`seatsForRole`、Room 幾何、Zone(Desk+Workstation 用 `<Instances>`) / In: T-401 / Out: `office3d/scene/*` / AC: layout 測試（座位不重疊、在房內） / Validate: `pnpm -F web test layout`

**T-403 · visual/mapping.ts** — activity+role → VisualState（02 §7 對照表） / In: T-108 / Out: `office3d/visual/mapping.ts` / AC: 表全覆蓋測試 / Validate: `pnpm -F web test mapping`

**T-404 · Avatar asset & clips** — 取得/製作 CC0 或授權低模 GLB，6 clips；家具 3 GLB；記錄授權 / In: — / Out: `public/models/*.glb`, `office3d/assets/LICENSES.md` / AC: 總三角形 < 50k；授權可商用 / Validate: `pnpm -F web check-assets`（gltf-validator + 三角形計數腳本）

**T-405 · AgentAvatar** — GLB clone、mixer、crossFade、transient subscribe、role 顏色與 label / In: T-402, T-403, T-404 / Out: `office3d/agents/AgentAvatar.tsx` / AC: store 變更不觸發 React render（用 render 計數斷言） / Validate: `pnpm -F web test avatar`

**T-406 · Screen + StatusIndicator** — emissive 螢幕、桌燈顏色/blink、頭頂 badge / In: T-403, T-402 / Out: `office3d/agents/{Screen,StatusIndicator}.tsx` / Validate: `pnpm -F web test indicator`

**T-407 · Animation Director + CueRunner** — `cuesFor(event, storeBefore)`、合併/TTL/中止規則、useFrame 執行 / In: T-305, T-403 / Out: `office3d/visual/{director,cues}.ts`, `CueRunner` / AC: 04 §4 規則測試 / Validate: `pnpm -F web test director`

**T-408 · Courier (handoff walk)** — 沿 layout 路徑走到目標桌/Approval Desk、carry prop、return / In: T-407, T-405 / Out: `office3d/agents/Courier.tsx` / AC: 新 run 開始時中止並回座 / Validate: `pnpm -F web test courier`

**T-409 · CameraRig + picking** — OrbitControls 限制、focusOn tween、raycast → uiStore / In: T-405, T-307 / Out: `office3d/camera/*`, `office3d/interaction/*` / Validate: `pnpm -F web test picking`

**T-410 · OfficeBoard2D fallback** — 同 store 的卡片牆 + handoff 箭頭 / In: T-403, T-305 / Out: `office3d/fallback/OfficeBoard2D.tsx` / Validate: `pnpm -F web test board2d`

**T-411 · Office page integration** — `/office` = Canvas + Agent Detail Panel(T-310) + 迷你 Dashboard 條 + 連線指示 / In: T-401–T-410, T-310 / Out: `app/(admin)/office/page.tsx` / Validate: `pnpm -F web e2e office`

**T-413 · 外觀調整（2026-09-19 使用者回饋新增）** — 配色協調、大門 / 玄關、分區邊界與標示、人物頭部比例、可切換的幾套風格（調色盤） / In: T-402, T-405 / Out: `office3d/palette.ts`、`scene/*` / Validate: 截圖檢視 + 既有版面測試

**T-412 · Perf & soak test** — 2 小時 simulation soak（heap、FPS 記錄腳本）、lint 邊界 / In: T-411 / Out: `apps/web/e2e/office-soak.spec.ts`, 報告 / AC: Phase 4 Acceptance / Validate: `pnpm -F web e2e office-soak`

---

## Phase 5 — Newsroom

**T-500 · web_search tool: Tavily adapter + fixture mode** — `SearchProvider` 介面、`TavilySearchProvider`（key 只在 env、timeout、rate limit）、`FixtureSearchProvider`；每次呼叫記 tool_cost / In: T-204, T-209 / Out: `domains/newsroom/tools/search.py`, `infra/search/tavily.py` / AC: 結果不建立 Evidence；`TOOLS_PROFILE=fixture` 不打外部 API；integration smoke 記錄成本 / Validate: `pytest tests/newsroom/test_search.py && pytest -m integration tests/newsroom/test_tavily.py`

**T-501 · Sources & poller** / In: T-212 / Out: models、RSS/URL poller、去重 / Validate: `pytest tests/newsroom/test_sources.py`
**T-502 · fetch_url tool + Evidence + snapshot** / In: T-204, T-210 / Validate: `pytest tests/newsroom/test_evidence.py`
**T-503 · evidence_chunks + embedding binding** / In: T-502, T-207 / Validate: `pytest tests/newsroom/test_chunks.py`
**T-504 · Stories + dedup** / In: T-501, T-503 / Validate: `pytest tests/newsroom/test_stories.py`
**T-505 · Claims / ClaimEvidence + tools (create_claim, link_evidence with quote 定位)** / In: T-504 / Validate: `pytest tests/newsroom/test_claims.py`
**T-506 · Researcher agent (prompt, ResearchNote schema, validators)** / In: T-211, T-500, T-502 / Validate: `pytest tests/newsroom/agents/test_research.py`
**T-507 · Analyst agent (AnalysisNote, claims)** / In: T-505 / Validate: `pytest tests/newsroom/agents/test_analyst.py`
**T-508 · Articles / ArticleVersions (lang, draft_group) + write_draft tool** / In: T-504 / AC: 雙語 claim 集合一致 validator；`primary_lang=zh-TW`、`require_all_langs` 讀自 policy（D-002）/ Validate: `pytest tests/newsroom/test_articles.py`
**T-509 · Writer agent (bilingual draft)** / In: T-507, T-508 / Validate: `pytest tests/newsroom/agents/test_writer.py`
**T-510 · Deterministic fact-check validators + run_fact_check tool** / In: T-505, T-508 / Validate: `pytest tests/newsroom/test_factcheck_rules.py`
**T-511 · Editor agent (EditorReview, request_revision/accept_draft)** / In: T-509, T-510 / Validate: `pytest tests/newsroom/agents/test_editor.py`
**T-512 · Publisher service (PublishArticle command, 冪等, Distribution site)** / In: T-508, T-205 / Validate: `pytest tests/newsroom/test_publisher.py`
**T-513 · Marketing agent (DistributionPlan, create_distribution: DB only)** / In: T-512 / Validate: `pytest tests/newsroom/agents/test_marketing.py`
**T-514 · story_to_article_v2 template + register() + activity_links** / In: T-203, T-506–T-513 / AC: import-linter；links 出現在 activity.detail / Validate: `lint-imports && pytest tests/newsroom/test_workflow.py`
**T-515 · Public site (zh-TW/en) + beacon API** / In: T-512 / Validate: `pnpm -F web test site && pytest tests/api/test_beacon.py`
**T-516 · Analytics collector (hourly → analytics_daily, event)** / In: T-515, T-212 / Validate: `pytest tests/newsroom/test_analytics.py`
**T-517 · Newsroom admin pages (stories, articles, versions, fact-check, distribution, timeline)** / In: T-308, T-311 / Validate: `pnpm -F web test newsroom-pages`
**T-518 · Simulation fixtures (FakeModelProvider scripts + fixture HTML + revise 分支)** / In: T-207, T-514 / AC: `MODEL_PROVIDER=fake` 跑完整 workflow / Validate: `MODEL_PROVIDER=fake pytest tests/e2e/test_newsroom_sim.py`
**T-519 · Real-model smoke** / In: T-208, T-514 / Validate: `pytest -m integration tests/e2e/test_newsroom_real.py`
**T-520 · Phase 5 E2E (3D → Writer → draft page)** / In: T-411, T-517, T-518 / Validate: `pnpm -F web e2e newsroom`

---

## Phase 6 — Autonomous Loop

**T-601 · Cycle FSM + stage runner + deadlines** / In: T-212, T-104 / Validate: `pytest tests/company/test_cycle.py`
**T-602 · Ledger: settle model_calls → expense; balances** / In: T-209, T-103 / Validate: `pytest tests/company/test_ledger.py`
**T-603 · Reporting + CompanySnapshot (token cap)** / In: T-602, T-516 / Validate: `pytest tests/company/test_reporting.py`
**T-604 · Command pipeline (instantiate_workflow, pause/kill, allocate_budget, update_strategy)** / In: T-205 / Validate: `pytest tests/company/test_commands.py`
**T-605 · CEO agent (CyclePlan / CycleReview schemas, validators, prompts)** / In: T-211, T-603, T-604 / Validate: `pytest tests/company/test_ceo.py`
**T-606 · Governance: kill criteria auto-pause, kill proposal → approval** / In: T-603, T-604 / Validate: `pytest tests/company/test_governance.py`
**T-607 · Fallback plan + daily summary document** / In: T-601, T-605 / Validate: `pytest tests/company/test_fallback.py`
**T-608 · Cycle pages + Today's Goal from CyclePlan** / In: T-309, T-601 / Validate: `pnpm -F web test cycles`
**T-609 · agent_memory (bounded recent-run summaries into context)** / In: T-211 / Validate: `pytest tests/runtime/test_agent_memory.py`
**T-610 · 7-cycle soak (accelerated)** / In: all / Validate: `pytest tests/acceptance/test_autonomous.py`

---

## Dependency Graph（摘要）

```
T-101 → T-102 → T-103 ─┬→ T-107 ─────────────────┐
              ├→ T-104 │                          │
              └→ T-105 → T-106 → T-107            │
                     └→ T-108 (schema gen) ───────┼──→ T-305 → T-306 → T-315
T-103,T-104 → T-201 → T-202 → T-203 → T-213 → T-215
T-106 → T-204 → T-205 → T-206 ─┐
T-201 → T-207 → T-208           ├→ T-211 → T-213
T-207 → T-209 ──────────────────┤
T-201,T-106 → T-210 ────────────┘
T-107,T-201 → T-301 → T-302 ; T-301 → T-303 → T-304
T-108 → T-305 ; T-305,T-303 → T-306 ; T-110,T-210 → T-308
T-305,T-308 → T-309, T-310, T-312 ; T-308 → T-311 ; T-206 → T-313
T-307 → T-401 → T-402 → T-405 ; T-108 → T-403 ; T-404 → T-405
T-305,T-403 → T-407 → T-408 ; T-405,T-307 → T-409 ; T-403 → T-410
T-401..T-410, T-310 → T-411 → T-412
T-212 → T-501 → T-504 ; T-204,T-210 → T-502 → T-503 → T-504 → T-505
T-211,T-502 → T-506 ; T-505 → T-507 → T-509 ; T-504 → T-508 → T-509
T-505,T-508 → T-510 → T-511 ; T-508,T-205 → T-512 → T-513
T-203, T-506..T-513 → T-514 → T-518, T-519 ; T-512 → T-515 → T-516
T-308,T-311 → T-517 ; T-411,T-517,T-518 → T-520
T-212,T-104 → T-601 ; T-209 → T-602 → T-603 ; T-205 → T-604
T-211,T-603,T-604 → T-605 ; T-603,T-604 → T-606 ; T-601,T-605 → T-607
T-309,T-601 → T-608 ; T-211 → T-609 ; all → T-610
```

可平行的批次：
- Phase 1：T-103 / T-104 / T-105 同時；T-108 / T-109 / T-110 同時。
- Phase 2：T-204→205→206、T-207→208→209、T-210 三條線平行，匯入 T-211。
- Phase 3：後端 T-301–T-304 與前端 T-305–T-308 平行。
- Phase 4：T-402 / T-403 / T-404 平行。
- Phase 5：T-501–T-505（資料層）與 T-508（文章層）平行；agents 依序。

---

## Phase 1 實作註記（T-101 ~ T-110 完成，2026-09-17）

與上方規格不同或補充之處（程式碼為準）：

| Task | 實際位置 / 差異 |
|---|---|
| T-103/104 | 共用 migration `0002`；`transactions`、`state_transitions` 以 DB trigger 強制 append-only；`projects` 以 CHECK 強制「核准後必有 kill_criteria」 |
| T-105 | 事件目錄依層擁有：`runtime/events/catalog.py`、`company/events.py`；newsroom 事件 Phase 5 由 domain 註冊。新增 `AGENT_IDLE` |
| T-106 | `events.seq` 為 identity；`emit()` 取 **per-company transaction advisory lock**，保證同公司 commit 順序 = seq 順序（否則以 `seq > last_seq` 追蹤的讀者可能永久漏掉較小的 seq）。事件僅 `processed_at` 可被設定一次（trigger） |
| T-107 | activity state 由 payload 型別推導（不另傳 state），row 與 event 無法不一致；COMPLETED 過期→IDLE 為投影規則 `effective_state()` |
| T-108 | 產生器為自有 Python codegen（`runtime/events/codegen.py` → `scripts/gen_event_schema.py`），輸出 zod v4 + `z.infer` 型別與 `schema/events.catalog.json`；不使用外部 JSON Schema→zod 轉換器。`make gen-schema-check` 與 pytest 皆檢查產生物是否過期 |
| T-109 | `.importlinter`（layers + runtime/company/db 禁止依賴 fastapi）；`apps/web/eslint.config.mjs`；`src/boundaries.test.ts` 以 ESLint API 證明規則確實生效 |
| T-110 | `apps/api/autora_api/{app,deps,problems}.py`、`routers/{companies,events}.py`；錯誤一律 problem+json；`GET /api/events` 回傳 `{items, next_after, has_more}` |
| 驗收 | `make phase1-acceptance`：Python 在 Postgres 走完 8 個 activity 狀態並輸出真實事件 → TS 以產生的 zod 全數解析 |
| CI | Python job 內建 pgvector Postgres；`AUTORA_REQUIRE_DB=1` 讓 DB 不可用時測試失敗而非 skip |

## Phase 2 實作註記（完成，2026-09-17 ~ 09-18）

| Task | 實際位置 / 差異 |
|---|---|
| T-201 | `db/models/tasks.py`、`runtime/lifecycles.py`（`TASK_FSM`、`AGENT_RUN_FSM`、`WORKFLOW_RUN_FSM`）；DB 強制「RUNNING ⇔ 持有租約」、「終止 ⇔ finished_at」、每個（task, attempt）一個 run、`agent_steps` append-only |
| T-202 | `runtime/task_manager.py`（遷移 0008：`tasks.available_at`、`tasks.lease_token`、每個 agent 至多一個未完成 run 的唯一索引）；claim token 防止被回收的 worker 完成任務；每次 claim 消耗一次 attempt（含預算受阻）；重試指數退避；依賴傳播留給 T-203 的 `on_task_finished` / `on_task_failed` 掛鉤；FSM 新增 `shortest_path` / `transition_via` |
| T-204 | `runtime/tools/registry.py`；TOOL_CALLED 單獨提交，工具領域寫入與 TOOL_COMPLETED 同交易，失敗另行提交 TOOL_FAILED（避免長時間持有公司事件鎖） |
| T-207 | `runtime/models/{types,router,gateway}.py`、`providers/fake.py`；`model_calls` 為模型成本唯一來源（append-only）；結構化輸出不合格以 `output_issues` 回傳而非拋錯 |
| T-209 | `runtime/cost/guard.py`、`cost_reservations`；`BUDGET_EXHAUSTED` 移至執行環境事件目錄；cycle 預算暫以日計 |
| T-210 | `infra/blobstore.py`（僅 LocalFS，S3 延後，共用契約測試）、`runtime/trace/`、`apps/api/autora_api/routers/runs.py` |
| T-212 | `runtime/scheduler.py`；**不使用 APScheduler**，改為輪詢 `schedules` 表 + `croniter`；錯過的執行合併為一次 |
| T-203 | `runtime/dag.py`（`WorkflowTemplate`、`TemplateRegistry`、`WorkflowEngine`）；接 T-202 掛鉤，在同一交易內解鎖下游（`TASK_SUCCEEDED.unlocks`）與傳遞取消；新增 `WORKFLOW_RUN_CANCELLED` |
| T-205 | `runtime/policy/engine.py`；規則由擁有者註冊：`company/policy.py`、`domains/newsroom/policy.py`；公司覆寫只能收緊、不可逆一定經人；`policy_decisions` 稽核表（遷移 0009）；D-001 以 `newsroom.auto_approve_if_fact_check_passed` 實作 |
| T-206 | `runtime/approvals/service.py`、`approvals` 表（遷移 0010）、`APPROVAL_FSM`、`api/.../routers/approvals.py`；三種掛法（代理執行中 / 人工任務節點 / 獨立）；到期只標記 EXPIRED，任務繼續等待 |
| T-208 | `runtime/models/providers/anthropic.py`、`runtime/models/factory.py`；通用 `OpaqueBlock` 逐字往返 thinking 等區塊；伺服器端 fallbacks 預設開啟；以實際服務模型計價（`ModelRouter.price_for`）；結構化輸出用 `output_config.format`（不用 `parse`，它在非 JSON 回覆時拋錯）；`count_tokens` 已實作但成本守門員仍用本地估算；`MODEL_PRICES` 必填；測試用 `httpx2.MockTransport` |
| T-211 | `runtime/agent_runner.py`、`runtime/behaviors.py`（`AgentBehavior` / `BehaviorRegistry`：領域提供 prompt、輸出模型、驗證器、工具）；每個寫入交易先心跳（租約遺失後零寫入）；審批後從步驟 blob 的 `messages_after` 延續對話；policy deny → abort(policy)；拒答不可重試；**預算耗盡 = ABORTED + WAITING{budget}**（沿用 T-202，非 AC 寫的 FAILED），另補 `AGENT_RUN_ABORTED{budget}` 軌跡事件 |
| T-213 | `runtime/worker.py`（維護 + 派工迴圈、SIGTERM 寬限）、`backend/worker/main.py`、`app.build_worker`；`domains/echo/`（`echo.chain_v1`：researcher → analyst → writer、`echo_note` 工具與 `echo_notes` 表（遷移 0011）、模擬模型）；`company/agents.hire_agent`；**事件分派器延後**（尚無處理器）；領域資料表在領域內，Alembic env 經 `app.load_models()` 取得（`.importlinter` 唯一例外）；驗證改為 `tests/e2e/test_echo_workflow.py` |
| T-214 | `company/workflows.start_workflow`（專案須 ACTIVE；決策寫入 `policy_decisions`）、`api/.../routers/workflows.py`；404 / 422 / 403 / 401 |
| T-215 | `tests/e2e/test_recovery.py`：真實程序、SIGKILL、租約 2 秒；重跑經冪等鍵拿回同一筆產物 |

## Phase 3 實作註記（完成，2026-09-18；驗收通過）

| Task | 實際位置 / 差異 |
|---|---|
| T-301 | `realtime/projection.py`（`load_snapshot`、`begin_consistent_read`：REPEATABLE READ 一致讀取）、`api/.../routers/realtime.py`；任務的 `since` / `last_event_seq` 取自最後一個 TASK_* 事件（reducer 可重建）；`kpis` / `cycle` 暫為 null（T-314 / Phase 6）；不含 `tasks.progress`、`agents.status`；本機中位數約 21 ms（20k 事件） |
| T-302 | `realtime/reducer.py`（`RealtimeState.from_snapshot / apply / view`、`canonical`）、`tests/realtime/test_contract.py`（真實程式產生的隨機歷史，8 種子）、`make realtime-fixture` → `frontend/web/src/realtime/__fixtures__/contract.json`；`AGENT_CREATED.avatar_key`、`AGENT_RUN_ABORTED.final`；修正暫停代理阻擋任務管理員（`set_activity_unless_paused`）；**事件 seq 為全域，`05` §4 的 `last_seq + 1` 缺口判斷不可用**，留給 T-303 / T-306 |
| T-303 | `realtime/gateway.py`（`EventHub`：LISTEN + 2 秒輪詢、公司游標、即時事件轉送、心跳；`Connection`：有界佇列、溢出 → SNAPSHOT_REQUIRED）、`api/.../routers/ws.py`（`/ws/companies/{id}?token&since`，錯誤先送 ERROR 再關閉 4401 / 4404）、`outbox.publish_ephemeral`；**缺口由閘道保證不斷流，不做序號算術**（`05` §4 已修訂） |
| T-304 | `runtime/progress.py`（`ProgressPublisher`：每執行 ≤ 1 則 / 秒、間隔內合併為最新、結束時丟棄、失敗不影響執行）、執行器在模型呼叫與工具呼叫後回報、`ToolResult.progress`；**閘道尚無串流，進度只在步驟之間更新** |
| T-305 | `frontend/web/src/realtime/{snapshot,reducer}.ts`、`src/stores/realtime.ts`（Zustand；`useRealtime` 與 `realtimeStore.subscribe`）；與 Python reducer 以 T-302 契約檔比對完全相同；**無等待緩衝 / 缺口偵測**（依 T-303）；`liveProgress` 在代理換執行時清除 |
| T-306 | `frontend/web/src/realtime/client.ts`（hydrate → WS since → 套用；退避重連、5 次後 offline、SNAPSHOT_REQUIRED / 背景 60 秒重新 hydrate、心跳逾時、unauthorized / not_found 停止、ACK）、`src/config.ts`、API CORS（`CORS_ORIGINS`）；**無 REST 補洞**（依 T-303）；不依賴 close 事件（Node undici 連線被拒時只發 error） |
| T-307 | `frontend/web/src/stores/ui.ts`（selectedAgentId、panelTab、cameraMode、timelinePaused、filters；follow 需有選取） |
| T-308 | `backend/scripts/gen_openapi.py` → `src/api/openapi.json` → `schema.gen.ts`（`make gen-api`，CI 兩端檢查）、`src/api/{client,auth,queries,invalidation}.ts`（openapi-fetch、TanStack Query、`eventToQueryKeys`）；權杖存 localStorage 不編進程式；補上 `GET /api/runs/{id}`、`GET /api/tasks/{id}`，trace 回傳型別化 |
| T-314 | `company/reporting_min.py`、`GET /api/companies/{id}/kpis`；模型費用只由 `model_calls` 計一次；**KPI 為伺服器狀態（Query），不在即時 store**（模型費用不是事件；snapshot.kpis 維持 null） |
| T-309 | `/dashboard`（`features/dashboard/{model,DashboardView,DashboardPage}`、`features/auth/TokenGate`、`features/company/useCompanyStream`）；Tailwind（D-007）；權杖存 localStorage |
| T-310 | `features/agent-panel/{model,AgentCards,AgentPanelView,AgentPanel,StateBadge}`；產出計數來自 `TOOL_COMPLETED.produced`、下一步來自交接或下游任務、進度只在回報時顯示；Output 分頁暫為 JSON（依格式顯示留到階段 5） |
| T-311 | `features/trace-viewer/{model,TraceView,TracePage}`、`/trace/[runId]`、`/tasks/[taskId]`（每次嘗試連到軌跡）；未知類型以原始資料顯示並列於頁首；未被事件指向的步驟依時間插入、共用步驟只顯示一次；完整提示按需從 blob API 載入；**文章入口待 T-517**；OpenAPI 的兩個 `TaskOut` 改名為 `WorkflowTaskOut` / `TaskDetailOut` |
| T-312 | `features/timeline/{model,Timeline,TimelineView,TimelinePage}`、`/timeline`；由新到舊；暫停凍結畫面並計數新事件（store 照常套用）；篩選取交集、存在 UI store；事件說明抽成 `features/events/describe.ts`（與 T-311 共用）；`CompanyScope`、`connectionModel` 由 Dashboard 抽出共用 |
| T-313 | `features/approvals/{model,ApprovalInbox,ApprovalsPage}`、`/approvals`；決議走 REST，列表由 APPROVAL_* 事件失效重取（連線中斷時立即重取；409 / 404 提示並重取）；Dashboard 顯示待審批數；`seed_echo.py --approval on|off`（公司政策覆寫 `echo_note`/`writer` = `needs_approval`）提供真實審批 |
| T-315 | `frontend/web/e2e/{realtime.spec,stack}.ts`、`playwright.config.ts`、`backend/scripts/e2e_prepare.py`、`make e2e`、CI `e2e` 工作；獨立資料庫 `<db>_e2e`、API :8100、web :3100（`.next-e2e`）、不讀 `.env`；驗收四項：兩個 Dashboard 分頁卡片同步、API 停 20 秒後自動恢復、最新 seq = 伺服器、停機期間的軌跡完整 |

## Phase 4 實作註記（進行中，2026-09-18 起）

| Task | 實際位置 / 差異 |
|---|---|
| T-401 | `office3d/{OfficeCanvas,Canvas3D,capabilities,usePageVisible,palette}`、`fallback/OfficeBoard2D`（暫時版，T-410 完成）、`features/office/OfficePage`、`/office`（`?view=3d\|2d`）；context 遺失 → 提示 + 手動重建（換新畫布），不自動重試；分頁隱藏 `frameloop="never"`；**React 釘在 19.2.8（D-009，R3F 9.7 的支援範圍）**；瀏覽器測試 `e2e/office.spec.ts`（CI 用 SwiftShader） |
| T-403 | `office3d/visual/mapping.ts`（`visualState`、`visualForAgent`）；`stores/realtime` 轉出型別與 `effectiveState` 供 3D 使用；zh-TW 標籤與代理卡片一致（`src/labels.test.ts`）；`handoff` 為陣列、`walkTo` 以角色表示；差異記於 02 §7 實作註記 |
| T-410 | `office3d/fallback/{board,OfficeBoard2D}`：同 store + 對照表；卡片依平面排列（`assignSeats`）；最近完成取自事件緩衝區；點卡片寫 `selectedAgentId`；交接以弧線箭頭顯示 2.5 秒（只顯示看板開啟後的新交接） |
| T-404 | Kenney Mini Characters 1.0（CC0，使用者核准下載）：`public/models/characters/` 12 個 GLB + 共用貼圖；`office3d/assets/{characters.ts,LICENSES.md}`（`POSE_CLIP`、`characterFor`）；`scripts/check-assets.mjs`（glTF 驗證、三角形上限、授權清單完整，CI 執行）；家具不用 GLB（D-010 程式產生） |
| T-405 | `office3d/agents/{Agents,AgentAvatar,AvatarController}`；名單以字串 selector 只在變動時重繪，人物以 `realtimeStore.subscribe` + `useFrame` 讀狀態（每秒重讀處理 display_until）；`SkeletonUtils.clone`、放大 2 倍、坐椅面 / 完成站到椅後；素材只有一種坐姿，打字 / 思考 / 閱讀 / 失敗為上半身骨頭疊加；名字標籤用 drei `Html`；AC 以 R3F test renderer + Profiler 驗證（148 事件後提交次數不變） |
| T-406 | `office3d/visual/tracker.tsx`（全場景共用的視覺狀態追蹤器，version 變動才更新）、`agents/{roster,indicators,StatusIndicators}`；螢幕 / 燈罩 / 光暈為三個 InstancedMesh 每幀設顏色；**桌燈不用 PointLight**（自發光燈罩 + 加法混合光暈，避免增加像素光源成本）；頭頂標籤以 ref 直接寫 DOM；重複泡泡隱藏 |
| T-407 | `office3d/visual/{cues,director,CueRunner}`：`cuesFor`（04 §4 表）、`CueQueue`（同目標不重複、新執行中止、TTL 10 秒、每人最多 4 趟、效果並行）、`CueDirector` 只處理新事件、`routeFor`（路線與時間，供 T-408）；審批桌燈依狀態閃；**審批事件的 `agent_id` 為空，改取 actor** |
| T-408 | `office3d/agents/courier.ts`（`along`、`courierState`）+ `AgentAvatar` 內的走路；人物本身走（不做分身）、胸前文件道具、交接時面向同事點頭；`routeFor` 加 `lookAt`；AC 以整條鏈路測試（新執行開始 → 立即回座） |
| T-409 | `office3d/camera/{framing,CameraRig}`、`interaction/{picking,SelectionMarker}`；**自算總覽縮放取代 drei `Bounds`**；選取 → 聚焦並跟隨、拖曳 → free、取消 → 回預設等角總覽；OrbitControls 俯角 / 方位角 / 縮放 / 平移限制；點空白或 Esc 取消、1～6 選角色；HeadTag 在 `Html` 重建 DOM 後重寫 |
| T-411 | `features/office/{OfficePage,MiniDashboard}`：標頭 + 迷你 Dashboard（共用 `dashboardModel`）+ 辦公室 + 代理面板（T-310）；選取時相機避開右側面板（`selectionInsetRight`）；`window.__autoraOfficeCues` 探針；瀏覽器驗收：面板 4～7 ms 出現、標籤依序變化、交接走路、2D 看板開同一面板 |
| T-412 | `e2e/office-soak.spec.ts`（`make soak`）：回收後 heap、DOM 節點、監聽數、FPS、繪製呼叫；2 小時 25 輪 heap +5.0 MB、FPS 全程 60（`logs/perf/2026-09-19-office-soak-120min.json`）；heap 快照比對找到 drei `<Environment>` portal 的 `setEvents` 閉包串洩漏 → `scene/OfficeEnvironment`（PMREMGenerator 烘一次）；store 丟掉 10 分鐘前完成的任務 |
| T-413 | `palette.ts`（`THEMES`：日式無印、侘寂風、工業風、Google 風、電光風；D-011）、`theme.ts`（`localStorage`）、`scene/{layout (ENTRANCE, ZONES, LABELS),furniture,Floors,Labels,textures}`：入口、接待區、每部門地毯與植栽隔間、地面字與門牌（一張貼圖一次繪製）、霓虹零件另成不受光照的網格、光線依風格；`AvatarController` 頭部 0.8 倍；地板依風格重建（材質加貼圖需要新著色器） |
| T-500 | `infra/search/{__init__,tavily,fixture}`（`SearchProvider`、`SearchUnavailable` / `SearchRejected`）、`domains/newsroom/tools/{__init__,search}`、`domains/newsroom/fixtures/search.json`（虛構題材、`*.fixtures.autora.test`）；Tavily 用 Bearer 標頭、`include_usage` 計成本、滑動視窗速率限制；`web_search` 不寫資料、`produced` 為空；登錄表尊重例外的 `retryable`；空白金鑰視為未設定 |
| T-501 | `domains/newsroom/{models,feeds,sources,events}`、migration `0012`（`sources`、`source_items`：每個來源依 `external_id` 與 `content_hash` 唯一）、`infra/http`（`HttpFetcher`：拒絕私有位址、大小 / 轉址上限；`FixtureFetcher` + `fixtures/routes.json`）；排程 `newsroom.poll_sources`（每 5 分鐘，`app.build_scheduler`）；兩段式輪詢（先抓、後寫）、每次最多 50 則、連續 5 次失敗自動暫停；事件 `SOURCE_POLLED`、`SOURCE_ITEM_DISCOVERED`、`SOURCE_PAUSED`；來源管理 API 延到 T-517 |
| T-502 | `domains/newsroom/{extract,tools/evidence}`、migration `0013`（`evidence`：公司 + 網址 + 內容雜湊 + 日期唯一）；本文抽取用標準函式庫（未採用 trafilatura，不新增相依套件）；`fetch_url`（快照進 BlobStore、`EVIDENCE_CAPTURED`、重用也回報 produced）、`read_evidence`（分段、限本公司）；非文字 / 無可讀文字 / 私有位址不重試；`render` 尚無 Playwright；`fixtures/pages/` |
| T-503 | `runtime/models/embeddings.py`（`Embedder`、`OpenAICompatibleEmbeddings`、`HashingEmbeddings`；每次呼叫記 `model_calls` alias `embed`）、`runtime/models/factory.embedder_from_settings`、`db/vector.py`（`halfvec` 型別，不用 pgvector 套件）、`domains/newsroom/chunks.py`、migration `0014`（`evidence_chunks`：起訖位置、`halfvec(2048)`、HNSW）；`fetch_url` 寫入前先分段與算向量、失敗仍擷取；`search_evidence`（向量，退回關鍵字）；D-012 `nvidia/nemotron-3-embed-1b` |
| T-504 | `domains/newsroom/stories.py`（`StoryDesk`、`STORY_FSM`）、migration `0015`（`stories` 含中心向量 `halfvec(2048)` + HNSW、`story_items`）；排程 `newsroom.cluster_stories`（`2-59/5`，與輪詢一起建立）；同網址併入、相似度 ≥ `STORY_MATCH_THRESHOLD`（0.65，實測決定）併入、比對不分狀態（去重與「寫過嗎」）；分數 = 0.5 佐證 + 0.3 新鮮 + 0.2 信任；事件 `STORY_DISCOVERED` / `SELECTED` / `DROPPED` |
| T-505 | `domains/newsroom/{quotes,tools/claims}`、migration `0016`（`claims`：類型、狀態、呼叫鍵唯一；`claim_evidence`：原文引文與起訖位置、支持 / 矛盾 / 背景）；引文只容許排版差異（中文字旁空白不算）、4～500 字；`create_claim`（可附引文、全有或全無、`CLAIM_CREATED`）、`link_evidence`、新動作 `list_claims`（已加入 platform/07 與權限矩陣） |
| T-508 | `domains/newsroom/{articles,tools/drafts}`、`policy.language_policy`（D-002）、migration `0017`（`articles`：slug 全域唯一、`ARTICLE_FSM`；`article_versions`：同草稿群組共用版本號、區塊帶 `claim_ids`、翻譯關係）；`check_draft` 一次列出全部問題（語言政策、段落必引主張、主張屬本題材且未駁回、**各語言主張集合相同**、非 DRAFT 不能寫）；`write_draft`（`ARTICLE_CREATED` 只在初稿）、`read_draft` |
| T-510 | `domains/newsroom/{factcheck,tools/factcheck}`、migration `0018`（`fact_check_reports`）、`policy.trust_policy`（`newsroom.min_source_trust` 0.4、`newsroom.default_source_trust` 0.5）；第一層：支持引文、引文完整、低信任不能單獨支持（或兩個獨立來源）、數字換算比對；第二層：矛盾 → 不過（爭議改寫成歸屬）、相關段落僅供參考；主張狀態 VERIFIED / REJECTED 與事件；第三層清單附各語言句子給編輯；窄範圍向量搜尋改精確掃描 |
| T-512 | `domains/newsroom/publisher.py`（指令：`approve_article`：查核通過才可、系統需公司開啟自動核准；`reject_article`：只有人、題材放棄；`publish_article`：冪等、鎖文章列、語言政策、`published_group_id`、題材 → PUBLISHED、`site` 發布紀錄）、migration `0019`（`distributions`，site 每篇一筆）；事件 `ARTICLE_APPROVED` / `REJECTED` / `PUBLISHED`、`DISTRIBUTION_CREATED`；Dashboard 今日發布改由 `ARTICLE_PUBLISHED` 事件計數 |
| T-506 | `domains/newsroom/agents/{__init__,researcher}`（`research` 任務、`ResearchNote`、背景含題材與線索、驗證：證據必須是本任務 `fetch_url` 產生的〔查事件〕、≥ `min_sources` 且不只一個網站、每份一段摘要）、`domains/newsroom/simulation.py`（研究員的模擬：搜尋 → 擷取 3 份 → 用實際證據 ID 回報）；`app.build_behaviors` / `simulated_model` 註冊新聞室 |
| T-507 | `domains/newsroom/agents/analyst.py`（`analysis` 任務、`AnalysisNote`〔主張、角度、關鍵數字、矛盾〕、背景列出上游研究任務的證據與摘要、驗證：主張必須是本任務為本題材建立的、≥ `min_claims`、**每則主張都要能通過事實查核**〔`check_claim` 第 1～2 層〕、關鍵數字指向數字主張）、`tools/factcheck.claim_quotes` 抽出共用；模擬：讀證據 → 每份取含數字的句子建主張（原文即引文）→ 回報 |
| T-509 | `domains/newsroom/agents/writer.py`（`draft` 任務，含修訂：`params.issues`；`ArticleDraft`；背景列出語言政策、分析師的角度與主張〔標出關鍵數字〕、有爭議之處、修訂時的編輯意見；驗證：回報的草稿群組是本任務 `write_draft` 寫的〔查事件〕、是文章目前的草稿、ID 與語言相符，引用分析師標出的每個關鍵數字〔被查核駁回的除外〕，修訂要寫 `change_summary`）；模擬：修訂先 `read_draft` → 每則主張一段、兩語言引用同一組主張 → 回報；測試共用 `tests/newsroom/agents/conftest.run_line` |
| T-511 | `domains/newsroom/review.py`（指令：`accept_draft`〔DRAFT → IN_REVIEW，需草稿**最新**且通過的查核報告〕、`request_revision`〔維持 DRAFT、`revision_count` + 1；第 3 次 → 文章 REJECTED、題材 DROPPED〕；每份草稿只決定一次，重試回傳原決定、不同決定拒絕）、`tools/review.py`（兩個工具，`Issue{message, kind, lang?, block_ref?}`）、`agents/editor.py`（`review` 任務、`EditorReview`、驗證：決定是本任務做的且與回報一致、查核報告是本任務產生且屬於本文章、accept 需通過且無問題、revise 至少一個問題且與送出的數量相同）；事件 `ARTICLE_REVIEWED`、`ARTICLE_REVISION_REQUESTED`；`stories.drop_story` 共用；模擬：讀草稿 + 查核 → 通過就接受、否則每個未過的主張一個問題 |
| T-513 | `tools/distribution.py`（`create_distribution`：只接受已發布文章、只寫資料庫草稿〔`social_draft`、`status=draft`〕、每個發布語言一則、連結由工具加上、每篇每個通路一筆〔重試回傳原筆、不同內容拒絕〕、`DISTRIBUTION_CREATED`）、`agents/marketing.py`（`distribute` 任務、`DistributionPlan`〔site + social_draft 與貼文〕、`policy_facts` 提供文章狀態給「只限已發布」的權限、背景列出各語言標題 / 網址 / 摘要 / 開頭、驗證：網站那筆是出版服務的、社群那筆是本任務建立的、回報的貼文就是存下的）；模擬：每語言一則貼文 → 回報；前端發布紀錄顯示中文通路與狀態 |
| T-514 | Runtime（D-013）：`dag.NodeSpec.service`、`dag.Loop`（`WORKFLOW_RUN_EXTENDED`）、`runtime/services.py`（`ServiceRegistry` / `ServiceDispatcher`，`Worker.services`）、`ApprovalService.on_decided`、`TaskManager.link_hooks` / `fail_without_run`、任務 FSM 的 READY → SUCCEEDED / FAILED；新聞室：`workflow.py`（`newsroom.story_to_article_v2`：research → analysis → draft → review →〔revise ≤ 2 輪〕→ approve〔服務，human〕→ publish〔服務，system〕→ distribute；`start_story` 走 `start_workflow` 權限並讓題材 IN_PRODUCTION；核准步驟：自動核准或建立 `approve_article` 核准；人的決定經掛鉤核准 / 駁回文章）、`activity_links.py`、`newsroom.register(runtime)`；`app.build_runtime` / `build_worker` 組裝；量測排程留給 T-516 |
| T-518 | `domains/newsroom/simulation.py` 的示範設定（`params.demo`：`pace_seconds` 每次回覆的延遲、`revise_first_review` 第一次審稿一定退回 → 寫手加導言並寫修改說明 → 第二次接受）；`workflow.start_story(demo=...)`、`staff_newsroom`（五位代理）；`domains/newsroom/demo.py`（`seed_demo` 公司 / 專案 / 代理 / 兩個 fixture 來源、`gather_stories` 立即讀取並分群、`pick`、`start_demo_story`）；`backend/scripts/seed_newsroom.py`（`--start --pace --revise --story`）；RUNBOOK「試跑新聞室」；`tests/e2e/test_newsroom_sim.py` |
| T-515 | 後端：`analytics_events`（migration `0020`；`view` / `read_complete`、每日隨機的 `session_hash`、UTC `day`，唯一鍵〔文章、語言、種類、session、日〕去重）、`domains/newsroom/site.py`（`published_article` 只給已發布那一版與該語言、資料來源〔引用主張的證據，去重〕、各語言網址；`published_articles`；`record_beacon`）、`api/routers/public.py`（`GET /api/public/articles`、`GET /api/public/articles/{lang}/{slug}`、`POST /api/analytics/beacon`，不需權杖）；前端：`(site)/[lang]`（版頭、首頁列表、文章頁 + `generateMetadata` 的 canonical / hreflang）、`features/site/`（`api.ts` 公開讀取、`ArticleView`、`ArticleList`、`Beacon`〔進頁一次瀏覽、讀到結尾一次讀完〕、`session.ts` 每日 ID、`i18n.ts`）、`config.SERVER_API_URL`（`API_INTERNAL_URL`）、docker-compose 補 `API_INTERNAL_URL` |
| T-516 | `analytics_daily`（migration `0021`；每篇、每語言、每天：`views` / `uniques` / `read_complete`）、`domains/newsroom/analytics.py`（`AnalyticsCollector.collect`：重算昨天與今天〔UTC〕、有變才 upsert 並發 `ANALYTICS_DAILY_UPDATED`〔每篇每天一則、跨語言合計與各語言瀏覽〕、刪除 30 天前的原始 beacon；`ensure_analytics_schedule` 每小時第 7 分，由發布服務在發布時建立）、`app.build_scheduler` 註冊；事件契約產生器支援 `date` 格式；D-014（量測不另排 +1h/+24h/+7d） |
| T-402 | **（D-010 重做）** `office3d/scene/{layout,kit,furniture,textures,Floors,Screens,OfficeScene}`：等角剖面辦公室（後排執行長 / 會議室 / 茶水間、兩條走道、長條桌、前排單桌 / 審批櫃台 / 休息區），靜態家具合併成一個頂點色網格（16 次繪製、約 2.1 萬三角形）、正交等角相機 + `Bounds`、左上主光陰影、`Lightformer` 環境光、Neutral 色調映射；初版：`office3d/scene/{layout,Room,Zones,Furniture,OfficeScene}`、`perf/StatsProbe`（`window.__autoraOffice`：FPS、繪製呼叫、三角形）；每角色 2 張桌（行銷 3、未知角色空桌 3），`assignSeats` 依 id 穩定、坐不下列入 `unseated`；`walkPath` 走前 / 後走道與中央走道；家具全用 `<Instances>`（34 次繪製、2,288 三角形）；`flat`（不用 ACES 色調映射）；drei 10.7.8 |
