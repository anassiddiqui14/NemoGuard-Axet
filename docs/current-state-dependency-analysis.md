# NemoGuard — Current-State Dependency Analysis

## Executive summary

NemoGuard is a **containerized, multi-service incident-response platform** with a modern React dashboard, a Python/FastAPI incident API, asynchronous Temporal workflow support, PostgreSQL persistence, an aXet-integrated AI agent layer, and separate simulation/lab tooling.

The project is technically capable and already contains substantial product surface area. Its largest planning constraints are:

1. **Dependency management is fragmented** across multiple manifests and Dockerfiles.
2. **The API composition is centralized** in one very large FastAPI module.
3. **Runtime behavior has two modes**—Temporal orchestration and direct fallback—which must be treated as first-class product states.
4. **Deployment/config documentation has drifted** from legacy NVIDIA references toward aXet-managed model integration.
5. The frontend is modern and modular enough to support the dashboard redesign without a framework migration.

---

# 1. Application dependency map

```text
Browser
  │
  ▼
React / Vite SPA (renderer)
  ├─ /api/* ───────────────► Nginx reverse proxy ─► FastAPI API
  └─ /sim/* ───────────────► Nginx reverse proxy ─► Simulator API
                                                       │
FastAPI API                                            │
  ├─ PostgreSQL ───────────────────────────────────────┘
  ├─ Temporal Server (optional in some deployments)
  ├─ aXet-managed LLM / OpenAI-compatible API
  ├─ MCP / FastMCP integrations
  ├─ Optional Outlook email bridge/webhooks
  ├─ Optional Datadog / Airflow connectors
  └─ Optional LocalStack AWS remediation lab

Temporal Worker
  ├─ PostgreSQL
  ├─ Temporal Server
  └─ Same agent/domain/capability stack as API
```

## Runtime services defined in Docker Compose

| Service | Role | Hard dependency status |
|---|---|---|
| `frontend` | Static React SPA served by Nginx | Required for browser UI |
| `api` | FastAPI application / REST API / SSE / auth / orchestration | Core |
| `postgres` | Primary application datastore | Core in containerized production mode |
| `temporal` | Workflow orchestration server | Optional at runtime; default enabled locally |
| `temporal-worker` | Executes Temporal incident workflows | Required only when Temporal orchestration is enabled |
| `simulator` | Scenario Cockpit / incident simulation API | Product/demo tooling, not needed for core command center |
| `localstack` | Emulated AWS services for remediation effectiveness lab | Optional, Docker Compose profile `lab` |

---

# 2. Frontend dependency analysis

## Production dashboard: `renderer`

### Framework and tooling

| Dependency | Current role | Assessment |
|---|---|---|
| `react` / `react-dom` 19.2 | Component runtime | Current; no migration needed |
| `typescript` 6 | Static typing | Current, but high-version adoption should be locked and tested |
| `vite` 8 | Dev server and production bundling | Modern, appropriate |
| `react-router-dom` 7 | SPA routing / route protection | Appropriate |
| `tailwindcss` 4 + PostCSS | Visual styling system | Strong fit for the new UI direction |
| `framer-motion` 13 | Motion, interaction transitions | Strong fit for reel-inspired motion; standardize usage |
| `recharts` 3 | Operational charts | Sufficient for initial dashboard redesign |
| `lucide-react` | UI iconography | Strong fit; establishes a clean icon system |
| `react-hot-toast` | Toasts/notifications | Appropriate |
| `oxlint` | Linting | Useful but currently limited as a quality gate |
| `vite-plugin-singlefile` | Build plugin | Present in dependencies; confirm it is truly needed before retaining |

### Frontend architecture

The renderer is already separated into useful UI layers:

```text
renderer/src
├── app/
│   ├── RequireAuth.tsx
│   └── routes.tsx
├── components/
│   ├── shell/                 ← navigation, header, mobile drawer, account menu
│   ├── dashboard/             ← incident command-center experience
│   └── Dashboard.tsx
├── design-system/
│   ├── components/            ← Button, Input, Modal, Skeleton, Tooltip, etc.
│   └── motion/                ← motion tokens
├── hooks/                     ← data, events, auth, notifications, navigation
├── contexts/                  ← auth gate and theme
├── pages/                     ← route-level product surfaces
└── lib/dashboardCache.ts      ← session cache
```

