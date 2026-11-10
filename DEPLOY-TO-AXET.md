# Deploying NemoGuard V22 to aXet Flows

This document reflects the current `axet-migration` architecture. Older guidance that used generic `settings.json` middleware or direct NVIDIA/Nemotron configuration is obsolete for this branch.

## Current Flows architecture

```text
Browser
  |
  v
axet-spa-app  (App ID: seed-app)
  |
  |  public UI: /flows/cloud/<FLOW_ID>/ui/seed-app/
  |
  v
python-gateway
  public prefix:  /backend/api
  backend prefix: /api/v2
  ASGI app:       backend.app.main:app
  |
  v
python-agent / Uvicorn
  internal port: 8200
  |
  v
FastAPI
```

The renderer's aXet runtime resolver uses `AXET_CONFIG.flowsBaseUrl` to construct cloud-safe API URLs. Do not hard-code the cloud deployment prefix in React code.

## 1. Repository/branch

Use the repository branch containing this V22 code (`axet-migration` unless you intentionally rename it).

The Python Agent source root must contain:

```text
backend/
renderer/
config/
...
```

## 2. SPA node

Configure the aXet SPA node for the Vite renderer:

```text
App ID:     seed-app
Entry file: index.html
Repository: this repository / axet-migration
```

The public cloud URL is expected to look like:

```text
https://axet.nttdata.com/flows/cloud/<FLOW_ID>/ui/seed-app/
```

The deployment root `/flows/cloud/<FLOW_ID>/` itself is not the SPA route and may legitimately return `Cannot GET /`.

## 3. Python Agent

Configure the Python Agent with HTTP API exposure enabled and attach it to the NemoGuard Python Gateway.

Expected reverse-proxy behavior:

```text
Gateway mode: reverse-proxy
Uvicorn port: 8200
ASGI app: backend.app.main:app
```

The Python Agent's **Uvicorn Startup Timeout** can be set higher (for example 200 seconds) to tolerate slow FastAPI startup. This setting is separate from the Python Gateway's agent-discovery wait; see the known platform issue below.

## 4. Python Gateway

Use:

```text
Mode:           Reverse proxy (FastAPI)
Public Prefix:  /backend/api
Backend Prefix: /api/v2
ASGI App:       backend.app.main:app
```

Healthy registration produces logs similar to:

```text
using agent ... uvicorn port=8200
registerProxy called with: prefix=/backend/api, backendPrefix=/api/v2, target=http://127.0.0.1:8200
reverse proxy active: /backend/api -> http://127.0.0.1:8200
```

Verification from the Designer/container:

```bash
curl -i http://127.0.0.1:8200/api/v2/status
```

should return HTTP 200 once FastAPI is running.

The Node-RED/Designer-facing route:

```bash
curl -i http://localhost:<designer-port>/backend/api/status
```

should also return 200 once the Gateway has registered.

## 5. aXet LLM configuration

Active NemoGuard agents use the shared `BaseAgent` OpenAI-compatible aXet integration.

The aXet Python Agent runtime is expected to inject:

```text
AXET_LLM_BASE_URL
OPENAI_API_KEY
AXET_MODEL
AXET_PROJECT_ID
AXET_USER_ID
DEPTAPPS_WEB_BACKEND_JWT_TOKEN
```

No NVIDIA API key is required by the active V22 agent path.

`GET /api/v2/status` reports whether the required inference configuration is present. It deliberately does not call that state "healthy" without a live probe.

### Admin knowledge and model controls

Deploy the updated SPA and Python Agent together, then sign in with an admin account and open **Admin → SOP and dependency administration**. The knowledge library is scoped to the admin's tenant and workspace. A Git push does not itself update a running Flow.

The **AI model configuration** panel shows the API process's `AXET_MODEL` and workspace-specific overrides for individual agent operations. Administrators can set an exact provider model ID or reset an operation to its runtime default. The provider does not validate model availability or tool-calling compatibility through this interface; an unsupported ID can cause new operations to fail. Changes affect new operations, not calls already in progress. Unscoped webhook classification uses its runtime default.

A separate Temporal worker can have a different `AXET_MODEL` default. Deploy the updated worker as well and ensure it shares the same persistent database as the API for overrides to take effect. The panel does not report the worker's actual environment. The exact provider ID and availability of GPT-6.1 Terra have not been verified; it is not selected automatically.

## 6. Authentication

Use the aXet/Okta-provided application session in cloud deployment. NemoGuard's backend APIs still enforce role and tenant/workspace boundaries.

The application can bootstrap a first SQLite-cloud admin only when the explicit bootstrap configuration is used; do not rely on arbitrary first-user auto-admin behavior.

## 7. Database

### Current repository support

The repository currently implements:

```text
NEMOGUARD_DB_BACKEND=postgres
NEMOGUARD_DB_BACKEND=sqlite
```

### Important cloud storage guidance

The aXet team has advised that SQLite on `/internal-storage-files/files` is not recommended for application persistence because SQLite locking/journaling semantics are not reliable on that mounted storage.

SQLite at `/tmp/...` is suitable only for diagnostics/demos and is **not durable**.

The aXet team recommends the Flow MySQL-compatible service for durable cloud application data:

- external tools (for example DBeaver): port `3307`
- application inside Flow: port `3306`

A MySQL database backend is intentionally **not** implemented in this V22 package yet because the final internal hostname/credential/database contract should be confirmed before credentials or assumptions are added to code. Track this as the next persistence hardening item.

## 8. Temporal

For the current aXet leadership/demo deployment, Temporal can be disabled:

```text
NEMOGUARD_TEMPORAL_ENABLED=false
```

The application uses its implemented direct fallback path and V22 surfaces the workflow mode to the operator.

For future durable production orchestration, provide a supported Temporal service and worker and set the corresponding environment variables.

## 9. Outlook/forwarded-alert bridge

The approved real-world ingestion path for the current prototype is forwarded alert email through the dedicated Outlook bridge. Keep that distinction explicit:

- Production Overview = persisted production-side observations only
- Outlook forward = untrusted/source-reported evidence
- Scenario Lab = synthetic/lab data

Do not represent Scenario Lab data as production telemetry.

## 10. Known aXet Python Gateway cold-start issue

The installed aXet `python-gateway` implementation currently waits exactly 60 seconds for one connected Python Agent to expose `uvicornPort`.

If the Agent becomes ready after that window, the Gateway returns from startup and does not retry, so `/backend/api/*` remains 404 even though Uvicorn is healthy on port 8200.

This has been reproduced and isolated from NemoGuard application routing. Restarting/redeploying the Gateway while the Agent is ready registers the proxy successfully.

The aXet team is investigating whether the discovery timeout is configurable or requires a platform fix. Increasing the Python Agent's **Uvicorn Startup Timeout** does not change this separate 60-second Gateway discovery window.

## 11. Cloud verification order

After deployment:

1. confirm Python Agent/Uvicorn started on 8200;
2. confirm direct `GET 127.0.0.1:8200/api/v2/status` returns 200;
3. confirm Gateway logs `reverse proxy active`;
4. confirm `/backend/api/status` through the Flows runtime returns 200;
5. open `/flows/cloud/<FLOW_ID>/ui/seed-app/`;
6. confirm Production Overview loads without a lost-connection banner;
7. confirm Alert Inbox/Outlook heartbeat as appropriate.

## 12. Production readiness

This V22 package is optimized for a leadership prototype and read/understand/prioritize workflows. Before broader production adoption, close the items in `V22_KNOWN_LIMITATIONS.md`, especially durable MySQL persistence, Gateway cold-start behavior, and approved read-only integrations.
