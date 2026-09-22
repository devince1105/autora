# 16 — Risks & Open Questions (Platform)

3D / Realtime 專屬風險見 `3d-office/12_RISKS.md`。

---

## 1. 風險

| # | 風險 | 嚴重度 | 緩解 |
|---|---|---|---|
| R1 | **成本失控**：長時間自主運作累積 token 費用 | P0 | 三層預算硬上限、每日 cap、CostGuard 呼叫前預留、每日成本摘要、simulation 零成本 |
| R2 | **內容品質 / 幻覺 / 斷章取義** | P0 | Evidence-first、三層 fact-check、MVP 人審核、revise 上限、低 trust 來源不可單獨支持 |
| R3 | **版權與轉載** | P0 | 快照僅內部；引文長度 validator；來源標註；3D 資產授權記錄 |
| R4 | **CEO 提案品質** | P1 | 決策空間 schema 限制；kill criteria 確定性；人可覆寫且理由回饋 |
| R5 | **來源生態變化**（RSS 消失、反爬） | P1 | 健康度監控、失敗自動 paused、Playwright 後備 |
| R6 | **Structured output 不穩定** | P1 | schema 驗證 + repair；provider 差異封裝 |
| R7 | **Postgres-as-queue 極限** | P2 | 每日 <1000 tasks 遠低於瓶頸；Phase 8 前引入 Redis |
| R8 | **單一供應商中斷** | P2 | Router fallback alias；第二 provider adapter |
| R9 | **Modular monolith 邊界腐蝕** | P2 | import-linter 進 CI；P-11 |
| R10 | **Analytics 隱私** | P2 | 無 IP/PII；session hash 日內輪換 |
| R11 | **Agent 記憶膨脹** | P2 | agent_memory TTL + 筆數上限；context token 上限 |
| R12 | **雙語事實漂移** | P1 | 共用 claim_ids validator；語意層各做一次 |

## 2. Open Questions

**P0（Phase 1 前）** — 已決定者見 `logs/DECISIONS.md`
1. 發布通路：只用自有網站是否接受？若一開始就要外部通路，Distribution adapter 提前到 Phase 5。— ✅ **只用自有網站（D-022）**；之後方向是 Instagram / Threads 每週固定時間產出（T-704）
2. Web search 供應商。— ✅ **Tavily（D-003）**
3. Embedding 供應商。— **未決，暫用抽象 `embed` alias + OpenAI-compatible adapter**
4. 文章主語言、垂直領域、是否允許只發一種語言。— ✅ **主語言 zh-TW（D-002）**；垂直領域與單語發布仍未決，暫用「兩語皆需」
5. 每日預算數量級。— **未決，暫用 $10/day、max_workflows_per_cycle=5**
6. MVP 的發布審批是否一定要人。— ✅ **人核准（D-001）**

**P1（Phase 6–7）**
7. 第一條營收模型（訂閱 / 贊助 / 聯盟 / API）。— ✅ **一次付款買一年使用權，統一金流 PAYUNi（D-024，取代 D-022 的 Stripe 訂閱）**
8. 人類審批可接受延遲（影響 stage deadline）。
9. 是否需要圖片（圖片生成成本與 asset 類型）。
10. 幣別 / 稅務是否需區分。— ✅ **主幣別新台幣；模型費用仍以美元計量，結算時以固定匯率換算（D-023）**；稅務未處理

**P2**
11. 第二家公司類型（Research Company 最省事；SaaS Company 需 Developer Agent 與部署工具）。
12. 是否需要 agent 績效影響 model_policy / 派工。
13. 多人操作與審批權限。

## 3. 明確拒絕的方案

- LLM 驅動 control flow / 動態 DAG（MVP）。
- Event sourcing、CQRS。
- 微服務、K8s、Kafka、Temporal（MVP–Phase 7）。
- Finance Agent 可寫財務表。
- 前端推導 agent 狀態。
- 「先跑起來再加治理」。

## 4. 如果我是 CTO，現在會先做的 5 件事

1. 回答 P0 Open Questions 1–6。
2. 先做 T-202 + T-211 + T-209（任務 → agent → 成本主幹），第一週接真實 Anthropic API 跑 EchoWorkflow。
3. 手動（不用程式）對一個真實題目走一次 Evidence → Claim → 雙語 Article，驗證 domain model 的粒度。
4. 寫下第一版 `company_policies` 與 kill criteria 的實際數字。
5. 把 import-linter、ESLint 邊界、schema 產生一致性檢查放進第一個 CI。
