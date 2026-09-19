# 開發紀錄 05 — 階段 5 Newsroom（AI 雙語新聞室）

- 期間：2026-09-19 起（進行中）
- 目標：6 個代理的 `story_to_article_v2` 真實跑通（真模型與模擬兩種模式），雙語文章發布到公開站，從 3D 辦公室可以進入每個產物。
- 驗收條件（`logs/3d-office/09_DEVELOPMENT_ROADMAP.md` 階段 5）：`logs/3d-office/11_MVP_ACCEPTANCE.md` 的 AC-1 ～ AC-10；每條在 `MODEL_PROVIDER=fake` 下都要通過，真模型跑 AC-2、AC-5 的 smoke。
- 設計依據：`platform/05_NEWSROOM_DOMAIN.md`（領域模型、流程、fact-check 三層、雙語規則、工具）、`3d-office/06_NEWSROOM_INTEGRATION.md`（6 個代理、資料流、從 3D 進入內容、模擬模式）、`platform/10_DATABASE_SCHEMA.md`（Phase 5 表）。
- 相關決策：D-001（發布先由人核准）、D-002（主語言 zh-TW，en 第二語言，兩語都通過才發布）、D-003（web_search 用 Tavily，結果只是候選，Evidence 必須由 `fetch_url` 快照）、D-006（模型 `z-ai/glm-5.3-flash`）。
- 使用模型：Claude Opus 5（未另行指定前）。

## 開發計畫

任務來自 `3d-office/10_TASK_BREAKDOWN.md`。Newsroom 不進 Core：一律透過 `domains/newsroom` 註冊（工具、代理行為、流程範本、事件、activity links），由 `autora/app.py` 組裝；`runtime` 不可 import `domains`（import-linter 已強制）。

| 任務 | 內容 | 依賴 | 狀態 |
|---|---|---|---|
| T-500 | `web_search` 工具：Tavily adapter + fixture 模式（`SearchProvider` 介面、金鑰只在環境變數、逾時、速率限制、每次呼叫記成本） | T-204、T-209 | ✅ |
| T-501 | Sources 與 poller（models、RSS / URL 輪詢、去重） | T-212 | ✅ |
| T-502 | `fetch_url` 工具 + Evidence + 快照（BlobStore） | T-204、T-210 | ✅ |
| T-503 | `evidence_chunks` + embedding（pgvector） | T-502、T-207 | ✅ |
| T-504 | Stories + 去重 | T-501、T-503 | ✅ |
| T-505 | Claims / ClaimEvidence + 工具（`create_claim`、`link_evidence`，引文定位） | T-504 | ✅ |
| T-506 | Researcher 代理（提示、`ResearchNote` schema、validators） | T-211、T-500、T-502 | ✅ |
| T-507 | Analyst 代理（`AnalysisNote`、claims） | T-505 | ✅ |
| T-508 | Articles / ArticleVersions（lang、draft_group）+ `write_draft` 工具；雙語 claim 集合一致 | T-504 | ✅ |
| T-509 | Writer 代理（雙語草稿） | T-507、T-508 | ✅ |
| T-510 | 確定性 fact-check validators + `run_fact_check` 工具 | T-505、T-508 | ✅ |
| T-511 | Editor 代理（`EditorReview`、`request_revision` / `accept_draft`） | T-509、T-510 | ✅ |
| T-512 | Publisher（`PublishArticle` command、冪等、Distribution site） | T-508、T-205 | ✅ |
| T-513 | Marketing 代理（`DistributionPlan`、`create_distribution`：只寫 DB） | T-512 | ✅ |
| T-514 | `story_to_article_v2` 範本 + `register()` + `activity_links` | T-203、T-506 ～ T-513 | ✅ |
| T-515 | 公開站（zh-TW / en）+ beacon API | T-512 | ✅ |
| T-516 | Analytics collector（每小時 → `analytics_daily`，事件） | T-515、T-212 | ⏳ |
| T-517 | Newsroom 管理頁（stories、articles、versions、fact-check、distribution、timeline） | T-308、T-311 | ⏳ |
| T-518 | 模擬資料（FakeModelProvider 劇本 + fixture HTML + revise 分支） | T-207、T-514 | ✅ |
| T-519 | 真模型 smoke | T-208、T-514 | ⏳ |
| T-520 | 階段 5 E2E（3D → Writer → 草稿頁） | T-411、T-517、T-518 | ⏳ |

建議順序（依賴與「先有資料層、再接代理」）：
T-500 → T-501 → T-502 → T-503 → T-504 → T-505 → T-508 → T-510 → T-512（資料層與服務，都可用確定性測試驗證）→ T-506 → T-507 → T-509 → T-511 → T-513（代理，依流程順序）→ T-514 → T-518（整條流程在模擬模式跑通）→ T-515 → T-516 → T-517 → T-520 → T-519（真模型最後跑，花費最少）。

### 進入條件與帶過來的事項

- **進入條件**：「階段 4 驗收 + 版權確認的 3D 資產」。
  - ✅ 階段 4 於 2026-09-19 驗收通過（`04_PHASE_4.md` 的驗收表：2 小時長時間測試、面板 4～7 毫秒）。
  - ✅ 3D 資產只有 Kenney Mini Characters（CC0），每個檔案的來源與授權記在 `office3d/assets/LICENSES.md`，`check-assets` 在 CI 檢查（T-404、D-008）；辦公室場景全部是程式產生的幾何（D-010）。
- 帶過來的：
  - `TOOLS_PROFILE`、`TAVILY_API_KEY` 設定欄位在 T-209 就已存在（`TOOLS_PROFILE=live` 沒有金鑰會拒絕啟動）；**`.env` 目前沒有 `TAVILY_API_KEY`**，所以 Tavily 的真實呼叫測試（integration）要等使用者填入金鑰才能跑，其餘都用 fixture。
  - 模型：NVIDIA `z-ai/glm-5.3-flash`（D-006），免費端點有速率限制；真模型測試放在最後（T-519）。
  - **Embedding 供應者尚未決定**（`DECISIONS.md` Q-embed，暫用預設：以任一 OpenAI 相容的 embedding 端點實作 adapter，換供應者只改設定）。T-503 前要確認；NVIDIA Build 也有 OpenAI 相容的 embedding 模型，可沿用現有金鑰。→ T-503 決定（D-012）。
  - 資料庫映像已是 `pgvector/pgvector:pg16`，不需要換。
  - `domains/newsroom/policy.py`（T-205 的權限規則）已存在；`autora/app.py` 已預留 newsroom 的事件、範本、工具、行為與模擬的註冊位置。
  - `activity.detail.links` 的消費端（代理面板的連結）在階段 3 已預留，T-514 開始有資料。

---

## T-500 · `web_search`（Tavily adapter + fixture 模式）

### 做了什麼
- `autora/infra/search/`：`SearchProvider` 介面（`search(query, k, recency_days) -> SearchResponse{results, provider, cost_usd}`）與兩種錯誤——`SearchUnavailable`（逾時、429、5xx，值得重試）、`SearchRejected`（400 / 401 / 422 / 432 / 433：金鑰錯、額度用完、參數錯，重試沒用）。放在 infra 層，因為它只是對外部服務的 adapter，不知道新聞室。
  - `TavilySearchProvider`：照 Tavily 目前的 API 文件（2026-09-19 查證）以 `Authorization: Bearer` 送金鑰，金鑰不放在 body、不出現在任何錯誤訊息；每次呼叫有逾時；單一程序內的滑動視窗速率限制（滿了就等空位，要等太久就以可重試的錯誤結束）；`include_usage` 取得實際用掉的 credits，沒有就依深度（basic 1、advanced 2）計算，乘上 `TAVILY_COST_PER_CREDIT` 得到成本；摘要截到 1,000 字（搜尋結果是摘要，不是全文）；`recency_days` 轉成 Tavily 的 `time_range`。
  - `FixtureSearchProvider`：離線語料，依查詢詞重疊排序（英文依單字、中文依單字元），**沒有符合就回空結果，不會捏造網頁**。
- 新聞室的離線語料 `domains/newsroom/fixtures/search.json`：虛構的「流明市（Lumen City）微電網」題材，中英混合、含一則數字不一致的分析（之後給 fact-check 的矛盾情境用）與一則無關的新聞；網址都在不存在的 `*.fixtures.autora.test`，不會和真實新聞混淆。
- `domains/newsroom/tools/search.py`：`web_search` 工具（read、可重試、20 秒逾時、參數有上限：k 1～10、recency 1～365 天）。**不寫資料、`produced` 永遠是空的**；輸出附上「這些是候選不是證據，要用請 `fetch_url`」，每次呼叫都提醒模型（D-003）。成本記在 `TOOL_COMPLETED.cost_usd`。
- 組裝：`autora/app.py` 的 `build_search_provider` 依 `TOOLS_PROFILE` 選 Tavily 或離線語料；`build_tools` 註冊新聞室工具（工作程序的設定會傳進去）。新設定 `TAVILY_SEARCH_DEPTH`、`TAVILY_COST_PER_CREDIT`、`TAVILY_TIMEOUT_SECONDS`、`TAVILY_REQUESTS_PER_MINUTE`（`.env.example` 有說明）。
- 兩個通用的小改動：
  - **工具登錄表**：工具丟出的例外若自帶 `retryable`，以它為準（原本一律用工具的預設值：金鑰錯誤也會被重試）。
  - **設定**：`.env` 裡留空的金鑰（`TAVILY_API_KEY=`，照 `.env.example` 複製來的）現在等於沒設。原本空字串會被當成有金鑰——這是寫 T-500 的整合測試時發現的：測試沒有跳過，而是執行並失敗（用空金鑰向 Tavily 送出一次請求；沒有有效金鑰，不會計費）。三個金鑰欄位（Anthropic、NVIDIA、Tavily）都套用。

### 驗證
- `tests/newsroom/test_search.py`（22 個）：Tavily 的請求內容與標頭（金鑰只在標頭）、結果解析（兩種日期格式、無法解析的日期丟掉、摘要截斷）、recency 對應、成本（有 / 沒有 usage、basic / advanced）、各種錯誤碼的分類與訊息不含金鑰、逾時；速率限制（等空位、等太久放棄）；離線語料的排序、中文查詢、不捏造、recency；工具經過登錄表：`TOOL_COMPLETED` 帶成本、`produced` 為空、失敗時 `will_retry` 依錯誤種類、參數上限；依設定選 provider。Tavily 都用 mock transport，不連網。
- `tests/newsroom/test_tavily.py`（integration，需要 `TAVILY_API_KEY`）：一次真實搜尋、成本記錄、沒有產生 Evidence。當時 `.env` 沒有金鑰，確認會跳過；**使用者在 T-502 期間填入金鑰後實際執行通過**（basic 搜尋 1 次，成本 0.008 美元記在 `TOOL_COMPLETED`）。
- `tests/infra/test_settings.py`、`tests/runtime/test_tools.py` 各加一個測試（空白金鑰、例外自帶 retryable）。
- 後端 678 個測試通過；`ruff check`、`ruff format`、`lint-imports`（2 個合約）通過。

---

## T-501 · 新聞來源與輪詢

