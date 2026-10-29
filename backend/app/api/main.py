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

from app.store.postgres_database import PostgresDatabase
from app.domain.enums import IncidentState
from app.domain.orchestrator import IncidentOrchestrator
from app.domain.correlator import CorrelatorEngine
from app.domain.models import Incident, Alert
from app.utils.telemetry import setup_telemetry
from app.api.auth import (
    get_current_user, require_role, User, get_mock_token,
    LoginRequest, verify_password, issue_token_for_user, IS_DEV_ENV,
)

from temporalio.client import Client
from app.domain.workflows.incident_workflow import IncidentLifecycleWorkflow

app = FastAPI(title="NemoGuard - Pipeline Incident Commander", version="2.0.0")
setup_telemetry("nemoguard_api")

temporal_client = None

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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
        "roles": roles,
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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    try:
        # Initialize the base schema only for a brand-new database.
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'incident'"
            )
            schema_exists = cursor.fetchone() is not None

        if not schema_exists:
            print("Initializing database schema...")
            db.init_schema()
        else:
            # Existing deployments still need any newly-added migrations.
            print("Applying pending database migrations...")
            db.apply_pending_migrations()

    except Exception as e:
        print(f"Failed to initialize database: {e}")

    try:
        temporal_url = os.getenv("TEMPORAL_URL", "localhost:7233")
        temporal_client = await Client.connect(temporal_url)
        print(f"Connected to Temporal server at {temporal_url}")
    except Exception as e:
        print(f"Failed to connect to Temporal: {e}")

# --- 12.1 Overview endpoints ---

@app.get("/api/v2/status")
def get_status():
    return {
        "environment": "development",
        "inference_provider": "nvidia_nim",
        "orchestrator": "healthy",
        "policy_engine": "active",
        "database": "healthy",
        "last_checked_at": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/v2/overview")
def get_overview():
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    try:
        with db.get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM incident WHERE status != 'RESOLVED'")
            open_incidents = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM incident WHERE status != 'RESOLVED' AND severity = 'SEV-1'")
            critical = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM incident WHERE status != 'RESOLVED' AND severity = 'SEV-2'")
            high = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM alert WHERE status = 'acknowledged'")
            correlated = cur.fetchone()[0]
            
            return {
                "open_incidents": open_incidents,
                "critical_incidents": critical,
                "high_incidents": high,
                "alerts_correlated_today": correlated,
                "alerts_suppressed_today": 0,
                "jobs_currently_affected": 8 if open_incidents > 0 else 0,
                "data_products_at_risk": 3 if open_incidents > 0 else 0
            }
    except Exception:
        return {}

@app.get("/api/v2/incidents")
def list_incidents(state: str = "open"):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
                "FROM incident WHERE status NOT IN ('RESOLVED', 'FAILED', 'CLOSED', 'CANCELLED') ORDER BY detected_at DESC"
            )
        else:
            cursor = conn.execute("SELECT incident_id, title, status, severity, detected_at, next_sla_breach_at, owner_team, primary_job_id, summary, resolved_at FROM incident ORDER BY detected_at DESC")
            
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

# --- 12.2 Incident detail endpoints ---

@app.get("/api/v2/incidents/{incident_id}")
def get_incident(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM incident WHERE incident_id = %s", (incident_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found")
        cols = [col[0] for col in cursor.description]
        return dict(zip(cols, row))

@app.get("/api/v2/incidents/{incident_id}/summary")
def get_incident_summary(incident_id: str):
    # For now, just return the incident details
    return get_incident(incident_id)