### Auth and browser-state dependencies

The frontend operates in two authentication modes:

1. **Standalone/local mode**
   - JWT is persisted as `localStorage.nemoguard_token`.
   - Uses `/api/v2/auth/login`.
   - Supports development-only mock login.

2. **aXet Flows runtime**
   - Can use aXet-provided session/token infrastructure.
   - Protected API behavior is expected to rely on backend identity enforcement.

Other persisted client state includes:

- theme preference
- collapsed navigation preference
- dashboard stale-while-revalidate cache
- scenario correlation job ID
- notification preferences

### UI API communication model

The UI uses a **custom `fetch`-based auth/data layer** rather than:

- TanStack Query / React Query
- Redux / Zustand
- OpenAPI-generated API client
- GraphQL client

This is viable today, but becomes a constraint when the product adds:

- richer cache invalidation
- optimistic mutations
- cross-page real-time updates
- pagination/filter state synchronization
- offline/retry behavior
- typed API contract management

### Real-time dependency

Incident event streaming is implemented with authenticated streaming `fetch`, not native `EventSource`, because authorization headers are needed for aXet/Okta compatibility.

This is a correct practical choice. It should be retained during the dashboard redesign and eventually wrapped in one reusable stream client with reconnection, backoff, and page-visibility handling.

---

# 3. Frontend route and feature surface

The production SPA currently has these authenticated screens:

| Route | Product purpose | UI redesign relevance |
|---|---|---|
| `/app` | Production overview | Primary dashboard redesign target |
| `/app/command-center` | Detailed incident workspace | High-value operational UI |
| `/app/alert-inbox` | Outlook/email alert intake and review | Specialized workflow |
| `/app/incidents` | Incident queue/list | High priority |
| `/app/agent-operations` | Agent monitoring/operations | High priority for AI operations positioning |
| `/app/intelligence` | Intelligence/reporting surface | Lazy loaded |
| `/app/scenario-cockpit` | Incident simulation and lab testing | Lazy loaded / lower daily-use priority |
| `/app/adoption-readiness` | Adoption/readiness reporting | Lazy loaded |
| `/app/admin` | Capability policy and other governance tabs | Role-gated, lazy loaded |
| `/app/settings` | Application preferences/settings | Reuse design system |
| `/login` | Local credential login | Separate auth design concern |
| `/` | Public landing page | Marketing/product narrative |

### Current code-splitting strategy

Lazy-loaded routes:

- Intelligence
- Scenario Cockpit
- Adoption Readiness
- Admin

Eager-loaded routes:

- Overview
- Command Center
- Alert Inbox
- Incidents
- Agent Operations
- Settings

This is an intentional performance decision. Preserve this route-level split when redesigning UI components. Consider also lazy-loading heavyweight visualization or simulation-only modules rather than moving all common components behind lazy boundaries.

---

# 4. Backend Python dependency analysis

## Dependency declarations

There are currently **three competing dependency authorities**:

1. Root `pyproject.toml`
2. `backend/requirements.txt`
3. Explicit `pip install` additions inside Dockerfiles

### Root `pyproject.toml`

Declares:

- YAML/data: `pyyaml`, `pandas`, `pyarrow`, `duckdb`
- API/runtime: `fastapi`, `uvicorn`, `httpx`
- AI orchestration: `openai`, `langgraph`, `mcp`
- auth: `pyjwt`
- document ingestion: `python-docx`, `pypdf`, `extract-msg`
- build: Hatchling

It identifies the package as `pipeline-copilot`, not NemoGuard.

### `backend/requirements.txt`

Includes all root packages plus:

- `opentelemetry-api`
- `opentelemetry-sdk`
- `temporalio`
- `psycopg2-binary`
- `python-dotenv`
- `fastmcp`