### 做了什麼
- **資料表**（migration `0012`，`domains/newsroom/models.py`）：
  - `sources`：種類 `rss`（RSS / Atom 網址）、`url_list`（`config.urls`，要追蹤的網頁）、`search_query`（`config.query`，用 T-500 的搜尋供應者）；`trust_level` 0～1（T-510 查核用：信任度低的來源不能單獨支持一個主張）、語言、輪詢間隔（至少 60 秒）、狀態 `active` / `paused`、`next_poll_at`、連續失敗次數與最後錯誤。
  - `source_items`：每個來源依 `external_id` 與 `content_hash`（標準網址 + 標題）各有唯一限制，同一則再被列出不會變成新項目。
- **標準網址**（`canonical_url`）：scheme 與主機轉小寫、去掉預設埠、去掉 `#` 片段、去掉追蹤參數（`utm_*`、`fbclid`、`gclid` 等）、去掉結尾斜線。同一頁不同寫法算同一頁。**跨來源**的重複（兩個來源連到同一篇）留給 T-504 的故事分群。
- **讀 feed**（`domains/newsroom/feeds.py`）：只用標準函式庫，支援 RSS 2.0、RSS 1.0（RDF）、Atom。feed 是不可信的輸入：**有 DTD 或 entity 宣告的文件一律拒絕**（XML 炸彈），HTML 摘要去標籤、解碼、截到 1,000 字，沒有連結的項目略過。
- **抓網頁的共用元件**（`infra/http`，T-502 的 `fetch_url` 也會用）：
  - `HttpFetcher`：只允許 http / https，有逾時、大小上限（預設 5 MB）、轉址次數上限，**拒絕私有位址**（連線前先解析網域，loopback、內網、link-local、保留位址、IPv4 對應的 IPv6 都拒絕；每次轉址重新檢查）。不做這個檢查的話，模型或 feed 裡的網址可以讓伺服器去讀 `localhost:8000` 的 API 或雲端的 metadata 端點。已知限制：檢查與實際連線之間仍有 DNS 重綁的空隙，要完全封住需要固定解析結果連線，列為之後的強化。
  - `FixtureFetcher`：依 `fixtures/routes.json` 把網址對應到本地檔案，其他網址回 404，檔案不能跑出 fixture 目錄。
- **輪詢**（`domains/newsroom/sources.py` 的 `SourcePoller`）：
  - 每家公司一個排程 `newsroom.poll_sources`（每 5 分鐘），觸發時輪詢該公司「到期、啟用中」的來源（每次最多 20 個、同時 4 個）。新增第一個來源時自動建立排程（`add_source`）。
  - 分兩段：先並行抓取（不寫資料），再寫入新項目、發事件、把 `next_poll_at` 往後移。**等網路時不發事件**，所以不會占住公司的事件鎖。
  - 每次最多收 50 則、由新到舊，第一次讀大型 feed 時不會洗版時間軸。
  - 事件：每則新項目一個 `SOURCE_ITEM_DISCOVERED`、每次輪詢一個 `SOURCE_POLLED`（新增幾則、共列出幾則、成本、錯誤）；失敗會記錄並在下個間隔重試，**連續 5 次失敗自動暫停**並發 `SOURCE_PAUSED`；成功一次就歸零。`search_query` 來源的搜尋成本記在 `SOURCE_POLLED.cost_usd`。
  - 模式與工具相同：`TOOLS_PROFILE=fixture` 讀 `fixtures/feeds/` 裡的虛構 feed（RSS 與 Atom 各一，含追蹤參數、重複、沒有連結的項目等情況），`live` 才連網。
- 組裝：`autora/app.py` 新增 `build_page_fetcher`、`build_scheduler`（工作程序的排程器現在會註冊新聞室的處理器）；設定 `FETCH_TIMEOUT_SECONDS`、`FETCH_MAX_BYTES`。
- 前端：事件型別重新產生（`make gen-schema`）；時間軸與軌跡頁加上三個新事件的中文說明（「讀取來源：新增 2 則・共 5 則」、「來源讀取失敗」、「來源暫停」）。
- **還沒有的**：新增 / 管理來源的 API 與頁面（計畫中的 `/api/sources` 與管理頁屬於 T-517）；在那之前只能從程式呼叫 `add_source`。

### 驗證
- `tests/newsroom/test_sources.py`（31 個）：
  - feed：RSS、Atom、RDF 的解析；DTD / entity、壞 XML、不是 feed 的文件都拒絕。
  - 標準網址與 `content_hash`。
  - 新增來源的檢查（錯誤的 scheme、空清單、`javascript:` 網址、空查詢、k 超出範圍、未知種類）與排程只建一個。
  - 輪詢：RSS 第一次 3 則新項目（追蹤參數已去掉、由新到舊）、第二次 0 則；網址清單裡 `/a` 與 `/a/` 算一則；搜尋來源（fixture 不計費、付費的記成本）；上限只留最新的；失敗記錄、連續 3 次（測試設定）暫停、成功後歸零；`poll_due` 只處理到期且啟用中的來源。
  - **經過排程器的端到端**：工作程序的排程器觸發 `newsroom.poll_sources`，項目寫入資料庫（這個測試會 commit，結束時停用自己的排程，避免影響其他排程測試）。
  - 抓網頁：轉址、`localhost`、`127.0.0.1`、`10.x`、`169.254.169.254`、`[::1]`、`[::ffff:127.0.0.1]`、`file://`、`ftp://` 都在連線前拒絕；轉址到內網拒絕；大小上限、404、503、429、轉址迴圈；fixture 的 404 與路徑跳脫。
- 寫測試時抓到自己的錯：網址清單的項目 ID 與標題原本直接用原始網址，`/a` 與 `/a/` 被當成兩則。改成先轉標準網址。
- 後端 709 個測試通過；`ruff`、`lint-imports`、`make db-check`（模型與 migration 一致）通過；前端 event-schema 8 個、web 208 個測試與 typecheck、lint 通過。

---

## T-502 · `fetch_url` 與 Evidence

### 做了什麼
- **資料表 `evidence`**（migration `0013`）：網址（標準形式）、最後落點、標題、內容類型、語言、擷取時間與日期、BlobStore 的快照位置、抽出的本文與其雜湊、是否截斷，以及來源（若某個來源列過這個網址，信任度就跟著它）、任務、執行。**同一家公司、同一網址、同一天、同樣內容只有一筆**（唯一限制），再抓一次就重用。
- **抽本文**（`domains/newsroom/extract.py`）：用標準函式庫的 `html.parser`，不另裝擷取套件、也不用模型——同一頁永遠得到同樣的文字，之後引文才能精確定位（T-505）。去掉 script、style、導覽、頁首頁尾、側欄、表單；頁面有 `<article>` / `<main>` 就只取那段；區塊元素變成段落（段落間空一行），HTML 原始碼裡的換行只是排版、當成空格；編碼依序看回應標頭、`<meta charset>`、預設 UTF-8（未知編碼名稱退回 UTF-8）；純文字直接分段。
  - 沒有用設計文件提到的 trafilatura：新增套件要下載，而標準函式庫版本已足以處理新聞頁；之後若遇到抽不好的真實網站再評估。
- **`fetch_url` 工具**（write、可重試、30 秒）：抓網頁（共用 T-501 的抓取元件：只抓公開網址）→ 原始內容存進 BlobStore（`evidence/<公司>/<年>/<月>/<日>/<id>.snapshot`，私有、不公開）→ 抽本文（上限 20 萬字，超過截斷並標記）→ 寫入 `evidence`、`TOOL_COMPLETED`，新的快照另發 `EVIDENCE_CAPTURED`，都在同一個交易。回傳 evidence_id、前 1,500 字摘錄，並提醒模型「引文必須與證據文字完全一致」。
  - 重用時也回報 `produced: evidence`：代理確實用了這份證據，AC-5 面板上的「Sources」計數才正確。
  - 不能成為證據的直接失敗、不重試：非文字內容（PDF、圖片）、沒有可讀文字的頁面（多半要執行 JavaScript）、私有位址、4xx。
  - `render=true`（執行 JavaScript 後再讀）目前沒有 Playwright 可用：照常讀取並在結果中說明，不假裝有執行。
- **`read_evidence` 工具**（read）：分段讀證據全文（offset、limit 最多 8,000 字、`next_offset`），只能讀自己公司的證據。
- `FixtureFetcher` 像真的伺服器一樣忽略網址的查詢參數與 `#` 片段（沒有路由特別指定時）；離線頁面 `fixtures/pages/`：搜尋結果與 feed 背後的 4 個虛構新聞頁（外面包著導覽、script、側欄、頁尾），另有一個需要 JavaScript 的頁面和一個 PDF。
- 組裝：`build_tools` 會帶入 BlobStore（工作程序用設定的目錄）；時間軸加上「擷取證據」的說明。

### 過程中的問題
- 測試用了隨便產生的執行 ID，`evidence.run_id` 有外鍵，寫入失敗。改用測試共用的 `running_agent_run` 建立真的代理、任務與執行——也順便驗證了事件上帶著正確的 run、task、agent。
- 第一版測試用 `add_source` 建來源，它會建立真的輪詢排程，而其他排程測試會觸發資料庫裡所有到期的排程；改成直接建立來源資料列。
- 已知限制：快照先寫進 BlobStore 再寫資料庫；資料庫交易若失敗，會留下一個沒有被引用的快照檔（不影響正確性，之後可加清理）。

### 驗證
- `tests/newsroom/test_evidence.py`（12 個）：抽本文（去掉周邊雜訊、段落切分、中文頁、og:title / h1 當標題、沒有 article 時用 body、Big5 與未知編碼、純文字）；`fetch_url` 建立證據與快照（帶追蹤參數的網址存成標準網址、快照內容與原頁相同、連到列過它的來源、事件帶 run / task / agent）；同一天同內容重用、隔天重新擷取；PDF、需要 JavaScript 的頁面、404 都失敗且不重試；截斷；`read_evidence` 分段與只能讀自己公司的。
- **真實連網（integration）**：`test_fetch_live.py` 抓 `https://example.com`（免費）通過——真實的 DNS 公開位址檢查、串流讀取、抽取、快照；`test_tavily.py` 的真實搜尋也通過（見 T-500）。
- 後端 721 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 208 個測試與 typecheck、lint 通過。

---

## T-503 · 證據分段與向量搜尋

### 決定 embedding 模型（D-012）
- 使用者同意沿用 NVIDIA 的建議。先列出帳號可用的 embedding 模型（免費的模型清單查詢），再實際試兩個：`nvidia/llama-3.2-nv-embedqa-1b-v1` 對這個帳號不可用（404）；`nvidia/nemotron-3-embed-1b` 可用，而且**跨中英檢索有效**：中文問句「停電時微電網可以供電多久？」對英文相關段落 0.51、中文相關段落 0.58、無關的咖啡價格 −0.01。
- 它只提供 2048 維（試了 `dimensions` 1024 / 768，伺服器回「只能 2048」）。pgvector 的 `vector` 型別索引上限是 2000 維，所以改用 **`halfvec(2048)`**（16 位元浮點，索引上限 4000 維、儲存減半）；資料庫的 pgvector 是 0.8.6，支援。
- 沒有安裝 Python 的 `pgvector` 套件：自己寫一個 SQLAlchemy 型別（`db/vector.py`），向量以文字形式傳送、在 SQL 裡轉型，asyncpg 不需要額外的編碼器。

