from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain.operations_intelligence import build_operations_snapshot
from app.store.sqlite_database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(str(tmp_path / "nemoguard-ops.db"))
    db.init_schema()
    return db


def test_snapshot_derives_attention_and_gaps_without_invented_metrics(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at,tenant_id,workspace_id,next_sla_breach_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "INC-DEMO", "Reservation feed late", "INVESTIGATING", "SEV-2",
                now.isoformat(), now.isoformat(), now.isoformat(),
                "default_tenant", "default_workspace", (now + timedelta(minutes=7)).isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO alert (alert_id,opened_ts,severity,alert_type,source_system,message,status,tenant_id,workspace_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("ALT-DEMO", now.isoformat(), "high", "JOB_FAILED", "Outlook", "late", "open", "default_tenant", "default_workspace"),
        )
        conn.execute(
            "INSERT INTO audit_event (audit_event_id,incident_id,actor_type,actor_id,event_type,event_summary,created_at,tenant_id,workspace_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("AUD-DEMO", "INC-DEMO", "SYSTEM", "RCA Agent", "HYPOTHESIS_CREATED", "RCA created", (now + timedelta(minutes=2)).isoformat(), "default_tenant", "default_workspace"),
        )

    snapshot = build_operations_snapshot(
        db,
        "default_tenant",
        "default_workspace",
        {
            "database": "healthy",
            "database_backend": "sqlite",
            "temporal": "disabled",
            "policy_engine": "configured",
            "inference_status": "configured",
            "last_checked_at": now.isoformat(),
        },
        now=now,
    )

    assert snapshot["summary"]["open_incidents"] == 1
    assert snapshot["summary"]["high_priority_alerts"] == 1
    assert snapshot["summary"]["median_triage_seconds"] == 120
    assert snapshot["needs_attention"][0]["kind"] == "sla_risk"
    assert snapshot["needs_attention"][0]["score"] == 98
    assert snapshot["coverage"]["score"] == 0
    assert snapshot["coverage"]["needs"][0]["key"] == "independent_evidence"


def test_snapshot_counts_only_verified_recovery(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)
    with db.get_connection() as conn:
        for incident_id, verification in (("INC-PASS", "PASSED"), ("INC-FAIL", "FAILED")):
            conn.execute(
                "INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at,resolved_at,tenant_id,workspace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (incident_id, incident_id, "RESOLVED", "SEV-3", now.isoformat(), now.isoformat(), now.isoformat(), now.isoformat(), "default_tenant", "default_workspace"),
            )
            conn.execute(
                "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status,tenant_id,workspace_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (f"RUN-{incident_id}", incident_id, "SYSTEM", "test", "COMPLETED", "default_tenant", "default_workspace"),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,overall_risk,rationale,expected_outcome,rollback_summary,created_at,tenant_id,workspace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (f"PLAN-{incident_id}", incident_id, f"RUN-{incident_id}", 1, "EXECUTED", "LOW", "r", "o", "rb", now.isoformat(), "default_tenant", "default_workspace"),
            )
            conn.execute(
                "INSERT INTO verification_result (verification_id,incident_id,action_plan_id,check_name,status,expected_json,actual_json,evidence_ids_json,checked_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (f"VRF-{incident_id}", incident_id, f"PLAN-{incident_id}", "check", verification, "{}", "{}", "[]", now.isoformat()),
            )

    snapshot = build_operations_snapshot(
        db,
        "default_tenant",
        "default_workspace",
        {"database": "healthy", "database_backend": "sqlite", "temporal": "connected", "policy_engine": "configured", "inference_status": "configured"},
        now=now,
    )
    assert snapshot["summary"]["verified_recoveries_30d"] == 1
