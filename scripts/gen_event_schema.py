"""Write the generated event contract into packages/event-schema.

make gen-schema            # regenerate
make gen-schema-check      # fail if the committed output is stale (CI)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autora.app import load_event_catalogs
from autora.runtime.events.codegen import generate
from autora.runtime.events.schema import registered_event_types

ROOT = Path(__file__).resolve().parents[1]
TS_OUT = ROOT / "packages/event-schema/src/generated.ts"
JSON_OUT = ROOT / "packages/event-schema/schema/events.catalog.json"


def _display(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def render() -> dict[Path, str]:
    load_event_catalogs()
    source, catalog = generate(registered_event_types())
    return {TS_OUT: source, JSON_OUT: json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="exit 1 if outputs are stale")
    args = parser.parse_args()

    stale = []
    for path, content in render().items():
        current = path.read_text() if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(_display(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            print(f"wrote {_display(path)}")

    if stale:
        print("event schema is stale; run `make gen-schema`:", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
