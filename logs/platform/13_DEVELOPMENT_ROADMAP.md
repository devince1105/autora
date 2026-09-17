# 13 — Development Roadmap (Platform)

> **執行順序以 `3d-office/09_DEVELOPMENT_ROADMAP.md` 為準**（Phase 1–6：Company+Agent Core → Runtime → WS+Dashboard → 3D Office → Newsroom → Autonomous Loop）。
> 本文件保留平台層的兩個後續 Phase（Revenue、Multi-company）、技術棧逐項評估、與 Phase 對應表。

---

## 1. Phase 對應

| 平台 Phase（第一輪） | 3d-office Phase（執行用） |
|---|---|
| P0 Architecture | 0 |
| P1 Core Runtime | 1 + 2 |
| P2 Company Engine | 6（前半） |
| P3 Newsroom | 5 |
| P4 Autonomous Loop | 6 |
| — | 3 WS + Dashboard、4 3D Office（新增） |
| P5 Revenue | **Phase 7**（本文件 §2） |
| P6 Multi-company | **Phase 8**（本文件 §3） |

---

## 2. Phase 7 — Revenue

- **Goal**：至少一條真實營收通路，ROI 進入 CEO 決策。
- **Features**：`domains/business`：products、customers、leads、payments webhook（只寫入）、Marketing 真實通路 adapter（依 Open Question）、Finance Agent（只讀 + 預算提案）、Business Agent（opportunity 提案）、revenue KPI、campaign cap。
- **Files**：`autora/domains/business/**`、`features/revenue/**`。
- **Database**：products, customers, leads, opportunities, subscriptions, orders, payments, campaigns, campaign_spend。
- **API**：`/api/products`, `/api/customers`, `/api/leads`, `/api/campaigns`, `POST /api/webhooks/payments/{provider}`。
- **Events**：CUSTOMER_CREATED, LEAD_CREATED, OPPORTUNITY_DISCOVERED, CAMPAIGN_CREATED, CAMPAIGN_SPEND_RECORDED, PAYMENT_RECEIVED, REVENUE_RECORDED。
- **Tests**：webhook 冪等；ad spend 超 cap → HUMAN；revenue 進 snapshot；Finance 提案不可寫 transactions。
- **Acceptance**：一筆真實 revenue 由 webhook 寫入並出現在 Dashboard 與 CycleReview；Marketing 超額被攔。
- **Dependencies**：Phase 6；Open Question「營收模型」。
- **Risks**：外部平台政策；財務正確性。

## 3. Phase 8 — Multi-company

- **Goal**：同一 runtime 跑第二種公司（建議 AI Research Company）。
- **Features**：第二個 domain package、workflow_templates 與 model_policies 入 DB、company 級配額、多人 auth/RBAC、Redis（多 worker rate limit / pub/sub）、events 分區（若需要）。
- **Acceptance**：兩家公司同時運作，成本與事件完全隔離；新 domain 零修改 runtime/company/realtime。

---

## 4. 技術棧逐項評估

| 技術 | MVP | Phase 7–8 | 何種規模才需要 | 若不用的替代 |
|---|---|---|---|---|
| Next.js + TS | 需要 | 需要 | — | — |
| Python + FastAPI | 需要 | 需要 | — | — |
| PostgreSQL + pgvector | 需要 | 需要 | — | — |
| Redis | **不需要** | 需要（多 worker、rate limit、pub/sub） | ≥2 worker 或 API >3 副本 或每分鐘 >100 tool calls 限流 | Postgres SKIP LOCKED + LISTEN/NOTIFY |
| S3-compatible | 介面需要；實作 LocalFS | 真 S3/R2 | 快照 >10GB 或多機 | LocalFS adapter |
| Playwright | 選用（`fetch_url render=true`） | 需要 | 來源 >20% 需 JS | httpx + trafilatura |
| Docker compose | 需要 | 需要 | — | — |
| Sentry | 需要 | 需要 | — | 結構化 log |
| OpenTelemetry | 不需要（domain trace 表已有） | 需要 | 多 process latency 分析 | 內建 trace |
| APScheduler | 需要 | 需要 | — | 自寫 loop |
| SQLAlchemy 2 + Alembic、pydantic v2 | 需要 | 需要 | — | — |
| React Three Fiber + drei | 需要（Phase 4） | 需要 | — | 2D board |
| Zustand + TanStack Query | 需要 | 需要 | — | — |
| Kafka / K8s / Temporal / microservices / service mesh | 否 | 否 | Kafka >10k ev/s；K8s 多機擴縮；Temporal 跨天 durable timers + 團隊 >5 | 本設計已覆蓋 |
| Auth | env bearer token | 正式 auth（Phase 8） | 多人 | — |

---

## 5. Gate 總則

進入任一 Phase 的條件 = 前一 Phase 的 Acceptance 全部通過 + 該 Phase 的 P0 Open Question 有答案 + P0 風險有處置。詳見 3d-office/09 §7。