### Dockerfile-specific additions

| Image | Additional packages installed directly |
|---|---|
| API | OpenTelemetry, Temporal, PostgreSQL driver, `boto3==1.34.144`, `botocore==1.34.144` |
| Temporal worker | OpenTelemetry, Temporal, PostgreSQL driver |
| Simulator | FastAPI, Uvicorn, Pydantic, HTTPX, PostgreSQL driver, OpenAI |

## Backend capabilities by dependency group

| Group | Dependencies | What they enable |
|---|---|---|
| API platform | FastAPI, Uvicorn, Pydantic, HTTPX | REST endpoints, validation, service calls |
| Data/persistence | PostgreSQL driver, DuckDB, Pandas, PyArrow | operational persistence, analytics/data manipulation |
| AI agents | OpenAI SDK, LangGraph | investigation, RCA, runbooks, commander interactions |
| AI integration | MCP, FastMCP | model-context-protocol tools/services |
| Orchestration | Temporal SDK | durable lifecycle orchestration |
| Observability | OpenTelemetry API/SDK | telemetry instrumentation |
| Content ingestion | PyPDF, Python DOCX, extract-msg | document/email evidence intake |
| AWS lab/remediation | Boto3/Botocore | LocalStack-backed remediation exercises |
| Auth | PyJWT | user/session tokens, role enforcement |
| Configuration | PyYAML, dotenv | policy/config loading |

---

# 5. Backend architecture and internal coupling

## Main modules

```text
backend/app
├── api/
│   ├── main.py               ← principal HTTP composition module
│   ├── auth.py
│   └── mcp_server.py
├── capabilities/             ← capability definitions, policies, intent→execution compilation
├── domain/
│   ├── agents/               ← watcher, RCA, dependency, runbook, commander agents
│   ├── workflows/            ← Temporal incident lifecycle
│   ├── connectors/           ← Airflow and Datadog
│   ├── commander/            ← commander conversation and status projection
│   ├── orchestrator.py
│   ├── correlator.py
│   ├── state_machine.py
│   └── triage_scheduler.py
├── store/
│   ├── PostgreSQL/SQLite implementations
│   └── schema + migration behavior
├── tools/                    ← read/write tool contracts
├── generator/                ← synthetic/demo scenario generation
├── ui/                       ← alert/incident projection helpers
└── utils/telemetry.py
```

## API composition findings

`backend/app/api/main.py` is a major concentration point. It currently owns or coordinates:

- credential and development authentication
- database initialization/migration startup
- bootstrap admin provisioning
- Temporal connectivity/fallback selection
- health/status API
- overview and incident data endpoints
- evidence, plans, alerts, and correlation operations
- webhook/email/Outlook ingestion
- approval and execution workflows
- admin capability policy endpoints
- Commander Live / Commander Pulse APIs
- SSE streaming

### Implication

This module is functional and thoughtfully documented, but is a **maintainability hotspot**. As feature scope grows, it should be incrementally split into routers/services rather than expanded further.

A recommended boundary split:

```text
api/
├── routers/
│   ├── auth.py
│   ├── overview.py
│   ├── incidents.py
│   ├── ingestion.py
│   ├── commander.py
│   ├── administration.py
│   └── health.py
├── dependencies.py
├── errors.py
└── main.py                   ← route assembly + app lifecycle only
```

Do not treat that refactor as a prerequisite for the visual dashboard refresh; it is a parallel platform-hardening workstream.

---

# 6. Database and workflow dependencies

## Storage

- PostgreSQL 15 Alpine is the intended containerized production datastore.
- SQLite support exists in the abstraction layer, likely serving local/development or constrained aXet environments.
- Schema setup happens dynamically at startup.
- PostgreSQL receives pending migrations; SQLite migration behavior is intentionally limited/no-op in places.

### Planning concern

The application uses a database abstraction, but migration behavior is not equivalent between PostgreSQL and SQLite. Product features involving tenancy, history, policy/audit, or new dashboards should be validated primarily against PostgreSQL if that is the production target.

