# 12 — Risks & Open Questions

---

## 1. 架構風險

| # | 風險 | 嚴重度 | 緩解 / 決策 |
|---|---|---|---|
| R1 | **3D 變成第二個真相來源**：有人為了「動畫好看」在前端加狀態推導或 timer | P0 | ESLint 邊界（office3d 只讀 store）；AC-S6 靜態掃描；`visual/mapping.ts` 是唯一狀態→畫面知識，有測試；code review checklist |
| R2 | **Snapshot 與 reducer 漂移**：兩邊各自演化，重連後畫面錯 | P0 | 投影規則單一來源（Python）+ 跨語言契約測試（AC-S3）；任何新 event 必須同時更新 projection 與 reducer 否則 CI 失敗 |
| R3 | **事件洪流**：tool 事件多、progress 事件高頻，WS 與 UI 卡 | P1 | progress 走 ephemeral 且限速；持久化事件每 run 上限（max_steps 已限制）；bounded queue + SNAPSHOT_REQUIRED；ring buffer |
| R4 | **NOTIFY 遺失或 API 重啟 cursor 遺失** | P1 | 2 秒 poll fallback；cursor 以 head_seq 起始 + 前端 since 補洞；`realtime_cursors` 可選 |
| R5 | **Agent Activity 與 Task/Run FSM 不一致**（例如 run FAILED 但 activity 仍 WORKING） | P1 | ActivityService 只在 FSM 轉換的同一 TX 呼叫；一致性測試（`last_event_seq`）；reconciliation job 每小時修正孤兒（並記錄為 bug） |
| R6 | **Handoff 視覺誤導**：動畫顯示 A 走向 B，但 B 的 task 其實被 CANCELLED | P2 | cue 由 `unlocks` 產生；B 若在 walk 期間收到 TASK_CANCELLED，cue 中止；狀態優先於動畫 |
| R7 | **雙語內容漂移**：兩語言事實不一致 | P1 | 共用 claim_ids validator；fact-check 語意層對兩語各做一次；Editor 一次審兩語 |
| R8 | **Simulation 與正式 Runtime 分岔** | P1 | simulation 只替換 provider 與 tool 資料來源；CI 同時跑 fake 與（每日）真模型 smoke；禁止 simulation 專用事件 |

## 2. 產品 / 內容風險

| # | 風險 | 嚴重度 | 緩解 |
|---|---|---|---|
| R9 | **幻覺與斷章取義** | P0 | Evidence-first；確定性 fact-check；MVP 人審核發布；trust_level 低的來源不可單獨支持 claim |
| R10 | **版權**：快照、引文、3D 資產 | P0 | 快照僅內部；引文長度上限 validator；3D 資產授權記錄（T-404 LICENSES.md）；不用未授權模型。**已落實（2026-09-19）**：人物用 Kenney Mini Characters（CC0），`pnpm -F web check-assets` 在 CI 檢查每個素材檔都列在 LICENSES.md；房間與家具全為程式產生（D-010） |
| R11 | **成本失控** | P0 | CostGuard 三層；每日 cap；Dashboard Expenses 即時；simulation 零成本 demo |
| R12 | **3D 讓人誤以為「即時」= 「正確」** | P2 | 面板永遠顯示 trace 入口；連線狀態與「資料時間」可見 |

## 3. 技術風險

| # | 風險 | 嚴重度 | 緩解 |
|---|---|---|---|
| R13 | R3F/Three 版本升級破壞 skinned mesh clone 或 drei API | P2 | 鎖版本；avatar 測試用 test-renderer |
| R14 | 長時間開啟的 WebGL context lost | P2 | overlay + 手動重建；不自動重試 |
| R15 | Postgres-as-queue + LISTEN 在多 API 副本下的重複廣播 | P2 | 每 process 獨立 cursor 與 socket 集合，不會重複送同一 socket；>3 副本再引入 Redis |
| R16 | Structured output 不穩定造成 REVIEWING/REPAIRING 循環頻繁 | P1 | repair_limit；FAILED 可見；evaluator 回饋精確 |
| R17 | 前端 bundle 過大（three + drei） | P2 | dynamic import；只在 `/office` 載入 |

## 4. Open Questions（影響架構者標 P0）

| # | 問題 | 等級 | 若無答案的預設 |
|---|---|---|---|
| Q1 | Approval 在 MVP 是否一定要人？ | ✅ 已決（D-001） | **人核准**；demo 可用 policy flag 切 auto（本身是 HUMAN action） |
| Q2 | 雙語的主語言與「只發一種語言」是否允許？ | ✅ 主語言已決（D-002） | **主語言 zh-TW**；單語發布未決，暫用兩語皆需 |
| Q3 | Web search / embedding 供應商 | search ✅（D-003）／embedding 未決 | **search: Tavily**（結果只是候選，evidence 仍由 fetch_url 快照）；embedding: 抽象 `embed` alias，MVP 用 OpenAI-compatible adapter |
| Q4 | 3D 資產來源：自製、CC0、或購買？ | P0（Phase 4 gate） | ✅ 已決定（D-008）：CC0 低多邊形人物，風格參考《動物森友會》（只參考風格，不用任天堂素材）。T-404 選定 Kenney Mini Characters 1.0（CC0）；場景見 D-010 |
| Q5 | 每日預算數量級 | P1（Phase 6 gate） | $10/day，max_workflows_per_cycle=5 |
| Q6 | 是否需要多人同時操作（審批權限）？ | P2 | 單一 operator |
| Q7 | 公開站是否與 admin 同一部署？ | P2 | 同一 Next.js app 兩個 route group |
| Q8 | Marketing 的第一個真實對外通路是什麼？ | P2（Phase 5 之後） | MVP 只產文案草稿 |

## 5. 明確拒絕的方案（避免日後重提）

- 前端從 tasks/runs 推導 agent 狀態 → 違反 R1，拒絕。
- 用 WS 傳 command → 拒絕；所有操作走 REST Command + PolicyEngine。
- Event sourcing / 用 events 重建 domain state → 拒絕；events 是影子。
- 獨立 `agent-runtime` 服務 → 拒絕；同套件不同進程。
- Redis / Kafka 在 MVP → 拒絕；Postgres LISTEN/NOTIFY + outbox 足夠。
- 在 3D 中放操作按鈕 → MVP 拒絕；面板（HTML）承載操作。
