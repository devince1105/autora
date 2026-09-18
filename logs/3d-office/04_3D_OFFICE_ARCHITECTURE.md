# 04 — 3D Office Architecture

技術：Next.js (App Router) + TypeScript + React Three Fiber + Three.js + drei。
原則：**3D 層是 store 的訂閱者，不是資料的擁有者。** 它讀 `realtimeStore`（domain 投影）與 `uiStore`（選取/相機），
只寫 `uiStore`（點擊選取）。它沒有 fetch、沒有 WebSocket、沒有 business logic。

---

## 1. 目錄與元件

```
apps/web/src/office3d/
├── OfficeCanvas.tsx            <Canvas> 設定、DPR、frameloop、WebGL 偵測 → fallback
├── scene/
│   ├── OfficeScene.tsx         組裝：Room + Zones + Agents + Courier + CameraRig + Lights
│   ├── Room.tsx                Floor / Walls / 簡單隔間（低模）
│   ├── Zone.tsx                一個工作區：Desk + Workstation(螢幕) + 位置標記
│   ├── layout.ts               ★ 固定佈局表：role → {zone, desk position, chair rotation, screen position}
│   └── Lights.tsx              Hemisphere + 1 directional（無陰影 MVP）+ 每桌一盞 point light（狀態燈）
├── agents/
│   ├── AgentAvatar.tsx         單一 GLB、SkeletonUtils.clone、AnimationMixer、pose 切換
│   ├── StatusIndicator.tsx     桌燈顏色 + 頭頂 badge（drei <Html> 或 sprite）
│   ├── Screen.tsx              螢幕材質：off/dim/active/alert（emissive 切換，不換貼圖）
│   └── Courier.tsx             handoff 走路：沿 layout 路徑移動 avatar 的「分身」或本體
├── visual/
│   ├── mapping.ts              activity → VisualState（純函式，見 02 §7）
│   ├── director.ts             event → VisualCue[]（純函式）+ cue queue 合併規則
│   └── cues.ts                 VisualCue 型別
├── camera/
│   └── CameraRig.tsx           OrbitControls（限制角度）+ focusOn(agentId) tween
├── interaction/
│   └── useAgentPicking.ts      raycast 點擊 → uiStore.selectAgent
├── fallback/
│   └── OfficeBoard2D.tsx       無 WebGL / 行動裝置：同一個 store 的 2D 卡片牆
└── assets/
    ├── avatar.glb              一個低模人形（≤ 3k tris），6 個 clips
    ├── desk.glb, chair.glb, monitor.glb（各 ≤ 500 tris）
    └── palette.ts              role → 顏色（用 vertex color / material color，不用貼圖）
```

**依賴規則**（ESLint `no-restricted-imports`）：`office3d/**` 只能 import `@/stores/realtime`（selectors）、`@/stores/ui`、`@/packages/event-schema` 的型別。禁止 `@/api/**`、`@/realtime/client`。

---

## 2. 場景佈局（MVP：一間辦公室、6 個 Agent）

```
                 z
        ┌────────────────────────────────────────┐
        │  CEO Office (玻璃隔間)     Meeting Table │
        │  [CEO desk]                (裝飾, 無互動)│
        ├────────────────────────────────────────┤
        │  Research Area        Editorial Area    │
        │  [Researcher] [Analyst]   [Writer] [Editor] │
        │                                          │
        │  Growth Area          Approval Desk (☐)  │
        │  [Marketing]          (human 審批的視覺位置)│
        └────────────────────────────────────────┘ x
```

- `layout.ts` 是唯一的座標來源；Desk、Avatar、Courier 路徑、Camera focus 都查它。
- 佈局按 **role** 鍵入（不是 agent_id），因為 agent 是公司資料，佈局是視覺設定。`realtimeStore.agents[].role` 決定坐哪。同一 role 有多個 agent（Phase 6）→ 同區域多張桌，`layout.ts` 提供 `seatsForRole(role, n)`。
- Meeting Room 與 CEO Office 在 MVP 是純幾何裝飾，不承載狀態。

---

## 3. Agent Avatar

### 3.1 資料 → 畫面

