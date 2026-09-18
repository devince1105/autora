# 開發紀錄 04 — 階段 4 3D 辦公室

- 期間：2026-09-18 起（進行中）
- 目標：把同一個即時 store 以 3D 辦公室呈現；可以點選代理、看到交接動畫與狀態燈，沒有 WebGL 時改用 2D 看板。
- 驗收條件（`logs/3d-office/09_DEVELOPMENT_ROADMAP.md` 階段 4）：連續開啟 2 小時（模擬模式每 5 分鐘跑一輪），記憶體不持續成長（heap 差 < 50 MB）、FPS ≥ 30；點任一代理，右側面板 300 毫秒內顯示即時狀態。
- 設計依據：`3d-office/04_3D_OFFICE_ARCHITECTURE.md`（元件、佈局、效能、2D 備援、測試）、`3d-office/02_AGENT_STATE_MODEL.md` §7（活動狀態 → 視覺狀態）、`3d-office/05_REALTIME_ARCHITECTURE.md` §5（3D 只讀 store）。
- 風格（D-008）：CC0 低多邊形人物，畫風參考《動物森友會》，辦公室場景參考《Good Job!》；只參考風格，不用任天堂素材。
- 使用模型：Claude Opus 5（未另行指定前）。

## 開發計畫

任務來自 `3d-office/10_TASK_BREAKDOWN.md`。3D 層只讀 store（`stores/realtime`、`stores/ui`），不呼叫 API、不開連線；這條界線已由 ESLint 規則（`eslint.config.mjs` 的 `office3d` 區塊）強制。

| 任務 | 內容 | 依賴 | 狀態 |
|---|---|---|---|
| T-401 | OfficeCanvas：WebGL 偵測、動態載入、DPR、頁面隱藏時停止繪製、WebGL context 遺失的處理、切換 2D | T-307 | ✅ |
| T-402 | `layout.ts` 與房間 / 工作區（role → 座位、`seatsForRole`、桌椅用 Instances） | T-401 | ⏳ |
| T-403 | `visual/mapping.ts`：活動狀態 + 角色 → 視覺狀態（02 §7 對照表） | T-108 | ⏳ |
| T-404 | 人物與家具素材（CC0 低多邊形 GLB、6 個動作、授權紀錄 `LICENSES.md`、三角形數檢查） | — | ⏳ |
| T-405 | AgentAvatar（GLB 複製、動畫混合、transient subscribe：store 變化不觸發 React 重繪） | T-402、T-403、T-404 | ⏳ |
| T-406 | 螢幕與狀態指示（螢幕亮度、桌燈顏色 / 閃爍、頭頂標籤） | T-402、T-403 | ⏳ |
| T-407 | Animation Director 與 CueRunner（事件 → 動畫提示，合併 / 逾時 / 中止規則） | T-305、T-403 | ⏳ |
| T-408 | Courier：交接時走到目標桌或審批桌再回座 | T-405、T-407 | ⏳ |
| T-409 | 相機與點選（OrbitControls 限制、focusOn、點選 → UI store） | T-405、T-307 | ⏳ |
| T-410 | OfficeBoard2D：2D 備援看板（同一個 store 與 mapping） | T-403、T-305 | ⏳ |
| T-411 | `/office` 頁整合（畫布 + 代理面板 + 迷你 Dashboard + 連線狀態） | T-401 ~ T-410、T-310 | ⏳ |
| T-412 | 效能與長時間測試（2 小時模擬、heap 與 FPS 紀錄） | T-411 | ⏳ |

建議順序：T-401 → T-403 → T-402 → T-410（先有不需要素材的部分：畫布、對照表、佈局、2D 看板）→ T-404（素材）→ T-405 → T-406 → T-407 → T-408 → T-409 → T-411 → T-412。

### 進入條件與帶過來的事項

- **進入條件**：「階段 3 驗收 + 投影契約測試在 CI」。✅ 階段 3 於 2026-09-18 驗收通過（T-315）；契約測試在 CI 的 python 工作中執行。
- 帶過來的：
  - 即時 store、UI store（含 `cameraMode`、`selectedAgentId`）、代理面板都已存在，`/office` 直接重用；
  - 瀏覽器端到端測試的環境（`frontend/web/e2e/stack.ts`）可重用來看 3D 畫面：這個環境用測試專用權杖，**我可以在 Playwright 裡實際看到登入後的畫面並截圖**（開發伺服器那邊的權杖畫面我不輸入）；
  - 素材的授權要逐一記錄（D-008、`12_RISKS` Q4）。

---