## Workflow engine

Temporal is the desired durable execution path:

- API creates/schedules workflow work.
- Temporal worker runs lifecycle behavior.
- Plan approvals can signal active workflows.

Fallback behavior exists when Temporal is disabled/unavailable:

- direct background triage
- direct plan execution in specific fallback paths

### Planning consequence

Every new incident automation feature should explicitly define:

| Condition | Expected behavior |
|---|---|
| Temporal healthy | Durable workflow with retries/signals |
| Temporal disabled | Direct execution path |
| Temporal unreachable during API request | Clear failure/recovery semantics |
| Worker unavailable | User-visible operational state and retry strategy |

The product should expose workflow mode/health more visibly in admin/operator interfaces; the API already provides Temporal status in `/api/v2/status`.

---

# 7. External integration dependency map

| Integration | Current evidence in repository | Planning implication |
|---|---|---|
| aXet-managed LLM | Compose variables, shared `BaseAgent`, runtime integration comments | Primary AI-provider path; define provider health and fallback UX |
| OpenAI-compatible SDK | Python `openai` dependency | SDK-level interface; potentially used against aXet endpoint |
| NVIDIA API | `.env.example`, Compose requirement remnants | Documentation/config drift; clarify whether legacy or supported |
| Temporal | Docker service + worker + scheduler | Optional but central to robust automation |
| PostgreSQL | Compose + database layer | Core persistence |
| Outlook email bridge | webhook endpoints and inbox review flow | Important external evidence source; security-sensitive |
| MCP/FastMCP | dependencies and `mcp_server.py` | Extensibility point for agents/tools |
| Airflow | connector module | Integration-specific product surface |
| Datadog | connector module | Integration-specific product surface |
| LocalStack | optional lab profile | Test/demo infrastructure, not customer runtime |
| AWS SDK | Boto3/Botocore pin | Limited to remediation laboratory capability |

---

# 8. Configuration dependencies

## Versioned configuration files

```text
config/
├── action_policy.yaml
├── capability_policy.yaml
├── demo.yaml
└── scoring.yaml
```

These drive behavior rather than presentation only:

- capability policy controls what actions exist and their effective risk/autonomy
- action policy likely governs execution behavior
- scoring influences prioritization/correlation/product behavior
- demo config supports simulation/testing

### Strength

Policy is externalized rather than hardcoded; administrators can inspect/reload capability policy through API endpoints.

### Improvement opportunity

Treat configuration as a product-controlled artifact:

- validate all config files against typed schemas in CI
- record config version/hash with incident decisions and executions
- provide staged rollout / preview for policy changes
- distinguish environment-specific operational config from versioned policy-as-code
- make UI policy changes auditable and reversible

---

# 9. Deployment dependency analysis

## Frontend delivery

```text
Node 20 build stage
  → Vite static bundle
  → Nginx Alpine runtime
  → Nginx proxies /api to FastAPI and /sim to simulator
```

Positive characteristics:

- browser has a single origin
- avoids CORS complexity
- static frontend deployment is simple
- reverse proxy has explicit 180-second AI response timeout
- Docker DNS resolution is refreshed for backend container recreation

## Notable deployment concerns

### 1. Non-deterministic frontend installation

`renderer/Dockerfile.frontend` runs:

```dockerfile
RUN npm install
```

Despite `package-lock.json` existing.

**Recommendation:** use `npm ci` in CI and Docker builds. This ensures locked, reproducible production installations.

### 2. Mutable image tags

Current Compose references include:

```text
temporalio/admin-tools:latest
```

**Recommendation:** replace mutable runtime tags with tested version pins/digests.

### 3. Credentials in development defaults

Compose includes local PostgreSQL credentials in source. This is acceptable for local development but must be separated from production deployment configuration.

### 4. Docker socket exposure in LocalStack profile

The LocalStack lab mounts Docker socket access:

```text
/var/run/docker.sock:/var/run/docker.sock
```

