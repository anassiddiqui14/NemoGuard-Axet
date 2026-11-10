from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
from pydantic import BaseModel
import random
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import os
import json
import uuid
import hashlib
import hmac

from app.store.database_factory import get_database, get_db_backend_name
from app.domain.enums import IncidentState
from app.utils.telemetry import setup_telemetry
from app.api.auth import (
    get_current_user, require_role, User, get_mock_token,
    LoginRequest, verify_password, issue_token_for_user, IS_DEV_ENV,
    serialize_roles, deserialize_roles, hash_password,
)

# --- Import-time performance note (aXet Cloud 60s startup deadline) -------
# IncidentOrchestrator (app.domain.orchestrator), CorrelatorEngine
# (app.domain.correlator), Incident/Alert (app.domain.models),
# temporalio.client.Client, and IncidentLifecycleWorkflow
# (app.domain.workflows.incident_workflow) are DELIBERATELY NOT imported
# at module load time here. IncidentOrchestrator alone transitively pulls
# in the full agent stack (RCA/Dependency/Runbook/Commander/Watcher
# agents -> `openai` SDK -> `langgraph`), and the temporalio imports pull
# in its native bridge extension -- importtime profiling measured these
# as the dominant cost of importing this module (see Phase-1-cloud-
# startup investigation). None of these symbols are used anywhere at
# module scope in this file; every real use is already inside a request
# handler body or the startup() function below, so each is imported
# lazily at its own first point of use instead, with ZERO change to
# runtime behavior -- the same class/function objects, constructed and
# called exactly the same way, just imported a few lines later (on first
# use within that same function call) rather than at process import time.
# This also means a FAILURE importing any one of these heavy stacks can
# no longer prevent the whole ASGI app object (and therefore basic routes
# like /api/v2/status) from loading at all.

app = FastAPI(title="NemoGuard - Pipeline Incident Commander", version="2.0.0")

from app.api.incident_knowledge import router as incident_knowledge_router
app.include_router(incident_knowledge_router)

_startup_t0 = __import__("time").perf_counter()
setup_telemetry("nemoguard_api")
print(f"[startup-timing] telemetry setup complete at +{__import__('time').perf_counter() - _startup_t0:.2f}s")

temporal_client = None


def _bootstrap_admin_if_configured(db) -> None:
    """
    Provisions exactly one admin platform_user on startup when
    NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL is set -- the ONLY way to get a first
    account into a brand-new, empty platform_user table (e.g. a fresh
    SQLite file on aXet Cloud). Without this, AXET_FLOWS_AUTH=true against
    an empty platform_user table means _get_axet_user() can never match
    any okta_id/email and NOBODY can ever sign in.

    Idempotent and safe to run on every startup:
      - If a platform_user with this email already exists, it is left
        completely untouched (no password/roles/okta_id overwrite) --
        this bootstrap only ever CREATES the account once; it is not a
        mechanism for repeatedly resetting it on every restart.
      - If the email is unset, this is a complete no-op (existing
        deployments that have never set this variable are unaffected).

    The provisioned account has roles=["admin"] and a random, never-
    logged password (the account is expected to authenticate via
    AXET_FLOWS_AUTH's Okta-backed flow, which looks up by okta_id/email,
    not by password) -- but a real PBKDF2 hash is still stored so the
    standalone email/password login path also works if ever needed.
    """
    bootstrap_email = os.environ.get("NEMOGUARD_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    if not bootstrap_email:
        return

    import secrets
    import uuid as _uuid

    try:
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id FROM platform_user WHERE email = %s", (bootstrap_email,),
            )
            if cursor.fetchone():
                print("Bootstrap admin account already provisioned; skipping.")
                return

            user_id = f"USR-{_uuid.uuid4().hex[:8]}"
            random_password = secrets.token_urlsafe(32)
            password_hash = hash_password(random_password)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO platform_user (user_id, email, password_hash, display_name, roles, "
                "tenant_id, workspace_id, is_active, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    user_id, bootstrap_email, password_hash, "Bootstrap Admin",
                    serialize_roles(["admin"], db=db), "default_tenant", "default_workspace", 1, now,
                ),
            )
        print("Bootstrap admin account provisioned.")
    except Exception as e:
        print(f"Failed to provision bootstrap admin account: {e}")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"Global error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "detail": str(exc), "path": request.url.path}
    )

@app.get("/api/v2/auth/mock-login")
def mock_login(role: str = "commander"):
    if not IS_DEV_ENV:
        raise HTTPException(status_code=404, detail="Not found")
    return {"access_token": get_mock_token(role), "token_type": "bearer"}

@app.get("/api/v2/auth/config")
def auth_config():
    """
    Tells the frontend how it is allowed to authenticate in THIS deployment.
    - dev_login_enabled: whether the no-credential mock-login endpoint is
      reachable at all (only true in development/dev/local).
    - credential_login_enabled: whether real email/password sign-in against
      platform_user is available (true whenever the table has at least one
      active account provisioned).
    This lets the login screen stop advertising "demo mode" / "skip login"
    as an option the moment a real deployment has real accounts, without any
    client-side toggle able to re-enable it.
    """
    db = get_database()
    has_real_users = False
    try:
        with db.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM platform_user WHERE is_active = TRUE")
            has_real_users = cursor.fetchone()[0] > 0
    except Exception:
        has_real_users = False
    return {
        "dev_login_enabled": IS_DEV_ENV,
        "credential_login_enabled": has_real_users,
    }

@app.post("/api/v2/auth/login")
def login(req: LoginRequest):
    """
    Real credential-backed sign-in. Verifies the submitted password against
    the PBKDF2 hash stored for the account and, on success, issues a JWT
    carrying that account's actual roles/tenant/workspace -- replacing the
    mock-login flow where any typed password worked and the "role" was
    whatever the operator happened to click in a dropdown.
    """
    db = get_database()
    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT user_id, email, password_hash, roles, tenant_id, workspace_id, is_active FROM platform_user WHERE email = %s",
            (req.email.strip().lower(),),
        )
        row = cursor.fetchone()

    invalid_credentials = HTTPException(status_code=401, detail="Invalid email or password")
    if not row:
        raise invalid_credentials

    user_id, email, password_hash, roles, tenant_id, workspace_id, is_active = row
    if not is_active or not verify_password(req.password, password_hash):
        raise invalid_credentials

    token = issue_token_for_user({
        "user_id": user_id,
        "email": email,
        "roles": deserialize_roles(roles),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
    })

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE platform_user SET last_login_at = %s WHERE user_id = %s",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )

    return {"access_token": token, "token_type": "bearer"}

