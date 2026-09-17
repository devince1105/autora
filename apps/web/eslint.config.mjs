// Frontend boundaries (logs/3d-office/08_REPOSITORY_STRUCTURE.md §3, 04_3D_OFFICE_ARCHITECTURE.md §1).
//
// The 3D office is a read-only subscriber of the stores: it never fetches, never opens sockets,
// never reaches into features. The realtime layer never depends on UI. Features use the office
// only through its public <OfficeCanvas/> component.
import tseslint from "typescript-eslint";

const importsOf = (dir) => [`@/${dir}/*`, `@/${dir}`, `**/${dir}/*`];

export const boundaries = [
  {
    files: ["src/office3d/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [...importsOf("api"), ...importsOf("realtime"), ...importsOf("features"), ...importsOf("app")],
              message:
                "office3d is a read-only view: read state via @/stores selectors, never call the API, the realtime client, features or routes.",
            },
          ],
        },
      ],
      "no-restricted-globals": [
        "error",
        ...["fetch", "WebSocket", "EventSource", "XMLHttpRequest"].map((name) => ({
          name,
          message: "office3d must not do I/O; data arrives through the realtime store.",
        })),
      ],
    },
  },
  {
    files: ["src/realtime/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [...importsOf("features"), ...importsOf("office3d"), ...importsOf("app")],
              message: "realtime must not depend on UI (features, office3d, routes).",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/features/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              // No bare "@/office3d" pattern here: excluding the directory itself would stop the
              // negations below from re-allowing OfficeCanvas (gitignore semantics).
              group: ["@/office3d/**", "**/office3d/**", "!@/office3d/OfficeCanvas", "!**/office3d/OfficeCanvas"],
              message: "Use the office only through @/office3d/OfficeCanvas; its internals are private.",
            },
          ],
        },
      ],
    },
  },
];

export default tseslint.config(
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  ...tseslint.configs.recommended,
  ...boundaries,
);