### 做了什麼
- **`embed` binding**（`runtime/models/embeddings.py`，屬於執行層，不知道新聞室）：`Embedder` 把文字分批（每批 16）送給供應者、檢查每個向量的維度與儲存一致，**每次呼叫都記一筆 `model_calls`**（alias `embed`、capability `embedding`，歸屬到該次代理執行、任務與角色），成本照 `MODEL_PRICES` 的 `input` 價格（沒列就是 0）。供應者：`OpenAICompatibleEmbeddings`（NVIDIA 的 `/embeddings`，區分 query 與 passage）、`HashingEmbeddings`（模擬與測試用：確定性的特徵雜湊，英文依單字、中文依兩字組，共用字詞的文字才會相近，不連網）。
- 設定：`EMBED_PROVIDER`（`fake` / `nvidia`）、`EMBED_MODEL_ID`（非 fake 時必填，啟動時檢查；NVIDIA 需要金鑰）。**已在使用者的 `.env` 加上 `EMBED_PROVIDER=nvidia`、`EMBED_MODEL_ID=nvidia/nemotron-3-embed-1b`**（非機密設定）；`.env.example` 有說明。
- **分段**（`domains/newsroom/chunks.py`）：依段落合併到約 900 字，超過 1,400 字的段落在句尾（。！？.!?）切開，沒有句尾就硬切；**每段記錄它在證據原文中的起訖位置**，命中結果可以放回原文脈絡，引用時一律引證據原文、不是段落。
- **資料表 `evidence_chunks`**（migration `0014`）：段落文字、起訖位置、`halfvec(2048)` 向量（可為空）、算出向量的模型；HNSW 索引（cosine）。
- **`fetch_url` 擷取新證據時一併分段並算向量**，而且在寫入任何資料之前就算好，所以等 embedding 服務時不會占住公司的事件鎖。**embedding 失敗不影響擷取**：證據照存，段落沒有向量（失敗也記在 `model_calls`）。一頁最多先算 64 段，更長的部分只能用關鍵字找到。重用既有證據時不重算。
- **`search_evidence` 工具**（read）：把問句轉成向量，在自己公司的段落中找最近的（只比對同一個模型算的向量；過濾條件下用 pgvector 0.8 的 iterative scan，確保過濾後仍有 k 筆），可限定在某幾份證據內；向量不可用或沒有結果時退回**關鍵字搜尋**（英文 3 字以上的詞、中文兩字組），結果標示用的是哪種方法。回傳段落、所屬證據、起訖位置、分數，並提醒「引文必須與證據原文一致」。

### 過程中的問題
- `halfvec` 型別第一版：寫入時參數型別被改成 Text，自己的轉換函式沒被呼叫（asyncpg 收到 list 而報錯）；讀取時也因為轉成 Text 而拿到字串。改成用一個小的 TypeDecorator 負責寫入轉換、讀取時轉型後再宣告回向量型別。
- echo 的工作程序測試原本只把聊天模型固定成 fake，其他設定照開發者的 `.env`；加了 `EMBED_PROVIDER=nvidia` 之後，測試裡建立的工作程序會帶著真實的 embedding 設定（雖然 echo 用不到）。改成測試一律固定 fake embedding 與 fixture 工具，測試不依賴 `.env`、不連網。

### 驗證
- `tests/newsroom/test_chunks.py`（11 個）：分段（合併段落、每段可在原文定位、全文都被涵蓋、長段落依句尾切、無句尾硬切、空白文字）；雜湊向量（確定性、單位長度、共用字詞較近、中文兩字組）；NVIDIA 相容供應者（請求內容、依 index 排序、token 用量、429 / 5xx 可重試、4xx 不可）；`Embedder`（分批、每次呼叫一筆 `model_calls` 與成本、維度不符報錯、失敗也記錄）；設定檢查；`halfvec` 存取（0.25、−0.5、1.0 在半精度下完全相同）；`fetch_url` 產生段落與向量、段落都能在證據原文定位、重用時不重算；embedding 失敗仍擷取、搜尋退回關鍵字；`search_evidence` 以向量找到正確段落（含中文查詢）、限定證據、別家公司找不到。
- **真實 embedding（integration）**：`test_embed_live.py` 通過——2048 維、中文問句對相關英文段落的相似度比無關段落高出 0.2 以上、兩次呼叫各記一筆 `model_calls`。
- 後端 732 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；事件型別沒有變動。

---

## T-504 · 故事（Story）與去重

### 先量門檻
- 分群要一個「多像才算同一件事」的門檻，這取決於 embedding 模型，所以先用真實模型量 fixture 標題（免費呼叫）：同一事件的兩則英文標題 **0.73**；同一事件的中英版本 **0.57**；同一城市相關但不同的報導 **0.41～0.54**；無關 **0.12～0.21**。
- 中英同事件（0.57）與「相關但不同」（最高 0.54）太接近，硬要跨語言合併會誤併相關報導。**門檻定 0.65**：同語言的同一事件會合併，跨語言的暫時是兩個故事（研究員會把兩者都收成證據）。門檻是設定 `STORY_MATCH_THRESHOLD`，理由寫在 `stories.py`；`test_embed_live.py` 用真實模型檢查這個門檻仍然成立（換模型時會提醒）。

### 做了什麼
- **資料表**（migration `0015`）：
  - `stories`：標題、摘要、角度、狀態、分數（0～1）、項目數、不同來源數、首次出現、最新項目時間、**向量（成員項目的中心，`halfvec(2048)`、HNSW）**與模型、手動建立時的 `seed`（網址 / 查詢，研究員的起點）、選定後的專案。
  - `story_items`：項目屬於哪個故事（每個項目最多一個）、加入時的相似度（開頭那則為空）。
- **狀態機**（照 platform/02 §6）：DISCOVERED → SELECTED → IN_PRODUCTION → PUBLISHED；三個進行中的狀態都可以 → DROPPED；DISCOVERED → IGNORED。每次轉換都寫 `state_transitions` 稽核。
- **分群**（`domains/newsroom/stories.py` 的 `StoryDesk`，**不用語言模型**）：新排程 `newsroom.cluster_stories`（每 5 分鐘、比輪詢晚 2 分鐘，和輪詢排程一起在新增第一個來源時建立），處理還沒分群、7 天內的項目：
  1. 標題 + 摘要算向量（先算好才寫資料，不在等網路時占住事件鎖）；
  2. **同一個網址已經在某個故事裡 → 併入**（兩個 feed 連到同一篇）；
  3. 否則找 30 天內最相近的故事，相似度 ≥ 門檻 → 併入，並更新中心向量；
  4. 否則開新故事（DISCOVERED，發 `STORY_DISCOVERED`）。
  - **比對不分故事狀態**：已經在寫、寫過、被忽略、被放棄的故事也會吸收新項目，同一件事不會再開一次題（「我們寫過嗎」），被忽略的題目也不會換個來源又冒出來。
- **分數**（沒有模型）：0.5 × 佐證（不同來源數，3 個以上滿分）+ 0.3 × 新鮮度（最新項目，72 小時內線性降到 0）+ 0.2 × 來源平均信任度。每次有項目加入就重算。
- **生命週期操作**：`create`（手動建立，例如之後的「開始研究」，會算向量讓後來的項目能併入）、`select`（檢查專案屬於同一家公司、發 `STORY_SELECTED`）、`ignore`（只有稽核、不發事件）、`drop`（發 `STORY_DROPPED` 與原因）。IN_PRODUCTION / PUBLISHED 由之後的流程（T-514、T-512）推進。
- 事件：`STORY_DISCOVERED`、`STORY_SELECTED`、`STORY_DROPPED`（`{story_id, title, score}`，後兩個另有專案 / 原因）；時間軸顯示「發現題材：…・分數 57」「選定題材」「放棄題材：…・原因」。

### 過程中的問題
- `build_scheduler` 的修改第一次沒有生效：格式化工具先把那段程式碼換了行，我用文字取代的修改沒對上，只改到了說明文字。端到端的排程測試抓到了（「沒有 `newsroom.cluster_stories` 的處理器」），改用精確編輯補上。
- 同一個排程測試裡，分群的時段（T−8 分）比輪詢（T−5 分）早，第一次觸發時還沒有項目；把測試的時鐘往前推到下一個分群時段（T+2 分），和正式運作「:00 輪詢、:02 分群」一致。

### 驗證
- `tests/newsroom/test_stories.py`（9 個，向量用「依主題給定」的腳本，每個併入 / 新開的判斷都是確定的）：兩個 feed 的 5 則項目分成 3 個故事（微電網 3 則、2 個來源；相似度 0.6 < 0.65 的居民報導另開；咖啡另開），事件與相似度正確、再跑一次沒有待處理；同一網址從另一個來源進來會併入（相似度 1.0）；被忽略的故事吸收新項目、不開新題；超過 30 天的故事不比對；分數（1 個來源、剛發布、信任 0.5 → 0.567；一週前 → 0.267）；embedding 失敗什麼都不寫（排程之後重試）；選定 / 忽略 / 放棄、不合法的轉換、跨公司的專案被拒、稽核紀錄；**經過工作程序排程器：輪詢後分群**。
- 真實模型：`test_embed_live.py` 新增門檻檢查通過（同事件 ≥ 0.65、相關與無關 < 0.65）。
- 後端 741 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 209 個測試與 typecheck、lint 通過。

---

## T-505 · 主張（Claim）與證據引文

### 做了什麼
- **資料表**（migration `0016`）：
  - `claims`：屬於哪個題材、主張本文、類型（`fact` 事實、`number` 數字、`quote` 引述、`attribution` 歸屬、`opinion` 意見）、查核狀態（`UNVERIFIED` / `VERIFIED` / `REJECTED`，T-510 推進）、建立它的工具呼叫鍵（唯一：重試不會重複建立）、任務與執行。
  - `claim_evidence`：主張與證據的連結：**引文一律是證據原文中定位到的那一段**（含起訖位置），不是模型寫的版本；關係是 `supports` 支持 / `contradicts` 矛盾 / `context` 背景；同一主張、同一證據、同一位置、同一關係只有一筆。
- **引文定位**（`domains/newsroom/quotes.py`，fact-check 第一層的基礎）：只容許排版差異——連續空白算一個、彎引號算直引號、各種破折號算連字號、全形與不斷行空白算空白，**中文字旁邊的空白不算**（中文不用空格分詞，換行或空格只是排版）。字詞、數字、標點、大小寫都必須一致：改一個數字或把 six 寫成 five 都找不到。長度 4～500 字（太短沒意義；太長等於轉載，platform/05 §8）。回傳原文中的起訖位置，存進去的是原文。
- **工具**（`domains/newsroom/tools/claims.py`）：
  - `create_claim`（write，分析師）：建立主張，**可以同時附上證據引文**（通常的用法）；所有引文都找到才寫入，任何一段找不到整個呼叫失敗、什麼都不寫，錯誤訊息告訴模型怎麼修（「從 read_evidence / search_evidence 原樣複製，只有空白與引號可以不同」）。新主張發 `CLAIM_CREATED`（含引用的證據）、`produced: claim`（AC-5 分析師面板的「Claims」計數）。事實、數字、引述類的主張沒有支持引文時照樣建立，但結果會註明「查核前還需要至少一段支持引文」；意見不需要。
  - `link_evidence`（write，分析師）：之後再補引文（支持 / 矛盾 / 背景），同一條連結重複送只存一次。
  - `list_claims`（read，新動作）：列出題材的主張與引文（含證據網址、標題、狀態），寫手與編輯之後會用。權限：研究、分析、寫作、編輯、行銷、CEO 都可以讀（和 `read_evidence` 相同）；已同步 `platform/07` 權限表與權限矩陣測試。
