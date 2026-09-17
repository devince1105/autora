# 06 — Newsroom Integration (AI Bilingual Newsroom)

> Newsroom 是第一個 Business Domain。它掛在 Core Runtime 上（`domains/newsroom/register(runtime)`），
> Core 對它一無所知。3D Office 透過 Agent 的 `activity.detail.links[]` 進入它的內容頁面。

---

## 1. MVP 的 6 個 Agent 與 Workflow

```
Workflow template: newsroom.story_to_article_v2   (一個 Story 一次實例化)

 research(Researcher) ─▶ analysis(Analyst) ─▶ draft(Writer) ─▶ review(Editor) ─▶ approve(human*) ─▶ publish(service) ─▶ distribute(Marketing)
                                                   ▲               │
                                                   └──revise(≤2)───┘
 CEO 不在此 DAG 內：CEO 在 Cycle PLANNING 決定「今天做哪些 story、幾篇」，在 REVIEWING 看結果。
 * approve 由 company policy 決定：MVP 人核准（D-001）；policy 可切為「fact_check passed → auto」，切換本身是 HUMAN action。
```

| Agent | Task | 輸入 | 輸出（schema） | 主要 tools | 3D 中的可見產物 |
|---|---|---|---|---|---|
| **Researcher** | `research` | story seed（title/urls/query）、target_sources | `ResearchNote{story_id, evidence_ids[], source_summaries[], suggested_angles[]}` | `web_search`(Tavily, D-003；結果僅候選), `fetch_url`(→Evidence), `read_evidence` | Sources 數（EVIDENCE_CAPTURED） |
| **Analyst** | `analysis` | ResearchNote | `AnalysisNote{claims[{text,type,evidence_refs[{evidence_id,quote}]}], angle, key_numbers[], contradictions[]}` | `read_evidence`, `search_evidence`(向量), `create_claim`, `link_evidence` | Claims 數（CLAIM_CREATED） |
| **Writer** | `draft` | AnalysisNote + claims | `ArticleDraft{article_id, versions:{zh-TW: version_id, en: version_id}}` | `write_draft`（blocks 帶 claim_ids；兩語言） | Draft 連結 |
| **Editor** | `review` | ArticleDraft | `EditorReview{verdict: accept|revise, issues[], fact_check_report_id}` | `read_draft`, `run_fact_check`(確定性 validators + 語意檢查), `request_revision`, `accept_draft` | Review issues、Fact-check 結果 |
| **Marketing** | `distribute` | published article | `DistributionPlan{channels[{channel:"site"|"social_draft", copy:{zh-TW,en}}]}` | `create_distribution`（MVP 只寫 DB：site + social 文案草稿，不對外發） | Distribution 記錄 |
| **CEO** | Cycle PLANNING / REVIEWING | CompanySnapshot | `CyclePlan` / `CycleReview` | `submit_command` | 今日目標、review 摘要 |

Fact-check 在 MVP 沒有獨立 agent：**確定性層**（每個 fact/number/quote claim 有 supports evidence、quote 可在快照定位、無未解矛盾）由 `run_fact_check` tool 執行；**語意層**由 Editor 在 review 步驟做。Phase 5 之後可拆出 FactChecker agent，DAG 加一個節點，其餘不變。

---

## 2. 雙語模型

- `articles` 一篇對應一個 story；`article_versions` 增加 `lang`（`zh-TW` / `en`）與 `translation_of_version_id`。
- Writer 一次產出兩個 version（同一 `draft_group_id`），兩者的 blocks 引用**同一組 claim_ids**——這保證雙語內容的事實基礎一致，fact-check 只需驗證 claims 一次，語意層對兩語言各做一次。
- Editor 的 review 針對 draft_group（兩語言同時 accept / revise）。
- 公開站 `/{lang}/articles/{slug}`；`ARTICLE_PUBLISHED.payload.langs = ["zh-TW","en"]`。
- 語言策略（哪個是主語言、是否允許只發一種）是 company policy，不是程式碼。

---

## 3. 資料流（Evidence-first）

