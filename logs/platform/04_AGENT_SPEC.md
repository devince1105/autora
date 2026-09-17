# 04 — Agent Spec (MVP roles)

原則：**需要判斷的才是 Agent；其餘是 Service。** MVP LLM agents：CEO、Researcher、Analyst、Writer、Editor、Marketing。
Publisher、Analytics collector、Ledger、Fact-check 確定性層是 Service。FactChecker（獨立 agent）、Finance、Business、Developer 是 Phase 5+。

每個 agent 的 prompt 與 output schema 屬於 domain（`domains/newsroom/agents/*`）或 company（`company/agents/ceo`），Runtime 不知道它們的內容。

---

## 1. CEO（company）

| 項目 | 內容 |
|---|---|
| 觸發 | Cycle PLANNING、REVIEWING |
| 輸入 | CompanySnapshot |
| 輸出 | `CyclePlan` / `CycleReview`（見 02 §3） |
| tools | `submit_command`（create_cycle_goal, instantiate_workflow, pause_project, kill_project→HUMAN, allocate_budget≤門檻, update_strategy→HUMAN） |
| capabilities → alias | `reasoning → frontier` |
| budget | per_run $0.50、max_steps 6 |
| validators | workflows ≤ max_workflows_per_cycle；預算 ≤ 可用；project 必須 ACTIVE；decision ∈ enum |
| 失敗 | fallback plan |

## 2. Researcher（newsroom）

| 項目 | 內容 |
|---|---|
| task | `research` |
| 輸入 | story seed {title, urls[], query}, target_sources |
| 輸出 | `ResearchNote{story_id, evidence_ids[], source_summaries[{evidence_id, summary, trust}], suggested_angles[]}` |
| tools | `web_search`, `fetch_url`(→Evidence, produced), `read_evidence` |
| alias | `research_extraction → frontier`（P2 可降 fast） |
| budget | per_run $0.40、max_steps 25 |
| validators | evidence_ids 全部存在且屬於此 story；≥ min_sources |
| progress | `{label:"sources", current, target}` |

## 3. Analyst（newsroom）

| 項目 | 內容 |
|---|---|
| task | `analysis` |
| 輸入 | ResearchNote |
| 輸出 | `AnalysisNote{claims[{text, claim_type, evidence_refs[{evidence_id, quote}]}], angle, key_numbers[], contradictions[]}` |
| tools | `read_evidence`, `search_evidence`(向量), `create_claim`, `link_evidence`(quote 定位驗證) |
| alias | `reasoning → frontier` |
| budget | per_run $0.40、max_steps 30 |
| validators | 每個 fact/number/quote claim ≥1 supports evidence；quote 為 extracted_text 子字串 |
| progress | `{label:"claims", current}` |

## 4. Writer（newsroom）

| 項目 | 內容 |
|---|---|
| task | `draft`（含 revise） |
| 輸入 | AnalysisNote + claims；revise 時另附 issues[] |
| 輸出 | `ArticleDraft{article_id, draft_group_id, versions:{zh-TW: version_id, en: version_id}}` |
| tools | `write_draft`（blocks[{type, text, claim_ids[]}] × 2 langs） |
| alias | `drafting → frontier` |
| budget | per_run $0.60、max_steps 8 |
| validators | 兩語 claim_ids 集合相同；每個 fact block 至少一個 claim_id；引文長度上限；無未知 claim_id |
| progress | `{label:"sections", current, target}` |

## 5. Editor（newsroom）

| 項目 | 內容 |
|---|---|
| task | `review` |
| 輸入 | ArticleDraft（draft_group） |
| 輸出 | `EditorReview{verdict: accept|revise, issues[{block_ref, lang, kind, message}], fact_check_report_id}` |
| tools | `read_draft`, `run_fact_check`(確定性層 + 觸發語意層問題清單), `request_revision`, `accept_draft` |
| alias | `editing → frontier`, `verification → frontier` |
| budget | per_run $0.40、max_steps 12 |
| validators | verdict=accept 必須 fact_check passed；revise 必須有 ≥1 issue |
| 規則 | revise 次數由 workflow 控制（≤2） |

## 6. Marketing（newsroom，MVP 限縮）

| 項目 | 內容 |
|---|---|
| task | `distribute` |
| 輸入 | published article |
| 輸出 | `DistributionPlan{channels[{channel: site|social_draft, copy:{zh-TW, en}}]}` |
| tools | `create_distribution`（MVP 只寫 DB） |
| alias | `drafting → fast`（P2）/ frontier（MVP） |
| budget | per_run $0.15、max_steps 4 |
| 權限 | 只能 distribute 已 PUBLISHED 的 article；`spend_ad_budget` LIMITED → 超過 HUMAN（P5） |

## 7. Phase 5+ agents（規格佔位）

| Agent | 觸發 | 輸出 | 權限要點 |
|---|---|---|---|
| FactChecker | review 前獨立節點 | FactCheckReport | 只讀 + 標註 |
| Finance | Cycle MEASURING 後 | BudgetProposal | 只讀報表；提案走 HUMAN |
| Business | 每週 | OpportunityProposal[] | 只讀；建 project → HUMAN |
| Developer（SaaS company） | project task | Code change proposal | 部署 = irreversible → HUMAN |

---

## 8. 共通規則

- Agent 程式碼裡沒有 model id、沒有 SQL、沒有直接 HTTP；一切透過 tools 與 gateway。
- 每個 agent 的 output schema 是 pydantic model；validators 是純函式並有單元測試。
- prompt 版本化（`prompts/<role>/<version>.md`），run 記錄 prompt hash。
- Simulation 模式下，FakeModelProvider 依 `(role, task_name, attempt)` 回 fixture；agent 程式碼不變。
