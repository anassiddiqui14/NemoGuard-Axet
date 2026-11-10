"""Workspace-scoped model configuration for new agent operations."""
import re
import uuid
from datetime import datetime, timezone

OPERATIONS = {
    "watcher": "Watcher classification",
    "rca": "Root-cause analysis",
    "dependency": "Dependency analysis",
    "runbook": "Runbook planning",
    "critic": "Grounding critic",
    "commander": "Commander",
    "feedback": "Plan feedback revision",
}
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def initialize(db):
    with db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_model_setting (
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                operation VARCHAR(32) NOT NULL,
                model_id VARCHAR(200) NOT NULL,
                updated_by VARCHAR(200) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                PRIMARY KEY (tenant_id, workspace_id, operation)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_model_lock (
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                PRIMARY KEY (tenant_id, workspace_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workspace_model_audit (
                audit_id VARCHAR(64) PRIMARY KEY,
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                operation VARCHAR(32) NOT NULL,
                old_model VARCHAR(200),
                new_model VARCHAR(200),
                actor VARCHAR(200) NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
        """)


def get_overrides(db, tenant_id, workspace_id):
    if not tenant_id or not workspace_id:
        return {}
    if not db.table_exists("workspace_model_setting"):
        return {}
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT operation,model_id FROM workspace_model_setting "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        ).fetchall()
    return {operation: model for operation, model in rows if operation in OPERATIONS}


def for_incident(db, incident_id):
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT tenant_id,workspace_id FROM incident WHERE incident_id=%s",
            (incident_id,),
        ).fetchone()
    return get_overrides(db, row[0], row[1]) if row else {}


def apply_override(agent, operation, overrides):
    """Apply a snapshot to a newly-created agent before its first call."""
    if operation in overrides:
        agent.model = overrides[operation]


def apply_investigation(investigator, overrides):
    for operation, attribute in (
        ("rca", "rca_agent"),
        ("dependency", "dep_agent"),
        ("runbook", "runbook_agent"),
        ("critic", "critic"),
    ):
        if operation in overrides:
            apply_override(getattr(investigator, attribute), operation, overrides)


def set_override(db, tenant_id, workspace_id, operation, model_id, actor):
    if not tenant_id or not workspace_id or not actor:
        raise ValueError("A tenant, workspace and administrator are required.")
    if operation not in OPERATIONS:
        raise ValueError("Unknown operation.")
    if model_id is not None:
        model_id = model_id.strip()
        if not MODEL_ID.fullmatch(model_id):
            raise ValueError("Enter a valid provider model ID.")
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO workspace_model_lock (tenant_id,workspace_id) VALUES (%s,%s) "
            "ON CONFLICT (tenant_id,workspace_id) DO NOTHING",
            (tenant_id, workspace_id),
        )
        conn.execute(
            "UPDATE workspace_model_lock SET workspace_id=workspace_id "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        )
        now = datetime.now(timezone.utc).isoformat()
        old = conn.execute(
            "SELECT model_id FROM workspace_model_setting WHERE tenant_id=%s "
            "AND workspace_id=%s AND operation=%s",
            (tenant_id, workspace_id, operation),
        ).fetchone()
        if model_id is None:
            conn.execute(
                "DELETE FROM workspace_model_setting WHERE tenant_id=%s "
                "AND workspace_id=%s AND operation=%s",
                (tenant_id, workspace_id, operation),
            )
        else:
            conn.execute(
                "INSERT INTO workspace_model_setting "
                "(tenant_id,workspace_id,operation,model_id,updated_by,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,operation) "
                "DO UPDATE SET model_id=excluded.model_id,updated_by=excluded.updated_by,"
                "updated_at=excluded.updated_at",
                (tenant_id, workspace_id, operation, model_id, actor, now),
            )
        conn.execute(
            "INSERT INTO workspace_model_audit "
            "(audit_id,tenant_id,workspace_id,operation,old_model,new_model,actor,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (uuid.uuid4().hex, tenant_id, workspace_id, operation,
             old[0] if old else None, model_id, actor, now),
        )
