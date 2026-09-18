// Checks the 3D assets (T-404): `pnpm -F web check-assets`.
//   1. every glTF under public/models passes the Khronos glTF validator (no errors);
//   2. every file under public/models is listed in src/office3d/assets/LICENSES.md;
//   3. a triangle report, and no character over the per-avatar budget (04 §10: <= 3k).
// Which clips exist per character is checked by src/office3d/assets/assets.test.ts.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import validator from "gltf-validator";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODELS = join(WEB, "public", "models");
const LICENSES = readFileSync(join(WEB, "src", "office3d", "assets", "LICENSES.md"), "utf8");
const AVATAR_BUDGET = 3000;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

function gltfJson(bytes) {
  const length = bytes.readUInt32LE(12);
  return JSON.parse(bytes.subarray(20, 20 + length).toString("utf8"));
}

function triangles(json) {
  let count = 0;
  for (const mesh of json.meshes ?? []) {
    for (const p of mesh.primitives) {
      const accessor = json.accessors[p.indices ?? p.attributes.POSITION];
      count += Math.floor(accessor.count / 3);
    }
  }
  return count;
}

const problems = [];
const rows = [];
const all = files(MODELS);
for (const file of all) {
  const rel = `models/${relative(MODELS, file).split("\\").join("/")}`;
  if (!LICENSES.includes(`\`${rel}\``)) problems.push(`${rel}: not listed in LICENSES.md`);
  if (!file.endsWith(".glb")) continue;
  const bytes = readFileSync(file);
  const report = await validator.validateBytes(new Uint8Array(bytes), {
    uri: rel,
    externalResourceFunction: (uri) =>
      Promise.resolve(new Uint8Array(readFileSync(join(dirname(file), decodeURIComponent(uri))))),
  });
  const json = gltfJson(bytes);
  const tris = triangles(json);
  rows.push({ file: rel, triangles: tris, animations: json.animations?.length ?? 0, errors: report.issues.numErrors, warnings: report.issues.numWarnings });
  if (report.issues.numErrors > 0) {
    const first = report.issues.messages.filter((m) => m.severity === 0).slice(0, 3).map((m) => `${m.code} ${m.pointer ?? ""}`);
    problems.push(`${rel}: ${report.issues.numErrors} validator error(s): ${first.join("; ")}`);
  }
  if (rel.startsWith("models/characters/") && tris > AVATAR_BUDGET) problems.push(`${rel}: ${tris} triangles > ${AVATAR_BUDGET}`);
}

console.table(rows);
console.log(`${all.length} files, ${rows.length} glTF, largest avatar ${Math.max(...rows.map((r) => r.triangles))} triangles`);
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log("assets OK");
