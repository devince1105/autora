# 14 — Task Breakdown (Platform)

> **Phase 1–6 的 coding tasks 以 `3d-office/10_TASK_BREAKDOWN.md`（T-101 ~ T-610）為準**，它已涵蓋第一輪的 TASK-001 ~ TASK-054 並與 3D / Realtime 整合。
> 本文件提供：(1) 第一輪 task ID 對應表（避免兩套編號混淆）、(2) Phase 7–8 的 tasks。

---

## 1. 對應表（第一輪 → 執行用）

| 第一輪 | 內容 | 執行用 |
|---|---|---|
| TASK-001 | Settings & DB bootstrap | T-101, T-102 |
| TASK-002 | Generic FSM | T-104 |
| TASK-003 | Task/AgentRun/AgentStep schema | T-201 |
| TASK-004 | TaskManager | T-202 |
| TASK-005 | EventBus outbox | T-105, T-106 |
| TASK-006 | DAG instantiation | T-203 |
| TASK-007 | Scheduler | T-212 |
| TASK-008 | ToolRegistry | T-204 |
| TASK-009 | PolicyEngine | T-205 |
| TASK-010 | Approvals | T-206 |
| TASK-011 | AgentRunner | T-211 |
| TASK-012 | ModelGateway + Router | T-207 |
| TASK-013 | AnthropicProvider | T-208 |
| TASK-014 | Evaluator | 併入 T-211 |
| TASK-015 | CostGuard | T-209 |
| TASK-016 | Trace + BlobStore | T-210 |
| TASK-017/018 | EchoAgent + Worker + P1 integration | T-213, T-215 |
| TASK-020–028 | Company Engine（schema、ledger、commands、reporting、CEO、cycle、governance） | T-103, T-601–T-607 |
| TASK-030–045 | Newsroom | T-501–T-520 |
| TASK-050–054 | Autonomous Loop + UI | T-608–T-610, T-309–T-313 |

---

## 2. Phase 7 — Revenue

**T-701 · Business schema** — products, customers, leads, opportunities, subscriptions, orders, payments, campaigns, campaign_spend / Deps: T-103 / Validate: `pytest tests/db/test_business_models.py`
  > 實作註記（2026-09-22，D-022 → D-024）：範圍收斂為 `prices`、`memberships`、`payments`（migration 0035 建立、0037 把 `subscriptions` 換成 `memberships`；`company/memberships.py`）。營收模型最後定為**一次付款買一年使用權**（統一金流），不是定期訂閱。`products`（T-600）、`customers`（T-612）、`opportunities`（T-611）已存在；`leads`、`orders`、`campaigns`、`campaign_spend` 刻意不做，理由見 D-022。另有 `tests/company/test_memberships.py`。
**T-702 · Payment webhook (provider adapter, 冪等, → Ledger revenue)** / Deps: T-701, T-602 / AC: 重複 webhook 不重複入帳 / Validate: `pytest tests/business/test_webhook.py`
**T-703 · Campaign cap + spend_ad_budget policy (LIMITED → HUMAN)** / Deps: T-205, T-701 / Validate: `pytest tests/business/test_campaign_policy.py`
  > 延後（D-022）：只用自有網站、沒有付費廣告，沒有要設上限的花費。要買廣告時連同 `campaigns` 表一起做。
**T-704 · Distribution channel adapter #1**（依 Open Question 決定通路；介面 `DistributionChannel.publish(article, copy) -> external_ref`） / Deps: T-513 / Validate: `pytest -m integration tests/business/test_channel.py`
  > 方向（D-022）：Instagram / Threads，每週固定時間產出（adapter + 每週排程）。目前通路仍只有自有網站，開做時另立決策。
**T-705 · Finance Agent (read-only + BudgetProposal → approval)** / Deps: T-211, T-603 / AC: 無任何寫 transactions 的路徑 / Validate: `pytest tests/business/agents/test_finance.py`
  > 實作（D-031）：測試在 `tests/company/test_finance.py`（照 T-701 的前例：代理與財務是核心、不是某個事業領域）。提案就是 `AllocateBudget` 指令，財務角色送出一律等人審；每期 MEASURING 報表寫完後才問它。
**T-706 · Business Agent (OpportunityProposal → PROJECT_PROPOSED → HUMAN)** / Deps: T-211, T-604 / Validate: `pytest tests/business/agents/test_business.py`
  > 實作（D-032）：T-611 之後缺的是漏斗的起點——沒有任何東西會「發現」機會。商業代理每週在 EXECUTING 看一次市場，只能用 `DiscoverOpportunity`／`RecordOpportunitySignal` 記下機會與訊號（寫入時就檢查：公司數字要對得上報表、網頁要是這一輪自己抓的）；照 ARCHITECTURE_V2_1 §6，發現機會不經人審，人仍把關開事業與資本。測試在 `tests/company/test_market_watch.py`。
**T-707 · Revenue KPIs in snapshot & dashboard** / Deps: T-603, T-309 / Validate: `pytest tests/company/test_reporting_revenue.py && pnpm -F web test dashboard-revenue`
**T-708 · Phase 7 acceptance** / Validate: `pytest tests/acceptance/test_revenue.py`

## 3. Phase 8 — Multi-company

**T-801 · workflow_templates & model_policies 入 DB（載入器 + 快取）** / Deps: T-203, T-207
**T-802 · Company quotas + per-company cost caps** / Deps: T-209
**T-803 · Auth: users/memberships/roles; WS 與 REST 授權** / Deps: T-303
**T-804 · Redis: rate limiter + pub/sub（取代每 process LISTEN）** / Deps: T-303
**T-805 · Second domain package（research_company）** / Deps: T-514 as pattern / AC: 零修改 runtime/company/realtime
**T-806 · 3D Office: seatsForRole(n) 與多公司切換** / Deps: T-402
**T-807 · Phase 8 acceptance（兩公司隔離）** / Validate: `pytest tests/acceptance/test_multicompany.py`

---

## 4. Task 撰寫規則（給 coding agents）

- 單一責任；明確 input/output；列出 files；不得修改其他 domain。
- 每個 task 附 validation command；PR 必須附命令輸出。
- 修改 event payload 或 projection 的 task 必須同時更新 `packages/event-schema` 產生物與契約測試。
- 新增 tool 必須宣告 side_effect 與 produced 類型；新增 command 必須列入 Permission Matrix。
