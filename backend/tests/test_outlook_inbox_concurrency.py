"""Concurrent Outlook deliveries against a temporary SQLite database."""
import concurrent.futures
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET", "test-only-secret-longer-than-thirty-two-bytes")
os.environ["AXET_FLOWS_AUTH"] = "false"

from fastapi.testclient import TestClient
from app.api import main as api
from app.domain import orchestrator as domain
from app.domain import triage_scheduler
from app.store import alert_inbox
from app.store.sqlite_database import SqliteDatabase


class ConcurrencyTests(unittest.TestCase):
    def test_two_overlapping_deliveries_create_one_incident(self):
        barrier = threading.Barrier(2)

        class Watcher:
            async def analyze(self, payload, active_incidents):
                import asyncio
                await asyncio.to_thread(barrier.wait, 10)
                return {
                    "is_valid": True,
                    "normalized_alert": {
                        "severity": "high", "alert_type": "TEST",
                        "source_system": "Outlook", "message": "Synthetic failure",
                    },
                }

        async def schedule(*args, **kwargs):
            return {"status": "QUEUED_DIRECT"}

        with tempfile.TemporaryDirectory() as directory:
            db = SqliteDatabase(str(Path(directory) / "inbox.db"))
            db.init_schema()
            alert_inbox.initialize(db)
            with db.get_connection() as conn:
                conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
                conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
            payload = {"source": "Outlook email", "monitor_name": "Synthetic", "message": "Synthetic failure"}
            headers = {"X-NemoGuard-Webhook-Secret": "test-secret", "Idempotency-Key": "outlook:concurrent"}

            # Avoid running application startup against the test database.
            def deliver_without_startup():
                client = TestClient(api.app)
                try:
                    return client.post("/api/v2/ingest/outlook-bridge", json=payload, headers=headers)
                finally:
                    client.close()

            with patch.dict(os.environ, {"NEMOGUARD_WEBHOOK_SECRET": "test-secret"}), \
                 patch.object(api, "get_database", return_value=db), \
                 patch.object(domain, "get_database", return_value=db), \
                 patch.object(domain, "WatcherAgent", Watcher), \
                 patch.object(triage_scheduler, "schedule_triage_for_new_incident", side_effect=schedule):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _: deliver_without_startup(), range(2)))
            self.assertEqual([r.status_code for r in responses], [200, 200])
            results = [r.json() for r in responses]
            self.assertEqual(results[0]["incident_id"], results[1]["incident_id"])
            self.assertEqual(sum(bool(r.get("duplicate")) for r in results), 1)
            with db.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM outlook_inbox").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT state FROM outlook_inbox").fetchone()[0], "ingested")


    def test_two_concurrent_promotions_create_one_alert(self):
        from app.api.auth import create_access_token
        barrier = threading.Barrier(2)
        with tempfile.TemporaryDirectory() as directory:
            db = SqliteDatabase(str(Path(directory) / "inbox.db"))
            db.init_schema()
            alert_inbox.initialize(db)
            with db.get_connection() as conn:
                conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
            alert_inbox.receive(db, "outlook:promotion", "hash", "Synthetic", "Synthetic body")
            alert_inbox.record_result(db, "outlook:promotion", {"status": "ignored"})
            token = create_access_token({"sub": "test", "roles": ["operator"]})

            def promote():
                client = TestClient(api.app)
                try:
                    barrier.wait(timeout=10)
                    return client.post(
                        "/api/v2/outlook-inbox/outlook:promotion/promote",
                        headers={"Authorization": "Bearer " + token},
                        json={"severity": "high", "reason": "Investigate"},
                    )
                finally:
                    client.close()

            with patch.object(api, "get_database", return_value=db):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    responses = list(executor.map(lambda _: promote(), range(2)))
            self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
            with db.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM outlook_inbox_promotion").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