- 時間軸：「新增主張：數字・引用 2 份證據」。

### 過程中的問題
- 測試抓到兩個問題：
  1. 模型把中文引文換行（`1,200 組屋頂\n太陽能板`）時找不到——原本把所有空白當成一個空格，但原文在中文字之間沒有空格。改成中文字旁的空白一律不算，兩邊用同樣的規則正規化。
  2. 同一個交易裡建立的多條連結，建立時間相同，讀回來的順序不一定是寫入順序。連結的 ID 改用有時間順序的 UUIDv7，依 ID 排序。
- 新增 `list_claims` 動作後，權限矩陣測試提醒它不在矩陣裡（沒有宣告的動作預設拒絕，矩陣要涵蓋每個動作）；補上矩陣與 `platform/07`。

### 驗證
- `tests/newsroom/test_claims.py`（13 個）：引文定位（原文位置、空白 / 引號 / 破折號差異、拼字 / 大小寫 / 數字 / 內容不同都找不到、中文與中文空白、第一次出現、長度限制）；`create_claim` 附兩段引文（中英各一，存的是原文、位置正確、事件帶證據、run 正確）；重試回傳同一個主張；一段找不到就整個不寫；太長的引文、別家公司的題材與證據；之後補引文、重複連結只存一次、背景引文、意見不需證據、`list_claims`；權限。
- 後端 754 個測試通過（含權限矩陣）；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 210 個測試與 typecheck、lint 通過。

---

## T-508 · 文章、雙語版本與 `write_draft`

### 做了什麼
- **資料表**（migration `0017`）：
  - `articles`：一個題材一篇；全域唯一的網址代稱（`slug`，公開站 `/{lang}/articles/{slug}` 用；取英文標題的英數字 + 文章 ID 的末 6 碼，沒有英文就是 `article-xxxxxx`）、主語言標題、狀態、主語言、已發布語言、目前的草稿群組、被要求修改的次數、發布時間。
  - `article_versions`：每個版本 × 語言一筆；**同一次寫稿的各語言共用 `draft_group_id` 與版本號**；本文是區塊清單（`heading` / `paragraph` / `quote`，各帶 `claim_ids`）；記錄引用的主張、次要語言是哪個主語言版本的翻譯、修改說明、寫作的任務與執行。
- **狀態機** `ARTICLE_FSM`（照 platform/02 §6）：DRAFT → IN_REVIEW → APPROVED → PUBLISHED → ARCHIVED；IN_REVIEW → DRAFT（退回修改）或 REJECTED。審稿、核准、發布由 T-511、T-512 推進。
- **語言政策**（D-002，`domains/newsroom/policy.py` 的 `language_policy`）：讀公司政策 `newsroom.primary_lang`、`newsroom.langs`、`newsroom.require_all_langs`，沒設就用預設（zh-TW；zh-TW 與 en；兩語都要）；主語言一定在語言清單內。
- **寫稿檢查**（`domains/newsroom/articles.py` 的 `check_draft`，純函式，**一次列出所有問題**讓模型一次改完）：
  - 語言：主語言必須有、只能用政策內的語言、每種一次、`require_all_langs` 時全部都要；
  - 段落與引述區塊必須引用主張，標題不引用；引述區塊最多 500 字；
  - 引用的主張必須屬於這個題材、沒有被查核駁回；
  - **各語言引用的主張集合必須相同**（雙語規則：翻譯共用同一組事實基礎，查核只需驗一次），不同時列出各自多出的主張；
  - 題材已放棄 / 忽略 / 發布時不能寫；文章不在 DRAFT（例如編輯審稿中）時不能寫。
- **工具**（`domains/newsroom/tools/drafts.py`）：
  - `write_draft`（write，寫手）：一次寫所有語言；主語言先寫、次要語言標為它的翻譯；第一份草稿建立文章並發 `ARTICLE_CREATED`，之後（退回修改）是同一篇的新版本；重試回傳同一份草稿；每個語言版本都是 `produced`（AC-7 的草稿連結）。檢查不過就不寫，錯誤訊息列出全部問題。
  - `read_draft`（read，寫手 / 編輯 / 行銷 / CEO）：讀最新草稿或指定版本，附上引用的主張（本文、類型、狀態）；用文章 ID 或題材 ID 找；只能讀自己公司的。
- 時間軸：「文章初稿：zh-TW / en」。

### 驗證
- `tests/newsroom/test_articles.py`（17 個）：
  - 規則（純函式）：合格的雙語稿；缺英文、缺主語言、政策外語言、重複語言、段落沒引用、標題有引用、別的題材的主張、中英引用不同（訊息列出差異）；一次回報全部問題（題材已放棄、審稿中、引述過長、別題材的主張、被駁回的主張、中英不一致）；語言政策的預設與覆寫；slug；狀態機。
  - 工具：雙語初稿（文章、版本、群組、翻譯關係、主張集合、事件）、修改後的第 2 版（只有一次 `ARTICLE_CREATED`）、`read_draft` 最新與指定版本；重試回傳同一份、不合格的稿什麼都不寫；審稿中不能寫；公司政策改為不要求全部語言後只寫中文可以；別家公司不能寫也不能讀、`read_draft` 參數檢查；權限（寫手可寫、分析師不可、編輯可讀）。
- 後端 779 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 211 個測試與 typecheck、lint 通過。
- **CI 的 e2e 第一次失敗**（執行編號 `35430698537`）：辦公室的「一次執行」測試沒看到分析師 3 秒的忙碌狀態——那次 CI 特別慢（同一批辦公室測試比平常慢約一半），軟體算圖的一幀可以超過好幾秒。T-508 沒有動到 echo 流程或前端的這條路徑，重跑 e2e 通過（第 2 次嘗試）。為了不再偶發，把這段忙碌時間從 3 秒加長到 6 秒（測試多 3 秒），本機辦公室 e2e 6 個通過（提交 `871571b`）。

---

## T-510 · 確定性事實查核與 `run_fact_check`

### 做了什麼
- **規則**（`domains/newsroom/factcheck.py`，純函式，沒有語言模型做判斷；對應 platform/05 §3 的第一、二層）：
  - **第一層：結構**（事實 / 數字 / 引述類主張）：至少一段「支持」引文；每段引文仍是證據原文在記錄位置上的文字（完整性）；**低信任來源不能單獨支持**：至少一個支持來源達到門檻，或兩個獨立來源（不同來源、或不同網站）一致；**數字類主張裡的每個數字都要出現在支持引文中**，比的是數值且會換算單位——「NT$420 million」＝「4.2 億」、「3,000」＝「3000」、「約3,000戶」＝3000（英文的 thousand / million / billion / trillion 與中文的千 / 萬 / 億 / 兆）。
  - **第二層：交叉**：事實 / 數字 / 引述類主張只要有「矛盾」引文就不通過——有爭議的內容要寫成「誰說了什麼」（歸屬類主張，它可以帶著矛盾引文，只加註記）；另外用向量找同一題材其他證據中與主張相近的段落，**只列給編輯參考，不影響判定**。
  - 意見不需要證據；歸屬類至少要有一段引文。
- **門檻是公司政策**：`newsroom.min_source_trust`（預設 0.4）、`newsroom.default_source_trust`（沒有來源列過的證據，預設 0.5）。
- **資料表 `fact_check_reports`**（migration `0018`）：每次查核一份：對應的草稿（主語言版本與群組）、是否通過、每個主張的判定（問題、註記、相關段落）、**給編輯的第三層清單**、呼叫鍵（重試回傳同一份）。
- **`run_fact_check` 工具**（編輯）：查核文章目前的草稿（或指定版本），只在 DRAFT / IN_REVIEW 時可用；檢查各語言引用的主張一致；**更新主張狀態**：通過 → `VERIFIED`、不通過 → `REJECTED`，狀態有變才發 `CLAIM_VERIFIED` / `CLAIM_REJECTED`（後者帶問題）；被駁回的主張寫手不能再引用（T-508 的規則），所以修改稿必須拿掉或換掉它。
  - **第三層（語意）交給編輯**：報告列出每個通過的非意見主張、它的支持引文，以及**各語言草稿中陳述它的句子**，讓編輯逐語言判斷「句子說的是否就是引文說的」（platform/05 §3：雙語各做一次）。
- 向量搜尋的調整：候選只有少數幾頁時（查核的相關段落、`search_evidence` 指定 `evidence_ids`）改用精確掃描（靠 evidence_id 索引找出段落再排序），不走 HNSW 索引——HNSW 加上窄的過濾條件時，得掃過大量其他公司的段落才湊得到結果；全公司範圍的搜尋仍用 HNSW。
- 時間軸：「主張查核通過：數字」、「主張查核未過：<第一個問題>」。

### 驗證
- `tests/newsroom/test_factcheck_rules.py`（20 個）：
  - 數字抽取與換算（NT$420 million、4.2 億、約3,000戶、18%、4.6 billion、中文數字不抽）；
  - 規則：有支持的通過、數字跨語言換算通過、意見通過；沒有支持、只有背景、單一低信任、同一來源兩段（不算獨立）、數字不符、矛盾、引文不完整、沒有引文的歸屬都不通過且說明原因；兩個獨立低信任來源互相佐證可以通過；歸屬帶矛盾只加註記。
  - **端到端**：一份草稿引用 5 個主張（2 個數字、1 個被分析報告矛盾的事實、1 個歸屬、1 個意見）→ 查核不通過、只有那個有爭議的事實被駁回、其他 4 個標為已查核、事件數正確、第三層清單只有 3 個（不含意見與被駁回的）且附中英句子；重試回傳同一份報告、再跑一次沒有新事件；**帶著被駁回的主張重寫會被拒**，拿掉後的第 2 版查核通過。
  - 公司政策把預設信任度降到 0.2 → 單一網站支持的主張不通過；別家公司不能查、已核准的文章不能查、只有編輯能查。
- 後端 799 個測試通過；`ruff`、`lint-imports`、`make db-check` 通過；event-schema 8 個、web 212 個測試與 typecheck、lint 通過。（有一次整套跑了 3 分鐘：當時機器負載約 21，單獨重跑新聞室測試 6 秒，每個測試都在 1 秒內，不是程式變慢。）

---

## T-512 · 核准與發布（Publisher）