Keep this strictly isolated to the opt-in lab profile; never carry this pattern into production.

---

# 10. Dependency risks and technical debt

## Priority 0 — dependency management consolidation

### Current issue

Dependencies are declared in:

- root `pyproject.toml`
- `backend/requirements.txt`
- three Dockerfiles
- frontend package/lock files

The sources are out of sync.

Examples:

- `backend/requirements.txt` has Temporal, OpenTelemetry, psycopg2, dotenv, and FastMCP, but root `pyproject.toml` does not.
- Dockerfiles manually append packages after `pip install .`.
- API image includes boto3/botocore only in the Dockerfile.
- simulator image repeats direct dependency installation.
- root package retains legacy `pipeline-copilot` naming.

### Risks

- local, worker, API, and simulator environments can run different dependency graphs
- a fix tested locally can fail in a container
- dependency vulnerability scanning is incomplete/misleading
- upgrades become risky and difficult to reproduce

### Recommended target state

Adopt one Python dependency authority—preferably `pyproject.toml` with optional groups:

```toml
[project.optional-dependencies]
api = ["temporalio", "psycopg2-binary", "opentelemetry-api", "opentelemetry-sdk"]
simulator = ["..."]
lab = ["boto3==...", "botocore==..."]
dev = ["pytest", "ruff", "mypy", "..."]
```

Then use a lock mechanism compatible with your chosen workflow and install explicit extras per image:

```text
pip install ".[api]"
pip install ".[worker]"
pip install ".[simulator]"
pip install ".[lab]"
```

Retire or generate `backend/requirements.txt`; do not maintain it manually in parallel.

---

## Priority 1 — version pinning and supply-chain policy

### Current issue

Most Python requirements are unpinned. Frontend uses broad caret ranges. The Docker frontend runs `npm install`.

### Risks

- unexpected runtime breakage
- builds differ over time
- difficult security remediation/reproducibility
- AI/agent SDK changes can alter behavior materially

### Recommended plan

1. Generate committed lockfiles.
2. Use `npm ci` for Node build steps.
3. Add dependency scanning in CI.
4. Pin Docker base images and service image versions.
5. Establish upgrade cadence:
   - monthly security dependency updates
   - quarterly framework/tooling upgrades
   - explicit compatibility test suite for agent/LLM SDK upgrades

---

## Priority 2 — configuration and documentation drift

### Observed drift

- package identity: `pipeline-copilot` vs. NemoGuard
- `.env.example`: NVIDIA/Nemotron primary narrative
- simulator Compose comments: aXet-managed LLM is the current intended path
- `LAMBDA_DOCKER_NETWORK=pipeline-copilot_default`: legacy Compose project network naming remains

### Recommended plan

Create one canonical operational configuration matrix:

| Environment | Auth | Database | Workflow | LLM | Simulator | LocalStack |
|---|---|---|---|---|---|---|
| Local dev | local/dev | SQLite or Docker PG | optional | aXet/dev provider | optional | optional |
| Docker demo | JWT | PostgreSQL | Temporal | configured provider | enabled as needed | profile |
| aXet cloud | aXet identity | managed storage | disabled/fallback or hosted | aXet LLM | optional | disabled |
| Production | SSO | managed PostgreSQL | managed Temporal | approved provider | disabled or isolated | disabled |

Then update:

- `.env.example`
- README
- deployment guide
- Docker Compose comments/values
- frontend runtime configuration guidance

---

## Priority 3 — API modularity

### Current issue

`api/main.py` is very large and coordinates unrelated verticals.

### Risks

- feature changes have broad regression surface
- hard to test route behavior in isolation
- routes can bypass shared tenancy/auth invariants if not carefully reviewed
- difficult ownership as team grows

### Recommended plan

Extract routers gradually, retaining existing path contracts. Begin with:

1. auth/status
2. overview/incidents
3. ingestion/outlook
4. commander/SSE
5. admin/policy

Do this behind existing tests and without mixing it into major UI implementation commits.

---

## Priority 4 — frontend server-state strategy

### Current issue

