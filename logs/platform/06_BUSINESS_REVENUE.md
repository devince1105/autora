# 06 — Business / Revenue Layer

---

## 1. 實體

| 實體 | Phase | 定義 |
|---|---|---|
| **Budget** | MVP | 分配給 project 的信封：amount, period(cycle/month), spent(derived), hard_cap |
| **Transaction** | MVP | 唯一財務事實表：kind(expense/revenue/capital_in/capital_out/transfer), amount, currency, category(model_cost/tool_cost/ads/subscription/sponsorship/…), project_id?, ref_type/ref_id, occurred_at, source(system/human/integration), idempotency_key |
| **Revenue / Expense** | MVP | `transactions` 的視圖 |
| **CapitalAllocation** | MVP | `AllocateBudget` command 的結果（budgets row + transfer transaction） |
| **KPI / ROI** | MVP | `kpi_snapshots`：per project per cycle：cost, revenue, views, published_count, cost_per_article, views_per_cost_unit, revisions_rate（金額一律是主幣別新台幣，D-023） |
| **Product** | P5 | kind(subscription/one_time/sponsorship), price, status |
| **Customer / Lead / Opportunity** | P5 | Lead(來源、狀態) → Opportunity(金額、機率) → Customer |
| **Subscription / Order / Payment** | P5 | Payment 一律由外部整合 webhook 寫入；Agent 只能讀 |
| **Campaign / CampaignSpend** | P5 | Marketing 的預算信封與花費 |

---

## 2. Business Command Pipeline

```
Agent 呼叫 tool submit_command(CommandName, payload)
  → Command（pydantic，強型別）
  → PolicyEngine.decide(actor, command) → ALLOW | DENY | NEEDS_APPROVAL | LIMITED(cap)
  → Validator：領域不變量（預算不可負、project 必須 ACTIVE、article 必須 APPROVED 才能 publish、冪等鍵唯一）
  → Mutation：單一 TX 寫狀態表 + commands_log + events(outbox)
  → EventBus dispatch（含 realtime）
```

Agent 對 `transactions` / `budgets` / `projects` 沒有任何直接寫入工具。人類操作（UI）走同一 pipeline，actor=human。

MVP Commands：`CreateProject`(HUMAN)、`AllocateBudget`(CEO LIMITED / HUMAN)、`PauseProject`、`ResumeProject`(HUMAN)、`KillProject`(HUMAN)、`UpdateStrategy`(HUMAN)、`CreateCycleGoal`、`InstantiateWorkflow`、`PublishArticle`(system/human)、`RecordTransaction`(HUMAN / integration)、`DecideApproval`(HUMAN)。

---

## 3. Ledger Service

- 唯一可寫 `transactions` 的模組。
- `settle_cycle_costs(cycle_id)`：Σ model_calls + tool costs（按 project 分組）→ expense transactions（每 project 每 cycle 一筆，idempotency_key = `cycle:project:model_cost`）。
- `balance(company)`、`budget_spent(project, period)`。
- P5：`record_payment(webhook)` → revenue transaction + PAYMENT_RECEIVED。

---

## 4. CEO 決策的資訊來源

Reporting → CompanySnapshot（見 02 §3）。趨勢由 Reporting 算（7d、last cycle），CEO 解讀。
決策空間受 `CycleReview` schema 限制：`continue | modify(params) | pause | kill_proposal`。
自動 pause 由 kill criteria 確定性觸發；kill 一律 HUMAN。

---

## 5. Phase 5 營收路徑（待 Open Question）

候選：付費訂閱（Stripe webhook）、贊助（手動 RecordTransaction）、聯盟連結（分析回填）、內容 API。
無論哪條：Payment 由整合寫入、Marketing 的 ad spend 有 campaign cap、超過 → HUMAN。
Phase 1–4 就要把成本側做對（model_calls → expense → project ROI），否則 Phase 5 沒有分母。