```
Source ─poll─▶ SourceItem ─(CEO/plan 或 poller 評分)─▶ Story(DISCOVERED→SELECTED)
                                │
   Researcher: fetch_url ───────┴──▶ Evidence(快照+extracted_text) ─▶ evidence_chunks(embedding)
   Analyst:    create_claim + link_evidence ──▶ Claim ⇄ ClaimEvidence(quote, offset)
   Writer:     write_draft ──▶ ArticleVersion(zh-TW), ArticleVersion(en)  blocks[{type, text, claim_ids[]}]
   Editor:     run_fact_check ──▶ FactCheckReport ; accept_draft | request_revision
   human/auto: approve ──▶ Article.APPROVED
   Publisher:  publish ──▶ Article.PUBLISHED, Distribution(site)
   Marketing:  create_distribution ──▶ Distribution(social_draft)
   Site:       beacon ──▶ analytics_events ─hourly─▶ analytics_daily ─▶ KPI
```

每個 Claim 可追溯到 Evidence 快照；每個 Article block 可追溯到 Claim；每個 Evidence 可追溯到抓取它的 `TOOL_COMPLETED{produced}` 事件與 run。這就是「Agent → Task → Tool → Event → Output → Business Result」的鏈。

---

## 4. 從 3D Office 進入內容

`domains/newsroom/activity_links.py` 實作 hook `activity_links(task, run) -> [Link]`，Runtime 在更新 `agent_activity` 時呼叫並寫入 `detail.links`。

| Agent 點擊後 | Detail Panel 顯示（即時 + REST） | Deep links |
|---|---|---|
| Researcher | task、tool、`Sources: N`（來自 produced evidence 計數）、progress | Story → Sources / Evidence 列表（每筆可看快照文字） |
| Analyst | `Claims: M`、contradictions 數 | Story#claims（claim ↔ evidence quote 對照） |
| Writer | 進行中的 draft_group、sections 進度 | Article version（zh-TW / en 切換）、每段落的 claim 標記 |
| Editor | review verdict、issues、fact-check 通過率 | Article version + FactCheckReport |
| Marketing | distribution channels | Article#distribution |
| CEO | 今日 CyclePlan 摘要、pending approvals 數 | Cycle 頁（plan、review、timeline） |

「Next Step」欄位 = workflow template 中該 task 的下游節點名稱與角色（Runtime 給，非 LLM）。

---

## 5. 內容頁面（`apps/web/src/app/(admin)/newsroom/*`）

- `/newsroom/stories`、`/newsroom/stories/[id]`（sources、evidence、claims、timeline = `trace(correlation_id)`）
- `/newsroom/articles`、`/newsroom/articles/[id]`（versions、fact-check、distribution、analytics、trace）
- `/newsroom/approvals`（與 Dashboard 的 Approval Inbox 共用元件）
- 公開站 `(site)/[lang]/articles/[slug]`

這些頁面是普通 server-rendered 頁 + TanStack Query；它們不依賴 3D，也不依賴 WS（可選擇訂閱 realtime store 讓 timeline 自動更新）。

---

## 6. MVP 的 Simulation 模式（Demo）

- `FakeModelProvider`：依 `(role, task_name, attempt)` 回傳預先撰寫的結構化輸出（ResearchNote、AnalysisNote、雙語 draft、EditorReview…），含可設定的延遲與「第一次 review 要求 revise」的劇本分支。
- `FakeTools`：`web_search` 回傳 fixture 結果；`fetch_url` 讀本地 fixture HTML（仍走真實的 Evidence 建立、快照、chunk 流程）。
- 其餘全部真實：TaskManager、AgentRunner、PolicyEngine、Approvals、events、agent_activity、WS、site、beacon。
- 切換方式：`MODEL_PROVIDER=fake`、`TOOLS_PROFILE=fixture`。同一份 workflow template、同一套 schema。
- 這不是 fake agent：trace 中的每一筆 TOOL_CALLED 都真的執行了（只是工具的資料來源是 fixture）。

---

## 7. Newsroom 在 Cycle 中的位置（MVP 簡化）

| Cycle stage | Newsroom 行為 |
|---|---|
| PLANNING | CEO 讀 snapshot（含今日 SourceItems 摘要 top-N）→ `CyclePlan{stories:[{seed, priority}], target_articles}` → `instantiate_workflow(story_to_article_v2, story)` × N（受 `max_workflows_per_cycle`） |
| EXECUTING | DAG 推進；approval 進 inbox |
| MEASURING | analytics_daily、cost 結算、KPI |
| REVIEWING | CEO `CycleReview`；建議明日主題方向；kill criteria 由 governance 自動檢查 |

MVP 也提供手動入口 `POST /api/companies/{id}/workflows` (`template=story_to_article_v2`) 讓使用者「啟動一個 Research Task」而不必等 Cycle——這是 Command，走 PolicyEngine（human actor ALLOW）。
