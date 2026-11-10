"""Bounded, read-only context for workspace chat."""
import json

LIMIT = 20


def build_context(db, tenant_id, workspace_id, can_view_inbox):
    """Return scoped aggregates and recent samples, never email bodies."""
    with db.get_connection() as conn:
        incident_count = conn.execute(
            "SELECT COUNT(*) FROM incident WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        ).fetchone()[0]
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alert WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        ).fetchone()[0]
        incident_statuses = conn.execute(
            "SELECT status,COUNT(*) FROM incident WHERE tenant_id=%s AND workspace_id=%s GROUP BY status",
            (tenant_id, workspace_id),
        ).fetchall()
        alert_severities = conn.execute(
            "SELECT severity,COUNT(*) FROM alert WHERE tenant_id=%s AND workspace_id=%s GROUP BY severity",
            (tenant_id, workspace_id),
        ).fetchall()
        incidents = conn.execute(
            "SELECT incident_id,title,status,severity FROM incident "
            "WHERE tenant_id=%s AND workspace_id=%s ORDER BY updated_at DESC LIMIT %s",
            (tenant_id, workspace_id, LIMIT),
        ).fetchall()
        alerts = conn.execute(
            "SELECT a.alert_id,a.alert_type,a.severity,a.status,a.opened_ts,i.incident_id "
            "FROM alert a LEFT JOIN incident_alert ia ON ia.alert_id=a.alert_id "
            "LEFT JOIN incident i ON i.incident_id=ia.incident_id "
            "AND i.tenant_id=a.tenant_id AND i.workspace_id=a.workspace_id "
            "WHERE a.tenant_id=%s AND a.workspace_id=%s "
            "ORDER BY a.opened_ts DESC LIMIT %s",
            (tenant_id, workspace_id, LIMIT + 1),
        ).fetchall()
        alerts_truncated = len(alerts) > LIMIT
        alerts = alerts[:LIMIT]

        inbox = {"availability": "not authorized"}
        if can_view_inbox:
            if not db.table_exists("outlook_inbox"):
                inbox = {"availability": "not configured"}
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM outlook_inbox WHERE tenant_id=%s AND workspace_id=%s",
                    (tenant_id, workspace_id),
                ).fetchone()[0]
                rows = conn.execute(
                    "SELECT state,received_at,result_json,subject FROM outlook_inbox "
                    "WHERE tenant_id=%s AND workspace_id=%s ORDER BY received_at DESC LIMIT %s",
                    (tenant_id, workspace_id, LIMIT),
                ).fetchall()
                outcomes = []
                for state, received_at, raw, subject in rows:
                    try:
                        result = json.loads(raw or "{}")
                    except (ValueError, TypeError):
                        result = {}
                    if not isinstance(result, dict):
                        result = {}
                    outcomes.append({
                        "subject": str(subject or "")[:200],
                        "state": str(state or "")[:64],
                        "received_at": str(received_at or "")[:64],
                        "processing_status": str(result.get("status") or "")[:100],
                        "reported_alert_id": str(result.get("alert_id") or "")[:128],
                        "reported_incident_id": str(result.get("incident_id") or "")[:128],
                        "reasoning": str(result.get("reasoning") or "")[:500],
                    })
                inbox = {"availability": "available", "total": total,
                         "sample_truncated": total > LIMIT, "recent_outcomes": outcomes}

    return {
        "incident_total": incident_count,
        "incident_status_counts": {str(row[0] or "unknown"): row[1] for row in incident_statuses},
        "recent_incidents_truncated": incident_count > LIMIT,
        "recent_incidents": [
            {"incident_id": str(r[0])[:128], "title": str(r[1] or "")[:200],
             "status": str(r[2] or "")[:64], "severity": str(r[3] or "")[:64]}
            for r in incidents
        ],
        "alert_total": alert_count,
        "alert_severity_counts": {str(row[0] or "unknown"): row[1] for row in alert_severities},
        "recent_alerts_may_be_truncated": alerts_truncated,
        "recent_alerts": [
            {"alert_id": str(r[0])[:128], "type": str(r[1] or "")[:100],
             "severity": str(r[2] or "")[:64], "status": str(r[3] or "")[:64],
             "opened_at": str(r[4] or "")[:64], "linked_incident_id": r[5]}
            for r in alerts
        ],
        "outlook_inbox": inbox,
        "limitations": (
            "Samples are limited; alert rows can repeat when linked to multiple incidents. "
            "Email body fields and external Outlook mailbox data are not queried. "
            "Inbox subjects and model-generated, unverified reasoning can contain email-derived information."
        ),
    }
