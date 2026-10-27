# Deploying NemoGuard to Axet `.flows`

This document gives the exact, NemoGuard-specific values and gotchas for
importing this repository into an Axet `.flows` workspace, following the
standard React + FastAPI `.flows` template pattern. Read `HOW-TO-START.md`
first for local dev instructions; this file is deployment-only.

## 0. Prerequisites checklist

- [ ] This repo pushed to a Git server reachable from the `.flows` environment
      (branch: `axet-migration`, or whichever branch you push).
- [ ] A Git access token with read access to that repo.
- [ ] An Axet `.flows` workspace where you can import the template `.deptapp`.
- [ ] `NVIDIA_API_KEY` and `JWT_SECRET` values ready (never commit these).
- [ ] A reachable PostgreSQL instance and Temporal server for this deployment
      (NemoGuard does **not** use the template's default SQLAlchemy/DuckDB
      storage — see "Important divergence" below).

## 1. Push this repo

```bash
cd NemoGuard-Axet
git add .
git commit -m "Port NemoGuard to Axet flows structure"
git remote add origin <your-repo-url>
git push -u origin axet-migration
```

## 2. Import the base template `.deptapp` into `.flows`

Do **not** try to convert this Git repo into a `.deptapp` yourself. Import the
React + FastAPI `.flows` template's `.deptapp` export
(`template-react-fastapi-flows_v1.3.1_export.deptapp`, from the Axet cookbook
download) into your `.flows` workspace first — it already contains the
generic repo-clone, frontend-build, and backend-start workflow nodes. You
then point those nodes at this NemoGuard repo instead of the template repo.

## 3. Configure the repository-clone node

Set it to run (values come from step 0/1):

```bash
./setup_app.sh <repo-url> app <git-token> axet-migration
```

This clones the repo so the `.flows` runtime sees:

```
app/
├── renderer/
├── backend/
│   ├── requirements.txt   <- Python node needs this
│   └── app/
│       ├── main.py        <- re-exports the real FastAPI `app` object
│       └── api/main.py    <- NemoGuard's actual FastAPI app + all routes
├── settings.json
└── ...
```

## 4. Configure the frontend-build node

Run:

```bash
./setup_frontend_app.sh
```

This builds `renderer/` (Vite + React) using the `FRONTEND_DIR=renderer`
convention already matched by this repo's folder name, and copies the
production build to `renderer/dist`.

## 5. Configure the Python backend node

Point the backend application folder at:

```
app/backend
```

Install step uses `backend/requirements.txt` (already added at the repo
root of `backend/`). Start command should resolve to:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This works because `backend/app/main.py` is a thin shim that imports and
re-exports NemoGuard's real FastAPI `app` object from `backend/app/api/main.py`,
so Axet's generic `app.main:app` convention keeps working without having to
rewrite NemoGuard's internal module layout further.

## 6. Configure `settings.json`

For the deployed environment:

```json
{
  "baseUrl": "https://<flows-host>/<workspace-or-cloud-path>",
  "backendBaseUrl": "http://localhost:8000"
}
```

`baseUrl` is what the browser talks to (through `.flows`). `backendBaseUrl`
stays `http://localhost:8000` because FastAPI runs as an internal sidecar
process inside the same `.flows` runtime, not directly exposed to the
internet.

## 7. ⚠️ Important divergence: NemoGuard's API routes are NOT `/backend/api/`

The base Axet template's `flows_middleware.json` proxies a single generic
endpoint, `POST /backend/api/` (method+action JSON body), to the FastAPI
backend. **NemoGuard does not use that contract.** Its React frontend calls
many separate REST/SSE routes directly under `/api/v2/...`
(e.g. `/api/v2/incidents`, `/api/v2/incidents/{id}/events/stream`,
`/api/v2/auth/login`), as defined in `backend/app/api/main.py`.

You must therefore either:

