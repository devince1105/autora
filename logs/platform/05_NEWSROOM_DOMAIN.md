# 05 — Newsroom Domain

> Newsroom 不在 Core。它透過 `domains/newsroom/register(runtime)` 掛上 workflow templates、tools、validators、
> agents、event handlers、activity_links。Core 的表沒有 newsroom 欄位。

---

## 1. Domain Model（Evidence-first）

```
Source ─poll─▶ SourceItem ─cluster/dedup─▶ Story
                  │                          │
                  └─fetch+snapshot─▶ Evidence │
                                       ▲     ▼
                        Claim ◀────────┘  ResearchNote / AnalysisNote
                          │
                          ▼
             ArticleVersion(zh-TW) + ArticleVersion(en)  [同 draft_group, 同 claim_ids]
                          │
                  FactCheckReport ─▶ Article(APPROVED) ─▶ Distribution ─▶ AnalyticsEvent → AnalyticsDaily
```

| 實體 | 定義 | 關鍵欄位 |
|---|---|---|
| **Source** | 可輪詢的資訊來源 | kind(rss/url_list/search_query), url, poll_interval, trust_level(0–1), language, status |
| **SourceItem** | 輪詢得到的一筆 | source_id, external_id, url, title, published_at, content_hash |
| **Evidence** | URL 在某時刻的快照 + 抽取文本 | url, retrieved_at, blob_key, extracted_text, text_hash, source_id |
| **EvidenceChunk** | 向量檢索單位 | evidence_id, seq, text, embedding |
| **Story** | 值得寫的主題（SourceItem 聚類） | title, summary, angle, state, score, project_id, source_item_ids[] |
| **Claim** | 可驗證的事實陳述 | story_id, text, claim_type(fact/number/quote/attribution/opinion), status |
| **ClaimEvidence** | Claim ↔ Evidence | claim_id, evidence_id, quote, quote_offset, support_type(supports/contradicts/context) |
| **Article** | 發布單位（一 story 一 article） | story_id, slug, title, state, primary_lang, published_langs[], current_version_id |
| **ArticleVersion** | 版本 × 語言 | article_id, version, lang, draft_group_id, body(blocks[{type,text,claim_ids[]}]), author_run_id, change_summary |
| **FactCheckReport** | 一次 fact-check | article_version_id, results[{claim_id, verdict, method, note}], passed, run_id |
| **Distribution** | 對某通路的一次發布 | article_id, channel(site/social_draft/…), external_ref, status, copy jsonb |
| **AnalyticsEvent / AnalyticsDaily** | beacon 原始 / 日彙總 | article_id, lang, event_type, session_hash, ts / date, views, uniques, read_complete |

**不做** `URL → LLM → Article`。每個 fact/number/quote claim 必須綁 Evidence；每個 article block 引用 claim_ids。

---

## 2. Workflow Template `story_to_article_v2`

```
research(Researcher) → analysis(Analyst) → draft(Writer) → review(Editor) → approve(human | auto by policy)
                                              ▲               │
                                              └── revise ≤2 ──┘
                                            → publish(Publisher service) → distribute(Marketing)
                                            → measure(Scheduler: +1h, +24h, +7d)
```

- 每個 Story 實例化一次；掛在 project 下。
- `approve` 節點 role = `human`（policy `newsroom.auto_approve_if_fact_check_passed=true` 時由 system 節點替代）。
- `publish`、`measure` 是 service 節點（無 LLM）。

---

## 3. Fact-check 三層

1. **結構（確定性）**：每個 fact/number/quote claim ≥1 `supports` ClaimEvidence；quote 是 `extracted_text` 子字串（正規化空白）；trust_level < 門檻的來源不可單獨支持。
2. **交叉（確定性 + 向量）**：對 claim 檢索其他 evidence chunks，若存在未解決的 `contradicts` → 不通過。
3. **語意（LLM）**：Editor 對通過 1、2 的 claim 判斷 quote 是否真的支持 claim（避免斷章取義）；雙語各做一次。

未通過 → `request_revision` → Writer 新 task（同 draft_group 的下一 version）。超過 2 次 → story DROPPED。

---

## 4. 雙語規則

- Writer 一次產出兩個 version（同 `draft_group_id`），blocks 引用同一組 `claim_ids`（validator）。
- Editor 一次審一個 draft_group（兩語同 verdict）。
- 發布規則是 policy（D-002）：`newsroom.primary_lang="zh-TW"`、`newsroom.langs=["zh-TW","en"]`、`newsroom.require_all_langs=true`。Writer 先寫 zh-TW，再以同一組 claims 產 en。
- 審批規則是 policy（D-001）：`newsroom.auto_approve_if_fact_check_passed=false`（MVP 人核准）。
- 公開站 `/{lang}/articles/{slug}`。

---

## 5. Tools（newsroom 註冊）

| tool | side_effect | produced | 說明 |
|---|---|---|---|
| web_search | read | — | **Tavily**（D-003）；介面 `SearchProvider.search(query, k, recency?) -> [{url, title, snippet, published_at?}]`；每次呼叫記 tool_cost；fixture 模式回 fixture。**結果只是候選，不是 Evidence**——必須經 `fetch_url` 快照 |
| fetch_url | write | evidence | httpx+trafilatura；`render=true` 走 Playwright（若安裝）；同 URL 同日 hash 去重 |
| read_evidence / search_evidence | read | — | 後者向量檢索 |
| create_claim / link_evidence | write | claim / claim_evidence | link 時驗證 quote 定位 |
| write_draft | write | article_version ×2 | 驗證 claim_ids |
| read_draft | read | — | |
| run_fact_check | write | fact_check_report | 層 1、2 + 產生層 3 的待判清單 |
| request_revision / accept_draft | write | — | 驅動 Article FSM |
| create_distribution | write | distribution | MVP 只寫 DB |

Publisher 是 Command handler（`PublishArticle`），不是 tool；由 workflow 的 service 節點呼叫。

---

## 6. Event handlers（newsroom 訂閱）

| Event | Handler |
|---|---|
| ARTICLE_PUBLISHED | 建立 measure 排程（+1h/+24h/+7d） |
| SOURCE_POLLED | 評分 → 建 Story 候選（不呼叫 LLM） |
| CYCLE_STARTED | 提供 `candidates` 給 snapshot（hook） |

---

## 7. activity_links hook

`activity_links(task, run) -> [Link]`：research/analysis → Story；draft/review → ArticleVersion；distribute → Article#distribution。寫入 `agent_activity.detail.links`，供 3D Office / Panel 使用。

---

## 8. 版權與隱私

- Evidence 快照只存 BlobStore（私有），不公開。
- 引文長度上限（validator），文章不得大段轉載；來源標註。
- beacon 不存 IP / PII；session_hash 日內輪換。
