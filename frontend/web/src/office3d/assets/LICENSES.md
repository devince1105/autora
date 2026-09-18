# 3D 辦公室素材授權（T-404）

每個放進 `frontend/web/public/models/` 的檔案都必須列在這裡；`pnpm -F web check-assets` 會檢查（沒列出的檔案會讓檢查失敗）。

## 第三方素材

### Kenney — Mini Characters 1.0

| 項目 | 內容 |
|---|---|
| 作者 | Kenney（www.kenney.nl） |
| 授權 | **Creative Commons Zero（CC0 1.0）**，公眾領域：可商用、可修改、不需署名（作者歡迎署名，非必要） |
| 來源頁 | https://kenney.nl/assets/mini-characters |
| 下載檔 | `kenney_mini-characters.zip`（2,403,059 bytes），https://kenney.nl/media/pages/assets/mini-characters/bfc7e272b4-1774770718/kenney_mini-characters.zip |
| 壓縮檔 SHA-256 | `9e1d48e6d7b8479ebbe84df71eb5bd8e1b3f0da546dea641890dccc8a02d0999` |
| 取得日期 | 2026-09-19（使用者於對話中核准下載） |
| 包內授權檔 | `License.txt`：「Mini Characters (1.0) … License: (Creative Commons Zero, CC0)」 |
| 修改 | 無（原檔照用；只取 GLB 格式的 12 個人物與共用貼圖，沒有取輪椅與輔具模型） |

使用的檔案：

- `models/characters/character-female-a.glb`
- `models/characters/character-female-b.glb`
- `models/characters/character-female-c.glb`
- `models/characters/character-female-d.glb`
- `models/characters/character-female-e.glb`
- `models/characters/character-female-f.glb`
- `models/characters/character-male-a.glb`
- `models/characters/character-male-b.glb`
- `models/characters/character-male-c.glb`
- `models/characters/character-male-d.glb`
- `models/characters/character-male-e.glb`
- `models/characters/character-male-f.glb`
- `models/characters/Textures/colormap.png`

## 自製內容（非第三方）

- 房間、家具、植物、地板材質：全部由程式產生（`src/office3d/scene/`），沒有使用任何下載的模型或圖片。
- 風格參考（D-008、D-010）：《動物森友會》、《Good Job!》、使用者提供的等角辦公室渲染圖——**只參考風格，沒有使用或仿製其中任何素材**。
