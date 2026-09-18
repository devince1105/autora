"""Write the API's OpenAPI document for the web app's typed client (T-308).

    python backend/scripts/gen_openapi.py           # write frontend/web/src/api/openapi.json
    python backend/scripts/gen_openapi.py --check   # fail if it is stale (CI)

The frontend then generates TypeScript types from it (``pnpm -F web gen-api``). Both files
are committed, so a change to a response model shows up in review as a change to the types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "api"))

from autora_api.app import create_app  # noqa: E402

OUT = ROOT / "frontend" / "web" / "src" / "api" / "openapi.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is stale; run `make gen-api`", file=sys.stderr)
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