### 做了什麼
- **指令，不是工具**（`domains/newsroom/publisher.py`）：之後由流程的 `approve` / `publish` 節點（T-514）與人（API）呼叫；每個都先問權限引擎並記錄決定（與 `start_workflow` 相同的做法）。代理身分會從資料庫查出角色再判斷，不認得的代理直接拒絕。
  - `approve_article`（審稿中 → 已核准）：**目前草稿最新一次查核必須通過**。人一律可以核准；系統只有在公司開啟「查核通過自動核准」（`newsroom.auto_approve_if_fact_check_passed`，D-001 預設關）時可以，否則權限引擎回「需要人核准」。已核准 / 已發布的再核准一次什麼都不做。發 `ARTICLE_APPROVED`（`by`：human / system）。
  - `reject_article`（審稿中 → 駁回）：只有人可以；題材一併放棄（`STORY_DROPPED`），發 `ARTICLE_REJECTED`（含原因）。
  - `publish_article`（已核准 → 已發布）：
    - 被核准的草稿群組成為**公開站要顯示的版本**（`articles.published_group_id`，新欄位）；
    - 語言再依公司政策檢查一次（`require_all_langs` 時全部語言都要有，否則整個不發布、什麼都不改）；
    - 題材推進到已發布（從 SELECTED 會經過 IN_PRODUCTION，每一步都有稽核）；
    - 建立網站的發布紀錄（`distributions`，每篇文章在 `site` 通路只有一筆）：各語言的路徑 `/{lang}/articles/{slug}` 與標題；
    - 發 `ARTICLE_PUBLISHED`（slug、語言、主語言路徑）與 `DISTRIBUTION_CREATED`。
    - **冪等**：已發布的文章再發布一次，回傳當初的結果、不發任何事件（流程節點重試、重複點擊）；文章資料列在整個指令期間鎖住，兩個同時發布只有一個真的發布。
- **資料表**（migration `0019`）：`distributions`（通路、狀態 draft / published / failed、外部參照、各語言文案、建立者、執行；`site` 通路每篇一筆的部分唯一索引）、`articles.published_group_id`。
- **Dashboard 的「今日發布」**：原本標示「階段 5 才有」而回傳空值；現在由公司層直接數今天的 `ARTICLE_PUBLISHED` 事件（不需要依賴新聞室模組，分層不變）。
- 時間軸：「文章核准：人工核准 / 查核通過後自動核准」「文章駁回」「文章發布：/zh-TW/articles/…・zh-TW / en」「發布紀錄：site・published」。
- 測試整理：新聞室測試共用的準備（代理執行、選定的題材、兩份證據、兩個有支持引文的主張、以該執行呼叫工具）抽成 `tests/newsroom/conftest.py` 的 `newsroom_room`。

### 驗證
- `tests/newsroom/test_publisher.py`（7 個）：人核准（權限紀錄、事件、重複核准不重發）；系統核准預設需要人、開啟自動核准後可以，編輯代理與不認得的代理都被拒；沒通過查核、不在審稿中都不能核准；**發布**（狀態、語言、路徑、公開版本、題材經 IN_PRODUCTION 到 PUBLISHED、發布紀錄、事件、Dashboard 今日發布 = 1）與**重複發布不重發**；**兩個同時發布只發布一次**；未核准不能發布、核准後政策改成要求全部語言而缺英文 → 不發布且狀態不變；人駁回（題材放棄、事件）、系統不能駁回。
- 後端 806 個測試通過（KPI 測試改為「今日發布 = 0」）；`ruff`、`lint-imports`、`make db-check`、`gen-api-check`、`gen-schema-check` 通過；event-schema 8 個、web 213 個測試與 typecheck、lint 通過。

- **CI 的 e2e 又失敗一次**（執行編號 `35438255555`）：同一個辦公室測試，這次連 6 秒的分析師忙碌狀態都沒看到（研究員的有看到）。頭頂標籤在每一幀的繪製迴圈裡更新，T-413 之後場景變重，CI 軟體算圖一幀可能要好幾秒；本機一直通過，T-512 也沒有動到 echo 流程或前端。改成 **CI 上分析師忙 15 秒、本機 3 秒**（本機仍要求三人都被看到），CI 多 12 秒。
---

## T-506 · 研究員代理

### 做了什麼
- **行為**（`domains/newsroom/agents/researcher.py`，角色 `researcher`、任務 `research`，輸入 `params.story_id`）：工具 `web_search`、`fetch_url`、`read_evidence`、`search_evidence`；最多 12 次模型呼叫、2 次修正。
  - **提示**：從線索（公司來源已列出的網頁）開始、用搜尋找更多（尤其一手來源）；搜尋結果只是候選，要用 `fetch_url` 擷取才算證據；來自多個網站；不捏造來源、ID、事實或引文；摘要與角度用繁體中文（明確禁止簡體）。
  - **輸出 `ResearchNote`**：題材 ID、證據 ID、每份證據一段摘要、1～5 個建議角度。
  - **背景（第一則訊息）**：題材標題、ID、摘要、手動題材的建議搜尋與網址、分群進來的來源項目（最多 8 則）當線索、需要幾個來源。
  - **驗證（以執行實際發生的事為準，不是以模型說的為準）**：題材必須是任務的題材；**每個證據 ID 都必須是這個任務自己的 `fetch_url` 產生的**（查事件紀錄的 `TOOL_COMPLETED.produced`），模型不能引用沒擷取過的頁面或捏造 ID；至少 `params.min_sources`（預設 2）份、而且不是全部來自同一個網站；每份證據恰好一段摘要。
- **新聞室的模擬模型**（`domains/newsroom/simulation.py`，`MODEL_PROVIDER=fake` 時使用）：像真的模型一樣讀對話（第一則訊息、到目前為止的工具結果）並透過同樣的工具行動——先搜尋題材、再逐一擷取候選網頁（優先新的網站），擷取到 3 份後用**實際拿到的證據 ID** 回報。搜尋、擷取、證據、驗證全是真的，只有決策是腳本。T-518 會補齊其他角色與示範劇本。
- 組裝：`app.build_behaviors` 註冊新聞室行為（echo 的研究員仍處理自己的 `echo_research` 任務）；`app.simulated_model` 加入新聞室的模擬。

### 過程中的問題
- 建立測試專案時忘了「ACTIVE 專案必須有停損條件（kill criteria）」的公司規則，資料庫拒絕；照 echo 測試補上。
- 驗證失敗時 runner 回報的錯誤類別是 `EvaluationFailed`（訊息就是驗證器的說明），測試改成檢查確切的值；失敗的那次嘗試讓任務回到 READY、等重試延遲，`run_until_idle` 就停在那裡——這是預期行為，測試照實斷言。

### 驗證
- `tests/newsroom/agents/test_research.py`（5 個）：
  - **真正的工作程序**（模擬模型、離線工具）跑一個研究任務：成功、題材正確、至少 2 份證據且來自不同網站、證據都有 `EVIDENCE_CAPTURED` 事件、內容與題材相關、每份證據一段繁中摘要；
  - 背景內容（標題、摘要、建議搜尋、線索、來源數），找不到題材時明說；
  - 每個驗證器對著真實的證據與事件：合格的通過、題材不對、**捏造的證據 ID**、來源不足、摘要多 / 少 / 不在清單中；`min_sources` 設 1 時單一來源可以；
  - 工作程序找得到研究員行為（echo 的研究員不受影響）；
  - **模型捏造證據 ID** 三次（第一次回答 + 兩次修正）→ 這次嘗試以 `EvaluationFailed` 失敗、訊息說明「不是這個任務擷取的」，任務等待重試。
- 後端 811 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check` 通過。

- **CI 的 e2e 失敗**（執行編號 `35438774325`）：這次是「畫布有在畫」測試在預設的 5 秒內沒等到畫布量好尺寸——同一天 CI 明顯偏慢（前一次失敗見 T-512）。T-506 沒有動到前端；這個等待改成與同檔其他等待一致的 30 秒。
---

## T-507 · 分析師代理

### 做了什麼
- **行為**（`domains/newsroom/agents/analyst.py`，角色 `analyst`、任務 `analysis`，輸入 `params.story_id`，接在研究任務之後）：工具 `read_evidence`、`search_evidence`、`create_claim`、`link_evidence`、`list_claims`；能力 `reasoning`，最多 14 次模型呼叫、2 次修正。
  - **提示**：只有已擷取的證據算數；每則主張一句話、分型（事實 / 數字 / 引述 / 歸屬 / 意見），引文逐字取自證據；數字主張的每個數字都要出現在引文裡；低信任來源不能單獨支撐；**來源意見不一致時不選邊**，改寫成「誰說了什麼」（歸屬主張），把相反的引文連成 `contradicts`，列入矛盾；角度與說明用繁體中文。
  - **輸出 `AnalysisNote`**：題材 ID、主張 ID（1～40）、角度、關鍵數字（指向數字主張）、矛盾（說明 + 證據 / 主張 ID）。
  - **背景**：題材標題與 ID；**上游研究任務**（`depends_on`）回報的證據（ID、標題、網址）與研究員的摘要、建議角度；至少要幾則主張。沒有研究結果時，提示改用 `search_evidence`。
  - **驗證**：主張都存在、屬於這家公司、**是這個任務建立的**、關於這個題材；至少 `params.min_claims`（預設 3）則；**每則主張都用事實查核同一套規則（`check_claim` 第 1～2 層：引文定位、數字、信任度）先檢查一次**，會失敗的要補證據或拿掉——寫手拿到的主張都能通過查核；關鍵數字必須指向清單裡的數字主張。
- `tools/factcheck.py`：把「主張 → 引文事實」的查詢抽成 `claim_quotes`，事實查核與分析師共用。
- **模擬模型**：從背景解析題材與研究員的證據 → 一次讀完所有證據 → 每份證據取最多 2 個含數字的句子（20～300 字）當數字主張、原句即引文（最多 4 則）→ 用實際建立的主張 ID 回報。句子切分處理中英文標點，小數點與 `NT$4.6` 這類數字不會被切開。

### 過程中的問題
- 依賴任務要由工作流程引擎在前一個任務完成後解鎖（`TaskManager.on_task_finished`），單純 `add_task(depends_on=...)` 不會自己變 READY。測試先跑完研究任務，再建立已就緒、依賴研究任務的分析任務（T-514 的範本會接上真正的流程）。
- 模擬的第一版句子切分把「NT$4.6」的小數點當句號，數字主張的數字被截斷、查核不通過；改成句點後面接非空白字元時不算斷句。

### 驗證
- `tests/newsroom/agents/test_analyst.py`（4 個）：
  - **真正的工作程序**跑研究 → 分析：分析任務成功、至少 3 則主張、都是本任務為本題材建立的、**每則都通過 `check_claim`**、都有 `CLAIM_CREATED` 事件、引用的證據都來自研究員；
  - 背景列出研究員的每份證據、繁中摘要、建議角度與主張數量；
  - 驗證器對著真實的主張：合格的通過；捏造的主張 ID、題材不對、主張不足、別的任務建立的主張、**沒有引文支撐的數字主張**（「會通不過事實查核：沒有支撐的引文」）、關鍵數字指向不在清單的主張；
  - 工作程序找得到分析師行為與工具。
- 後端 815 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check` 通過。
---

## T-509 · 寫手代理

