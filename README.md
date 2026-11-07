# NemoGuard V22

NemoGuard is an AI-assisted production-operations and incident-response platform. The current V22 direction is deliberately simple:

> **Help a production-support team understand what is happening, what needs attention, what NemoGuard knows, what it does not know, and what should happen next — before expanding production autonomy.**

The repository follows the aXet React + FastAPI layout (`renderer/` + `backend/app/`) and supports local/standalone execution as well as an aXet Flows deployment.

## V22 product focus

- **Production Overview** — deterministic Needs Attention, Shift Brief, real KPIs, activity, health, recent investigations, and live persisted events.
- **What NemoGuard Needs** — investigation-coverage gaps such as missing independent evidence, runbook grounding, dependency context, execution IDs, or ownership.
- **Alert Inbox / Outlook bridge** — the current approved real-world intake path for forwarded production alerts.
- **Incident Command Center** — correlated alerts, evidence, RCA hypotheses, impact, runbook/plan state, agent progression, Commander, approvals, execution and verification.
- **Commander Live / Pulse** — persistent incident conversation and deterministic operational-status projection.
- **Governed capabilities** — policy, approval, precondition, execution and independent verification controls.
- **Scenario Lab** — explicitly synthetic/lab-only demonstrations of the future action lifecycle.

V22 does not fabricate production metrics and does not add unauthorized direct integrations or autonomous production writes.

## Architecture

```text
Browser / React Vite SPA
          |
          v
FastAPI REST + authenticated streaming
          |
   +------+--------------------+
   |      |                    |
Database  Agent layer          Optional Temporal
   |      |                    |
   |      +--> aXet-managed LLM|
   |                           |
   +--> incidents/alerts/evidence/plans/audit/Commander
```

Local/Docker deployments can use PostgreSQL and Temporal. The current repository also contains a SQLite backend used for tests/diagnostics and constrained deployments; see the cloud persistence limitation below.

## Project structure

```text
backend/app/
  api/                 FastAPI composition, auth, REST/SSE endpoints
  domain/              incident lifecycle, agents, correlation, Commander
  capabilities/        governed action/capability policy + verification
  store/               PostgreSQL/SQLite database abstraction
  tools/               read/write tool contracts

renderer/src/
  components/          shell + incident command-center components
  design-system/       reusable UI primitives and motion
  hooks/               data/auth/event/navigation hooks
  pages/               route-level product surfaces

config/                 capability/action/scoring configuration
localstack_lab/         optional isolated action/remediation lab
```

## Quick start

See [HOW-TO-START.md](HOW-TO-START.md).

For a local frontend install:

```bash
cd renderer
npm ci
npm run dev
```

For the backend:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python -m uvicorn backend.app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Configuration is documented in `.env.example`.

## aXet deployment

See [DEPLOY-TO-AXET.md](DEPLOY-TO-AXET.md).

The current cloud architecture uses:

```text
axet-spa-app -> python-gateway (/backend/api -> /api/v2) -> python-agent/Uvicorn -> FastAPI
```

Active agents use the aXet-managed OpenAI-compatible runtime (`AXET_LLM_BASE_URL`, `AXET_MODEL`, etc.). The active V22 path no longer requires `NVIDIA_API_KEY`.

## Persistence note

The aXet team has advised against SQLite on `/internal-storage-files/files` for durable application persistence because of locking/journaling behavior. SQLite in `/tmp` is diagnostic/ephemeral only.

For durable aXet cloud persistence, the platform team recommends the Flow MySQL-compatible service (internal port 3306). A MySQL backend is intentionally not included in this V22 package until the remaining internal hostname/credential/database contract is confirmed.

## Validation

The V22 change set includes tests for:

- operations intelligence / attention + coverage projection
- guarded incident cancellation
- alert re-correlation
- webhook idempotency and isolation
- existing Outlook/email and SQLite behaviors where dependencies are available

See [V22_IMPLEMENTATION_SUMMARY.md](V22_IMPLEMENTATION_SUMMARY.md) for the exact validation performed in the review environment.

## Leadership/demo docs

- [V22_DEMO_GUIDE.md](V22_DEMO_GUIDE.md)
- [V22_IMPLEMENTATION_SUMMARY.md](V22_IMPLEMENTATION_SUMMARY.md)
- [V22_KNOWN_LIMITATIONS.md](V22_KNOWN_LIMITATIONS.md)

## Safety posture

NemoGuard separates observation, recommendation, approval, execution, and verification. Forwarded email is intentionally treated as unverified/source-reported evidence. A plausible AI recommendation is not sufficient authority to change production.

The intended maturity path is:

```text
Observe -> Understand -> Prioritize -> Learn -> Recommend -> Governed Action -> Controlled Autonomy
```

## License

Confidential and Proprietary. All rights reserved.