@app.on_event("startup")
async def startup_event():
    global temporal_client
    db = get_database()
    try:
        # Initialize the base schema only for a brand-new database.
        # table_exists() is backend-agnostic (PostgresDatabase queries
        # pg_tables; SqliteDatabase queries sqlite_master) -- this route
        # no longer embeds Postgres-only catalog SQL directly.
        schema_exists = db.table_exists("incident")

        if not schema_exists:
            print("Initializing database schema...")
            db.init_schema()
        else:
            # Existing deployments still need any newly-added migrations
            # (Postgres only -- SqliteDatabase.apply_pending_migrations()
            # is an intentional no-op for Phase 1; see its docstring).
            print("Applying pending database migrations...")
            db.apply_pending_migrations()

    except Exception as e:
        print(f"Failed to initialize database (backend={get_db_backend_name()}): {e}")
        raise

    print(f"[startup-timing] database startup complete at +{__import__('time').perf_counter() - _startup_t0:.2f}s")

    # This table must also be created for existing SQLite databases, whose
    # PostgreSQL migration runner is intentionally disabled.
    with db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_delivery (
                delivery_key VARCHAR(200) PRIMARY KEY,
                payload_hash VARCHAR(64) NOT NULL,
                result_json TEXT NOT NULL
            )
        """)

    with db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reviewed_alert_document (
                document_id VARCHAR(64) PRIMARY KEY,
                alert_id VARCHAR(128) NOT NULL REFERENCES alert(alert_id),
                document_kind VARCHAR(32) NOT NULL,
                title VARCHAR(200) NOT NULL,
                content TEXT NOT NULL,
                submitted_by VARCHAR(200) NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
        """)

    from app.store import alert_inbox
    alert_inbox.initialize(db)
    from app.domain import incident_knowledge, model_settings
    incident_knowledge.initialize(db)
    model_settings.initialize(db)

    _bootstrap_admin_if_configured(db)
    print(f"[startup-timing] bootstrap admin complete at +{__import__('time').perf_counter() - _startup_t0:.2f}s")

    # NEMOGUARD_TEMPORAL_ENABLED defaults to "true" so EXISTING local/
    # standalone deployments keep attempting a Temporal connection exactly
    # as before. aXet Cloud (which has no Temporal service at all) sets
    # this to "false" explicitly, so startup never wastes time attempting
    # an unreachable localhost:7233 connection -- temporal_client simply
    # stays None, and every existing "if temporal_client: ... else:
    # direct-background fallback" code path already handles that exactly
    # the same way it does today when the optional Temporal connection
    # attempt below merely fails. The `temporalio.client` import itself
    # (which pulls in Temporal's native bridge extension) is deferred to
    # this branch -- it is never imported at all when Temporal is
    # disabled, which is the normal aXet Cloud configuration.
    temporal_enabled = os.getenv("NEMOGUARD_TEMPORAL_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")
    if not temporal_enabled:
        temporal_client = None
        print("Temporal is disabled (NEMOGUARD_TEMPORAL_ENABLED=false); deterministic direct-triage/direct-execution fallback remains available.")
    else:
        try:
            from temporalio.client import Client
            temporal_url = os.getenv("TEMPORAL_URL", "localhost:7233")
            temporal_client = await Client.connect(temporal_url)
            print(f"Connected to Temporal server at {temporal_url}")
        except Exception as e:
            print(f"Failed to connect to Temporal: {e}")

    print(f"[startup-timing] temporal startup phase complete at +{__import__('time').perf_counter() - _startup_t0:.2f}s")
    print(f"[startup-timing] FastAPI startup handler complete at +{__import__('time').perf_counter() - _startup_t0:.2f}s")

# --- 12.1 Overview endpoints ---