```
realtimeStore.agents[id]  ──select──▶  activity { state, detail, since }
                                          │
                                   visual/mapping.ts
                                          ▼
                          VisualState { pose, screen, deskLight, badge, bubble }
                                          │
               ┌──────────────────────────┼──────────────────────────┐
               ▼                          ▼                          ▼
        AgentAvatar (mixer)          Screen (emissive)        StatusIndicator (light+badge)
```

- `AgentAvatar` 用 `useStore.subscribe(selector, cb)`（Zustand transient subscription）而非 hook 重渲染：pose 變更 → `mixer.crossFade(from, to, 0.3)`。**React 不會因每個事件重渲染 avatar。**
- 一個 GLB、六個 clip：`sit_idle`、`sit_think`、`sit_type`、`sit_read`、`walk`、`slump`。`stand` 用 walk 的第 0 幀。
- 每個 avatar 是 `SkeletonUtils.clone(gltf.scene)`，共用 geometry/material；6–8 個 skinned mesh 在任何桌機都便宜。
- 角色辨識：顏色（palette）+ 頭頂 role label（`<Html>` 或 `<Text>`，距離遠時隱藏）。

### 3.2 Avatar 屬性（前端物件，非 domain）

```ts
type AvatarInstance = {
  agentId: string; role: Role;
  seat: Seat;                        // 來自 layout
  position: Vector3; rotation: Euler; // 目前實際位置（走路中會偏離 seat）
  pose: Pose; mixer: AnimationMixer;
  visual: VisualState;               // 最近一次 mapping 結果
  cueQueue: VisualCue[];             // 待播放的 cue（walk 等）
};
```

---

## 4. Animation Director（事件 → 動畫）

```
WS event ──▶ realtimeStore.applyEvent(event)      // domain 投影更新（reducer）
        └──▶ director.cuesFor(event, storeBefore)  // 純函式，產生 VisualCue[]
                    │
                    ▼
             cueQueue（per agent）──▶ useFrame 內的 CueRunner 逐一執行
```

| Event | Cue |
|---|---|
| AGENT_THINKING / WORKING / REVIEWING / WAITING / PAUSED | 無 cue；pose 由 mapping 直接決定（reducer 更新 activity → subscribe 觸發 crossFade） |
| AGENT_RUN_COMPLETED{handoff} 或 TASK_SUCCEEDED{unlocks} | `walk(agent → desk_of_role(unlock.required_role), carry:document, returnAfter:true)` |
| TASK_READY{required_role:"human"} / APPROVAL_REQUESTED | `walk(agent → ApprovalDesk, carry:document, returnAfter:true)` + ApprovalDesk 燈 blink_amber |
| AGENT_RUN_FAILED{final} | `flash(agent, red, 1s)` + pose slump（由 mapping） |
| ARTICLE_PUBLISHED | `celebrate(editor & writer, 1.5s)`（小動作，可省略） |
| CYCLE_STARTED | CEO 螢幕 alert 一次；無走路 |

**合併規則**（避免事件洪流變成動畫垃圾）：
- 同一 agent 的 pose 變更只取最新（reducer 已保證）。
- `walk` 若 queue 內已有相同目標，不重複加入。
- `walk` 進行中若該 agent 收到新的 run 開始（AGENT_RUN_STARTED），立即中止走路並瞬移回座位（狀態優先於動畫）。
- Cue 有 TTL（10 秒）：重連 replay 大量歷史事件時，director 對 `event.occurred_at < now - 10s` 的事件不產生 cue（只更新狀態）。

**沒有 timer-driven 動畫**：所有 cue 都由事件觸發；walk 的持續時間是視覺參數。

---

## 5. 互動

- 點擊：`useAgentPicking` 在 `<Canvas onPointerDown>` 用 R3F 內建 raycast（每個 avatar 的 `onClick`）→ `uiStore.selectAgent(id)`。
- 選取視覺：桌子外框 highlight（emissive）、相機 `focusOn(seat)`（tween 0.6s，可被使用者拖動中斷）。
- Hover：僅顯示 badge 放大；不觸發任何 fetch（fetch 在 Detail Panel，由 `uiStore.selectedAgentId` 驅動）。
- 鍵盤：`Esc` 取消選取；`1–6` 快速選取 role（開發便利）。

---

## 6. 相機與燈光

