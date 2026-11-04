"""Isolated tests for reviewed email evidence ingestion."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.store.sqlite_database import SqliteDatabase
from app.domain import orchestrator as module
from app.domain.agents import agent_tools


class FakeWatcher:
    result = {}

    async def analyze(self, payload, active_incidents):
        return self.result


class ReviewedIngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id), document_kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, submitted_by TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.patches = [
            patch.object(module, "get_database", return_value=self.db),
            patch.object(agent_tools, "get_database", return_value=self.db),
            patch.object(module, "WatcherAgent", FakeWatcher),
        ]
        for item in self.patches:
            item.start()
        self.obj = module.IncidentOrchestrator()
        FakeWatcher.result = {
            "is_valid": True,
            "normalized_alert": {"severity": "high", "alert_type": "TEST", "source_system": "Email", "message": "Synthetic failure"},
        }
        self.documents = [
            {"kind": "EMAIL_BODY", "title": "Test email", "content": "A batch failed."},
            {"kind": "EMAIL_ATTACHMENT", "title": "session.log", "content": "started\nERROR 1023 serialization violation\nstopped"},
        ]

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def ingest(self, key="event-1", digest="hash", docs=None):
        return asyncio.run(self.obj.process_webhook(
            {"message": "Synthetic failure"}, key, digest,
            reviewed_documents=self.documents if docs is None else docs,
            submitted_by="operator-1",
        ))

    def test_new_incident_retry_and_evidence(self):
        result = self.ingest()
        self.assertEqual(result["status"], "ingested_and_incident_created")
        retry = self.ingest()
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["alert_id"], result["alert_id"])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM reviewed_alert_document").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM evidence WHERE incident_id=%s AND authority=%s", (result["incident_id"], "LOW")).fetchone()[0], 2)
        evidence = json.loads(agent_tools.query_reviewed_email_evidence(result["incident_id"], "1023"))
        self.assertIn("unverified", evidence["source"])
        self.assertEqual(evidence["matches"][0]["line"], 2)
        self.assertIn("1023", evidence["matches"][0]["text"])
        self.assertEqual(self.ingest(digest="changed")["status"], "idempotency_conflict")

    def test_correlated_incident_gets_evidence_without_new_plan(self):
        now = "2026-01-01T00:00:00Z"
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO incident (incident_id,title,status,severity,detected_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", ("existing", "Existing", "PLAN_READY", "SEV_2", now, now, now))
        FakeWatcher.result["correlated_incident_id"] = "existing"
        result = self.ingest()
        self.assertEqual(result["status"], "ingested_and_correlated")
        self.assertEqual(result["incident_id"], "existing")
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM evidence WHERE incident_id=%s", ("existing",)).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(*) FROM action_plan WHERE incident_id=%s", ("existing",)).fetchone()[0], 0)

    def test_document_failure_rolls_back_alert_and_receipt(self):
        with self.assertRaises(KeyError):
            self.ingest(docs=[{"kind": "EMAIL_ATTACHMENT"}])
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM alert").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM webhook_delivery").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM reviewed_alert_document").fetchone()[0], 0)

    def test_email_plan_remains_needs_review(self):
        result = self.ingest()
        output = self.obj.save_agent_findings(
            result["incident_id"],
            {"evidence": [{"type": "Log", "excerpt": "synthetic"}], "hypotheses": [], "impacts": [], "action_plan": {"steps": []}},
            critic_status="PASSED",
        )
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT status, rationale FROM action_plan WHERE action_plan_id=%s", (output["plan_id"],)).fetchone()
            self.assertEqual(row[0], "NEEDS_REVIEW")
            self.assertIn("UNVERIFIED EMAIL EVIDENCE", row[1])


if __name__ == "__main__":
    unittest.main()