@app.get("/api/v2/status")
def get_status():
    """
    Reports real runtime state -- never leaks database paths, connection
    strings, tokens, or credentials. `database` is probed with a cheap
    live query (never just "assumed healthy") so a genuinely broken
    database connection is surfaced as "unhealthy" rather than always
    reporting "healthy" regardless of actual state.
    """
    database_backend = get_db_backend_name()
    database_status = "healthy"
    try:
        db = get_database()
        with db.get_connection() as conn:
            conn.execute("SELECT 1")
    except Exception:
        database_status = "unhealthy"

    temporal_status = "connected" if temporal_client else (
        "disabled" if os.getenv("NEMOGUARD_TEMPORAL_ENABLED", "true").strip().lower() in ("false", "0", "no", "off")
        else "disconnected"
    )

    policy_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../config/capability_policy.yaml"))
    policy_status = "configured" if os.path.exists(policy_path) else "not_configured"

    inference_requirements = {
        "base_url": os.environ.get("AXET_LLM_BASE_URL") or os.environ.get("OPENAI_API_BASE"),
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "model": os.environ.get("AXET_MODEL"),
        "project_id": os.environ.get("AXET_PROJECT_ID"),
        "user_id": os.environ.get("AXET_USER_ID"),
        "backend_jwt": os.environ.get("DEPTAPPS_WEB_BACKEND_JWT_TOKEN"),
    }
    inference_status = "configured" if all(inference_requirements.values()) else "not_configured"

    return {
        "environment": os.environ.get("ENV", "production"),
        "database_backend": database_backend,
        "database": database_status,
        # These are intentionally not labelled "healthy" without an active
        # dependency probe.  V22 distinguishes configured/available from
        # genuinely health-checked runtime state.
        "orchestrator": "available" if database_status == "healthy" else "degraded",
        "policy_engine": policy_status,
        "temporal": temporal_status,
        "inference_provider": "axet" if inference_status == "configured" else "not_configured",
        "inference_status": inference_status,
        "outlook_bridge_contract": 1,
        "outlook_bridge_attachments": True,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _operations_snapshot(current_user: User):
    from app.domain.operations_intelligence import build_operations_snapshot
    return build_operations_snapshot(
        get_database(),
        current_user.tenant_id,
        current_user.workspace_id,
        get_status(),
    )


@app.get("/api/v2/operations/snapshot")
def get_operations_snapshot(current_user: User = Depends(get_current_user)):
    """One deterministic, explainable snapshot for the V22 operations home."""
    return JSONResponse(_operations_snapshot(current_user), headers={"Cache-Control": "no-store"})


@app.get("/api/v2/overview")
def get_overview(current_user: User = Depends(get_current_user)):
    """Compatibility overview backed only by real persisted measurements."""
    snapshot = _operations_snapshot(current_user)
    summary = snapshot["summary"]
    return {
        "open_incidents": summary["open_incidents"],
        "critical_incidents": summary["high_priority_incidents"],
        "high_incidents": summary["high_priority_incidents"],
        "open_high_priority_alerts": summary["high_priority_alerts"],
        "verified_recoveries_30d": summary["verified_recoveries_30d"],
        "median_triage_seconds": summary["median_triage_seconds"],
        "alerts_last_24h": summary["alerts_last_24h"],
    }

@app.get("/api/v2/incidents")
def list_incidents(state: str = "open", current_user: User = Depends(get_current_user)):
    db = get_database()
    with db.get_connection() as conn:
        # Include resolved_at so the frontend can show "time to resolve" for
        # resolved incidents instead of a live-ticking elapsed-time clock that
        # keeps counting up forever even after the incident is closed.
        if state == "open":
            # FAILED is terminal as well: the recovery attempt has completed
            # but independent verification did not pass, so it must be worked
            # from the escalation/history view rather than presented as an
            # unchanged active incident in the operator queue.
            cursor = conn.execute(
                "SELECT incident_id, title, status, severity, detected_at, next_sla_breach_at, owner_team, primary_job_id, summary, resolved_at "
                "FROM incident WHERE tenant_id = %s AND (workspace_id = %s OR workspace_id IS NULL) "
                "AND status NOT IN ('RESOLVED', 'FAILED', 'CLOSED', 'CANCELLED') ORDER BY detected_at DESC",
                (current_user.tenant_id, current_user.workspace_id),
            )
        else:
            cursor = conn.execute(
                "SELECT incident_id, title, status, severity, detected_at, next_sla_breach_at, owner_team, primary_job_id, summary, resolved_at "
                "FROM incident WHERE tenant_id = %s AND (workspace_id = %s OR workspace_id IS NULL) ORDER BY detected_at DESC",
                (current_user.tenant_id, current_user.workspace_id),
            )
            
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

# --- 12.2 Incident detail endpoints ---

@app.get("/api/v2/incidents/{incident_id}")
def get_incident(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM incident WHERE incident_id = %s", (incident_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found")
        cols = [col[0] for col in cursor.description]
        return dict(zip(cols, row))

@app.get("/api/v2/incidents/{incident_id}/summary")
def get_incident_summary(incident_id: str, current_user: User = Depends(get_current_user)):
    # For now, just return the incident details.
    return get_incident(incident_id, current_user)

@app.get("/api/v2/incidents/{incident_id}/hypotheses")
def get_hypotheses(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM hypothesis WHERE incident_id = %s ORDER BY confidence DESC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        for r in rows:
            r['supporting_evidence_ids'] = json.loads(r['supporting_evidence_json'])
            r['contradicting_evidence_ids'] = json.loads(r['contradicting_evidence_json'])
        return rows

@app.get("/api/v2/incidents/{incident_id}/evidence")
def get_evidence(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM evidence WHERE incident_id = %s ORDER BY collected_at ASC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/incidents/{incident_id}/impact")
def get_impact(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT i.*, d.name as asset_name, d.freshness_sla_minutes 
            FROM incident_impact i 
            LEFT JOIN data_asset d ON i.asset_id = d.asset_id 
            WHERE i.incident_id = %s
        """, (incident_id,))
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        for r in rows:
            r['evidence_ids'] = json.loads(r['evidence_ids_json'])
        return rows

@app.get("/api/v2/incidents/{incident_id}/plans")
def get_plans(incident_id: str, current_user: User = Depends(get_current_user)):
    from app.domain.plan_hash import compute_plan_hash
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM action_plan WHERE incident_id = %s ORDER BY created_at DESC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        plans = [dict(zip(cols, row)) for row in cursor.fetchall()]
        
        for plan in plans:
            cursor = conn.execute("SELECT * FROM action_step WHERE action_plan_id = %s ORDER BY sequence_no ASC", (plan['action_plan_id'],))
            step_cols = [col[0] for col in cursor.description]
            plan['steps'] = [dict(zip(step_cols, row)) for row in cursor.fetchall()]
            # Compute the real content hash so the frontend can send it back unchanged on /approve.
            plan['plan_hash'] = compute_plan_hash(plan, plan['steps'])
        return plans

@app.get("/api/v2/incidents/{incident_id}/events")
def get_events(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM audit_event WHERE incident_id = %s ORDER BY created_at ASC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/incidents/{incident_id}/alerts")
def get_incident_alerts(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT a.* FROM alert a
            JOIN incident_alert ia ON a.alert_id = ia.alert_id
            WHERE ia.incident_id = %s
        """, (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


class AlertRecorrelationRequest(BaseModel):
    target_incident_id: str
    reason: str


@app.post("/api/v2/incidents/{incident_id}/alerts/{alert_id}/recorrelate")
def recorrelate_alert(
    incident_id: str, alert_id: str, req: AlertRecorrelationRequest,
    current_user: User = Depends(require_role("operator")),
):
    """
    Move an alert's correlation link to a different incident, recording the
    operator's reason on both incidents. This does NOT copy, move, or
    revise any evidence, hypotheses, or plans already generated against
    the source incident -- those remain as a historical record of what was
    investigated under the original (incorrect) correlation. The target
    incident does not automatically get new evidence derived from this
    alert; a fresh investigation/triage against the target incident is a
    separate, deliberate operator action.
    """
    reason = req.reason.strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(status_code=400, detail="A reason of at most 2000 characters is required")
    if req.target_incident_id == incident_id:
        raise HTTPException(status_code=400, detail="Target incident must differ from the current incident")

    db = get_database()
    now = datetime.now(timezone.utc).isoformat()
    with db.get_connection() as conn:
        _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
        _require_incident_in_tenant(db, req.target_incident_id, current_user.tenant_id, current_user.workspace_id)

        link = conn.execute(
            "SELECT correlation_score, correlation_reasons_json FROM incident_alert "
            "WHERE incident_id=%s AND alert_id=%s",
            (incident_id, alert_id),
        ).fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Alert is not correlated to this incident")
        if conn.execute(
            "SELECT 1 FROM incident_alert WHERE incident_id=%s AND alert_id=%s",
            (req.target_incident_id, alert_id),
        ).fetchone():
            raise HTTPException(status_code=409, detail="Alert is already correlated to the target incident")

        # Remove the old link and create the new one atomically, so an
        # alert is never simultaneously correlated to two incidents nor
        # orphaned if this request fails partway through.
        conn.execute(
            "DELETE FROM incident_alert WHERE incident_id=%s AND alert_id=%s",
            (incident_id, alert_id),
        )
        conn.execute(
            "INSERT INTO incident_alert (incident_id, alert_id, correlation_score, correlation_reasons_json, added_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (req.target_incident_id, alert_id, link[0],
             json.dumps([f"Operator re-correlation: {reason}"]), now),
        )

        for target, other, direction in (
            (incident_id, req.target_incident_id, "moved to"),
            (req.target_incident_id, incident_id, "moved from"),
        ):
            conn.execute(
                "INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (f"AUD-{uuid.uuid4().hex[:8]}", target, "HUMAN", current_user.user_id,
                 "ALERT_RECORRELATED",
                 f"Alert {alert_id} {direction} incident {other} by operator. Reason: {reason}",
                 now),
            )
    return {"status": "recorrelated", "alert_id": alert_id, "from_incident_id": incident_id, "to_incident_id": req.target_incident_id}

@app.get("/api/v2/alerts")
def get_all_alerts(current_user: User = Depends(get_current_user)):
    db = get_database()
    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM alert WHERE tenant_id = %s AND (workspace_id = %s OR workspace_id IS NULL) ORDER BY opened_ts DESC",
            (current_user.tenant_id, current_user.workspace_id),
        )
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

# --- 12.3 Context Endpoints (for NemoClaw Agents) ---

@app.get("/api/v2/context/alerts/{incident_id}")
def get_incident_alerts_context(incident_id: str):
    db = get_database()
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT a.alert_id, a.alert_type, a.source_system, a.message, a.opened_ts 
            FROM alert a
            JOIN incident_alert ia ON a.alert_id = ia.alert_id
            WHERE ia.incident_id = %s
        """, (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/context/logs/{incident_id}")
def get_incident_logs_context(incident_id: str):
    db = get_database()
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT primary_run_id FROM incident WHERE incident_id = %s", (incident_id,))
        run_id_row = cursor.fetchone()
        if run_id_row and run_id_row[0]:
            cursor = conn.execute("SELECT message FROM log_event WHERE run_id = %s", (run_id_row[0],))
            return [row[0] for row in cursor.fetchall()]
        return []

@app.get("/api/v2/context/cmdb")
def get_cmdb_context():
    import json
    with open("data/mock_dimensions/cmdb.json", "r") as f:
        return json.load(f)

@app.get("/api/v2/context/runbooks")
def get_runbooks_context():
    import json
    with open("data/mock_dimensions/runbooks.json", "r") as f:
        return json.load(f)

# --- 12.4 Workflow endpoints ---

@app.post("/api/v2/ingest/email-preview")
async def preview_email_export(
    request: Request,
    current_user: User = Depends(require_role("operator")),
):
    """Preview an Outlook MSG export without creating an alert or calling the LLM."""
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
    supplied = request.headers.get("X-NemoGuard-Webhook-Secret", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")

    limit = 5 * 1024 * 1024
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="Email export exceeds the 5 MB limit")
        data.extend(chunk)
    if not data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise HTTPException(status_code=400, detail="Expected an Outlook .msg file")
    try:
        import extract_msg
    except ImportError:
        raise HTTPException(status_code=503, detail="Outlook .msg parser is not installed")
    try:
        with extract_msg.Message(bytes(data), delayAttachments=True) as message:
            subject = message.subject or ""
            body = message.body or ""
            attachments = []
            all_attachments = message.attachments
            attachments_truncated = len(all_attachments) > 20
            for attachment in all_attachments[:20]:
                name = attachment.longFilename or attachment.shortFilename or "unnamed"
                name = str(name).replace("\\", "/").split("/")[-1][:200]
                item = {"name": name, "text": None, "reason": None}
                if not name.lower().endswith((".log", ".txt", ".csv")):
                    item["reason"] = "Unsupported attachment type"
                else:
                    raw = attachment.data
                    if not isinstance(raw, bytes) or len(raw) > 65536:
                        item["reason"] = "Attachment exceeds 64 KB or is not a file"
                    else:
                        try:
                            text = raw.decode("utf-8-sig")
                            if "\x00" in text:
                                raise ValueError("Binary content")
                            item["text"] = text
                        except (UnicodeError, ValueError):
                            item["reason"] = "Not supported UTF-8 text"
                attachments.append(item)
        if not isinstance(subject, str) or not isinstance(body, str):
            raise ValueError("Invalid message text")
        if len(subject) > 1000 or len(body) > 100000:
            raise HTTPException(status_code=413, detail="Message text is too large to preview")
        return JSONResponse({"subject": subject, "body": body, "attachments": attachments, "attachments_truncated": attachments_truncated}, headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Unable to parse this Outlook .msg file")

@app.post("/api/v2/ingest/webhook")
async def ingest_webhook(payload: dict, request: Request):
    """
    Ingest a webhook using a configured shared secret and a stable delivery ID.
    The receipt and alert are committed atomically by the orchestrator.
    """
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
    supplied = request.headers.get("X-NemoGuard-Webhook-Secret", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    delivery_key = request.headers.get("Idempotency-Key", "").strip()
    if not delivery_key or len(delivery_key) > 200 or not delivery_key.isascii():
        raise HTTPException(status_code=400, detail="A stable ASCII Idempotency-Key of at most 200 characters is required")
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    from app.domain.orchestrator import IncidentOrchestrator
    orchestrator = IncidentOrchestrator()
    reviewed = getattr(request.state, "reviewed_documents", None)
    if reviewed is None:
        result = await orchestrator.process_webhook(payload, delivery_key, payload_hash)
    else:
        classification_payload = {k: v for k, v in payload.items() if k != "attachments"}
        options = {}
        if getattr(request.state, "outlook_scope", None) is not None:
            options["trusted_scope"] = request.state.outlook_scope
        result = await orchestrator.process_webhook(
            classification_payload, delivery_key, payload_hash,
            reviewed_documents=reviewed, submitted_by=request.state.reviewed_by,
            **options,
        )

    if result.get("status") == "idempotency_conflict":
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different payload")
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
        
    if result.get("status") == "ingested_and_incident_created" and not result.get("duplicate"):
        incident_id = result.get("incident_id")
        global temporal_client
        # Unified scheduling: schedule_triage_for_new_incident() owns
        # BOTH the Temporal start_workflow() call (with failure handling
        # and conditional atomic status restoration) AND the no-Temporal
        # direct-background path, for a brand-new incident that
        # process_webhook() has already inserted with status
        # INVESTIGATING. This incident never existed before this request,
        # so schedule_triage()'s same-incident "already active" dedup
        # check is correctly skipped entirely -- there was nothing prior
        # to deduplicate against. `fallback_status` is the incident's
        # real pre-INVESTIGATING lifecycle state (DETECTED), taken from
        # the IncidentState enum rather than a hardcoded raw string.
        from app.domain.triage_scheduler import schedule_triage_for_new_incident
        schedule_result = await schedule_triage_for_new_incident(
            temporal_client, incident_id, fallback_status=IncidentState.DETECTED.value,
        )

        # The alert/incident itself WAS successfully ingested and created
        # regardless of what happens next -- that part of `result` is
        # never altered. Only the *automatic triage scheduling* outcome
        # is surfaced separately, so a caller cannot mistake a scheduling
        # failure for an ingestion failure (and vice versa). No raw
        # exception text is included -- schedule_triage_for_new_incident()
        # already logs the detailed exception server-side only.
        if schedule_result.get("status") == "TEMPORAL_START_FAILED":
            result = {
                **result,
                "triage_scheduling_status": "TEMPORAL_START_FAILED",
                "triage_scheduling_restored": schedule_result.get("restored", False),
            }
        else:
            result = {**result, "triage_scheduling_status": schedule_result.get("status")}

    return result

@app.post("/api/v2/ingest/outlook-bridge")
async def ingest_outlook_bridge(payload: dict, request: Request):
    """Ingest automatically captured Outlook text as unreviewed external evidence."""
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    supplied = request.headers.get("X-NemoGuard-Webhook-Secret", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    subject, body = payload.get("monitor_name"), payload.get("message")
    if (payload.get("source") != "Outlook email" or not isinstance(subject, str)
            or len(subject) > 2000 or not isinstance(body, str) or not body.strip()
            or "\x00" in subject or "\x00" in body or len(body.encode("utf-8")) > 400000):
        raise HTTPException(status_code=400, detail="Invalid Outlook email subject or body")
    from app.domain.outlook_attachments import validate_attachments
    try:
        attachments = validate_attachments(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    delivery_key = request.headers.get("Idempotency-Key", "").strip()
    if not delivery_key or len(delivery_key) > 200 or not delivery_key.isascii():
        raise HTTPException(status_code=400, detail="A stable ASCII Idempotency-Key of at most 200 characters is required")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    from app.store import alert_inbox
    db = get_database()
    if not alert_inbox.receive(db, delivery_key, digest, subject, body, canonical):
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different payload")
    request.state.reviewed_documents = [{"kind": "EMAIL_BODY", "title": subject[:200] or "Outlook email", "content": body}, *attachments]
    request.state.reviewed_by = "automatic-outlook-bridge"
    request.state.outlook_scope = alert_inbox.scope()
    try:
        result = await ingest_webhook(payload, request)
    except Exception:
        alert_inbox.record_result(db, delivery_key, {"status": "error", "message": "Processing failed; delivery can be retried."})
        raise
    alert_inbox.record_result(db, delivery_key, result)
    return result


@app.post("/api/v2/ingest/outlook-bridge-heartbeat")
def outlook_bridge_heartbeat(payload: dict, request: Request):
    """Record a completed Outlook folder scan without email content."""
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    supplied = request.headers.get("X-NemoGuard-Webhook-Secret", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
    if not hmac.compare_digest(supplied.encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    counts = [payload.get(key) for key in ("pending", "blocked", "baseline")]
    if any(type(value) is not int or value < 0 or value > 1000000000 for value in counts):
        raise HTTPException(status_code=400, detail="Invalid bridge counts")
    from app.store import alert_inbox
    tenant, workspace = alert_inbox.scope()
    with get_database().get_connection() as conn:
        conn.execute(
            "INSERT INTO outlook_bridge_heartbeat "
            "(tenant_id, workspace_id, observed_at, pending_count, blocked_count, baseline_count) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, workspace_id) DO UPDATE SET "
            "observed_at=excluded.observed_at, pending_count=excluded.pending_count, "
            "blocked_count=excluded.blocked_count, baseline_count=excluded.baseline_count",
            (tenant, workspace, datetime.now(timezone.utc).isoformat(), *counts),
        )
    return {"status": "recorded"}


@app.get("/api/v2/outlook-bridge-health")
def outlook_bridge_health(current_user: User = Depends(require_role("operator"))):
    with get_database().get_connection() as conn:
        row = conn.execute(
            "SELECT observed_at, pending_count, blocked_count, baseline_count "
            "FROM outlook_bridge_heartbeat WHERE tenant_id=%s AND workspace_id=%s",
            (current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
    data = None if not row else {
        "last_scan_at": row[0], "pending": row[1], "blocked": row[2], "baseline": row[3],
    }
    return JSONResponse({"last_report": data}, headers={"Cache-Control": "no-store"})


@app.get("/api/v2/outlook-inbox")
def list_outlook_inbox(current_user: User = Depends(require_role("operator"))):
    from app.store import alert_inbox
    items = alert_inbox.list_items(get_database(), current_user.tenant_id, current_user.workspace_id)
    return JSONResponse({"items": items, "limit": 200}, headers={"Cache-Control": "no-store"})


@app.get("/api/v2/outlook-inbox/{delivery_key}")
def get_outlook_inbox_item(delivery_key: str, current_user: User = Depends(require_role("operator"))):
    db = get_database()
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT subject, body, state, received_at, updated_at, result_json FROM outlook_inbox "
            "WHERE delivery_key=%s AND tenant_id=%s AND workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        reviews = conn.execute(
            "SELECT assessment, reason, actor_id, created_at FROM outlook_inbox_review "
            "WHERE delivery_key=%s AND tenant_id=%s AND workspace_id=%s ORDER BY created_at DESC",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchall() if row else []
        promotion = conn.execute(
            "SELECT alert_id, actor_id, reason, created_at FROM outlook_inbox_promotion WHERE delivery_key=%s",
            (delivery_key,),
        ).fetchone() if row else None
        payload_row = conn.execute(
            "SELECT p.payload_json, i.payload_hash FROM outlook_inbox_payload p "
            "JOIN outlook_inbox i ON i.delivery_key=p.delivery_key "
            "WHERE i.delivery_key=%s AND i.tenant_id=%s AND i.workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone() if row else None
    if not row:
        raise HTTPException(status_code=404, detail="Inbox item not found")
    payload = {}
    if payload_row and hashlib.sha256(payload_row[0].encode("utf-8")).hexdigest() == payload_row[1]:
        payload = json.loads(payload_row[0])
    return JSONResponse({
        "delivery_key": delivery_key, "subject": row[0], "body": row[1], "state": row[2],
        "attachments": payload.get("attachments", []),
        "omitted_attachments": payload.get("omitted_attachments", []),
        "attachments_recorded": "attachments" in payload and "omitted_attachments" in payload,
        "payload_available": bool(payload_row and payload),
        "received_at": row[3], "updated_at": row[4], "result": json.loads(row[5]),
        "reviews": [{"assessment": r[0], "reason": r[1], "actor_id": r[2], "created_at": r[3]} for r in reviews],
        "promotion": None if not promotion else {
            "alert_id": promotion[0], "actor_id": promotion[1], "reason": promotion[2], "created_at": promotion[3],
        },
    }, headers={"Cache-Control": "no-store"})


@app.post("/api/v2/outlook-inbox/{delivery_key}/retry")
async def retry_outlook_inbox_item(
    delivery_key: str,
    current_user: User = Depends(require_role("operator")),
):
    """Retry a retained delivery using its original payload and idempotency key."""
    from app.store import alert_inbox
    if (current_user.tenant_id, current_user.workspace_id) != alert_inbox.scope():
        raise HTTPException(status_code=404, detail="Inbox item not found")
    db = get_database()
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT i.state, i.payload_hash, p.payload_json FROM outlook_inbox i "
            "LEFT JOIN outlook_inbox_payload p ON p.delivery_key=i.delivery_key "
            "WHERE i.delivery_key=%s AND i.tenant_id=%s AND i.workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Inbox item not found")
        if row[0] not in ("received", "failed"):
            raise HTTPException(status_code=409, detail="Only failed or unfinished deliveries can be retried")
        if not row[2] or hashlib.sha256(row[2].encode("utf-8")).hexdigest() != row[1]:
            raise HTTPException(status_code=409, detail="Original delivery payload is unavailable or invalid")
        secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
        conn.execute(
            "INSERT INTO outlook_inbox_retry "
            "(retry_id, delivery_key, tenant_id, workspace_id, actor_id, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (f"RETRY-{uuid.uuid4().hex}", delivery_key, current_user.tenant_id,
             current_user.workspace_id, current_user.user_id, datetime.now(timezone.utc).isoformat()),
        )
        payload = json.loads(row[2])
    internal_request = Request({
        "type": "http", "method": "POST", "path": "/api/v2/ingest/outlook-bridge",
        "headers": [
            (b"x-nemoguard-webhook-secret", secret.encode("utf-8")),
            (b"idempotency-key", delivery_key.encode("ascii")),
        ],
    })
    return await ingest_outlook_bridge(payload, internal_request)


class OutlookInboxReview(BaseModel):
    assessment: str
    reason: str


@app.post("/api/v2/outlook-inbox/{delivery_key}/reviews")
def review_outlook_inbox_item(
    delivery_key: str, review: OutlookInboxReview,
    current_user: User = Depends(require_role("operator")),
):
    if review.assessment not in ("alert", "noise", "uncertain"):
        raise HTTPException(status_code=400, detail="Invalid assessment")
    reason = review.reason.strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(status_code=400, detail="A reason of at most 2000 characters is required")
    db = get_database()
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM outlook_inbox WHERE delivery_key=%s AND tenant_id=%s AND workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Inbox item not found")
        conn.execute(
            "INSERT INTO outlook_inbox_review "
            "(review_id, delivery_key, tenant_id, workspace_id, assessment, reason, actor_id, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"REV-{uuid.uuid4().hex}", delivery_key, current_user.tenant_id,
             current_user.workspace_id, review.assessment, reason, current_user.user_id,
             datetime.now(timezone.utc).isoformat()),
        )
    return {"status": "assessment_recorded"}


class OutlookInboxPromotion(BaseModel):
    severity: str
    reason: str


@app.post("/api/v2/outlook-inbox/{delivery_key}/promote")
def promote_outlook_inbox_item(
    delivery_key: str, promotion: OutlookInboxPromotion,
    current_user: User = Depends(require_role("operator")),
):
    if promotion.severity not in ("info", "warning", "high", "critical"):
        raise HTTPException(status_code=400, detail="Invalid severity")
    reason = promotion.reason.strip()
    if not reason or len(reason) > 2000:
        raise HTTPException(status_code=400, detail="A reason of at most 2000 characters is required")
    timestamp = datetime.now(timezone.utc).isoformat()
    alert_id = f"WEB-ALT-{uuid.uuid4().hex}"
    with get_database().get_connection() as conn:
        claimed = conn.execute(
            "UPDATE outlook_inbox SET state=%s, updated_at=%s "
            "WHERE delivery_key=%s AND tenant_id=%s AND workspace_id=%s AND state=%s",
            ("promoted", timestamp, delivery_key, current_user.tenant_id,
             current_user.workspace_id, "needs_review"),
        )
        item = conn.execute(
            "SELECT subject, body, state, result_json FROM outlook_inbox "
            "WHERE delivery_key=%s AND tenant_id=%s AND workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Inbox item not found")
        if claimed.rowcount != 1 or json.loads(item[3]).get("status") != "ignored":
            raise HTTPException(status_code=409, detail="Only ignored emails can be promoted")
        original = conn.execute(
            "SELECT p.payload_json, i.payload_hash FROM outlook_inbox_payload p "
            "JOIN outlook_inbox i ON i.delivery_key=p.delivery_key "
            "WHERE i.delivery_key=%s AND i.tenant_id=%s AND i.workspace_id=%s",
            (delivery_key, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        if not original or hashlib.sha256(original[0].encode("utf-8")).hexdigest() != original[1]:
            raise HTTPException(status_code=409, detail="Original delivery payload is unavailable or invalid")
        from app.domain.outlook_attachments import validate_attachments
        try:
            documents = validate_attachments(json.loads(original[0]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail="Original attachment data is invalid") from exc
        conn.execute(
            "INSERT INTO alert (alert_id, tenant_id, workspace_id, opened_ts, severity, alert_type, source_system, message, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (alert_id, current_user.tenant_id, current_user.workspace_id, timestamp,
             promotion.severity, "OPERATOR_REVIEWED_EMAIL", "Outlook email",
             item[0][:2000] or "Operator-promoted email alert", "open"),
        )
        conn.execute(
            "INSERT INTO reviewed_alert_document "
            "(document_id, alert_id, document_kind, title, content, submitted_by, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"DOC-{uuid.uuid4().hex}", alert_id, "EMAIL_BODY",
             item[0][:200] or "Outlook email", item[1], "automatic-outlook-bridge", timestamp),
        )
        for document in documents:
            conn.execute(
                "INSERT INTO reviewed_alert_document "
                "(document_id, alert_id, document_kind, title, content, submitted_by, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (f"DOC-{uuid.uuid4().hex}", alert_id, document["kind"], document["title"],
                 document["content"], "automatic-outlook-bridge", timestamp),
            )
        conn.execute(
            "INSERT INTO outlook_inbox_promotion (delivery_key, alert_id, actor_id, reason, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (delivery_key, alert_id, current_user.user_id, reason, timestamp),
        )
    return {"status": "promoted", "alert_id": alert_id}


@app.post("/api/v2/ingest/email-replay")
async def replay_reviewed_email(
    payload: dict,
    request: Request,
    current_user: User = Depends(require_role("operator")),
):
    """Replay reviewed text with operator authorization and webhook authentication."""
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook ingestion is not configured")
    if not hmac.compare_digest(request.headers.get("X-NemoGuard-Webhook-Secret", "").encode("utf-8"), secret.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid webhook credentials")
    subject, body = payload.get("monitor_name"), payload.get("message")
    attachments = payload.get("attachments", [])
    if not isinstance(subject, str) or len(subject) > 1000 or not isinstance(body, str) or not body.strip() or len(body.encode("utf-8")) > 65536:
        raise HTTPException(status_code=400, detail="Invalid reviewed email subject or body")
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise HTTPException(status_code=400, detail="Select at most three text attachments")
    documents = [{"kind": "EMAIL_BODY", "title": subject[:200] or "Reviewed email", "content": body}]
    total = len(body.encode("utf-8"))
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise HTTPException(status_code=400, detail="Invalid attachment")
        name, text = attachment.get("name"), attachment.get("text")
        if not isinstance(name, str) or not name or len(name) > 200 or "/" in name or "\\" in name or not name.lower().endswith((".log", ".txt", ".csv")):
            raise HTTPException(status_code=400, detail="Invalid attachment name")
        if not isinstance(text, str) or not text.strip() or "\x00" in text or len(text.encode("utf-8")) > 65536:
            raise HTTPException(status_code=400, detail="Invalid attachment text")
        total += len(text.encode("utf-8"))
        documents.append({"kind": "EMAIL_ATTACHMENT", "title": name, "content": text})
    if total > 131072:
        raise HTTPException(status_code=413, detail="Reviewed email and attachments exceed 128 KB")
    request.state.reviewed_documents = documents
    request.state.reviewed_by = current_user.user_id
    return await ingest_webhook(payload, request)

@app.post("/api/v2/incidents/{incident_id}/triage")
async def triage_incident(incident_id: str, current_user: User = Depends(get_current_user)):
    """
    Manual "Start Triage" entry point. Delegates the ENTIRE scheduling
    decision (Temporal vs. direct background execution, the actual
    `start_workflow()` call and its failure handling, same-incident
    deduplication via a DB-level `SELECT ... FOR UPDATE`, and background
    task lifetime tracking) to the single shared
    app.domain.triage_scheduler.schedule_triage() -- an `async def` --
    so the webhook ingestion path can use the exact same logic rather
    than duplicating it, and so a Temporal start_workflow() failure is
    caught and the incident's status restored inside schedule_triage()
    itself rather than left to this route to handle separately.
    """
    global temporal_client
    _require_incident_in_tenant(get_database(), incident_id, current_user.tenant_id, current_user.workspace_id)
    from app.domain.triage_scheduler import schedule_triage
    result = await schedule_triage(temporal_client, incident_id)

    if result["status"] == "INCIDENT_NOT_FOUND":
        raise HTTPException(status_code=404, detail="Incident not found")
    if result["status"] == "TRIAGE_ALREADY_ACTIVE":
        raise HTTPException(status_code=409, detail="Triage already in progress for this incident")
    if result["status"] == "SOP_GATE_FAILED":
        raise HTTPException(status_code=503, detail="Unable to check SOP availability; triage was not started.")
    if result["status"] == "TEMPORAL_START_FAILED":
        # Truthful detail text: only claim restoration happened if the
        # scheduler's atomic conditional restore actually reported success
        # (result["restored"]). If the incident had already moved on to a
        # different status by the time the restore ran, do not falsely
        # tell the operator their incident was reset.
        detail = (
            "Failed to schedule triage via Temporal; incident status restored"
            if result.get("restored")
            else "Failed to schedule triage via Temporal; incident status may require manual review"
        )
        raise HTTPException(status_code=502, detail=detail)

    return result

@app.post("/api/v2/incidents/{incident_id}/agent-findings")
def agent_findings(incident_id: str, payload: dict, current_user: User = Depends(get_current_user)):
    """
    Accepts externally-supplied agent findings (e.g. from a legacy/
    external TS-nemoclaw-style pipeline) and persists them. No in-process
    Grounding Critic safety review runs anywhere in this route -- the
    payload is an arbitrary, externally-supplied dict. Any critic_status/
    critic_passed-like field embedded inside `payload` is intentionally
    IGNORED and never forwarded to save_agent_findings(): there is
    currently no strongly-typed, authenticated, validated safety-verdict
    contract for this route, so trusting a self-reported verdict inside
    an arbitrary payload would let an external caller claim its own
    findings "passed" safety review without any real review ever having
    occurred. The safety status is therefore forced to INCONCLUSIVE here,
    which save_agent_findings() routes to NEEDS_REVIEW (never
    PENDING_APPROVAL) and logs as SAFETY_VALIDATION_INCONCLUSIVE.
    """
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    from app.domain.orchestrator import IncidentOrchestrator
    orchestrator = IncidentOrchestrator()
    res = orchestrator.save_agent_findings(
        incident_id, payload,
        critic_status="INCONCLUSIVE",
        critic_feedback=(
            "External agent findings were persisted without an in-process "
            "Grounding Critic safety review."
        ),
    )
    return res

@app.get("/api/v2/incidents/{incident_id}/agent-logs")
def agent_logs(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    import os
    log_file = f"logs/{incident_id}_nemoclaw.log"
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            return {"logs": f.read()}
    return {"logs": "Initializing NemoClaw Agent..."}

class ApprovalRequest(BaseModel):
    decision: str
    comment: Optional[str] = None
    plan_hash: str

class FeedbackRequest(BaseModel):
    feedback: str

@app.post("/api/v2/incidents/{incident_id}/feedback")
async def submit_feedback(incident_id: str, req: FeedbackRequest, current_user: User = Depends(get_current_user)):
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    from app.domain.orchestrator import IncidentOrchestrator
    orchestrator = IncidentOrchestrator()
    res = await orchestrator.triage_feedback(incident_id, req.feedback)
    if "error" in res:
        raise HTTPException(status_code=500, detail=res["error"])
    return res


class CancelIncidentRequest(BaseModel):
    reason: Optional[str] = None


@app.post("/api/v2/incidents/{incident_id}/cancel")
async def cancel_incident(
    incident_id: str,
    req: CancelIncidentRequest,
    current_user: User = Depends(require_role("commander")),
):
    """Cancel an incident only at the safe human-approval checkpoint."""
    global temporal_client
    from app.domain.state_machine import StateMachine, InvalidTransitionError

    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM incident WHERE incident_id = %s",
            (incident_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found")
        current_status = row[0]
        try:
            StateMachine.validate_transition(current_status, IncidentState.CANCELLED.value)
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        now = datetime.now(timezone.utc).isoformat()
        cancelled = conn.execute(
            "UPDATE incident SET status = %s, updated_at = %s WHERE incident_id = %s AND status = %s",
            (IncidentState.CANCELLED.value, now, incident_id, current_status),
        )
        if cancelled.rowcount != 1:
            raise HTTPException(status_code=409, detail="Incident state changed; cancellation was not applied.")
        conn.execute(
            "UPDATE action_plan SET status = 'CANCELLED' WHERE incident_id = %s "
            "AND status IN ('PENDING_APPROVAL','NEEDS_REVIEW','APPROVED')",
            (incident_id,),
        )
        reason = (req.reason or "No reason supplied").strip()[:1000]
        conn.execute(
            "INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, details_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                f"AUD-{uuid.uuid4().hex[:8]}", incident_id, "HUMAN", current_user.user_id,
                "INCIDENT_CANCELLED", f"Incident cancelled by operator: {reason}",
                json.dumps({"reason": reason}), now,
            ),
        )

    # If a durable workflow is waiting for approval, release it with a
    # non-approve decision. The database transition above is authoritative;
    # a stale/absent Temporal workflow must not make cancellation fail.
    if temporal_client:
        try:
            from app.domain.workflows.incident_workflow import IncidentLifecycleWorkflow
            handle = temporal_client.get_workflow_handle(f"incident-{incident_id}")
            await handle.signal(IncidentLifecycleWorkflow.approve_plan, {"decision": "cancel", "plan_id": None})
        except Exception as exc:
            print(f"Temporal cancellation signal failed for {incident_id}: {exc}")

    return {"status": "CANCELLED", "incident_id": incident_id}


@app.post("/api/v2/incidents/{incident_id}/plans/{plan_id}/approve")
async def approve_plan(incident_id: str, plan_id: str, req: ApprovalRequest, current_user: User = Depends(require_role("commander"))):
    global temporal_client

    # Decision validation: this endpoint exists ONLY to record an
    # approval -- any other decision value (reject/rejected/yes/true/
    # execute/approved/etc.) must be rejected outright rather than
    # silently proceeding through the approval mutation below. Rejection/
    # revision of a plan goes through the separate feedback/re-plan flow
    # (POST .../feedback), not this endpoint.
    if req.decision.lower() != "approve":
        raise HTTPException(
            status_code=400,
            detail=(
                "This endpoint only accepts decision='approve'. "
                "Use the feedback/re-plan flow to reject or revise a plan."
            ),
        )

    # Validate the plan_hash actually matches the current plan content (defense against stale/tampered approvals).
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    from app.domain.plan_hash import compute_plan_hash
    with db.get_connection() as conn:
        # Bound to BOTH action_plan_id AND incident_id (from the URL path):
        # action_plan_id alone is a unique primary key, but a plan
        # belonging to a DIFFERENT incident must never be validated,
        # approved, or launched into execution just because its plan_id
        # happened to be supplied alongside the wrong incident_id in this
        # route's URL. Missing predicate here would be the ownership gap
        # this check closes.
        cursor = conn.execute(
            "SELECT * FROM action_plan WHERE action_plan_id = %s AND incident_id = %s",
            (plan_id, incident_id),
        )
        plan_row = cursor.fetchone()
        if not plan_row:
            raise HTTPException(status_code=404, detail="Plan not found for this incident")
        reviewed_source = conn.execute(
            "SELECT 1 FROM reviewed_alert_document d JOIN incident_alert ia ON ia.alert_id = d.alert_id "
            "WHERE ia.incident_id = %s LIMIT 1", (incident_id,),
        ).fetchone()
        if reviewed_source:
            raise HTTPException(status_code=409, detail="Email-supplied evidence requires independent review; no approval workflow is available yet.")
        plan_cols = [c[0] for c in cursor.description]
        plan_dict = dict(zip(plan_cols, plan_row))
        cursor = conn.execute("SELECT * FROM action_step WHERE action_plan_id = %s ORDER BY sequence_no ASC", (plan_id,))
        step_cols = [c[0] for c in cursor.description]
        steps = [dict(zip(step_cols, r)) for r in cursor.fetchall()]

    expected_hash = compute_plan_hash(plan_dict, steps)
    if req.plan_hash != expected_hash:
        raise HTTPException(
            status_code=409,
            detail="Plan hash mismatch — the plan changed since it was presented for approval. Re-fetch and retry."
        )

    # Safety-correction guard: plan_hash matching alone does NOT establish
    # that this plan is actually approvable. A plan sitting in
    # NEEDS_REVIEW (set when the Grounding Critic genuinely FAILED the
    # plan, could not complete its review at all, or when this plan was
    # produced via a fallback/legacy path that never ran the critic) must
    # never be approvable through this route -- doing so would let an
    # operator (or any caller with a valid plan_hash) silently convert an
    # un-reviewed or rejected plan into APPROVED, bypassing the fail-closed
    # semantics enforced when the plan was created. Only PENDING_APPROVAL
    # (a plan the critic actually PASSED) may be approved here.
    if plan_dict.get("status") != "PENDING_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan cannot be approved: current status is "
                f"'{plan_dict.get('status')}', not PENDING_APPROVAL. Plans in "
                "NEEDS_REVIEW have not passed an automated safety review and "
                "require investigation/revision before they can be approved."
            ),
        )

    now = datetime.now(timezone.utc).isoformat()
    has_knowledge_table = db.table_exists("incident_knowledge")
    with db.get_connection() as conn:
        # Claim the incident first. This write serializes with the conditional
        # re-investigation claim on both supported databases.
        claimed = conn.execute(
            "UPDATE incident SET status = %s, updated_at = %s WHERE incident_id = %s "
            "AND tenant_id = %s AND (workspace_id = %s OR workspace_id IS NULL) "
            "AND status IN ('PLAN_READY','AWAITING_APPROVAL')",
            (IncidentState.EXECUTING.value, now, incident_id, current_user.tenant_id, current_user.workspace_id),
        )
        if claimed.rowcount != 1:
            raise HTTPException(status_code=409, detail="Incident is no longer at a plan-review checkpoint.")
        cursor = conn.execute(
            "SELECT * FROM action_plan WHERE action_plan_id = %s AND incident_id = %s",
            (plan_id, incident_id),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plan not found for this incident")
        current_plan = dict(zip([col[0] for col in cursor.description], row))
        latest = conn.execute(
            "SELECT action_plan_id FROM action_plan WHERE incident_id = %s "
            "ORDER BY plan_version DESC, created_at DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
        if current_plan.get("status") != "PENDING_APPROVAL" or not latest or latest[0] != plan_id:
            raise HTTPException(status_code=409, detail="Plan is no longer available for approval.")
        cursor = conn.execute("SELECT * FROM action_step WHERE action_plan_id = %s ORDER BY sequence_no ASC", (plan_id,))
        current_steps = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        if not current_steps:
            raise HTTPException(status_code=409, detail="A plan without steps cannot be approved.")
        if compute_plan_hash(current_plan, current_steps) != req.plan_hash:
            raise HTTPException(status_code=409, detail="Plan changed since it was presented for approval.")
        if conn.execute(
            "SELECT 1 FROM reviewed_alert_document d JOIN incident_alert ia ON ia.alert_id = d.alert_id "
            "WHERE ia.incident_id = %s LIMIT 1", (incident_id,),
        ).fetchone():
            raise HTTPException(status_code=409, detail="Email-supplied evidence requires independent review.")
        if has_knowledge_table and conn.execute(
            "SELECT 1 FROM incident_knowledge WHERE incident_id=%s AND tenant_id=%s "
            "AND workspace_id=%s AND status='PENDING' LIMIT 1",
            (incident_id, current_user.tenant_id, current_user.workspace_id),
        ).fetchone():
            raise HTTPException(status_code=409, detail="Incident references are awaiting admin review.")
        updated = conn.execute(
            "UPDATE action_plan SET status='APPROVED' WHERE action_plan_id=%s AND incident_id=%s AND status='PENDING_APPROVAL'",
            (plan_id, incident_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=409, detail="Plan is no longer available for approval.")
        conn.execute("""
            INSERT INTO approval (approval_id, incident_id, action_plan_id, requested_at, expires_at, decision, decided_at, approver_id, plan_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (f"APP-{uuid.uuid4().hex[:8]}", incident_id, plan_id, now, now, req.decision, now, current_user.user_id, req.plan_hash))
        
        # Log audit
        conn.execute("""
            INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (f"AUD-{uuid.uuid4().hex[:8]}", incident_id, "HUMAN", current_user.user_id, "APPROVAL_RECORDED", "Action plan approved for execution", now))

    signaled = False
    if temporal_client:
        try:
            from app.domain.workflows.incident_workflow import IncidentLifecycleWorkflow
            handle = temporal_client.get_workflow_handle(f"incident-{incident_id}")
            await handle.signal(IncidentLifecycleWorkflow.approve_plan, {"decision": req.decision, "plan_id": plan_id})
            signaled = True
        except Exception as e:
            print(f"Temporal signal failed (workflow may be stale/absent), falling back to direct execution: {e}")

    # Fallback: if we couldn't reach a live Temporal workflow, execute directly so the incident
    # doesn't get stuck in APPROVED forever (closes the gap documented in the architecture doc §8.3).
    if not signaled and req.decision == "approve":
        from app.domain.orchestrator import IncidentOrchestrator
        orchestrator = IncidentOrchestrator()
        orchestrator.execute_plan(incident_id, plan_id)
        return {"status": "executed_directly", "reason": "temporal_unavailable_or_stale"}

    return {"status": "signaled_temporal" if signaled else "success"}

@app.post("/api/v2/incidents/{incident_id}/plans/{plan_id}/execute")
async def execute_plan(incident_id: str, plan_id: str, current_user: User = Depends(require_role("commander"))):
    """
    Direct execution path (also used as the automatic fallback inside
    /approve when no live Temporal workflow can be signaled). Role
    authorization alone (require_role("commander")) is NOT sufficient to
    permit execution -- this route previously had no status check at all,
    meaning a commander could execute a plan sitting in NEEDS_REVIEW
    (safety FAILED/INCONCLUSIVE/fallback/legacy origin), PENDING_APPROVAL
    (never actually approved), SUPERSEDED, or any other non-APPROVED
    status. That implicit bypass is removed here: this route now performs
    an early, API-level check for a clean HTTP 409 response, and the
    authoritative enforcement lives in IncidentOrchestrator.execute_plan()
    itself (the lowest shared execution service), so no caller -- this
    route, the /approve fallback, or any future caller -- can execute a
    plan that is not APPROVED.

    No new emergency-override mechanism is introduced. If a genuine
    emergency bypass of automated safety review is ever required, it must
    be designed separately with an explicit override action, mandatory
    operator justification, a privileged role, a prominent audit event,
    and explicit acknowledgement that automated safety approval is being
    bypassed -- none of which exists today, so no such bypass is permitted
    through this route.
    """
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    with db.get_connection() as conn:
        # Bound to BOTH action_plan_id AND incident_id (from the URL path)
        # so a plan belonging to a DIFFERENT incident cannot satisfy this
        # pre-check just because its plan_id was supplied alongside the
        # wrong incident_id in the route.
        cursor = conn.execute(
            "SELECT status FROM action_plan WHERE action_plan_id = %s AND incident_id = %s",
            (plan_id, incident_id),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found for this incident")
    if row[0] != "APPROVED":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan cannot be executed: current status is '{row[0]}', not "
                "APPROVED. Plans in NEEDS_REVIEW have not passed an automated "
                "safety review and must be investigated/revised and explicitly "
                "approved before execution."
            ),
        )

    from app.domain.orchestrator import IncidentOrchestrator
    orchestrator = IncidentOrchestrator()
    orchestrator.execute_plan(incident_id, plan_id)
    return {"status": "EXECUTING"}


# ---------------------------------------------------------------------------
# Admin: Capability catalog + policy administration (spec §17.3/§17.4).
# Lets an admin see exactly what real capabilities are registered, what
# their EFFECTIVE (post-override) policy is, and force a live reload of
# config/capability_policy.yaml without a process restart -- all admin-only.
# ---------------------------------------------------------------------------

@app.get("/api/v2/admin/capabilities")
async def list_capabilities(current_user: User = Depends(require_role("admin"))):
    from app.capabilities import registry, policy
    from app.capabilities.models import CompiledAction, RiskLevel, AutonomyMode

    results = []
    for definition in registry.list_capabilities():
        # Build a throwaway CompiledAction just to run it through the same
        # effective-policy resolution the real execution engine uses, so
        # what the admin sees here is GUARANTEED to match runtime behavior
        # (no separate/divergent "display" logic).
        fake_action = CompiledAction(
            action_id="ADMIN-PREVIEW",
            sequence=1,
            capability_id=definition.capability_id,
            capability_version=definition.version,
            intent_type="ADMIN_PREVIEW",
            target_resource_type="N/A",
            target_resource_id="N/A",
            arguments={},
            risk_level=definition.risk_level,
            autonomy_mode=definition.autonomy_mode,
            supports_dry_run=definition.supports_dry_run,
            idempotency_key="admin-preview",
        )
        effective_risk, effective_autonomy = policy._effective_risk_and_autonomy(fake_action)
        results.append({
            "capability_id": definition.capability_id,
            "version": definition.version,
            "kind": definition.kind.value,
            "description": definition.description,
            "owner": definition.owner,
            "default_risk_level": definition.risk_level.value,
            "default_autonomy_mode": definition.autonomy_mode.value,
            "effective_risk_level": effective_risk.value,
            "effective_autonomy_mode": effective_autonomy.value,
            "overridden": (effective_risk != definition.risk_level) or (effective_autonomy != definition.autonomy_mode),
            "supports_dry_run": definition.supports_dry_run,
            "required_args": definition.required_args,
        })
    return results


@app.post("/api/v2/admin/capabilities/reload-policy")
async def reload_capability_policy(current_user: User = Depends(require_role("admin"))):
    from app.capabilities import policy
    policy.reload_policy_config()
    return {"status": "reloaded", "config_path": str(policy._CONFIG_PATH)}


@app.get("/api/v2/auth/me")
def auth_me(current_user: User = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
        "roles": current_user.roles,
        "tenant_id": current_user.tenant_id,
        "workspace_id": current_user.workspace_id,
    }


# ---------------------------------------------------------------------------
# Commander Live / Commander Pulse.
# Commander Pulse projects the existing audit_event stream into meaningful
# status updates; Commander Live is the persistent ASK/GUIDE/ADD_FACT
# conversation attached to every incident. Every endpoint below is scoped
# to the caller's tenant via _require_incident_in_tenant /
# CommanderConversation's own equivalent check.
# ---------------------------------------------------------------------------

def _require_incident_in_tenant(db, incident_id: str, tenant_id: str, workspace_id: str | None = None) -> None:
    """
    Verifies `incident_id` exists AND belongs to `tenant_id`, raising 404 if
    not (never 403 -- "doesn't exist" and "wrong tenant" are intentionally
    indistinguishable to the caller, so cross-tenant existence is never
    leaked).
    """
    with db.get_connection() as conn:
        if workspace_id:
            cursor = conn.execute(
                "SELECT 1 FROM incident WHERE incident_id = %s AND tenant_id = %s "
                "AND (workspace_id = %s OR workspace_id IS NULL)",
                (incident_id, tenant_id, workspace_id),
            )
        else:
            cursor = conn.execute(
                "SELECT 1 FROM incident WHERE incident_id = %s AND tenant_id = %s",
                (incident_id, tenant_id),
            )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Incident not found")


@app.get("/api/v2/incidents/{incident_id}/commander/status")
def get_commander_pulse(incident_id: str, current_user: User = Depends(get_current_user)):
    from app.domain.commander.status_projector import StatusProjector
    db = get_database()
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id, current_user.workspace_id)
    return StatusProjector(db).get_pulse(incident_id)


@app.get("/api/v2/incidents/{incident_id}/commander/messages")
def get_commander_messages(incident_id: str, current_user: User = Depends(get_current_user)):
    from app.domain.commander.conversation import CommanderConversation, IncidentNotFoundError
    conv = CommanderConversation()
    try:
        return conv.get_messages(incident_id, current_user.tenant_id)
    except IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")


class CommanderAskRequest(BaseModel):
    question: str


@app.post("/api/v2/incidents/{incident_id}/commander/ask")
async def commander_ask(incident_id: str, req: CommanderAskRequest, current_user: User = Depends(get_current_user)):
    from app.domain.commander.conversation import CommanderConversation, IncidentNotFoundError
    conv = CommanderConversation()
    try:
        return await conv.ask(incident_id, current_user.tenant_id, req.question, current_user.user_id)
    except IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")


class CommanderGuidanceRequest(BaseModel):
    text: str
    guidance_type: Optional[str] = "INVESTIGATION_DIRECTION"


@app.post("/api/v2/incidents/{incident_id}/commander/guidance")
async def commander_guidance(incident_id: str, req: CommanderGuidanceRequest, current_user: User = Depends(require_role("operator"))):
    from app.domain.commander.conversation import CommanderConversation, IncidentNotFoundError
    conv = CommanderConversation()
    try:
        return await conv.guide(
            incident_id, current_user.tenant_id, req.text, current_user.user_id,
            guidance_type=req.guidance_type or "INVESTIGATION_DIRECTION", is_fact=False,
        )
    except IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")


class CommanderFactRequest(BaseModel):
    text: str


@app.post("/api/v2/incidents/{incident_id}/commander/facts")
async def commander_add_fact(incident_id: str, req: CommanderFactRequest, current_user: User = Depends(require_role("operator"))):
    from app.domain.commander.conversation import CommanderConversation, IncidentNotFoundError
    conv = CommanderConversation()
    try:
        return await conv.guide(
            incident_id, current_user.tenant_id, req.text, current_user.user_id,
            guidance_type="MANUAL_EVIDENCE_REFERENCE", is_fact=True,
        )
    except IncidentNotFoundError:
        raise HTTPException(status_code=404, detail="Incident not found")

@app.get("/api/v2/incidents/{incident_id}/events/stream")
async def stream_events(
    incident_id: str,
    current_user: User = Depends(get_current_user),
):
    async def event_generator():
        yield ": ping\n\n"
        db = get_database()
        last_ts = ""
        # Elapsed-time heartbeat timer: in addition to polling the audit table
        # roughly every second, emit an SSE comment heartbeat (": ping\n\n")
        # approximately every 15 seconds so the connection stays alive through
        # proxies/load-balancers even while no audit events are being
        # generated. This is a plain SSE comment, never a fake AgentEvent.
        loop = asyncio.get_event_loop()
        last_heartbeat = loop.time()
        HEARTBEAT_INTERVAL_SECONDS = 15
        while True:
            with db.get_connection() as conn:
                try:
                    cursor = conn.execute(
                        "SELECT audit_event_id, created_at, actor_id, event_type, event_summary FROM audit_event WHERE incident_id = %s AND created_at > %s ORDER BY created_at ASC",
                        (incident_id, last_ts)
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        event_data = {
                            "id": row[0],
                            "timestamp": row[1],
                            "source": row[2],
                            "event_type": row[3],
                            "message": row[4]
                        }
                        yield f"data: {json.dumps(event_data)}\n\n"
                        last_ts = row[1]
                except Exception as e:
                    print(f"SSE Error: {e}")

            now = loop.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                yield ": ping\n\n"
                last_heartbeat = now

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