## T-401 · OfficeCanvas

### 做了什麼
- 新頁面 **`/office`**（Dashboard 右上角「辦公室」連結）：標頭有「自動 / 3D / 2D」切換（寫在網址 `?view=3d|2d`，可分享）、Dashboard 連結、連線狀態；下方是辦公室畫布。
- `office3d/OfficeCanvas.tsx`（3D 層唯一對外的元件，ESLint 規則已限制）：
  - **選擇 3D 或 2D**（`capabilities.ts`，純函式）：沒有 WebGL 2 一律 2D（就算選了 3D）；窄螢幕（≤ 768 px）自動用 2D，但可手動切到 3D；偵測用的 WebGL context 用完立即釋放。只在瀏覽器端偵測一次，伺服器端渲染時先顯示空白區。
  - **動態載入**：WebGL 部分（`Canvas3D.tsx`）用 `next/dynamic`（`ssr: false`）只在選了 3D 時才下載。
  - **DPR** `[1, 1.5]`；**分頁隱藏時停止繪製**（`frameloop="never"`）。
  - **WebGL context 遺失**：顯示「3D 暫停」與「重新建立 3D」「改用 2D 看板」兩個按鈕；**不自動重試**。重新建立 = 換一個新的畫布（新的 context）；瀏覽器自己恢復 context 時提示自動消失。
- `Canvas3D.tsx` 目前只有相機、燈光與地板（暖色粉彩，D-008）；房間、桌椅與人物從 T-402 開始。
- `fallback/OfficeBoard2D.tsx`：暫時的 2D 看板，列出代理的名字、角色、狀態；T-410 再依視覺對照表做完整版。

### 決策：React 釘在 19.2.8（D-009）
安裝 React Three Fiber 9.7（最新穩定版）時出現 peer 警告：它只支援 `react <19.3`，並內建對應 React 19.2 的 reconciler，而專案原本用 19.3.0。錯配是未經測試的組合，所以改用 19.2.8（同一大版本內的小降版，Next 16.3.5 支援），等 R3F 支援 19.3 再升級。改版後原有全部測試與階段 3 的兩個驗收測試都通過。

### 過程中的問題
- 安裝套件時 pnpm 重整 `node_modules`，開著的開發伺服器（3000）因此找不到套件、出現一連串錯誤；重啟開發伺服器後恢復正常。**之後安裝套件要順手重啟開發伺服器。**
- Playwright 的 CSS 選擇器 `[data-office-mode=3d]` 不合法（屬性值以數字開頭必須加引號）；Next 的路由播報器也是 `role="alert"`，測試改以文字找「3D 暫停」提示。

### 驗證
- `office-canvas.test.tsx`：13 個通過——3D / 2D 選擇的完整對照、`?view=` 解析、WebGL 2 偵測（含釋放偵測用 context、偵測拋錯）、無 WebGL 顯示 2D 與原因、窄螢幕、分頁隱藏停止繪製、context 遺失的提示與**只在按下按鈕時重建**、瀏覽器自行恢復。
- **真實瀏覽器**（`e2e/office.spec.ts`，Playwright + 測試環境，本機 Chrome）：3 個通過——桌機得到 WebGL 2 畫布且 context 正常；用 `WEBGL_lose_context` 真的讓 context 遺失 → 出現提示 → 按「重新建立 3D」得到新的可用 context；`?view=2d`、手機寬度（390 px）、停用 WebGL 2 的瀏覽器都顯示 2D 看板（列出 3 位代理）。我看過截圖：3D 畫面有打光的地板與暖色背景，連線狀態「即時」。
- CI 的 Linux 環境沒有 GPU：Playwright 在 CI 加上 SwiftShader 參數（軟體 WebGL）。
- web 共 110 個測試、event-schema 8 個通過；`typecheck`、`lint`、`next build`（新路由 `/office`）、`gen-api:check` 以結束碼確認通過；`make e2e` 5 個（辦公室 3 個 + 階段 3 驗收 2 個）通過。
- 開發伺服器重啟後 `/office` 編譯無錯誤、主控台無錯誤（停在權杖畫面）。

---

## 提交紀錄

| 提交 | 日期 | 內容 | 持續整合 |
|---|---|---|---|
| `857f647` | 2026-09-18 | T-401 OfficeCanvas 與 `/office`；D-009 React 19.2.8；階段 4 開發計畫 | ✅ 執行編號 `35340646575`（e2e 2 分 36 秒：辦公室 3 個含 CI 軟體 WebGL、階段 3 驗收 2 個；python 1 分 57 秒、web 37 秒） |
