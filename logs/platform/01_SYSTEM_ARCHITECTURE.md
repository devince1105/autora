# 01 — System Architecture (Platform)

> 範圍：Autora 平台層——Company Engine、Agent Runtime、Domains 的整體結構與原則。
> 3D Office / Realtime 的細節見 `logs/3d-office/`。本文件與該資料夾若有衝突，以 **較晚的決定（3d-office）** 為準，並在此標註。

---

## 1. 一句話

**Autora 是一個 Modular Monolith：一個 Python 套件、兩個進程（API / Worker）、一個 Postgres 作為唯一真相來源。
公司的一切都是資料庫裡的狀態；Agent 是被 Runtime 短暫喚起、受 Policy 約束、每一步都被記帳與追蹤的無狀態工人。**

---

## 2. 七個核心原則

1. **State in DB, not in process.** 任何 process 隨時可死，重啟後公司照常運作。
2. **LLM proposes, Policy disposes.** 所有有後果的動作走 `Command → Policy → Validation → Mutation → Event`。
3. **Everything is metered.** 每一次模型呼叫綁定 `company / project / task / run`，即時轉為 expense。
4. **Deterministic control flow.** FSM 決定生命週期、DAG 決定任務依賴、Scheduler 決定時間、Event 決定跨模組通知。LLM 不控制 control flow。
5. **Runtime 不知道 Newsroom 存在。** `runtime` 永不 import `domains/*`；Domain 透過 `register(runtime)` 掛進來。
6. **Evidence-first.** 內容的單位是 Claim；每個 Claim 可追溯到 Evidence 快照。
7. **No premature infrastructure.** MVP 沒有 Redis、Kafka、K8s、Temporal。Postgres 同時扮演 DB、queue、event log、scheduler lease、realtime fan-out（LISTEN/NOTIFY）。

---

## 3. 分層

```
┌──────────────────────────────────────────────────────────────┐
│ Presentation   apps/web: Admin (Dashboard, 3D Office, Newsroom │
│                pages, Approvals, Trace) + Public Site           │
├──────────────────────────────────────────────────────────────┤
│ API            apps/api: FastAPI routers + WS Gateway (thin)   │
├──────────────────────────────────────────────────────────────┤
│ Domains        domains/newsroom  domains/business (P5)         │
│                Workflow templates · Tools · Commands · Agents  │
│                (prompts+schemas) · Validators · Event handlers  │
├──────────────────────────────────────────────────────────────┤
│ Company Engine company/: Cycle loop · Projects · Budgets/Ledger│
│                · Reporting/Snapshot · Governance · Commands ·  │
│                CEO agent                                        │
├──────────────────────────────────────────────────────────────┤
│ Realtime       realtime/: projection (snapshot) · gateway      │
├──────────────────────────────────────────────────────────────┤
│ Runtime        runtime/: AgentRunner · TaskManager · FSM · DAG │
│                · Scheduler · Events(outbox, seq) · ToolRegistry│
│                · ModelGateway/Router/Providers · PolicyEngine  │
│                · Approvals · Activity · Memory · Trace · Cost  │
│                · Recovery                                       │
├──────────────────────────────────────────────────────────────┤
│ Infra          Postgres(+pgvector) · BlobStore · HTTP · Settings│
└──────────────────────────────────────────────────────────────┘
依賴只能往下：runtime ⟂ company ⟂ realtime ⟂ domains；只有 app.py 可全部 import。
```

---

## 4. 進程拓樸與資料流

```
Operator ──▶ web ──REST──▶ api ──SQL──▶ Postgres ◀──SQL── worker
Readers  ──▶ web(site) ──▶ api(beacon)      ▲  NOTIFY        │
                                            │                 ├─▶ Anthropic API (via ModelGateway)
web ◀──WS── api ◀──LISTEN───────────────────┘                 ├─▶ Web (httpx / Playwright)
                                                              └─▶ BlobStore (LocalFS / S3)
```

- **api**：無業務邏輯；每個 mutation endpoint 對應一個 Command。
- **worker**：Scheduler + Event dispatcher + Task loop（AgentRunner）。單副本（MVP）。
- **Postgres**：狀態表（真相）、`tasks`（queue, SKIP LOCKED）、`events`（outbox + audit + realtime 來源）、`schedules`（lease）。