Custom `fetch` calls and bespoke hooks are adequate but may become fragile as UI scope grows.

### Recommended decision

Before adding complex cross-screen analytics and real-time widgets, choose one of two paths:

**Option A — evolve current approach**

- consolidate fetch/auth handling into a typed API module
- standardize hook contracts
- add common error, retry, and cancellation handling
- preserve lightweight architecture

**Option B — adopt TanStack Query**

- route all REST reads/writes through query/mutation hooks
- gain cache invalidation, background refresh, deduplication, retries, pagination support
- pair it with manually typed API models or generated OpenAPI types

For the proposed premium dashboard and richer operations pages, **Option B is the stronger long-term choice**, but it is not required before the first visual redesign release.

---

# 11. Recommended improvement roadmap

## Phase A — establish engineering baseline

**Goal: remove dependency/config uncertainty before major expansion.**

1. Consolidate Python dependencies into the root project configuration.
2. Create lockfiles and deterministic builds.
3. Change frontend Docker install to `npm ci`.
4. Pin external container images.
5. Rename legacy package/config references from Pipeline Copilot to NemoGuard.
6. Publish the environment/configuration matrix.
7. Add a simple dependency/security scan to CI.

## Phase B — stabilize platform seams

**Goal: make growth safer.**

1. Split FastAPI routes into routers.
2. Add automated API contract validation/OpenAPI checks.
3. Normalize error response shape.
4. Formalize Temporal-vs-direct execution status.
5. Add schema validation/versioning for policy YAML.
6. Ensure PostgreSQL is the authoritative integration test environment.

## Phase C — improve frontend data architecture

**Goal: make data-heavy dashboard expansion reliable.**

1. Centralize API calls and TypeScript response models.
2. Add a query/cache strategy.
3. Consolidate authenticated SSE behavior into a reusable stream client.
4. Add route/page error boundaries and observability.
5. Formalize design tokens around the existing in-repo design system.

## Phase D — ship UI/product enhancements

**Goal: evolve the operational product experience.**

1. Redesign Production Overview with:
   - risk/incident KPIs
   - workflow mode and system health
   - critical event stream
   - priority alerts
   - agent and policy state
2. Modernize Incidents and Command Center next.
3. Add explicit integration health center:
   - PostgreSQL
   - Temporal
   - LLM provider
   - Outlook bridge
   - Airflow/Datadog
4. Create governance experiences:
   - policy diffs/approvals
   - action audit trails
   - AI evaluation and feedback loops
5. Separate demo/lab capabilities visually and operationally from production operations.

---

# 12. UI redesign-specific conclusions

The current codebase is a suitable base for the reel-inspired dashboard update.

You do **not** need to replace React, Tailwind, routing, charting, or motion libraries.

Recommended reuse:

| Existing asset | Redesign usage |
|---|---|
| `AppShell`, navigation rail, mobile drawer | Target dark premium application chrome |
| `design-system/components` | Tokenize/rebuild visual style instead of creating screen-specific controls |
| `ThemeContext` | Support dark-first visual system, retain accessible alternate theme if needed |
| `recharts` | KPI trends, incident history, risk distribution |
| `framer-motion` | Panel entrances, chart/card transitions, drawer/modals |
| `useIncidentData`, `useIncidentEvents` | Feed high-priority dashboard panels |
| `ProductionOverviewPage` | Primary UI transformation entrypoint |
| `Dashboard` / Command Center components | Second implementation phase after overview |

The dependency work should be treated as **platform hardening parallel to product redesign**, not a blocker to visual modernization.

---

# Planning recommendation

For the next state of the app, organize work into three independent but coordinated tracks:

1. **Product/UI track:** overview → incidents → command center → admin/governance.
2. **Platform track:** dependency consolidation, deterministic builds, config cleanup, API router extraction.
3. **Intelligence/automation track:** formalize agent evaluation, provider health, policy lifecycle, Temporal/direct-mode operator visibility.

This structure lets the UI ship meaningful value while reducing the operational risk of the underlying platform.
