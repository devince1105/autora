# Autora

AI Autonomous Company platform. First business domain: **AI Bilingual Newsroom**. First visible surface: **3D AI Office**.

Design documents live in [`logs/`](logs/README.md). Decisions in [`logs/DECISIONS.md`](logs/DECISIONS.md).

## Layout

```
apps/web         Next.js (admin console, 3D office, public site)
apps/api         FastAPI entry point (thin)
apps/worker      Worker entry point (scheduler, dispatcher, task loop)
packages/autora  The single Python package: runtime / company / realtime / domains / db / infra
packages/event-schema  Generated JSON Schema + TS types (source of truth is pydantic)
infra/           docker-compose, Dockerfiles
logs/            architecture & development plan
```

Dependency rule: `runtime` → nothing above it; `company` → `runtime`; `domains/*` → `runtime`, `company`; only `autora/app.py` imports everything.

## Prerequisites

- Python 3.12 (`python3.12` on PATH, or `make PYTHON=/path/to/python3.12 setup`)
- Node 22 + pnpm 9 (`.nvmrc` provided)
- Docker with Compose v2

## Quick start

```bash
make setup   # venv + pip install -e packages/autora[dev] + pnpm install + .env
make dev     # start Postgres (pgvector) on localhost:5434
make test    # pytest + vitest
```

Full stack in Docker: `make up` (api :8000, web :3000). Stop: `make down`.

Run API locally: `.venv/bin/uvicorn --app-dir apps/api main:app --reload`
Run worker locally: `.venv/bin/python apps/worker/main.py`
