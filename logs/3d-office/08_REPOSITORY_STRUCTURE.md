# 08 — Repository Structure

---

## 1. 判斷

| 問題 | 決定 | 理由 |
|---|---|---|
| Monorepo？ | **是**（pnpm workspace + 一個 Python 套件） | 前後端共享 event schema；一個 PR 可同時改 payload 與 UI |
| `services/api` 與 `services/agent-runtime` 分開？ | **否**。同一個 Python 套件 `autora`，兩個**進程入口**（`apps/api`, `apps/worker`） | 它們共享 repositories、schema、policy；分成服務只會產生 RPC 與版本漂移。進程分離已足以隔離 LLM 工作負載與 HTTP 延遲 |
| `packages/agent-core`、`company-core` 分開？ | **否**。是 `autora` 套件內的子模組 `runtime/`、`company/`，邊界用 import-linter 強制 | 套件切分的成本（發布、版本、循環依賴）在單團隊沒有回報 |
| `packages/event-schema` 獨立？ | **是**，但它是**產生物**（pydantic → JSON Schema → TS/zod），不是手寫 | 前端唯一需要的跨語言契約 |
| `packages/ui`、`packages/3d-office` 獨立？ | **否**（MVP）。放在 `apps/web/src/` 內的模組 | 只有一個消費者。當第二個 app（例：公開站拆出、Electron）需要它們時再抽 |
| `domains/newsroom` 在 Python 還是 TS？ | **Python**（`autora/domains/newsroom`）擁有 domain；TS 端只有頁面與型別 | Domain 邏輯只能有一份 |

---

## 2. 結構

```
autora/
├── apps/
│   ├── web/                         Next.js 15 (App Router) · TypeScript
│   │   └── src/
│   │       ├── app/
│   │       │   ├── (admin)/         /office  /dashboard  /newsroom/*  /trace/*  /approvals
│   │       │   └── (site)/          /[lang]/articles/[slug]  （公開站）
│   │       ├── office3d/            見 04（只讀 store）
│   │       ├── realtime/            RealtimeClient(WS)、reducer、hydrate、gap-fill
│   │       ├── stores/              realtime.ts(Zustand)、ui.ts
│   │       ├── api/                 typed REST client（openapi-typescript 產生）+ TanStack Query hooks
│   │       ├── features/            dashboard/、agent-panel/、trace-viewer/、newsroom/、approvals/
│   │       └── components/          通用 UI
│   ├── api/                         FastAPI 入口：routers、WS gateway、DI；無業務邏輯
│   └── worker/                      Worker 入口：scheduler、event dispatcher、task loop
├── packages/
│   ├── autora/                      ★ 唯一 Python 套件（pyproject）
│   │   └── autora/
│   │       ├── runtime/             agent_runner, task_manager, fsm, dag, scheduler, events/(schema, outbox, dispatcher),
│   │       │                        tools/, models/(gateway, router, providers/), policy/, approvals/, cost/, trace/, activity/
│   │       ├── company/             cycle, projects, ledger, reporting, snapshot, governance, commands/, agents/ceo
│   │       ├── realtime/            projection.py(snapshot 投影)、gateway.py(WS ConnectionManager, listener)
│   │       ├── domains/
│   │       │   ├── newsroom/        models, workflows, tools, agents(prompts, schemas), validators, publisher,
│   │       │   │                    analytics, activity_links, fixtures/(simulation)
│   │       │   └── business/        (Phase 6+)
│   │       ├── db/                  sqlalchemy models, repositories, alembic/
│   │       ├── infra/               settings, blobstore, http
│   │       └── app.py               組裝：register domains → runtime（唯一可同時 import 三層之處）
│   └── event-schema/                產生物：schemas/*.json、src/index.ts(types + zod)、version.json
├── infra/                           docker-compose.yml、Dockerfile.api、Dockerfile.worker、Dockerfile.web、env.example
├── logs/                            設計文件（platform/、3d-office/）
├── pnpm-workspace.yaml
├── Makefile                         make dev / test / gen-schema / lint-boundaries
└── .importlinter                    Python 邊界規則
```

---

## 3. 邊界規則（CI 強制）

### Python（import-linter）
```
runtime   ✗→ company, domains, realtime, apps
company   ✗→ domains, realtime, apps          （company 只依賴 runtime）
realtime  ✗→ domains                          （投影只看 runtime/company 的表）
domains/* ✗→ 其他 domains/*
app.py    ✓ 全部
```

### TypeScript（ESLint no-restricted-imports）
```
office3d/**   只可 import: stores/realtime(selectors), stores/ui, @autora/event-schema, three/r3f/drei
realtime/**   不可 import: features/**, office3d/**
features/**   不可 import: office3d/** 的內部（只可用 <OfficeCanvas/> 公開元件）
```

### Schema 產生
`make gen-schema`：pydantic → JSON Schema → `packages/event-schema`。CI 檢查產生物與 commit 一致（防止手改）。

---

## 4. 進程與部署

| 進程 | 內容 | 副本 |
|---|---|---|
| `api` | REST + WS gateway + LISTEN | 1（MVP） |
| `worker` | Scheduler + dispatcher + task loop（AgentRunner） | 1（MVP）；Phase 6 多副本（需 Redis rate limiter） |
| `web` | Next.js（admin + site） | 1 |
| `db` | Postgres 16 + pgvector | 1 |
| `blob` | MinIO（可選；MVP LocalFS） | 0/1 |

`docker compose up` 起全部；開發時 `make dev` 起 db + api + worker + web（web 走 `next dev`）。

---

## 5. 何時拆

| 訊號 | 動作 |
|---|---|
| 第二個前端 app 需要 3D 或 UI 元件 | 抽 `packages/ui`、`packages/office3d` |
| Worker 需要不同的 Python 依賴（例如 GPU、Playwright 很重） | 分 Dockerfile，不分套件 |
| 第二個 Business Domain 團隊獨立開發 | `domains/<name>` 仍在同套件，但擁有獨立 CODEOWNERS 與測試目錄 |
| API 需要 >3 副本 | 引入 Redis pub/sub 取代每 process LISTEN |
