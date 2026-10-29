-- schema_sqlite.sql
--
-- Phase 1 of the aXet Cloud SQLite migration.
--
-- This is the CURRENT-STATE SQLite schema for NemoGuard's live runtime
-- (backend/app/api/main.py + backend/app/domain/**/*.py), constructed by
-- folding together everything currently needed from:
--   backend/app/store/schema.sql
--   migrations/002_domain_model.sql
--   migrations/005_capability_gateway.sql (action_step/action_plan/
--     action_execution new columns)
--   migrations/006_evidence_authority.sql (evidence.authority column)
--   migrations/007_platform_users.sql (platform_user -- roles stored as
--     JSON TEXT here instead of Postgres TEXT[], see note below)
--   migrations/008_axet_okta_identity.sql (platform_user.okta_id)
--   migrations/009_commander_live.sql (Commander Live/Pulse/plan
--     versioning tables)
--
-- Explicitly NOT ported here (LocalStack-lab-only, never touched by the
-- live cloud runtime -- see Phase 1 report):
--   migrations/003_relax_incident_fks.sql's effect is already achieved
--     below by never adding the two FK constraints (primary_run_id/
--     primary_job_id) to `incident` in the first place.
--   migrations/004_glue_lab_target_table.sql (order_events /
--     order_events_run_manifest) -- only used by
--     backend/app/domain/tools/aws_observability_tools.py, which is
--     gated behind NEMOGUARD_LOCALSTACK_LAB=1 and connects via raw
--     psycopg2.connect() directly (Postgres/LocalStack-only feature,
--     profiled "lab" in docker-compose.yml -- not an aXet Cloud concern).
--
-- Type mapping used throughout (see Phase 1 assessment report):
--   VARCHAR/TEXT        -> TEXT
--   JSON/JSONB-ish *_json columns -> TEXT containing JSON (unchanged --
--                          the Postgres schema already stored these as
--                          VARCHAR/TEXT, never real JSONB)
--   TIMESTAMPTZ         -> TEXT (ISO-8601 UTC) -- matches the existing
--                          convention already used by EVERY timestamp
--                          column in the Postgres schema (e.g.
--                          incident.created_at), which are all plain
--                          VARCHAR/ISO8601 strings, not a native
--                          Postgres timestamp type, so no behavior
--                          change is needed here at all.
--   BOOLEAN             -> INTEGER (0/1) -- SQLite has no native boolean;
--                          existing code already writes 0/1 ints for the
--                          few boolean-ish columns (e.g.
--                          action_step.requires_approval), so this matches
--                          existing call-site behavior.
--   SERIAL              -> INTEGER PRIMARY KEY AUTOINCREMENT (not used
--                          here -- no live-runtime table uses SERIAL;
--                          only the skipped LocalStack-only
--                          order_events table did).
--   TEXT[]              -> TEXT containing a JSON array (platform_user.roles)