### 做了什麼
- **行為**（`domains/newsroom/agents/writer.py`，角色 `writer`、任務 `draft`，輸入 `params.story_id`，接在分析任務之後）：工具 `write_draft`、`read_draft`、`list_claims`、`read_evidence`；能力 `drafting`，最多 8 次模型呼叫、2 次修正（platform/04 §4）。
  - **提示**：文章裡的每個事實、數字、引述都來自主張，每段列出它陳述的主張、標題不引用；不加主張沒說的事、不改數字或說話的人；**先寫主要語言（繁體中文，明確禁止簡體），其他語言用完全相同的一組主張**，彼此忠實但不逐字翻譯；有爭議的寫成誰說了什麼；引文短；`write_draft` 拒絕時照列出的問題全部修正再寫；修訂時先讀目前的草稿、逐一處理編輯意見、寫明改了什麼。
  - **輸出 `ArticleDraft`**：文章 ID、草稿群組 ID、各語言版本 ID（3d-office/06 §1）。
  - **背景**：題材、**公司的語言政策**（主要語言、其他語言、是否全部必備）、上游分析任務的角度與主張（ID、類型、內容；**標出關鍵數字**；排除被查核駁回的）、有爭議之處；**修訂**（`params.issues`：`[{message, lang?, block_ref?, kind?}]`）時列出編輯的意見並要求先 `read_draft`。
  - **驗證**：文章規則（語言、每段引用主張、兩語言同一組主張、引文長度、只能引用本題材未被駁回的主張）已經由 `write_draft` 在寫入前把關；代理的驗證則看**回報是否和實際發生的事一致**——草稿群組必須是**這個任務自己的 `write_draft` 寫的**（查 `TOOL_COMPLETED.produced` 的 `article_version`）、是文章**目前**的草稿（之後又寫一版就要回報新的）、文章 ID 與各語言版本 ID 都要對；**分析師標出的每個關鍵數字都要引用**（被查核駁回的主張不能引用，所以不要求）；修訂必須寫 `change_summary`。
- **模擬模型**：從背景解析語言與主張 → 修訂時先 `read_draft` → 每則主張寫一段（最多 6 段）、每個語言引用同一組主張；主張本身是該語言就照用，否則標明「根據來源」（模擬不翻譯）→ 用 `write_draft` 回傳的 ID 回報。
- 測試整理：研究 → 分析 → 撰稿這條線的測試輔助移到 `tests/newsroom/agents/conftest.run_line`（分析師的測試也改用它）。

### 驗證
- `tests/newsroom/agents/test_writer.py`（5 個）：
  - **真正的工作程序**跑研究 → 分析 → 撰稿：成功、文章屬於該題材且為 DRAFT、中英兩版在同一個草稿群組、**兩語言引用同一組主張、正是分析師的主張**、每段都有引用、英文版標為中文版的翻譯、`ARTICLE_CREATED` 事件；
  - **修訂任務**（帶編輯意見）：先讀了草稿、寫出同一篇文章的第 2 版、成為目前草稿、有 `change_summary`；
  - 背景：語言政策、角度、主張與關鍵數字標記、爭議、修訂意見（含語言與段落位置）、找不到題材；
  - 驗證器對著真實寫入的草稿：合格通過；捏造的草稿群組、文章 ID 與版本 ID 不符、**別的任務寫的草稿**、回報舊的草稿；漏掉關鍵數字；關鍵數字的主張被駁回後不再要求；修訂沒寫 / 有寫 `change_summary`；
  - 工作程序找得到寫手行為（echo 的寫手仍處理自己的 `echo_write`）。
- 後端 820 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check` 通過。
---

## T-511 · 編輯代理

### 做了什麼
- **編輯的決定**（`domains/newsroom/review.py`，指令，與發布服務同一種寫法）：每個決定都記 `ARTICLE_REVIEWED`（決定、查核是否通過、角色）。
  - `accept_draft`（DRAFT → IN_REVIEW，交給核准）：必須用**這份草稿最新的**查核報告、而且通過；沒查核、拿舊報告、查核沒過都拒絕並說明該怎麼做。
  - `request_revision`（維持 DRAFT，寫手可以再寫下一版）：至少一個問題；`revision_count` + 1，記 `ARTICLE_REVISION_REQUESTED`（第幾次、幾個問題）。**每篇最多修訂 2 次**（platform/05 §3），第 3 次要求修訂 → 文章 REJECTED、題材 DROPPED（`ARTICLE_REJECTED`、`STORY_DROPPED`），之後不能再寫稿。
  - **每份草稿只決定一次**：同樣的決定重做（重試）回傳原本的結果、不重發事件；不同的決定拒絕（「這份草稿已經審過」）。以事件紀錄判斷，不需要新的資料表。
  - 狀態的解讀：DRAFT 是寫手與編輯之間的來回，IN_REVIEW 是編輯接受、等待核准（核准只接受 IN_REVIEW，T-512）；因此修訂不經過 IN_REVIEW，第 3 次修訂時沿 DRAFT → IN_REVIEW → REJECTED 駁回。
- **工具**（`tools/review.py`）：`accept_draft`、`request_revision`（問題 `{message, kind, lang?, block_ref?}`，kind：fact / unsupported / missing_context / translation / style / other）；回傳決定、第幾次修訂、剩幾次、是否已放棄題材，修訂會把送出的問題回傳給模型。
- **編輯代理**（`agents/editor.py`，角色 `editor`、任務 `review`）：工具 `read_draft`、`run_fact_check`、`accept_draft`、`request_revision`、`read_evidence`、`list_claims`；能力 `editing`，最多 12 次模型呼叫。
  - **提示**：先讀稿、跑查核（第 1～2 層），再自己做**第 3 層**——每則主張在每個語言裡是否真的說了引文說的事、沒有誇大或漏掉會改變意思的脈絡、各語言事實一致、中文是繁體、沒有沒主張的事實、爭議寫成誰說了什麼；只有查核通過且沒有要修的才接受，否則逐項列出問題（語言、段落）；查核沒過的主張不能再引用，要說明沒有它怎麼寫；最多修訂 2 次。
  - **輸出 `EditorReview`**：文章 ID、決定（accept / revise）、查核報告 ID、問題（platform/04 §5）。工作流程（T-514）會把問題交給寫手的修訂任務（T-509 的 `params.issues`）。
  - **背景**：題材、文章 ID、目前草稿的版本與語言與狀態、需要的語言、**已修訂幾次（沒有次數時明說再要求修訂會放棄題材）**、寫手在這一版寫的修改說明。
  - **驗證**：這個任務**確實做了決定**（本任務的 `ARTICLE_REVIEWED`），回報的決定與實際一致、是本題材的文章；查核報告存在、**是本任務的 `run_fact_check` 產生的**、屬於這篇文章；accept 需要報告通過且不列問題；revise 至少一個問題，且與 `request_revision` 送出的問題數量相同。
- `stories.drop_story`：放棄題材的共用函式（題材台、發布服務的駁回、編輯的第 3 次修訂都用它）。
- **模擬模型**：讀稿與查核同一回合 → 查核通過就 `accept_draft`，否則每個沒通過的主張一個問題（繁中說明）送 `request_revision` → 回報。
- 前端事件說明：`ARTICLE_REVIEWED`（編輯通過・送交核准 / 編輯退回・事實查核未過或需要修改）、`ARTICLE_REVISION_REQUESTED`（要求修改・第 N 次・M 個問題）；事件契約重新產生；`3d-office/03` 補上兩個事件的新欄位（`by_role`、`revision`）。

### 驗證
- `tests/newsroom/test_review.py`（4 個）：接受 → IN_REVIEW、事件內容、重試不重發、不同決定被拒、**之後人可以核准**；沒查核 / 舊報告 / 查核沒過都不能接受；修訂兩次（次數、剩餘、事件）→ **第 3 次放棄題材**（文章 REJECTED、題材 DROPPED、事件各一、之後不能寫稿）；修訂沒有問題、文章不存在。
- `tests/newsroom/agents/test_editor.py`（6 個）：
  - **真正的工作程序**跑研究 → 分析 → 撰稿 → 審稿：查核通過、接受、文章 IN_REVIEW、報告與事件屬於審稿任務、角色是 editor；
  - **修訂來回**：撰稿後讓一則被引用的主張失去證據 → 審稿查核沒過、要求修訂（問題類型 unsupported、繁中說明）→ 帶著問題的寫手修訂任務寫出第 2 版、**不再引用被駁回的主張** → 第二次審稿接受；
  - 背景（還沒有稿、版本與語言、修訂次數用完的提醒）；驗證器（還沒決定、決定不一致、文章不存在、別的題材、別的任務、報告不存在或不是本任務的、接受卻列問題、修訂沒問題、報告沒過卻接受、問題數量與送出的不同）；工作程序找得到編輯行為。
- 後端 830 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check` 通過；event-schema 8 個、web 213 個測試與 typecheck、lint 通過。
---

## T-513 · 行銷代理

### 做了什麼
- **`create_distribution` 工具**（`tools/distribution.py`，MVP，Q-channel：自有網站是唯一通路）：行銷寫的社群貼文**只存進資料庫當草稿**（`channel = social_draft`、`status = draft`），不對外發出。
  - 只接受**已發布**的文章（權限也這樣限制，工具本身再檢查一次）；網站那一筆是出版服務建立的（T-512），行銷不能建立 `site`。
  - 每個發布語言都要一則貼文、不能有沒發布的語言、每則最多 500 字；有問題一次列出全部、不存。
  - 每則貼文的連結由工具依語言加上（`/{lang}/articles/{slug}`），模型不會寫錯網址。
  - 每篇文章每個通路一筆：重試（內容相同，空白差異忽略）回傳原本那筆，不同內容拒絕；以文章列鎖避免同時寫兩筆，不需要新的索引。
  - 記 `DISTRIBUTION_CREATED`（通路、狀態）與 `produced`（`distribution`）。
- **行銷代理**（`agents/marketing.py`，角色 `marketing`、任務 `distribute`，發布之後）：工具 `create_distribution`、`read_draft`；能力 `drafting`。
  - **提示**：只根據文章寫（不加文章沒有的事實、數字、引述）、一兩句說明是什麼事與為什麼重要、不標題黨、繁體中文、各語言彼此忠實；存成草稿、不會發出。
  - **輸出 `DistributionPlan`**：文章 ID、通路清單（網站那筆、社群草稿那筆與各語言貼文）。貼文欄位叫 `posts`（`copy` 會蓋掉 pydantic `BaseModel.copy`，會有警告；資料庫欄位仍是 `copy`）。
  - **權限事實**（`policy_facts`）：`create_distribution` 呼叫前查文章狀態交給權限引擎，所以「只限已發布」的規則（`platform/07`）真的生效——未發布的文章，工具還沒執行，run 就因權限拒絕而中止。
  - **背景**：文章 ID、發布的語言、網站那筆的 ID，每個語言的標題、網址、摘要、第一段開頭；文章還沒發布時明說。
  - **驗證**：文章是本題材的；每個通路 ID 都屬於這篇文章、通路類型相符；網站那筆是出版服務的；社群那筆**是本任務建立的**、回報的貼文**就是存下的內容**；網站與社群各列一次。
  - 規格的 `max_steps` 是 4；實作用 6（寫一次、回報一次，還能各修正一次；工具被拒後再寫一次）。
- **模擬模型**：每個發布語言一則貼文（「新報導：…」/「New: …」）→ 用工具回傳的內容回報。
- 前端「發布紀錄」顯示中文的通路與狀態（網站・已發布、社群貼文・草稿（未發出））。
- 測試整理：`tests/newsroom/conftest.py` 加上 `approve_and_publish` 與 `Newsroom.publish()`（寫稿 → 查核 → 接受 → 人核准 → 發布）；核准與發布改用 `build_policy_engine()`（完整規則）。修正幾個代理測試裡斷言訊息用到不存在的 `Task.last_error`（只在失敗時才會被讀到）。

