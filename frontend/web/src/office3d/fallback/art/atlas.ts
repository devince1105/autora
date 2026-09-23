// The one sprite still drawn by hand (T-410 stage 4): the document a courier carries.
//
// Everything else on the 2D floor — the room, the furniture, the walls and now the people — is
// baked from the 3D office (D-026, D-027: ``tools/bake-sprites``, ``art/baked*.ts``). The paper is
// a flat white card in 3D too (``AgentAvatar``); four by five pixels, drawn at twice that, says so.

import { sprite } from "./sprites";

/** What somebody carries between desks. */
export const DOCUMENT = sprite(["wwww", "wvvw", "wwww", "wvvw", "xxxx"], { v: "#d9e2d5", w: "#f2f5ef", x: "#9aa89b" }, 4);
