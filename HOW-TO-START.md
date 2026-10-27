# How To Start

This repository is NemoGuard Pipeline Copilot, restructured to follow the
Axet `react-fastapi-flows` template's folder conventions (`renderer/` for the
frontend, `backend/app/` for the Python backend). Unlike the base Axet
template, this application still depends on external **PostgreSQL** and
**Temporal** services for durable incident workflow orchestration — these are
NOT replaced by the template's default SQLAlchemy/JSON/DuckDB storage.

## Prerequisites

- Node.js 18 or newer.
- Python 3.10 or newer.
- Docker + Docker Compose (recommended, provides Postgres + Temporal + all
  services in one command).
- Git.
- An NVIDIA NIM API key (`NVIDIA_API_KEY`) for the agent LLM calls.

## Local Development (Docker Compose — recommended)

1. Copy the example environment file and fill in secrets:

```bash
cp .env.example .env
```

Required variables: `NVIDIA_API_KEY`, `JWT_SECRET` (generate with
`python -c "import secrets; print(secrets.token_urlsafe(48))"`).

2. Start the full stack (Postgres, Temporal, API, Temporal worker, frontend,
   simulator):

```bash
docker-compose up -d --build
```

3. Open the services:

- NemoGuard API docs: `http://localhost:8000/docs`
- NemoGuard UI: `http://localhost:80`
- Simulator API: `http://localhost:8001/docs`
- Temporal Web UI: `http://localhost:8233`

To additionally run the optional LocalStack AWS-emulation lab:

```bash
docker-compose --profile lab up -d
```

## Local Development (without Docker)

You must have Postgres and a Temporal server reachable (e.g. `temporal
server start-dev`) before starting the app components below.

1. Install backend dependencies:

```bash
python -m venv .venv
source .venv/bin/activate   # or .\.venv\Scripts\Activate.ps1 on Windows
pip install -e .
pip install opentelemetry-api opentelemetry-sdk temporalio psycopg2-binary
```

2. Start the FastAPI backend:

```bash
python -m uvicorn backend.app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

3. Start the Temporal worker (separate terminal):

```bash
python -m backend.app.temporal_worker
```

4. Install and start the frontend:

```bash
cd renderer
npm install
npm run dev
```

Vite proxies `/api` requests to `http://127.0.0.1:8000` automatically (see
`renderer/vite.config.ts`).

## Configuration

Unlike the default Axet template, this app does not read `settings.json` for
backend/frontend wiring — configuration is environment-variable driven via
`.env` (see `.env.example`) and `config/*.yaml`:

- `config/capability_policy.yaml` — risk/autonomy overrides for the Governed
  Capability Gateway (`backend/app/capabilities/`).
- `config/action_policy.yaml` — legacy action policy rules.
- `config/scoring.yaml`, `config/demo.yaml` — scenario/demo scoring config.

Key environment variables (see `.env.example` for the full list):

- `POSTGRES_URL` — Postgres connection string.
- `TEMPORAL_URL` — Temporal server address (`host:port`).
- `NVIDIA_API_KEY` — required for all agent LLM calls.
- `JWT_SECRET` — required; signs/verifies auth JWTs.
- `ENV` — set to `development`/`dev`/`local` to enable the mock-login dev
  endpoint (`/api/v2/auth/mock-login`); any other value disables it.
- `NEMOGUARD_LOCALSTACK_LAB` — set to `1` to route capability execution/
  verification through the LocalStack lab instead of safe no-ops.

## Project Structure Mapping (original -> Axet-style)

| Original (NemoGuard-V2) | Axet-structured (this repo) |
|---|---|
| `frontend/` | `renderer/` |
| `src/` | `backend/app/` |
| `src/api/main.py` | `backend/app/api/main.py` |
| `src/domain/` | `backend/app/domain/` |
| `src/capabilities/` | `backend/app/capabilities/` |
| `src/temporal_worker.py` | `backend/app/temporal_worker.py` |

All Python imports (`from src.xxx import ...`) were rewritten to
`from app.xxx import ...` inside `backend/app/`, and to
`from backend.app.xxx import ...` in top-level `scripts/` and
`localstack_lab/` files that reach into the backend package.

## Validation

Run the backend tests:

```bash
python -m pytest
```

Build the frontend:

```bash
cd renderer
npm install
npm run build
```

Check backend health:

```bash
curl http://localhost:8000/docs
```

## Notes On Divergence From The Base Axet Template

This project intentionally keeps the following NemoGuard-specific
infrastructure instead of adopting the Axet template's lighter-weight
defaults, because the incident-commander domain requires them:

- **PostgreSQL** instead of SQLAlchemy/JSON/DuckDB local storage — incident,
  evidence, hypothesis, action-plan, and audit tables need relational
  integrity and concurrent multi-service access.
- **Temporal** for durable, resumable incident lifecycle workflows — the
  Axet template has no equivalent long-running-workflow primitive.
- **Docker Compose multi-service stack** (`postgres`, `temporal`, `api`,
  `temporal-worker`, `frontend`, `simulator`, optional `localstack`) instead
  of a single FastAPI process — this app genuinely needs an async
  worker and durable orchestration tier separate from the request/response
  API tier.

If you need a fully `.flows`-native, single-process variant, see the task
notes for the "simplified port" option instead of this one, which focuses on
folder/import conventions only.