### 驗證
- `tests/newsroom/test_distribution.py`（3 個）：未發布不能寫；發布後寫成草稿、兩語言連結、事件（網站已發布、社群草稿）、`produced`；重試回傳原筆、不同內容拒絕、仍只有兩筆；缺語言、多語言、太長一次列出；不能建立 `site`。
- `tests/newsroom/agents/test_marketing.py`（5 個）：
  - **真正的工作程序**跑研究 → 分析 → 撰稿 → 審稿 → 人核准與發布 → 行銷：成功、網站那筆就是出版服務的、社群草稿存在且狀態為草稿、兩語言貼文、連結與發布網址一致、事件；
  - **未發布的文章**：模型要寫貼文 → 權限引擎以「文章是 IN_REVIEW、不是 PUBLISHED」拒絕並記錄、run 中止、沒有任何社群草稿；
  - 背景與權限事實；驗證器（別的文章、通路類型對調、不存在的 ID、貼文與存下的不同、別的任務建立的、少了社群那筆）；工作程序找得到行銷行為。
- 後端 838 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check` 通過；web 213 個測試與 typecheck、lint 通過。
---

## T-514 · 新聞室工作流程範本、`register()` 與活動連結

### 做了什麼
T-203 的工作流程引擎只會跑固定的 DAG、而且每個節點都由代理執行；新聞室流程還需要不由代理執行的步驟（核准、發布）與「編輯要求修訂 → 再寫一輪」。這些在 runtime 做成通用機制（D-013），新聞室只宣告範本、註冊處理函式。

- **Runtime：服務步驟**（`runtime/services.py`）：範本節點 `NodeSpec(service="名稱")` 建出的任務輸入帶著名稱；工作程序每輪由 `ServiceDispatcher` 取出 READY 的服務任務（`FOR UPDATE SKIP LOCKED`，多個工作程序不會重複執行），在任務的交易裡呼叫註冊的處理函式。處理函式透過 `ServiceContext` 決定：`complete`（成功，引擎解鎖下游）、`request_approval`（等人決定）、`fail`（失敗，下游取消）；拋錯或沒有決定也算失敗，錯誤記在任務輸出。服務步驟沒有 run 與租約，任務狀態機新增 READY → SUCCEEDED / FAILED（`RUNNING` 必須有租約，資料庫有約束，不能繞過去）。
- **Runtime：迴圈**（`dag.Loop`）：`Loop(check, back_to, again, carry, max_rounds, round_label)`。`check` 成功且 `again(輸出)` 為真 → 新增 `back_to` … `check` 一輪任務（新任務依賴新一輪的上游與迴圈外的原任務；`carry(輸出)` 併入第一個任務的參數），原本等 `check` 的任務改等新一輪的 `check`，發 `WORKFLOW_RUN_EXTENDED`（第幾輪、新任務）；新一輪第一個任務直接 READY，並列在 `TASK_SUCCEEDED.unlocks`（3D 辦公室的交接動畫）。超過 `max_rounds` 還要再一輪 → 取消等待中的任務、工作流程 CANCELLED。範本建立時檢查迴圈的節點存在且 `back_to` 通往 `check`。
- **Runtime：核准掛鉤**：`ApprovalService.on_decided(action, hook)`，人做決定時在同一交易裡先執行掛鉤再讓任務前進；掛鉤失敗則決定不成立。
- **Runtime：活動連結**：`TaskManager.link_hooks`（各領域的 `activity_links(task, run)`）；代理思考 / 工作時（runner）與完成時（task manager）寫進 `agent_activity.detail.links`；在 savepoint 裡執行，出錯只記 log、不影響任務。
- **新聞室範本** `newsroom.story_to_article_v2`（`domains/newsroom/workflow.py`）：研究 → 分析 → 撰稿 → 審稿 → 核准（服務，role `human`）→ 發布（服務，role `system`）→ 推廣；審稿是迴圈的 check（`verdict == "revise"` → 再一輪撰稿 + 審稿，編輯的問題放進 `params.issues`，最多 2 輪，與 `review.MAX_REVISIONS` 一致：第 3 次要求修訂時編輯的工具已駁回文章並放棄題材，引擎再取消等待中的核准、發布、推廣）。任務顯示名稱用繁中（「撰稿：…」、「撰稿：…（第 2 輪）」）。
  - **核准步驟**：文章必須是 IN_REVIEW（編輯已接受）；先以系統身分呼叫 `approve_article`——公司開啟「查核通過自動核准」時直接核准並完成；否則權限回「需要核准」→ 建立 `approve_article` 核准（種類 article、摘要「核准發布：標題」、payload 有文章、題材、草稿群組）。**人的決定經掛鉤核准或駁回文章**（駁回 → 文章 REJECTED、題材 DROPPED，任務取消、下游跟著取消）。
  - **發布步驟**：`publish_article`（冪等），輸出網址與語言；失敗記錄原因。
  - `start_story`：走公司的 `start_workflow`（權限決定並記錄）、參數 `story_id` 與 `title`，題材 SELECTED → IN_PRODUCTION；不是 SELECTED 的題材不能開始。
  - 量測排程（發布後 +1h / +24h / +7d）留給 T-516。
- **活動連結**（`activity_links.py`）：研究 → 題材與來源；分析 → 題材的主張；撰稿 → 文章草稿 vN（還沒存稿時 → 題材的主張）；審稿 → 草稿 vN + 事實查核；推廣 → 發布紀錄。網址是 T-517 要做的管理頁面。
- `newsroom.register(runtime)`：兩個服務處理函式、核准掛鉤、活動連結；`newsroom.register_templates`。`app.build_runtime` 呼叫它，`Runtime` 多了 `services`；`build_worker` 接上 `ServiceDispatcher`（同一組公司篩選）。
- 前端：`WORKFLOW_RUN_EXTENDED` 顯示「工作流程加一輪・第 N 輪・M 個任務」；事件契約重新產生；`3d-office/03`、`platform/11` 補上新事件。

### 過程中的問題
- 原本想讓服務步驟經過 RUNNING（READY → RUNNING → SUCCEEDED），但資料庫約束「RUNNING ⇔ 持有租約」；服務步驟沒有租約，改為狀態機直接允許 READY → SUCCEEDED / FAILED，並寫進 lifecycles 的說明。
- `WORKFLOW_RUN_EXTENDED` 是新的 runtime 事件，事件目錄完整性測試與產生的契約要一起更新。

### 驗證
- `tests/runtime/test_services.py`（6 個，只用服務步驟、不需代理）：服務步驟跑完整個流程；**迴圈加兩輪**（任務順序、顯示名稱、依賴、`carry` 的參數、下游改等最後一輪、`WORKFLOW_RUN_EXTENDED`）；**超過輪數取消等待中的任務**、流程 CANCELLED；處理函式拋錯 / 沒決定 / 主動失敗 → 任務 FAILED 且錯誤記錄、下游取消、流程 FAILED；**等人核准**（核准掛鉤收到決定、之後下游完成、掛鉤不能重複註冊）；範本檢查迴圈。
- `tests/newsroom/test_workflow.py`（6 個，真正的工作程序、模擬模型、離線工具）：
  - **人核准**：研究到審稿成功 → 核准步驟等人（核准內容正確、文章 IN_REVIEW）→ 人核准 → 發布 → 推廣 → 全部成功、流程 SUCCEEDED、文章與題材 PUBLISHED、網站與社群草稿兩筆、交接依範本、**五個代理的活動都有正確連結**；
  - **自動核准**：沒有人被問，系統核准，文章發布；
  - **一輪修訂**：第一稿的一則主張失去證據 → 編輯退回 → 引擎加一輪（顯示名稱、編輯的問題帶進寫手參數、依賴分析）→ 第 2 版不再引用被駁回的主張 → 第二次審稿接受 → 核准步驟等的是新的審稿；
  - **修訂太多次**：每一稿都被退回 → 三輪後核准、發布、推廣取消、流程 CANCELLED、文章 REJECTED、題材 DROPPED；
  - **人駁回**：文章 REJECTED、題材 DROPPED、下游取消、流程 CANCELLED；
  - 只有 SELECTED 的題材能開始，流程參數正確。
- 後端 850 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check`、`gen-api-check` 通過；event-schema 8 個、web 213 個測試與 typecheck、lint 通過。
---

## T-518 · 模擬資料與示範新聞室

### 做了什麼
五個代理的模擬模型在 T-506 ～ T-513 已經各自完成，T-514 把它們串成工作流程；T-518 補上示範需要的部分，讓整條線在 `MODEL_PROVIDER=fake`、`TOOLS_PROFILE=fixture`（加上 `EMBED_PROVIDER=fake`）下從來源一路跑到發布，完全離線。

- **模擬模型的示範設定**（`simulation.py`，讀工作流程參數 `params.demo`——模擬模型像真的模型一樣從第一則訊息的 `Input:` 讀任務輸入）：
  - `pace_seconds`：每次回覆至少這麼久（上限 60 秒），在 3D 辦公室看得到代理在工作（3d-office/06 §6「可設定的延遲」）。
  - `revise_first_review`：**「第一次 review 要求 revise」的劇本分支**——即使查核通過，編輯第一次審稿（「已修訂 0 次」）也退回：「導言請先交代這件事對居民的意義，再談數字。」（missing_context、zh-TW、第 2 段）；寫手的修訂在第一段前加上導言（中文「對居民來說最重要的是：」、英文對應句），`change_summary` 寫明依哪些意見修改；第二次審稿接受。沒有這個設定時，只有查核不通過才會退回。
  - 編輯回報的決定改為依實際呼叫的工具（`request_revision` / `accept_draft`）。
- `workflow.start_story(..., demo=...)`：示範設定放進工作流程參數（只給模擬模型用）；`staff_newsroom`：補齊五位代理（Rae、Ana、Wren、Eli、Mika）。
- **示範新聞室**（`domains/newsroom/demo.py`）：`seed_demo`（公司「流明日報（示範）」、專案「流明市報導」、五位代理、兩個 fixture 來源——英文的 Lumen City News 與中文的流明市政府新聞稿；重複執行沿用）、`gather_stories`（立即讀取所有來源並分群，回傳開放中的題材、分數高的在前）、`pick`（標題含關鍵字的最佳題材）、`start_demo_story`（選定並啟動）。
- **`backend/scripts/seed_newsroom.py`**：`--start`（讀取、分群、啟動題材）、`--story`（題材關鍵字，預設 microgrid）、`--pace`、`--revise`；不是全離線設定時提醒。已在 e2e 資料庫實際執行一次：建立公司與兩個來源、選出「What a neighbourhood microgrid really costs」並啟動工作流程。
- RUNBOOK 新增「試跑新聞室（模擬模式）」；模型供應者表格改為 echo 與新聞室都有模擬。
- fixture 頁面沿用 T-500 ～ T-502 建立的流明市語料（兩個 feed、四個可擷取的頁面、JavaScript 頁與 PDF），沒有新增。

