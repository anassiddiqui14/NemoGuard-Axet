# How to Start NemoGuard V22

NemoGuard uses a React/Vite renderer (`renderer/`) and FastAPI backend (`backend/app/`). The current `axet-migration` branch supports two practical execution modes:

1. **Standalone/local** — PostgreSQL + optional Temporal + approved aXet-compatible LLM configuration.
2. **aXet Flows cloud** — `axet-spa-app` + `python-agent` + `python-gateway`, with aXet-managed identity/LLM injection. See `DEPLOY-TO-AXET.md`.

## Prerequisites

- Python 3.10+
- Node.js 18+ (Node 20+ recommended)
- Git
- For Docker mode: Docker + Docker Compose
- For AI-agent calls outside aXet cloud: approved values for the aXet/OpenAI-compatible environment variables in `.env.example`

## Local development with Docker Compose

1. Copy the example environment:

```bash
cp .env.example .env
```

2. Set a strong `JWT_SECRET`.

3. If you want real LLM-backed agents locally, populate the approved aXet-compatible values:

```text
AXET_LLM_BASE_URL
OPENAI_API_KEY
AXET_MODEL
AXET_PROJECT_ID
AXET_USER_ID
DEPTAPPS_WEB_BACKEND_JWT_TOKEN
```

The application can still start without those values, but LLM-backed agent calls will report configuration errors.

4. Start the stack:

```bash
docker compose up -d --build
```

Services:

- UI: `http://localhost/`
- API docs: `http://localhost:8000/docs`
- Simulator API: `http://localhost:8001/docs`
- Temporal UI: `http://localhost:8233`

Optional LocalStack action lab:

```bash
docker compose --profile lab up -d
```

## Local development without Docker

### Backend

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

Set at minimum:

```text
JWT_SECRET=<strong-random-secret>
NEMOGUARD_DB_BACKEND=postgres
POSTGRES_URL=<your-postgres-url>
```

For LLM-backed agents also set the aXet-compatible variables listed above.

Run:

```bash
python -m uvicorn backend.app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Temporal

Temporal is optional at runtime. To use durable workflow orchestration:

```text
NEMOGUARD_TEMPORAL_ENABLED=true
TEMPORAL_URL=localhost:7233
```

and run the worker in a separate terminal:

```bash
python -m backend.app.temporal_worker
```

If Temporal is disabled, NemoGuard uses the implemented deterministic/direct fallback path. The V22 UI reports that workflow mode explicitly.

### Frontend

Always install dependencies for the platform on which you are building. Do not reuse a `node_modules` directory copied from another OS.

```bash
cd renderer
npm ci
npm run dev
```

Production validation:

```bash
npm run build
```

## SQLite

SQLite remains useful for tests/diagnostics:

```text
NEMOGUARD_DB_BACKEND=sqlite
NEMOGUARD_SQLITE_PATH=/tmp/nemoguard.db
```

Do **not** treat `/tmp` as durable storage. The aXet team has also advised against using SQLite on `/internal-storage-files/files` for application database persistence because of locking/journaling behavior. See `V22_KNOWN_LIMITATIONS.md`.

## Authentication modes

Standalone/local:

```text
AXET_FLOWS_AUTH=false
```

uses NemoGuard JWT authentication.

In aXet Flows, identity is supplied by the platform and backend authorization remains enforced by NemoGuard role/tenant/workspace checks.

## V22 starting point

After sign-in, start at **Production Overview**. It is backed by `GET /api/v2/operations/snapshot` and intentionally uses only real persisted state.

For the intended leadership/demo flow see `V22_DEMO_GUIDE.md`.
