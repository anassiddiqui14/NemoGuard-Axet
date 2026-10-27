# NemoGuard Pipeline Copilot (Axet-structured)

NemoGuard Pipeline Copilot is an AI-driven, real-time Incident Commander platform powered by NemoClaw agents. It monitors complex data pipelines, triages anomalies across schemas, data quality, and compute layers, and automatically generates actionable recovery plans using autonomous AI agents.

This repository has been restructured to follow the **Axet `react-fastapi-flows` template conventions** (`renderer/` for the frontend, `backend/app/` for the Python backend) while keeping its original external service dependencies: **PostgreSQL** and **Temporal**. See `HOW-TO-START.md` for the full Axet-style setup guide.

## Project Structure

- `renderer/` - React frontend built with Vite, TailwindCSS, and Lucide Icons (renamed from `frontend/` to match the Axet template's `renderer/` convention). Features real-time SSE event streaming for agent activity and a fully responsive enterprise-grade dashboard.
- `backend/app/` - Backend Python application (FastAPI), relocated from the original `src/` to match the Axet template's `backend/app/` convention.
  - `backend/app/api/` - REST API endpoints and SSE endpoints.
  - `backend/app/domain/` - Orchestrator and agent definitions using LLM APIs to drive the NemoClaw agents.
  - `backend/app/capabilities/` - Governed Capability Gateway (typed action execution + policy + verification).
- `data/` - SQLite/legacy artifacts plus demo seed data.
- `scripts/` - Assorted helper scripts for scenario injection and configuration.
- `migrations/` - PostgreSQL schema migrations (still required — this app depends on an external Postgres instance, unlike the Axet template's default SQLAlchemy/DuckDB storage).
- `localstack_lab/` - Optional LocalStack-based AWS emulation lab for real remediation testing.

> **Note on external services:** Unlike the default Axet `.flows` template (which uses a single FastAPI backend + SQLAlchemy/JSON/DuckDB storage with no external services), NemoGuard requires **PostgreSQL** and a **Temporal** server for durable workflow orchestration. These remain as external dependencies — see `docker-compose.yml` and `HOW-TO-START.md`.

## Setup & Installation

### 1. Backend (FastAPI + Python)

Ensure you have Python 3.10+ installed.

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

### 2. Frontend (React + Vite)

Ensure you have Node.js 18+ installed.

```bash
cd renderer
npm install
```

## Running the Application

The simplest way to run everything (Postgres, Temporal, API, worker, frontend) is via Docker Compose:

```bash
docker-compose up -d --build
```

For local development without Docker, you need Postgres and Temporal running externally, then:

**Start the Backend Server (Terminal 1):**
```bash
# From the project root, with your venv activated:
python3 -m uvicorn backend.app.api.main:app --reload --port 8000
```
*Note: The backend runs on `http://localhost:8000`.*

**Start the Temporal Worker (Terminal 2):**
```bash
python3 -m backend.app.temporal_worker
```

**Start the Frontend Server (Terminal 3):**
```bash
cd renderer
npm run dev
```
*Note: The frontend typically runs on `http://localhost:5173`. The Vite config is set up to proxy `/api` requests automatically to `http://localhost:8000`.*

## Features

- **Live Agent Operations Console**: View real-time autonomous reasoning and execution traces via SSE.
- **Dynamic Causal Chain**: Visualizes the downstream impact of an alert leading to a root cause hypothesis.
- **Scenario Lab**: Generate fully synthetic, highly realistic pipeline incidents (Schema Regressions, Compute Crashes, DQ Anomalies) to test the agent platform.
- **Business Impact Dashboards**: See real-time blast radius analysis (Data Products affected, SLAs at risk).
- **Interactive Action Plans**: Review, edit, approve, or provide textual feedback on LLM-generated incident recovery runbooks.
- **Governed Capability Gateway**: Typed, policy-enforced action execution with independent verification (`backend/app/capabilities/`).

## License

Confidential and Proprietary. All rights reserved.