- **Option A (recommended, matches existing nginx setup):** configure the
  `.flows` middleware/reverse-proxy layer to forward `/api/*` requests
  (not just `/backend/api/`) straight through to `backendBaseUrl`, mirroring
  `renderer/nginx.conf`'s existing rule:

  ```
  location /api/ {
      proxy_pass http://<backendBaseUrl>/api/;
      proxy_buffering off;         # required for the SSE events/stream route
      proxy_set_header Connection '';
      proxy_http_version 1.1;
      chunked_transfer_encoding off;
  }
  ```

  A minimal Node-RED `flows_middleware.json` function node doing a transparent
  passthrough proxy (instead of the template's method/action JSON wrapper) is
  the Node-RED-native way to express this; see the template's existing SSE
  proxy function node for a working reference you can adapt (it already
  streams `text/event-stream` correctly for `/backend/api/stream/` — change
  the upstream path construction to pass through `msg.req.url` verbatim
  instead of hardcoding `/backend/api/stream/`).

- **Option B (more invasive, not done in this port):** rewrite NemoGuard's
  frontend `fetch()` calls and backend route prefixes to adopt the generic
  `method`/`action` JSON contract and register each capability as a
  `BaseHandler` in a `registry.py`, matching the base template exactly. This
  was explicitly out of scope for this port (see `HOW-TO-START.md`'s
  "Notes On Divergence" section) — it would mean re-implementing ~30 REST
  endpoints and the SSE stream as handler classes.

The `/backend/api/health` alias added in `backend/app/main.py` already works
out of the box with the template's default health-check convention; it is
the one route that matches Axet's generic expectation without extra proxy
configuration.

## 8. Environment variables / secrets

Set these in `.flows`'s environment configuration or `ENV_JSON` — never in
`settings.json` or committed files:

| Variable | Required | Notes |
|---|---|---|
| `NVIDIA_API_KEY` | Yes | Agent LLM calls will fail without it. |
| `JWT_SECRET` | Yes | App raises `RuntimeError` on startup if unset. |
| `POSTGRES_URL` | Yes | Must point at a real Postgres instance reachable from the `.flows` runtime. |
| `TEMPORAL_URL` | Yes | Must point at a real Temporal server (`host:port`). Without one, `/triage` and `/approve` fall back to direct synchronous execution (see `backend/app/api/main.py`), which works but loses durability. |
| `ENV` | No | Set to `development`/`dev`/`local` only in non-production deployments to enable the mock-login endpoint. |
| `NEMOGUARD_LOCALSTACK_LAB` | No | Leave `0`/unset in a real Axet deployment. |

## 9. Deploy and verify

Run the `.flows` deployment, then verify in order:

1. `GET https://<flows-url>/backend/api/health` → `{"statusCode": 200, "status": "ok"}`
2. `GET https://<flows-url>/api/v2/status` → NemoGuard's own status payload
   (confirms the `/api/*` proxy rule from step 7 is working).
3. Open the main app URL → Landing page → Login → Dashboard → Incidents.

If step 2 fails with a 404, the `.flows` middleware is only proxying
`/backend/api/` and not the broader `/api/*` prefix NemoGuard needs — revisit
step 7.

## 10. Postgres schema + Temporal worker

This repo's `docker-compose.yml` still shows the full intended topology
(Postgres, Temporal, API, Temporal worker, frontend, simulator). If your
`.flows` workspace does not run a Temporal worker process alongside the API
node, you'll need either:

- A second `.flows` Python node running
  `python -m backend.app.temporal_worker` against the same `TEMPORAL_URL`, or
- Accept the degraded mode where `/triage` and `/approve` execute directly
  without Temporal's durability guarantees (already implemented as a
  fallback in `backend/app/api/main.py`).

The Postgres schema bootstraps itself automatically on API startup
(`backend/app/api/main.py::startup_event` calls `db.init_schema()` if the
`incident` table doesn't exist yet), so no manual migration step is required
on first deploy — just make sure `POSTGRES_URL` points at an empty, reachable
database.