### 驗證
- `tests/e2e/test_newsroom_sim.py`：**從 fixture feed 到發布**——示範新聞室建立（重複執行沿用）、兩個來源讀到至少 5 個項目、分群成至少兩則題材、選出微電網題材、以修訂分支啟動 → 真正的工作程序跑到核准收件匣（兩輪撰稿與審稿、第一次退回的問題就是示範問題、第二次接受）→ 人核准 → 發布與推廣：流程 SUCCEEDED、題材與文章 PUBLISHED、發布的是第 2 版中英兩語、中文第一段有加上的導言、修改說明寫著編輯的意見、網站與社群草稿兩筆、至少 2 份證據、引用的主張（≥ 3）都通過查核規則、7 次代理執行全部完成、模型呼叫只有 fake、每個工具呼叫都有結束事件、五位代理的活動都有連結。
- `tests/newsroom/test_simulation.py`：`pace_seconds` 生效、上限 60 秒、格式不對或沒有時不延遲。
- 後端 852 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check`、`gen-api-check` 通過。
- 有一次全套測試出現 1 個失敗，之後連續三次全套與六次新測試都通過，無法重現；那次失敗的測試名稱沒有留下（之後的通過清掉了 pytest 的紀錄）。先記錄在這裡，若再出現會記下名稱並處理。
---

## T-515 · 公開站（中 / 英）與讀者計數 API

### 做了什麼
- **讀者計數的資料表**（`analytics_events`，migration `0020`）：文章、語言、種類（`view` 打開文章 / `read_complete` 讀到結尾）、`session_hash`、收到的 UTC 日期。**不存 IP 或任何關於讀者的資料**（platform/05 §8）：`session_hash` 是讀者瀏覽器每天產生的隨機 ID（16～64 個十六進位字元，資料庫也檢查格式），無法對應到人、也無法跨日追蹤。唯一鍵（文章、語言、種類、session、日）讓同一讀者同一天重複送出只算一次（重新整理、重送）。規格寫的 `ts` 用 `created_at` 表示，另加 `day` 作為去重的日界。彙總與 30 天後清除屬於 T-516。
- **公開讀取與計數**（`domains/newsroom/site.py`）：
  - `published_article(lang, slug)`：只給發布出去的那一版（`published_group_id`）、而且文章有以該語言發布；回傳標題、摘要、段落（只有類型與文字，不含主張 ID 等內部資料）、**資料來源**（文章引用的主張所依據的證據：標題、網站、連結，依第一次引用的順序、每個網址一次）、各語言的網址（語言切換與 hreflang）、公司名稱。
  - `published_articles(lang, company?)`：最新發布在前，最多 50 篇，可限定公司。
  - `record_beacon`：只接受已發布文章的已發布語言；`INSERT … ON CONFLICT DO NOTHING`。
- **API**（`api/routers/public.py`，不需權杖）：`GET /api/public/articles?lang=&company=&limit=`、`GET /api/public/articles/{lang}/{slug}`（沒發布或沒有該語言 → 404）、`POST /api/analytics/beacon`（`{article_id, lang, event_type, session_hash}` → 204；重複也回 204；文章 / 語言不對 → 404；格式不對 → 422）。OpenAPI 與前端型別重新產生。
- **前端公開站**（`app/(site)/[lang]`）：
  - 版頭（站名、另一語言的首頁連結）；`/zh-TW`、`/en` 最新報導；`/{lang}/articles/{slug}` 文章頁：標題、摘要、公司與發布日期（台北時間）、「閱讀其他語言」、段落（小標、段落、引文）、**資料來源**（`rel="noopener nofollow"`）、示範說明。`generateMetadata` 給標題、描述、canonical 與各語言的 `hreflang`。其他語言代碼 → 404。
  - 根版面（admin 頁面共用）是 `<html lang="zh-TW">`，公開站在自己的區塊設 `lang`，英文頁的內容仍標為英文。
  - `features/site/`：`api.ts`（沒有權杖的型別化讀取，伺服器端用 `SERVER_API_URL`）、`ArticleView`、`ArticleList`、`Beacon`（進頁送一次 `view`；文章結尾進入畫面時送一次 `read_complete`，用 IntersectionObserver）、`session.ts`（每日 ID 存 localStorage，換日就換；storage 被封鎖時用這一頁自己的 ID；送出失敗不影響讀者，`keepalive`）、`i18n.ts`（兩種語言與介面文字；站名預設「Autora 新聞 / Autora News」，不用示範公司的名字）。
  - `config.SERVER_API_URL`：伺服器端讀 API 的位址（`API_INTERNAL_URL`，預設同 `NEXT_PUBLIC_API_URL`）；docker-compose 的 web 服務設為 `http://api:8000`。`SITE_COMPANY` 可限定首頁的公司。
- RUNBOOK 前端一節加上公開站、`SITE_COMPANY`、`API_INTERNAL_URL`。

### 過程中的問題
- `Beacon.tsx` 與 `beacon.ts` 在不分大小寫的檔案系統上是同一個名字，TypeScript 拒絕；工具函式改名為 `session.ts`。
- alembic 產生的 migration 用單引號，改 revision ID 的替換沒有生效，已套用到開發資料庫的是隨機 ID；先 downgrade、改成 `0020` 再升級。
- 實際在瀏覽器試時，計數一開始失敗（`ERR_FAILED`）：用的是之前準備的 e2e 資料庫，還沒有 `0020` 的資料表，API 回 500 而 500 沒有 CORS 標頭；升級資料庫後正常。

### 驗證
- `tests/api/test_beacon.py`（4 個）：已發布文章中英兩種語言都能讀（標題、網址、各語言網址、段落不含內部資料、資料來源兩個網站依序）、列表與限定公司；草稿、沒有的語言、不合法的語言代碼 → 404 / 422；**同一 session 送三次只算一次**，換種類 / 語言 / session 各算一次；`session_hash` 太短、非十六進位、太長、像 email → 422，不存在的種類 → 422，不存在的文章或沒發布的語言 → 404。
- `src/features/site/site.test.tsx`（10 個）：文章頁（標題、小標、段落、引文、公司、日期、資料來源連結與 nofollow、英文連結與 hreflang）、打開時送一次瀏覽（網址、內容、keepalive、32 位十六進位 ID）、讀到結尾送一次讀完並停止觀察、首頁列表與空列表、每日 ID（同一天相同、隔天換新、沒有 storage、storage 壞掉或被封鎖）、送出失敗不拋錯、公開讀取（沒有 Authorization、404 → null、500 → 錯誤、列表的查詢參數）、語言判斷。
- **實際在瀏覽器看過**：在 e2e 資料庫以模擬模式跑完一篇（T-518 的示範）並核准發布，開 API（8001）與 Next（3001）：中文與英文文章頁、首頁都正常顯示；讀者計數存下一筆瀏覽與一筆讀完（開發模式的 React 重複執行多送的一次瀏覽被去重）；`/ja` 與不存在的文章 404；英文頁有兩個 `hreflang` 的 alternate 連結。
- 後端 856 個測試通過；`ruff`、`lint-imports`、`make db-check`、`gen-schema-check`、`gen-api-check` 通過；web 223 個測試與 typecheck、lint 通過。
---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `c4ba4ee` | 2026-09-19 | T-518 模擬模式的示範新聞室 | ✅ 執行編號 `35445028213`（e2e 5 分 13 秒、python 2 分 27 秒、web 1 分 7 秒） |
| `3487cca` | 2026-09-19 | T-514 新聞室工作流程、服務步驟、迴圈與活動連結 | ✅ 執行編號 `35443539975`（e2e 5 分 29 秒、python 2 分 20 秒、web 1 分 1 秒） |
| `800d5f9` | 2026-09-19 | T-513 行銷代理 | ✅ 執行編號 `35442758423`（e2e 5 分 10 秒、python 2 分 24 秒、web 1 分 10 秒） |
| `8df495f` | 2026-09-19 | T-511 編輯代理 | ✅ 執行編號 `35441424337`（e2e 4 分 26 秒、python 2 分 19 秒、web 1 分 2 秒） |
| `1f8ec8b` | 2026-09-19 | T-509 寫手代理 | ✅ 執行編號 `35440343501`（e2e 5 分 12 秒、python 2 分 6 秒、web 1 分 7 秒） |
| `26c233f` | 2026-09-19 | T-507 分析師代理 | ✅ 執行編號 `35439199271`（e2e 5 分 23 秒、python 2 分 11 秒、web 1 分 3 秒） |
| `b4e1ba8` | 2026-09-19 | e2e：畫布尺寸最多等 30 秒 | ✅ 執行編號 `35438923718`（e2e 5 分 15 秒、python 1 分 48 秒、web 1 分 2 秒） |
| `d383591` | 2026-09-19 | T-506 研究員代理 | ❌ 執行編號 `35438774325`（python、web 通過；e2e：CI 慢，畫布 5 秒內沒量好尺寸，與 T-506 無關）→ 見 `b4e1ba8` |
| `a36f35c` | 2026-09-19 | e2e：CI 上分析師忙 15 秒 | ✅ 執行編號 `35438599647`（e2e 5 分 35 秒、python 2 分 2 秒、web 1 分 6 秒） |
| `fb690bf` | 2026-09-19 | T-512 核准與發布 | ❌ 執行編號 `35438255555`（python、web 通過；e2e：CI 軟體算圖太慢，沒看到分析師 6 秒的忙碌狀態，與 T-512 無關）→ 見下一列 |
| `f90dbf3` | 2026-09-19 | T-510 確定性事實查核與 `run_fact_check` | ✅ 執行編號 `35432092570`（e2e 5 分 19 秒、python 1 分 55 秒、web 1 分 12 秒） |
| `871571b`、`43167d7` | 2026-09-19 | e2e：分析師的忙碌時間加長到 6 秒；開發紀錄 | ✅ 執行編號 `35431280017`、`35431294868`（e2e 5 分 9 秒、python 2 分 7 秒、web 1 分） |
| `cd255be` | 2026-09-19 | T-508 文章、雙語版本與 `write_draft` | ❌ 執行編號 `35430698537` 第 1 次（e2e：CI 太慢，沒看到分析師的忙碌狀態，與 T-508 無關）→ ✅ 重跑 e2e 通過（e2e 5 分 17 秒、python 1 分 49 秒、web 1 分 2 秒） |
| `1a02e14` | 2026-09-19 | T-505 主張與證據引文 | ✅ 執行編號 `35429514477`（e2e 5 分 24 秒、python 1 分 52 秒、web 1 分 7 秒） |
| `361d02f` | 2026-09-19 | T-504 故事、分群與去重 | ✅ 執行編號 `35428926753`（e2e 4 分 45 秒、python 1 分 41 秒、web 53 秒） |
| `7456a94` | 2026-09-19 | T-503 證據分段、`embed` binding、`search_evidence`；D-012 | ✅ 執行編號 `35427610141`（e2e 4 分 14 秒、python 1 分 39 秒、web 59 秒） |
| `e785fd7` | 2026-09-19 | T-502 `fetch_url` 與 Evidence、`read_evidence` | ✅ 執行編號 `35426803364`（e2e 5 分 13 秒、python 1 分 55 秒、web 1 分） |
| `074fae2` | 2026-09-19 | T-501 新聞來源與輪詢 | ✅ 執行編號 `35425897013`（e2e 5 分 2 秒、python 1 分 46 秒、web 1 分 8 秒） |
| `8dc5d52` | 2026-09-19 | 階段 5 開發計畫；T-500 `web_search`（Tavily + 離線語料） | ✅ 執行編號 `35424065962`（e2e 4 分 4 秒、python 1 分 45 秒、web 51 秒） |