@app.get("/api/v2/incidents/{incident_id}/hypotheses")
def get_hypotheses(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM hypothesis WHERE incident_id = %s ORDER BY confidence DESC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        for r in rows:
            r['supporting_evidence_ids'] = json.loads(r['supporting_evidence_json'])
            r['contradicting_evidence_ids'] = json.loads(r['contradicting_evidence_json'])
        return rows

@app.get("/api/v2/incidents/{incident_id}/evidence")
def get_evidence(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM evidence WHERE incident_id = %s ORDER BY collected_at ASC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/incidents/{incident_id}/impact")
def get_impact(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
def get_plans(incident_id: str):
    from app.domain.plan_hash import compute_plan_hash
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
def get_events(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM audit_event WHERE incident_id = %s ORDER BY created_at ASC", (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/incidents/{incident_id}/alerts")
def get_incident_alerts(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT a.* FROM alert a
            JOIN incident_alert ia ON a.alert_id = ia.alert_id
            WHERE ia.incident_id = %s
        """, (incident_id,))
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

@app.get("/api/v2/alerts")
def get_all_alerts():
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    with db.get_connection() as conn:
        cursor = conn.execute("SELECT * FROM alert ORDER BY opened_ts DESC")
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

# --- 12.3 Context Endpoints (for NemoClaw Agents) ---

@app.get("/api/v2/context/alerts/{incident_id}")
def get_incident_alerts_context(incident_id: str):
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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

@app.post("/api/v2/ingest/webhook")
async def ingest_webhook(payload: dict):
    """
    Generic webhook endpoint for Datadog, PagerDuty, or Email-to-Webhook parsing.
    Passes the payload to the WatcherAgent to determine if it's a valid alert.
    """
    orchestrator = IncidentOrchestrator()
    result = await orchestrator.process_webhook(payload)
    
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
        
    if result.get("status") == "ingested_and_incident_created":
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
    from app.domain.triage_scheduler import schedule_triage
    result = await schedule_triage(temporal_client, incident_id)

    if result["status"] == "INCIDENT_NOT_FOUND":
        raise HTTPException(status_code=404, detail="Incident not found")
    if result["status"] == "TRIAGE_ALREADY_ACTIVE":
        raise HTTPException(status_code=409, detail="Triage already in progress for this incident")
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
def agent_logs(incident_id: str):
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
def submit_feedback(incident_id: str, req: FeedbackRequest, current_user: User = Depends(get_current_user)):
    orchestrator = IncidentOrchestrator()
    res = orchestrator.triage_feedback(incident_id, req.feedback)
    if "error" in res:
        raise HTTPException(status_code=500, detail=res["error"])
    return res

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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
    with db.get_connection() as conn:
        conn.execute("""
            INSERT INTO approval (approval_id, incident_id, action_plan_id, requested_at, expires_at, decision, decided_at, approver_id, plan_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (f"APP-{uuid.uuid4().hex[:8]}", incident_id, plan_id, now, now, req.decision, now, current_user.user_id, req.plan_hash))
        
        conn.execute("UPDATE action_plan SET status = 'APPROVED' WHERE action_plan_id = %s", (plan_id,))
        
        # Log audit
        conn.execute("""
            INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (f"AUD-{uuid.uuid4().hex[:8]}", incident_id, "HUMAN", current_user.user_id, "APPROVAL_RECORDED", "Action plan approved for execution", now))

    signaled = False
    if temporal_client:
        try:
            handle = temporal_client.get_workflow_handle(f"incident-{incident_id}")
            await handle.signal(IncidentLifecycleWorkflow.approve_plan, {"decision": req.decision, "plan_id": plan_id})
            signaled = True
        except Exception as e:
            print(f"Temporal signal failed (workflow may be stale/absent), falling back to direct execution: {e}")

    # Fallback: if we couldn't reach a live Temporal workflow, execute directly so the incident
    # doesn't get stuck in APPROVED forever (closes the gap documented in the architecture doc §8.3).
    if not signaled and req.decision == "approve":
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
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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

def _require_incident_in_tenant(db: PostgresDatabase, incident_id: str, tenant_id: str) -> None:
    """
    Verifies `incident_id` exists AND belongs to `tenant_id`, raising 404 if
    not (never 403 -- "doesn't exist" and "wrong tenant" are intentionally
    indistinguishable to the caller, so cross-tenant existence is never
    leaked).
    """
    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM incident WHERE incident_id = %s AND tenant_id = %s",
            (incident_id, tenant_id),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Incident not found")


@app.get("/api/v2/incidents/{incident_id}/commander/status")
def get_commander_pulse(incident_id: str, current_user: User = Depends(get_current_user)):
    from app.domain.commander.status_projector import StatusProjector
    db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
    _require_incident_in_tenant(db, incident_id, current_user.tenant_id)
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
        db = PostgresDatabase(os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"))
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