- `CameraRig`：OrbitControls，`minPolarAngle=0.3, maxPolarAngle=1.3`，`minDistance=6, maxDistance=30`，禁止 pan 出房間。預設「等角俯視」。
- 燈光：`HemisphereLight` + 一盞 `DirectionalLight`（MVP 無陰影；Phase 4 後段可加 contact shadow）。每桌一盞小 `PointLight` 作狀態燈（6 盞，便宜）。
- 無 post-processing（MVP）。

---

## 7. 效能計畫（MVP 優先可維護性）

| 項目 | 決定 |
|---|---|
| 目標 FPS | 桌機 60、筆電 ≥30。`<Canvas dpr={[1, 1.5]}>`。 |
| frameloop | `always`（有 idle 動畫）。頁面不可見時瀏覽器自動節流；額外：`document.hidden` 時 `mixer.timeScale=0`，並停止 cue 執行（cue 不丟，恢復時快進）。 |
| 幾何 | 全部低模，總三角形 < 50k。桌椅螢幕用 `<Instances>`（drei）合併 draw call。 |
| 貼圖 | MVP 零貼圖（純色材質）。若加，≤1024²、KTX2 壓縮。 |
| 動畫 | 一個 mixer / avatar；crossFade；不用 morph targets。 |
| Culling | Three.js 預設 frustum culling；房間小，效益有限但不關。 |
| 載入 | `/office` route 用 `next/dynamic({ ssr:false })` 載入整個 `office3d`；GLB 用 `useGLTF.preload`；顯示 2D 骨架直到 ready。 |
| 記憶體 | 長時間開啟的主要風險是 **store 內的事件累積**，不是 3D。`recentEvents` ring buffer 上限 500；tasks 完成後 10 分鐘清出。 |
| 行動裝置 / 無 WebGL | `OfficeCanvas` 啟動時偵測 `WebGL2` 與 `matchMedia(max-width:768px)` → 直接渲染 `OfficeBoard2D`（同 store）。不嘗試在手機跑 3D。 |
| WebGL context lost | 監聽 `webglcontextlost` → 顯示 overlay「3D 暫停」+ 按鈕重建；不自動無限重試。 |
| CPU | Zustand transient subscribe 避免 React 重渲染；每幀只做 mixer.update 與 cue 步進。 |

明確不做：LOD、baked lighting、occlusion、worker-thread 渲染、custom shader。

---

## 8. 2D Fallback（OfficeBoard2D）

- 一個 grid：每個 agent 一張卡（角色色、狀態 badge、current task、tool、since、最近完成）。
- Handoff 以「卡片間箭頭閃一下」呈現。
- 它消費**完全相同**的 `realtimeStore` selectors 與 `visual/mapping.ts`。這也是 3D 層的參考實作：任何 3D 顯示不出的資訊，先在 2D 卡確認 store 有沒有。

---

## 9. 3D 層的測試

- `visual/mapping.test.ts`：對照表全覆蓋。
- `visual/director.test.ts`：事件序列 → cue 序列（含合併、TTL、中止規則）。
- `layout.test.ts`：每個 role 有座位、座位不重疊、Courier 路徑在房間內。
- 元件 smoke：`@react-three/test-renderer` 掛載 `OfficeScene`，餵 fake store，斷言 avatar 數量與 pose。
- 視覺回歸（Phase 4 後段，可選）：Playwright 對 `/office?fixture=…` 截圖比對。

---

## 10. 資產計畫

- 一個人形 GLB（可先用 Mixamo 風格低模 + 自製 6 clips，或 Kenney/Quaternius 類 CC0 低模）。**版權必須確認**，見 12_RISKS。
- **（D-008）風格參考《動物森友會》，辦公室場景另參考《Good Job!》（明亮色彩、Q 版上班族、俏皮的辦公室佈局）**：Q 版比例、圓潤造型、柔和粉彩、溫馨辦公室。只參考風格，不使用任天堂的任何素材或可辨識的仿製；只用 CC0 素材（候選 Quaternius、Kenney、KayKit），來源與授權記入 `LICENSES.md`。
- 家具 3 個 GLB。所有資產進 `public/models/`，透過 `useGLTF`。
- 不做角色客製化系統。