CREATE TABLE IF NOT EXISTS job (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    job_id VARCHAR PRIMARY KEY,
    job_name VARCHAR NOT NULL,
    platform VARCHAR NOT NULL,
    domain VARCHAR NOT NULL,
    stage VARCHAR NOT NULL,
    schedule VARCHAR NOT NULL,
    criticality INTEGER NOT NULL,
    default_duration_sec INTEGER NOT NULL,
    owner_team VARCHAR NOT NULL,
    retry_policy VARCHAR NOT NULL,
    active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dependency (
    edge_id VARCHAR PRIMARY KEY,
    parent_job_id VARCHAR NOT NULL REFERENCES job(job_id),
    child_job_id VARCHAR NOT NULL REFERENCES job(job_id),
    dependency_type VARCHAR NOT NULL,
    max_lag_min INTEGER NOT NULL,
    required BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS business_asset (
    asset_id VARCHAR PRIMARY KEY,
    asset_name VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    owner VARCHAR NOT NULL,
    sla_minutes INTEGER NOT NULL,
    criticality INTEGER NOT NULL,
    communication_template VARCHAR
);

CREATE TABLE IF NOT EXISTS asset_dependency (
    asset_id VARCHAR NOT NULL REFERENCES business_asset(asset_id),
    job_id VARCHAR NOT NULL REFERENCES job(job_id),
    PRIMARY KEY (asset_id, job_id)
);

CREATE TABLE IF NOT EXISTS execution (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    run_id VARCHAR PRIMARY KEY,
    job_id VARCHAR NOT NULL REFERENCES job(job_id),
    scheduled_ts VARCHAR NOT NULL,
    start_ts VARCHAR NOT NULL,
    end_ts VARCHAR,
    status VARCHAR NOT NULL CHECK(status IN ('queued','running','succeeded','failed','blocked','skipped')),
    attempt INTEGER NOT NULL DEFAULT 1,
    records_in INTEGER,
    records_out INTEGER,
    schema_version VARCHAR,
    incident_id VARCHAR
);

CREATE TABLE IF NOT EXISTS log_event (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    log_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL REFERENCES execution(run_id),
    timestamp VARCHAR NOT NULL,
    level VARCHAR NOT NULL CHECK(level IN ('DEBUG','INFO','WARN','ERROR')),
    component VARCHAR NOT NULL,
    error_code VARCHAR,
    message VARCHAR NOT NULL,
    attributes_json VARCHAR
);

CREATE TABLE IF NOT EXISTS alert (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    alert_id VARCHAR PRIMARY KEY,
    run_id VARCHAR,
    opened_ts VARCHAR NOT NULL,
    severity VARCHAR NOT NULL CHECK(severity IN ('info','warning','high','critical')),
    alert_type VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL DEFAULT 'Pipeline Monitor',
    message VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved'))
);

-- incident: deliberately WITHOUT FOREIGN KEY constraints on
-- primary_job_id/primary_run_id (see migrations/003_relax_incident_fks.sql's
-- rationale -- real-world alerts from external monitoring have no
-- matching execution/job row at all).
CREATE TABLE IF NOT EXISTS incident (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    incident_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    summary VARCHAR,
    status VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    primary_job_id VARCHAR,
    primary_run_id VARCHAR,
    owner_team VARCHAR,
    detected_at VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL,
    resolved_at VARCHAR,
    correlation_confidence REAL,
    rca_confidence REAL,
    next_sla_breach_at VARCHAR,
    actual_root_cause VARCHAR,
    resolution_summary VARCHAR,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS incident_alert (
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    alert_id VARCHAR NOT NULL REFERENCES alert(alert_id),
    relation_type VARCHAR NOT NULL DEFAULT 'CORRELATED',
    correlation_score REAL NOT NULL,
    correlation_reasons_json VARCHAR NOT NULL,
    added_at VARCHAR NOT NULL,
    PRIMARY KEY(incident_id, alert_id)
);

CREATE TABLE IF NOT EXISTS agent_run (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    agent_run_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    agent_name VARCHAR NOT NULL,
    parent_agent_run_id VARCHAR REFERENCES agent_run(agent_run_id),
    objective VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    model_provider VARCHAR,
    model_name VARCHAR,
    started_at VARCHAR,
    completed_at VARCHAR,
    token_input INTEGER,
    token_output INTEGER,
    latency_ms INTEGER,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS agent_step (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    agent_step_id VARCHAR PRIMARY KEY,
    agent_run_id VARCHAR NOT NULL REFERENCES agent_run(agent_run_id),
    sequence_no INTEGER NOT NULL,
    step_type VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    input_json VARCHAR,
    output_json VARCHAR,
    started_at VARCHAR NOT NULL,
    completed_at VARCHAR,
    UNIQUE(agent_run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS tool_call (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    tool_call_id VARCHAR PRIMARY KEY,
    agent_run_id VARCHAR NOT NULL REFERENCES agent_run(agent_run_id),
    agent_step_id VARCHAR REFERENCES agent_step(agent_step_id),
    tool_name VARCHAR NOT NULL,
    risk_level VARCHAR NOT NULL,
    arguments_json VARCHAR NOT NULL,
    result_json VARCHAR,
    status VARCHAR NOT NULL,
    started_at VARCHAR NOT NULL,
    completed_at VARCHAR,
    duration_ms INTEGER,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS evidence (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    evidence_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    tool_call_id VARCHAR REFERENCES tool_call(tool_call_id),
    evidence_type VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL,
    source_record_id VARCHAR,
    title VARCHAR NOT NULL,
    excerpt VARCHAR NOT NULL,
    occurred_at VARCHAR,
    collected_at VARCHAR NOT NULL,
    reliability REAL NOT NULL DEFAULT 1.0,
    metadata_json VARCHAR,
    -- migrations/006_evidence_authority.sql
    authority VARCHAR NOT NULL DEFAULT 'MEDIUM'
);

CREATE TABLE IF NOT EXISTS hypothesis (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    hypothesis_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    agent_run_id VARCHAR NOT NULL,
    rank_no INTEGER NOT NULL,
    statement VARCHAR NOT NULL,
    cause_type VARCHAR,
    confidence REAL NOT NULL,
    status VARCHAR NOT NULL,
    supporting_evidence_json VARCHAR NOT NULL,
    contradicting_evidence_json VARCHAR NOT NULL,
    missing_evidence_json VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS data_asset (
    asset_id VARCHAR PRIMARY KEY,
    asset_type VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    owner_team VARCHAR,
    criticality VARCHAR,
    freshness_sla_minutes INTEGER,
    business_process VARCHAR,
    estimated_user_count INTEGER,
    impact_band VARCHAR,
    metadata_json VARCHAR
);

CREATE TABLE IF NOT EXISTS incident_impact (
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    asset_id VARCHAR NOT NULL REFERENCES data_asset(asset_id),
    impact_type VARCHAR NOT NULL,
    impact_status VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    expected_breach_at VARCHAR,
    impact_score REAL NOT NULL,
    evidence_ids_json VARCHAR NOT NULL,
    PRIMARY KEY(incident_id, asset_id)
);

CREATE TABLE IF NOT EXISTS runbook (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    runbook_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    incident_type VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    owner_team VARCHAR NOT NULL,
    approval_policy VARCHAR NOT NULL,
    prerequisites_json VARCHAR NOT NULL,
    verification_json VARCHAR NOT NULL,
    rollback_json VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS runbook_step (
    runbook_id VARCHAR NOT NULL REFERENCES runbook(runbook_id),
    step_no INTEGER NOT NULL,
    title VARCHAR NOT NULL,
    instruction VARCHAR NOT NULL,
    tool_name VARCHAR,
    risk_level VARCHAR NOT NULL,
    requires_approval INTEGER NOT NULL,
    parameters_template_json VARCHAR,
    PRIMARY KEY(runbook_id, step_no)
);

-- action_plan: includes migrations/005_capability_gateway.sql's
-- compiled_plan_hash AND migrations/009_commander_live.sql's
-- parent_plan_id/feedback_reference, all folded into the CREATE TABLE
-- directly (no separate ALTER TABLE needed under SQLite).
CREATE TABLE IF NOT EXISTS action_plan (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    action_plan_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    agent_run_id VARCHAR NOT NULL,
    runbook_id VARCHAR REFERENCES runbook(runbook_id),
    plan_version INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    overall_risk VARCHAR NOT NULL,
    rationale VARCHAR NOT NULL,
    expected_outcome VARCHAR NOT NULL,
    rollback_summary VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    compiled_plan_hash VARCHAR,
    parent_plan_id VARCHAR,
    feedback_reference VARCHAR
);

-- action_step: includes migrations/005_capability_gateway.sql's
-- capability_id/capability_version folded in directly.
CREATE TABLE IF NOT EXISTS action_step (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    action_step_id VARCHAR PRIMARY KEY,
    action_plan_id VARCHAR NOT NULL REFERENCES action_plan(action_plan_id),
    sequence_no INTEGER NOT NULL,
    action_type VARCHAR NOT NULL,
    tool_name VARCHAR NOT NULL,
    risk_level VARCHAR NOT NULL,
    requires_approval INTEGER NOT NULL,
    parameters_json VARCHAR NOT NULL,
    preconditions_json VARCHAR NOT NULL,
    expected_postconditions_json VARCHAR NOT NULL,
    rollback_tool_name VARCHAR,
    rollback_parameters_json VARCHAR,
    status VARCHAR NOT NULL,
    capability_id VARCHAR,
    capability_version VARCHAR,
    UNIQUE(action_plan_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS approval (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    approval_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    action_plan_id VARCHAR NOT NULL REFERENCES action_plan(action_plan_id),
    requested_at VARCHAR NOT NULL,
    expires_at VARCHAR NOT NULL,
    decision VARCHAR NOT NULL,
    decided_at VARCHAR,
    approver_id VARCHAR,
    approver_role VARCHAR,
    comment VARCHAR,
    plan_hash VARCHAR NOT NULL
);

-- action_execution: includes migrations/005_capability_gateway.sql's
-- capability_id/verification_status/verification_details_json folded in
-- directly.
CREATE TABLE IF NOT EXISTS action_execution (
    action_execution_id VARCHAR PRIMARY KEY,
    action_step_id VARCHAR NOT NULL REFERENCES action_step(action_step_id),
    approval_id VARCHAR REFERENCES approval(approval_id),
    idempotency_key VARCHAR NOT NULL UNIQUE,
    status VARCHAR NOT NULL,
    started_at VARCHAR,
    completed_at VARCHAR,
    before_state_json VARCHAR,
    after_state_json VARCHAR,
    result_json VARCHAR,
    error_message VARCHAR,
    capability_id VARCHAR,
    verification_status VARCHAR,
    verification_details_json VARCHAR
);

CREATE TABLE IF NOT EXISTS verification_result (
    verification_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    action_plan_id VARCHAR NOT NULL REFERENCES action_plan(action_plan_id),
    check_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    expected_json VARCHAR NOT NULL,
    actual_json VARCHAR NOT NULL,
    evidence_ids_json VARCHAR NOT NULL,
    checked_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL REFERENCES incident(incident_id),
    submitted_at VARCHAR NOT NULL,
    submitted_by VARCHAR NOT NULL,
    actual_root_cause VARCHAR,
    recommendation_correct INTEGER,
    impact_correct INTEGER,
    runbook_helpful INTEGER,
    action_successful INTEGER,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS audit_event (
    tenant_id VARCHAR DEFAULT 'default_tenant',
    workspace_id VARCHAR,
    environment_id VARCHAR,
    audit_event_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR REFERENCES incident(incident_id),
    actor_type VARCHAR NOT NULL,
    actor_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    event_summary VARCHAR NOT NULL,
    details_json VARCHAR,
    created_at VARCHAR NOT NULL
);

-- migrations/007_platform_users.sql + 008_axet_okta_identity.sql.
-- `roles` is TEXT containing a JSON array (e.g. '["viewer"]') instead of
-- Postgres TEXT[] -- see backend/app/api/auth.py's serialize_roles()/
-- deserialize_roles() helpers, which are the ONLY place this JSON
-- encoding is handled; the rest of the app continues to receive/produce
-- a plain Python list[str].
CREATE TABLE IF NOT EXISTS platform_user (
    user_id         TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
    roles           TEXT NOT NULL DEFAULT '["viewer"]',
    tenant_id       TEXT NOT NULL DEFAULT 'default_tenant',
    workspace_id    TEXT NOT NULL DEFAULT 'default_workspace',
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_login_at   TEXT,
    okta_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_platform_user_email ON platform_user (email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_user_okta_id
    ON platform_user (okta_id)
    WHERE okta_id IS NOT NULL;

-- migrations/009_commander_live.sql -- Commander Live/Pulse + plan
-- versioning. The `source_audit_event_id` uniqueness guarantee (achieved
-- in Postgres via a DO $$ ... pg_constraint ... ALTER TABLE ADD
-- CONSTRAINT block, which has no SQLite equivalent at all) is instead
-- expressed directly as a UNIQUE INDEX here -- CREATE UNIQUE INDEX IF
-- NOT EXISTS gives the identical idempotent-uniqueness guarantee without
-- needing a named table CONSTRAINT.
CREATE TABLE IF NOT EXISTS commander_status_update (
    update_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    source_audit_event_id VARCHAR,
    update_type VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    headline VARCHAR NOT NULL,
    summary VARCHAR,
    severity VARCHAR,
    lifecycle_state VARCHAR,
    evidence_refs_json VARCHAR,
    plan_id VARCHAR,
    visibility VARCHAR NOT NULL DEFAULT 'OPERATOR',
    notify_external INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_commander_status_update_source_audit_event_id
    ON commander_status_update(source_audit_event_id);

CREATE INDEX IF NOT EXISTS idx_commander_status_incident
    ON commander_status_update(incident_id, created_at);

CREATE TABLE IF NOT EXISTS incident_message (
    message_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    intent VARCHAR,
    content VARCHAR NOT NULL,
    created_by VARCHAR,
    created_at VARCHAR NOT NULL,
    evidence_refs_json VARCHAR,
    guidance_id VARCHAR,
    guidance_revision INTEGER
);

CREATE INDEX IF NOT EXISTS idx_incident_message_incident
    ON incident_message(incident_id, created_at);

CREATE TABLE IF NOT EXISTS commander_guidance (
    guidance_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    created_by VARCHAR,
    source_channel VARCHAR NOT NULL DEFAULT 'UI',
    guidance_type VARCHAR NOT NULL,
    authority VARCHAR NOT NULL DEFAULT 'HUMAN_OBSERVATION',
    text VARCHAR NOT NULL,
    guidance_revision INTEGER NOT NULL,
    triggered_reinvestigation INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_commander_guidance_incident
    ON commander_guidance(incident_id, created_at);

CREATE TABLE IF NOT EXISTS incident_guidance_revision (
    incident_id VARCHAR PRIMARY KEY,
    current_revision INTEGER NOT NULL DEFAULT 0,
    updated_at VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_feedback (
    plan_feedback_id VARCHAR PRIMARY KEY,
    incident_id VARCHAR NOT NULL,
    rejected_plan_id VARCHAR NOT NULL,
    feedback_text VARCHAR NOT NULL,
    submitted_by VARCHAR NOT NULL,
    submitted_at VARCHAR NOT NULL,
    resulting_plan_id VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_plan_feedback_incident ON plan_feedback(incident_id);
CREATE INDEX IF NOT EXISTS idx_action_plan_parent ON action_plan(parent_plan_id);

-- General indexes carried over from schema.sql / 002_domain_model.sql.
CREATE INDEX IF NOT EXISTS idx_execution_job ON execution(job_id);
CREATE INDEX IF NOT EXISTS idx_execution_status ON execution(status);
CREATE INDEX IF NOT EXISTS idx_execution_scheduled ON execution(scheduled_ts);
CREATE INDEX IF NOT EXISTS idx_log_run ON log_event(run_id);
CREATE INDEX IF NOT EXISTS idx_log_level ON log_event(level);
CREATE INDEX IF NOT EXISTS idx_log_error_code ON log_event(error_code);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alert(status);
CREATE INDEX IF NOT EXISTS idx_alert_run ON alert(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_event(incident_id);
CREATE INDEX IF NOT EXISTS idx_alert_status_time ON alert(status, opened_ts);
CREATE INDEX IF NOT EXISTS idx_incident_status_updated ON incident(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_incident_alert_alert ON incident_alert(alert_id);
CREATE INDEX IF NOT EXISTS idx_log_event_run_time ON log_event(run_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_call_agent ON tool_call(agent_run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence(incident_id, evidence_type);
CREATE INDEX IF NOT EXISTS idx_evidence_authority ON evidence(authority);
CREATE INDEX IF NOT EXISTS idx_action_step_capability ON action_step(capability_id);
CREATE INDEX IF NOT EXISTS idx_action_execution_capability ON action_execution(capability_id);

-- Schema-versioning metadata for SQLite-specific future migrations.
-- Postgres migrations (migrations/*.sql) are never executed against
-- SQLite; any future SQLite-only schema change should bump
-- sqlite_schema_version and be applied by a small, dedicated SQLite
-- migration runner added to SqliteDatabase at that time.
CREATE TABLE IF NOT EXISTS nemoguard_schema_metadata (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

INSERT OR IGNORE INTO nemoguard_schema_metadata (key, value)
VALUES ('sqlite_schema_version', '1');
