# Autora — Design Documents Index

Autora 是一個 AI Autonomous Company 平台。第一個 Business Domain 是 **AI Bilingual Newsroom**，
第一個「使用者可見」的介面是 **3D AI Office**（Autonomous Company Runtime 的可視化層）。

## 資料夾

| 資料夾 | 內容 | 狀態 |
|---|---|---|
| `platform/` | 平台層架構（Company Model、Agent Runtime、Agent Spec、Newsroom Domain、Business/Revenue、Permission、Memory、Model Gateway、Database、Event Catalog、API、Roadmap、Task Breakdown、Acceptance、Risks）— 16 份 | ✅ |
| `3d-office/` | 3D Office / Realtime / Agent State / Event Model / Newsroom Integration 與對應的 Roadmap、Task Breakdown、Acceptance、Risks — 12 份 | ✅ |

## 開發紀錄

`devlog/` — 每個階段一份實際執行紀錄（做了什麼、為什麼、遇到的問題、驗證結果），以繁體中文撰寫。從 `devlog/README.md` 開始。

## 本機運作手冊

`RUNBOOK.md` — 如何在 localhost 安裝、執行測試、實際運作 API 與工作程序、以 Docker 跑完整系統，以及常見問題。

## 決策紀錄

`DECISIONS.md` — 已拍板的 P0 決定（審批先用人、主語言 zh-TW、search 用 Tavily）與未決項的暫用預設。文件內以 `D-00x` 引用。

## 兩個資料夾的關係

- `platform/` 描述「公司如何運作」；`3d-office/` 描述「如何讓人看到它在運作」以及執行順序。
- **執行計畫以 `3d-office/09`、`3d-office/10` 為準**（Phase 1–6，T-101 ~ T-610）；`platform/13`、`platform/14` 只補 Phase 7（Revenue）、Phase 8（Multi-company）與第一輪 task 編號對應表。
- Event Envelope 以 `3d-office/03` 為準；完整目錄在 `platform/11`。
- DB 完整 schema 在 `platform/10`（已合併 `3d-office/07` 的 delta）。

## 閱讀順序（platform）

`01` 架構與原則 → `02` Company Model 與 Cycle loop → `03` Runtime → `04` Agent Spec → `05` Newsroom Domain → `06` Business → `07` Permission → `08` Memory → `09` Model Gateway → `10` DB → `11` Events → `12` API → `13`–`16`

## 閱讀順序（3d-office）

1. `01_SYSTEM_ARCHITECTURE.md` — 3D Office 在系統中的位置、十個核心問題的答案、產品定位
2. `02_AGENT_STATE_MODEL.md` — Runtime State 與 Visual State 的分離
3. `03_EVENT_MODEL.md` — 統一 Event Envelope、Catalog、持久化分類
4. `05_REALTIME_ARCHITECTURE.md` — WebSocket、重連、排序、去重、hydration、前端 state 邊界
5. `04_3D_OFFICE_ARCHITECTURE.md` — 場景、Avatar、動畫導演、效能、fallback
6. `06_NEWSROOM_INTEGRATION.md` — 6 個 Agent 的 MVP 流程、雙語模型、從 3D 進入內容
7. `07_DATABASE_MODEL.md` — 相對平台 schema 的變更
8. `08_REPOSITORY_STRUCTURE.md`
9. `09_DEVELOPMENT_ROADMAP.md` → `10_TASK_BREAKDOWN.md` → `11_MVP_ACCEPTANCE.md` → `12_RISKS.md`

## 不變的原則

- Three.js 是 Visualization Layer，不是 Runtime。3D 層無 business logic、不呼叫 LLM、不寫 DB、不推測 Agent 狀態。
- 正確資料流：`Agent Runtime → Company State (Postgres) → Event → WebSocket → Next.js store → React Three Fiber → Animation`
- Demo 使用 deterministic simulation 時，simulation = **真實 Runtime + Fake Model Provider + Fake Tools**，共用同一套 Event Schema / State Model / Task Model。沒有「假 agent」或「假 log」。