---

## 5. 控制流分工（何時用什麼）

| 機制 | 用在 | 不用在 |
|---|---|---|
| **FSM** | Task、AgentRun、Agent Activity、Project、Article、Story、Cycle、Approval 的生命週期 | 跨實體協調、時間觸發 |
| **DAG** | Workflow Template（research → analysis → draft → review → approve → publish → distribute） | Cycle 階段推進、跨 project 協調 |
| **Event-driven** | 跨模組通知（ARTICLE_PUBLISHED → analytics 排程）、審計、realtime 推播 | 任務派工（用 DB queue）、狀態真相（不是 event sourcing） |
| **Scheduler** | Cycle 開始、source 輪詢、analytics 收集、預算重置、租約回收 | 任務之間的推進 |

明確不做：event sourcing、CQRS、動態 LLM 生成 DAG（MVP）。

---

## 6. 核心概念（一句話）

| 概念 | 定義 | 存放 |
|---|---|---|
| Company | 資料，不是 process（見 02） | 多張表 |
| Cycle | 公司迴圈的一次實例（MVP 每日一次），FSM | `cycles` |
| Project | 預算、ROI、kill criteria 的單位 | `projects` |
| Workflow Template | 程式碼宣告的 DAG | code（Phase 6 後可入表） |
| Workflow Run | Template 的一次實例化 | `workflow_runs` |
| Task | 原子工作單位，FSM、depends_on、required_role、budget | `tasks` |
| Agent | 定義（role、permissions、model policy、budget）；執行是 AgentRun | `agents` / `agent_runs` |
| Agent Activity | 「這個人現在在幹嘛」（8 狀態） | `agent_activity` |
| Tool | Agent 的手；註冊時宣告 schema、side_effect、cost class | ToolRegistry |
| Command | 對公司狀態的意圖性變更 | code + `commands_log` |
| Event | 已發生的事實，append-only，有 `seq` | `events` |
| Policy | 對 Command / Tool call 的確定性裁決 | `company_policies` + role defaults |
| Approval | 待人類裁決的請求 | `approvals` |

---

## 7. 第一輪發現的架構矛盾與修正（保留為決策紀錄）

| 原始想法 | 矛盾 | 修正 |
|---|---|---|
| CEO 決定下一個 Task | LLM 控制 control flow → 無法保證終止與預算 | CEO 是 Runtime 呼叫的 agent，只在 Cycle 的 PLANNING / REVIEWING 被喚起，輸出結構化提案 |
| 12 個 Agent | Publisher / Analytics / Finance 不需要 LLM 判斷 | 需要判斷的才是 Agent；其餘是 Service。MVP LLM agents：CEO、Researcher、Analyst、Writer、Editor、Marketing |
| Finance Agent 管理收支 | 與「LLM 不可改財務資料」衝突 | Ledger Service（唯一可寫）+ Finance Agent（P5，只讀 + 提案） |
| Fact-check Agent 驗證內容 | 同模型驗證自己輸出價值有限 | 三層：結構（確定性）→ 交叉（向量）→ 語意（LLM） |
| Agent 有 memory / state | 易被做成常駐 process | Agent 無狀態；狀態在 DB |
| DAG 由 Agent 動態規劃 | 無法事前驗證預算與權限 | 宣告式 template；CEO 只選 template + 參數 |
| RAG 記憶 | 結構化資料用向量是錯誤工具 | pgvector 只用在 evidence 檢索、story 去重、「寫過嗎」 |

---

## 8. 必要假設

| # | 假設 |
|---|---|
| A1 | 發布目標是自有網站（Next.js 公開站讀同一 Postgres）；外部通路是 Distribution adapter |
| A2 | Analytics 為第一方 beacon |
| A3 | Source 以 RSS/Atom + URL 清單為主；web search 供應商是 Open Question |
| A4 | 單一操作者；審批在 admin console |
| A5 | 單一 frontier model（Claude via Anthropic API）；model id 只在設定檔 |
| A6 | 單機 docker-compose；一個 API + 一個 Worker |
| A7 | 幣別 USD；token 單價表在設定檔 |
| A8 | Schema 從第一天有 `company_id`；MVP 只跑一家 |
| A9 | 雙語（zh-TW / en），主語言與發布規則為 policy |
