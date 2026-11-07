"""Durable intake records for the Outlook bridge."""
import json
import os
from datetime import datetime, timezone


def scope():
    tenant = os.getenv("NEMOGUARD_OUTLOOK_TENANT_ID", "default_tenant").strip()
    workspace = os.getenv("NEMOGUARD_OUTLOOK_WORKSPACE_ID", "default_workspace").strip()
    if not tenant or not workspace:
        raise ValueError("Outlook inbox tenant and workspace must not be empty")
    return tenant, workspace


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize(db):
    with db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_inbox (
                delivery_key VARCHAR(200) PRIMARY KEY,
                payload_hash VARCHAR(64) NOT NULL,
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                state VARCHAR(32) NOT NULL,
                received_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                result_json TEXT NOT NULL
            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_inbox_payload (
                delivery_key VARCHAR(200) PRIMARY KEY REFERENCES outlook_inbox(delivery_key),
                payload_json TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_inbox_review (
                review_id VARCHAR(64) PRIMARY KEY,
                delivery_key VARCHAR(200) NOT NULL REFERENCES outlook_inbox(delivery_key),
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                assessment VARCHAR(32) NOT NULL,
                reason TEXT NOT NULL,
                actor_id VARCHAR(200) NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_bridge_heartbeat (
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                observed_at VARCHAR(64) NOT NULL,
                pending_count INTEGER NOT NULL,
                blocked_count INTEGER NOT NULL,
                baseline_count INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, workspace_id)
            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_inbox_retry (
                retry_id VARCHAR(64) PRIMARY KEY,
                delivery_key VARCHAR(200) NOT NULL REFERENCES outlook_inbox(delivery_key),
                tenant_id VARCHAR(200) NOT NULL,
                workspace_id VARCHAR(200) NOT NULL,
                actor_id VARCHAR(200) NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS outlook_inbox_promotion (
                delivery_key VARCHAR(200) PRIMARY KEY REFERENCES outlook_inbox(delivery_key),
                alert_id VARCHAR(128) NOT NULL,
                actor_id VARCHAR(200) NOT NULL,
                reason TEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL
            )
        """)


def receive(db, key, payload_hash, subject, body, payload_json=None):
    timestamp = now()
    tenant, workspace = scope()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO outlook_inbox (delivery_key, payload_hash, tenant_id, workspace_id, subject, body, state, received_at, updated_at, result_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (delivery_key) DO NOTHING",
            (key, payload_hash, tenant, workspace, subject, body, "received", timestamp, timestamp, "{}"),
        )
        row = conn.execute(
            "SELECT payload_hash, tenant_id, workspace_id FROM outlook_inbox WHERE delivery_key=%s", (key,)
        ).fetchone()
        matches = row[0] == payload_hash and row[1] == tenant and row[2] == workspace
        if matches and payload_json is not None:
            conn.execute(
                "INSERT INTO outlook_inbox_payload (delivery_key, payload_json) VALUES (%s,%s) "
                "ON CONFLICT (delivery_key) DO NOTHING",
                (key, payload_json),
            )
        return matches


def record_result(db, key, result):
    status = result.get("status")
    accepted = {
        "ingested", "ingested_and_correlated", "ingested_and_incident_created",
        "ingested_and_incident_auto_resolved",
    }
    state = "needs_review" if status == "ignored" else "ingested" if status in accepted else "failed"
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE outlook_inbox SET state=%s, updated_at=%s, result_json=%s "
            "WHERE delivery_key=%s AND state NOT IN ('ingested', 'needs_review', 'promoted')",
            (state, now(), json.dumps(result), key),
        )


def list_items(db, tenant_id, workspace_id):
    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT delivery_key, subject, state, received_at, updated_at, result_json "
            "FROM outlook_inbox WHERE tenant_id=%s AND workspace_id=%s ORDER BY received_at DESC LIMIT 200",
            (tenant_id, workspace_id),
        )
        return [
            {"delivery_key": r[0], "subject": r[1], "state": r[2], "received_at": r[3],
             "updated_at": r[4], "result": json.loads(r[5])}
            for r in cursor.fetchall()
        ]
