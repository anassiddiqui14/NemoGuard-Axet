"""Deterministic operational-intelligence projections for NemoGuard V22.

This module intentionally does not call an LLM.  It turns data NemoGuard
already persists (incidents, alerts, evidence, plans, audit events,
verification results and Outlook bridge heartbeat) into an operator-facing
snapshot that answers four questions:

* What needs attention now?
* What happened during the current shift?
* What does NemoGuard know / not know about active incidents?
* Which parts of the platform are actually healthy, merely configured, or
  unknown?

Keeping this projection deterministic makes the leadership/demo surface both
fast and explainable, while leaving Commander free to add conversational
interpretation on top of the same facts later.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional
import json


_TERMINAL_STATES = {"RESOLVED", "FAILED", "CLOSED", "CANCELLED"}
_HIGH_SEVERITIES = {"CRITICAL", "HIGH", "SEV-1", "SEV-2", "SEV_1", "SEV_2"}
_AGENT_NAMES = ["Watcher", "RCA", "Dependency", "Runbook", "Safety", "Commander"]


def _dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _rows(conn, query: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    """Execute a query and return dictionaries. Missing optional tables are safe."""
    try:
        cursor = conn.execute(query, tuple(params))
        columns = [col[0] for col in (cursor.description or [])]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception:
        return []


def _one(conn, query: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = _rows(conn, query, params)
    return rows[0] if rows else None


def _scope_where(prefix: str = "") -> str:
    p = f"{prefix}." if prefix else ""
    return f"{p}tenant_id = %s AND ({p}workspace_id = %s OR {p}workspace_id IS NULL)"


def _severity_high(value: Any) -> bool:
    return str(value or "").upper().replace("_", "-") in {"CRITICAL", "HIGH", "SEV-1", "SEV-2"}


def _json_list(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _minutes_since(value: Any, now: datetime) -> Optional[int]:
    parsed = _dt(value)
    if not parsed:
        return None
    return max(0, int((now - parsed).total_seconds() // 60))


def _system_health(runtime_status: Dict[str, Any], bridge_row: Optional[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    database = runtime_status.get("database", "unknown")
    db_backend = runtime_status.get("database_backend", "unknown")
    temporal = runtime_status.get("temporal", "unknown")
    inference = runtime_status.get("inference_status", "unknown")
    policy = runtime_status.get("policy_engine", "unknown")

    items: List[Dict[str, Any]] = [
        {
            "key": "database",
            "label": "Operational database",
            "state": "healthy" if database == "healthy" else "critical" if database == "unhealthy" else "unknown",
            "detail": f"{db_backend} · live query {database}",
        },
        {
            "key": "workflow",
            "label": "Workflow orchestration",
            "state": "healthy" if temporal == "connected" else "warning" if temporal == "disabled" else "critical" if temporal == "disconnected" else "unknown",
            "detail": "Temporal connected" if temporal == "connected" else "Direct fallback configured" if temporal == "disabled" else "Temporal expected but unavailable" if temporal == "disconnected" else "Workflow state unknown",
        },
        {
            "key": "policy",
            "label": "Capability policy",
            "state": "unknown" if policy in {"configured", "active"} else "warning" if policy == "not_configured" else "unknown",
            "detail": "Configured; no active policy-engine health probe" if policy in {"configured", "active"} else "Policy configuration not confirmed",
        },
        {
            "key": "inference",
            "label": "aXet inference",
            "state": "unknown" if inference == "configured" else "warning" if inference == "not_configured" else "unknown",
            "detail": "Configured; no active inference health probe" if inference == "configured" else "Runtime inference configuration not confirmed",
        },
    ]

    if bridge_row:
        last_scan = _dt(bridge_row.get("observed_at"))
        age_minutes = int((now - last_scan).total_seconds() // 60) if last_scan else None
        if age_minutes is None:
            state, detail = "unknown", "No usable heartbeat timestamp"
        elif age_minutes <= 5:
            state = "warning" if (bridge_row.get("blocked_count") or 0) > 0 else "healthy"
            detail = f"Last scan {age_minutes}m ago · {bridge_row.get('pending_count', 0)} pending · {bridge_row.get('blocked_count', 0)} blocked"
        else:
            state, detail = "warning", f"Last heartbeat {age_minutes}m ago · alert intake may be stale"
    else:
        state, detail = "unknown", "No Outlook bridge heartbeat recorded"

    items.append({"key": "outlook", "label": "Alert mailbox", "state": state, "detail": detail})
    return items


def build_operations_snapshot(
    db,
    tenant_id: str,
    workspace_id: str,
    runtime_status: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    with db.get_connection() as conn:
        incidents = _rows(
            conn,
            "SELECT incident_id, title, summary, status, severity, primary_job_id, primary_run_id, "
            "owner_team, detected_at, created_at, updated_at, resolved_at, correlation_confidence, "
            "rca_confidence, next_sla_breach_at FROM incident WHERE " + _scope_where() + " ORDER BY detected_at DESC",
            (tenant_id, workspace_id),
        )
        alerts = _rows(
            conn,
            "SELECT alert_id, opened_ts, severity, alert_type, source_system, message, status "
            "FROM alert WHERE " + _scope_where() + " ORDER BY opened_ts DESC",
            (tenant_id, workspace_id),
        )
        plans = _rows(
            conn,
            "SELECT action_plan_id, incident_id, runbook_id, plan_version, status, overall_risk, created_at "
            "FROM action_plan WHERE " + _scope_where() + " ORDER BY created_at DESC",
            (tenant_id, workspace_id),
        )
        evidence = _rows(
            conn,
            "SELECT evidence_id, incident_id, evidence_type, source_system, reliability, authority, collected_at "
            "FROM evidence WHERE " + _scope_where() + " ORDER BY collected_at DESC",
            (tenant_id, workspace_id),
        )
        audit = _rows(
            conn,
            "SELECT audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at "
            "FROM audit_event WHERE " + _scope_where() + " ORDER BY created_at DESC LIMIT 500",
            (tenant_id, workspace_id),
        )
        hypotheses = _rows(
            conn,
            "SELECT incident_id, confidence, status, missing_evidence_json, created_at FROM hypothesis "
            "WHERE " + _scope_where() + " ORDER BY created_at DESC",
            (tenant_id, workspace_id),
        )
        # These tables are scoped through their incident relationship rather than tenant columns.
        scoped_incident_ids = {row["incident_id"] for row in incidents}
        impacts = _rows(conn, "SELECT incident_id, asset_id, impact_status, impact_score FROM incident_impact")
        verifications = _rows(conn, "SELECT incident_id, status, checked_at FROM verification_result ORDER BY checked_at DESC")
        bridge_row = _one(
            conn,
            "SELECT observed_at, pending_count, blocked_count, baseline_count FROM outlook_bridge_heartbeat "
            "WHERE tenant_id=%s AND workspace_id=%s",
            (tenant_id, workspace_id),
        )

    impacts = [row for row in impacts if row.get("incident_id") in scoped_incident_ids]
    verifications = [row for row in verifications if row.get("incident_id") in scoped_incident_ids]

    open_incidents = [row for row in incidents if str(row.get("status") or "").upper() not in _TERMINAL_STATES]
    high_open = [row for row in open_incidents if _severity_high(row.get("severity"))]
    high_open_alerts = [row for row in alerts if str(row.get("status") or "").lower() == "open" and _severity_high(row.get("severity"))]

    plans_by_incident: Dict[str, Dict[str, Any]] = {}
    for plan in plans:
        plans_by_incident.setdefault(plan.get("incident_id"), plan)

    evidence_by_incident: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        evidence_by_incident[item.get("incident_id")].append(item)

    impacts_by_incident: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in impacts:
        impacts_by_incident[item.get("incident_id")].append(item)

    hypothesis_by_incident: Dict[str, Dict[str, Any]] = {}
    for item in hypotheses:
        hypothesis_by_incident.setdefault(item.get("incident_id"), item)

    audit_by_incident: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in audit:
        audit_by_incident[item.get("incident_id")].append(item)

    # --- real KPIs -------------------------------------------------------
    verification_cutoff = now - timedelta(days=30)
    verified_incidents = {
        row.get("incident_id")
        for row in verifications
        if str(row.get("status") or "").upper() == "PASSED" and (_dt(row.get("checked_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= verification_cutoff
    }

    triage_durations: List[float] = []
    for incident in incidents:
        start = _dt(incident.get("detected_at") or incident.get("created_at"))
        if not start:
            continue
        completion = None
        for event in reversed(audit_by_incident.get(incident.get("incident_id"), [])):
            if event.get("event_type") in {"PLAN_CREATED", "HYPOTHESIS_CREATED", "SAFETY_VALIDATION_PASSED", "SAFETY_VALIDATION_INCONCLUSIVE"}:
                completion = _dt(event.get("created_at"))
                if completion:
                    break
        if completion and completion >= start:
            triage_durations.append((completion - start).total_seconds())
    median_triage = int(median(triage_durations)) if triage_durations else None

    # --- Needs Attention ------------------------------------------------
    attention_by_incident: Dict[str, Dict[str, Any]] = {}
    for incident in open_incidents:
        incident_id = incident.get("incident_id")
        status = str(incident.get("status") or "").upper()
        severity = str(incident.get("severity") or "").upper()
        reasons: List[str] = []
        score = 45
        kind = "open_incident"
        action = "Review incident"

        if _severity_high(severity):
            score = max(score, 92 if severity in {"CRITICAL", "SEV-1", "SEV_1"} else 82)
            reasons.append(f"{severity.replace('_', '-')} severity")
        if status in {"PLAN_READY", "AWAITING_APPROVAL"}:
            score = max(score, 88)
            kind, action = "approval_required", "Review recovery plan"
            reasons.append("Recovery plan requires operator decision")
        elif status == "NEEDS_REVIEW":
            score = max(score, 90)
            kind, action = "safety_review", "Review safety concern"
            reasons.append("Safety/grounding review is incomplete")
        elif status == "MANUAL_ACTION_REQUIRED":
            score = max(score, 94)
            kind, action = "manual_action", "Perform or revise manual recovery"
            reasons.append("Automation was blocked; manual action is required")

        breach = _dt(incident.get("next_sla_breach_at"))
        if breach:
            mins = int((breach - now).total_seconds() // 60)
            if mins <= 30:
                score = max(score, 98 if mins <= 10 else 91)
                kind, action = "sla_risk", "Protect the SLA"
                reasons.append(f"SLA {'breached' if mins < 0 else 'breaches'} {'%dm ago' % abs(mins) if mins < 0 else 'in %dm' % mins}")

        updated = _dt(incident.get("updated_at") or incident.get("detected_at"))
        if status in {"CORRELATING", "TRIAGING", "INVESTIGATING"} and updated and now - updated > timedelta(minutes=30):
            score = max(score, 76)
            kind, action = "stalled_investigation", "Check investigation progress"
            reasons.append(f"Investigation has had no incident update for {int((now - updated).total_seconds() // 60)}m")

        if not reasons:
            reasons.append(f"Incident is {status.replace('_', ' ').title()}")

        attention_by_incident[incident_id] = {
            "id": f"incident:{incident_id}",
            "kind": kind,
            "score": score,
            "incident_id": incident_id,
            "title": incident.get("title") or incident_id,
            "detail": " · ".join(reasons[:2]),
            "reasons": reasons,
            "severity": incident.get("severity"),
            "status": incident.get("status"),
            "action": action,
            "to": f"/app/command-center?incident={incident_id}",
            "observed_at": incident.get("updated_at") or incident.get("detected_at"),
        }

    # Recent failed verification remains important even though FAILED is terminal.
    for event in audit:
        if event.get("event_type") != "VERIFICATION_FAILED":
            continue
        when = _dt(event.get("created_at"))
        if not when or now - when > timedelta(hours=24):
            continue
        incident_id = event.get("incident_id")
        existing = attention_by_incident.get(incident_id)
        if existing:
            existing["score"] = max(existing["score"], 99)
            existing["reasons"].insert(0, "Independent recovery verification failed")
            existing["detail"] = "Independent recovery verification failed"
            existing["kind"] = "verification_failed"
            existing["action"] = "Escalate and review recovery"
        else:
            incident = next((i for i in incidents if i.get("incident_id") == incident_id), {})
            attention_by_incident[incident_id] = {
                "id": f"verification:{incident_id}", "kind": "verification_failed", "score": 99,
                "incident_id": incident_id, "title": incident.get("title") or incident_id,
                "detail": "Independent recovery verification failed in the last 24 hours",
                "reasons": [event.get("event_summary") or "Independent verification failed"],
                "severity": incident.get("severity"), "status": incident.get("status"),
                "action": "Escalate and review recovery", "to": f"/app/command-center?incident={incident_id}",
                "observed_at": event.get("created_at"),
            }

    system_health = _system_health(runtime_status, bridge_row, now)
    for item in system_health:
        if item["state"] == "critical":
            attention_by_incident[f"system:{item['key']}"] = {
                "id": f"system:{item['key']}", "kind": "platform_health", "score": 100,
                "incident_id": None, "title": item["label"], "detail": item["detail"],
                "reasons": ["NemoGuard platform health requires attention"], "severity": "critical",
                "status": item["state"], "action": "Review platform health", "to": "/app/settings",
                "observed_at": runtime_status.get("last_checked_at"),
            }
        elif item["key"] == "outlook" and item["state"] == "warning":
            attention_by_incident[f"system:{item['key']}"] = {
                "id": "system:outlook", "kind": "alert_intake", "score": 84,
                "incident_id": None, "title": "Alert mailbox needs attention", "detail": item["detail"],
                "reasons": ["Forwarded production alerts may not be reaching NemoGuard promptly"],
                "severity": "warning", "status": "warning", "action": "Review Alert Inbox",
                "to": "/app/alert-inbox", "observed_at": bridge_row.get("observed_at") if bridge_row else None,
            }

    attention = sorted(attention_by_incident.values(), key=lambda item: (-item["score"], str(item.get("observed_at") or "")))[:12]

    # --- Coverage / "What NemoGuard Needs" -----------------------------
    coverage_dimensions: Dict[str, Optional[int]] = {
        "ownership": None,
        "execution_context": None,
        "evidence": None,
        "independent_evidence": None,
        "dependency_context": None,
        "runbook": None,
    }
    improvement_needs: List[Dict[str, Any]] = []
    if open_incidents:
        total = len(open_incidents)
        values = {key: 0 for key in coverage_dimensions}
        missing: Dict[str, List[str]] = defaultdict(list)
        for incident in open_incidents:
            incident_id = incident.get("incident_id")
            inc_evidence = evidence_by_incident.get(incident_id, [])
            independent = [e for e in inc_evidence if str(e.get("authority") or "MEDIUM").upper() in {"HIGH", "MEDIUM"} and float(e.get("reliability") or 0) >= 0.5]
            latest_plan = plans_by_incident.get(incident_id)

            checks = {
                "ownership": bool(incident.get("owner_team")),
                "execution_context": bool(incident.get("primary_run_id") or incident.get("primary_job_id")),
                "evidence": bool(inc_evidence),
                "independent_evidence": bool(independent),
                "dependency_context": bool(impacts_by_incident.get(incident_id)),
                "runbook": bool(latest_plan and latest_plan.get("runbook_id")),
            }
            for key, ok in checks.items():
                if ok:
                    values[key] += 1
                else:
                    missing[key].append(incident_id)

        for key in coverage_dimensions:
            coverage_dimensions[key] = round(values[key] * 100 / total)

        need_defs = {
            "independent_evidence": ("Independent telemetry is missing", "Forwarded/source-reported evidence alone cannot fully validate root cause or recovery.", "Provide runtime logs, monitoring evidence, or another independently observed signal."),
            "dependency_context": ("Dependency context is incomplete", "Impact and blast-radius analysis is limited when affected assets are unknown.", "Add or supply dependency/CMDB context for the affected service or pipeline."),
            "runbook": ("Approved runbooks are missing", "NemoGuard can investigate but has less grounded recovery guidance without an approved procedure.", "Review repeated incidents and publish an approved runbook for common recovery paths."),
            "execution_context": ("Execution context is missing", "Job/run identifiers make telemetry correlation and verification substantially stronger.", "Include job or run identifiers in forwarded alerts where the source provides them."),
            "ownership": ("Support ownership is missing", "NemoGuard cannot reliably route or escalate an incident without an owner.", "Assign an owner/support team to the affected production component."),
            "evidence": ("Incident evidence is sparse", "Investigation confidence is constrained when there is no persisted evidence beyond incident metadata.", "Attach or ingest logs, email evidence, or monitoring details for this incident."),
        }
        order = ["independent_evidence", "dependency_context", "runbook", "execution_context", "ownership", "evidence"]
        for key in order:
            ids = missing.get(key, [])
            if not ids:
                continue
            title, why, recommendation = need_defs[key]
            improvement_needs.append({
                "key": key,
                "title": title,
                "detail": why,
                "recommendation": recommendation,
                "affected_count": len(ids),
                "incident_ids": ids[:5],
                "priority": "high" if key in {"independent_evidence", "dependency_context"} else "medium",
            })

        score = round(sum(value or 0 for value in coverage_dimensions.values()) / len(coverage_dimensions))
    else:
        score = None

    # Surface explicit missing-evidence requests from the latest hypotheses as a more specific improvement hint.
    hypothesis_missing: List[str] = []
    for incident in open_incidents:
        latest = hypothesis_by_incident.get(incident.get("incident_id"))
        for value in _json_list(latest.get("missing_evidence_json") if latest else None):
            text = str(value).strip()
            if text and text not in hypothesis_missing:
                hypothesis_missing.append(text)
    if hypothesis_missing:
        improvement_needs.insert(0, {
            "key": "requested_evidence",
            "title": "Investigations are asking for more evidence",
            "detail": hypothesis_missing[0],
            "recommendation": "Provide the requested evidence through Commander or the approved ingestion path, then re-investigate.",
            "affected_count": len(hypothesis_missing),
            "incident_ids": [],
            "priority": "high",
        })

    # --- Shift brief -----------------------------------------------------
    shift_start = now - timedelta(hours=12)
    opened_shift = [i for i in incidents if (_dt(i.get("created_at") or i.get("detected_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= shift_start]
    resolved_shift = [i for i in incidents if (_dt(i.get("resolved_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= shift_start]
    alerts_shift = [a for a in alerts if (_dt(a.get("opened_ts")) or datetime.min.replace(tzinfo=timezone.utc)) >= shift_start]
    verified_shift = {
        v.get("incident_id") for v in verifications
        if str(v.get("status") or "").upper() == "PASSED" and (_dt(v.get("checked_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= shift_start
    }
    shift_brief = {
        "window_hours": 12,
        "incidents_opened": len(opened_shift),
        "incidents_resolved": len(resolved_shift),
        "alerts_received": len(alerts_shift),
        "high_priority_alerts": sum(1 for a in alerts_shift if _severity_high(a.get("severity"))),
        "verified_recoveries": len(verified_shift),
        "attention_items": len(attention),
    }

    # --- Activity series ------------------------------------------------
    start_day = (now - timedelta(days=13)).date()
    series_map: Dict[str, Dict[str, Any]] = {}
    for offset in range(14):
        day = start_day + timedelta(days=offset)
        series_map[day.isoformat()] = {"date": day.isoformat(), "alerts": 0, "incidents": 0, "resolved": 0}
    for alert in alerts:
        parsed = _dt(alert.get("opened_ts"))
        if parsed and parsed.date().isoformat() in series_map:
            series_map[parsed.date().isoformat()]["alerts"] += 1
    for incident in incidents:
        opened = _dt(incident.get("created_at") or incident.get("detected_at"))
        if opened and opened.date().isoformat() in series_map:
            series_map[opened.date().isoformat()]["incidents"] += 1
        resolved = _dt(incident.get("resolved_at"))
        if resolved and resolved.date().isoformat() in series_map:
            series_map[resolved.date().isoformat()]["resolved"] += 1
    activity_series = list(series_map.values())

    # --- Agent activity projection --------------------------------------
    agent_events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in audit:
        actor = str(event.get("actor_id") or "")
        upper = actor.upper()
        mapped = None
        if "WATCHER" in upper:
            mapped = "Watcher"
        elif "RCA" in upper:
            mapped = "RCA"
        elif "IMPACT" in upper or "DEPENDENCY" in upper:
            mapped = "Dependency"
        elif "RUNBOOK" in upper:
            mapped = "Runbook"
        elif "SAFETY" in upper or "CRITIC" in upper:
            mapped = "Safety"
        elif "COMMANDER" in upper:
            mapped = "Commander"
        if mapped:
            agent_events[mapped].append(event)

    agents = []
    for name in _AGENT_NAMES:
        events = agent_events.get(name, [])
        latest = events[0] if events else None
        when = _dt(latest.get("created_at")) if latest else None
        age = (now - when) if when else None
        if age is not None and age <= timedelta(minutes=5):
            status = "active"
        elif age is not None and age <= timedelta(hours=24):
            status = "recent"
        else:
            status = "standing_by"
        agents.append({
            "name": name,
            "status": status,
            "detail": latest.get("event_summary") if latest else "No recent activity recorded",
            "last_event_at": latest.get("created_at") if latest else None,
            "incident_id": latest.get("incident_id") if latest else None,
            "events_24h": sum(1 for event in events if (_dt(event.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= now - timedelta(hours=24)),
        })

    # --- Recent investigations / live feed ------------------------------
    recent_investigations = []
    for incident in incidents[:8]:
        inc_id = incident.get("incident_id")
        latest_plan = plans_by_incident.get(inc_id)
        latest_hyp = hypothesis_by_incident.get(inc_id)
        recent_investigations.append({
            "incident_id": inc_id,
            "title": incident.get("title"),
            "status": incident.get("status"),
            "severity": incident.get("severity"),
            "rca_confidence": incident.get("rca_confidence") or (latest_hyp.get("confidence") if latest_hyp else None),
            "evidence_count": len(evidence_by_incident.get(inc_id, [])),
            "impact_count": len(impacts_by_incident.get(inc_id, [])),
            "plan_status": latest_plan.get("status") if latest_plan else None,
            "updated_at": incident.get("updated_at") or incident.get("detected_at"),
        })

    live_events = [
        {
            "id": event.get("audit_event_id"),
            "incident_id": event.get("incident_id"),
            "actor": event.get("actor_id"),
            "event_type": event.get("event_type"),
            "summary": event.get("event_summary"),
            "created_at": event.get("created_at"),
        }
        for event in audit[:12]
    ]

    # --- Deterministic Commander/shift headline -------------------------
    if attention:
        top = attention[0]
        commander_headline = f"{len(attention)} item{'s' if len(attention) != 1 else ''} need attention."
        commander_summary = f"Highest priority: {top['title']} — {top['detail']}"
    elif open_incidents:
        commander_headline = f"{len(open_incidents)} open incident{'s' if len(open_incidents) != 1 else ''}; no urgent operator action detected."
        commander_summary = "Review open incidents for their current status."
    else:
        commander_headline = "No urgent items identified."
        commander_summary = "Review alert intake and system status below. Missing data may limit this summary."

    return {
        "generated_at": now.isoformat(),
        "summary": {
            "open_incidents": len(open_incidents),
            "high_priority_incidents": len(high_open),
            "high_priority_alerts": len(high_open_alerts),
            "verified_recoveries_30d": len(verified_incidents),
            "median_triage_seconds": median_triage,
            "alerts_last_24h": sum(1 for a in alerts if (_dt(a.get("opened_ts")) or datetime.min.replace(tzinfo=timezone.utc)) >= now - timedelta(hours=24)),
        },
        "commander_brief": {"headline": commander_headline, "summary": commander_summary},
        "needs_attention": attention,
        "coverage": {
            "score": score,
            "dimensions": coverage_dimensions,
            "active_incidents_assessed": len(open_incidents),
            "needs": improvement_needs[:6],
        },
        "shift_brief": shift_brief,
        "agents": agents,
        "activity_series": activity_series,
        "recent_investigations": recent_investigations,
        "live_events": live_events,
        "system_health": system_health,
        "outlook_bridge": None if not bridge_row else {
            "last_scan_at": bridge_row.get("observed_at"),
            "pending": bridge_row.get("pending_count") or 0,
            "blocked": bridge_row.get("blocked_count") or 0,
            "baseline": bridge_row.get("baseline_count") or 0,
        },
    }
