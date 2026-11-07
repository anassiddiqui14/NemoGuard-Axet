"""Dependency-free tests of webhook persistence using an isolated SQLite database."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.store.sqlite_database import SqliteDatabase

source = Path(__file__).resolve().parents[1] / "app/domain/orchestrator.py"
module = ast.parse(source.read_text(encoding="utf-8"))
klass = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "IncidentOrchestrator")
method = next(n for n in klass.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_webhook")

class State:
    class RESOLVED:
        value = "RESOLVED"
    class INVESTIGATING:
        value = "INVESTIGATING"

class MockWatcher:
    result = None
    async def analyze(self, payload, active_incidents):
        return self.result

namespace = {
    "Dict": dict, "Any": object, "List": list, "json": json, "os": os, "uuid": uuid,
    "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
    "WatcherAgent": MockWatcher, "IncidentState": State,
}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), "exec"), namespace)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE alert (alert_id TEXT PRIMARY KEY, run_id TEXT, opened_ts TEXT NOT NULL, severity TEXT NOT NULL, alert_type TEXT NOT NULL, source_system TEXT NOT NULL, message TEXT NOT NULL, status TEXT)")
            conn.execute("CREATE TABLE incident (incident_id TEXT PRIMARY KEY, title TEXT, summary TEXT, primary_run_id TEXT, status TEXT, updated_at TEXT, resolved_at TEXT)")
            conn.execute("CREATE TABLE incident_alert (incident_id TEXT, alert_id TEXT, correlation_score REAL, correlation_reasons_json TEXT, added_at TEXT)")
            conn.execute("CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, incident_id TEXT, actor_type TEXT, actor_id TEXT, event_type TEXT, event_summary TEXT, created_at TEXT)")
        self.obj = type("Orchestrator", (), {"db": self.db, "process_webhook": namespace["process_webhook"]})()
        MockWatcher.result = {"is_valid": True, "normalized_alert": {"severity": "warning", "message": "test"}}

    def tearDown(self):
        self.tmp.cleanup()

    def run_delivery(self, key="one", payload_hash="hash"):
        return asyncio.run(self.obj.process_webhook({"message": "test"}, key, payload_hash))

    def count(self, table):
        with self.db.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_retry_and_conflict(self):
        first = self.run_delivery()
        retry = self.run_delivery()
        self.assertEqual(first["status"], "ingested")
        self.assertTrue(retry["duplicate"])
        self.assertEqual(first["alert_id"], retry["alert_id"])
        self.assertEqual(self.run_delivery(payload_hash="changed")["status"], "idempotency_conflict")
        self.assertEqual(self.count("alert"), 1)
        self.assertEqual(self.count("webhook_delivery"), 1)

    def test_concurrent_retry(self):
        barrier = threading.Barrier(2, timeout=10)
        async def synchronized_analyze(watcher, payload, active_incidents):
            barrier.wait()
            return MockWatcher.result
        with patch.object(MockWatcher, "analyze", synchronized_analyze):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.run_delivery(), range(2)))
        self.assertEqual({r["status"] for r in results}, {"ingested"})
        self.assertEqual(len({r["alert_id"] for r in results}), 1)
        self.assertEqual(sum(bool(r.get("duplicate")) for r in results), 1)
        self.assertEqual(self.count("alert"), 1)
        self.assertEqual(self.count("webhook_delivery"), 1)

    def test_ignored_delivery_is_deduplicated(self):
        MockWatcher.result = {"is_valid": False, "reasoning": "noise"}
        self.assertEqual(self.run_delivery()["status"], "ignored")
        self.assertTrue(self.run_delivery()["duplicate"])
        self.assertEqual(self.count("alert"), 0)
        self.assertEqual(self.count("webhook_delivery"), 1)

    def test_bad_classification_is_not_acknowledged(self):
        MockWatcher.result = {}
        self.assertEqual(self.run_delivery()["status"], "error")
        self.assertEqual(self.count("webhook_delivery"), 0)

    def test_rollback_does_not_claim_key(self):
        MockWatcher.result["normalized_alert"]["message"] = None
        with self.assertRaises(Exception):
            self.run_delivery()
        self.assertEqual(self.count("webhook_delivery"), 0)
        self.assertEqual(self.count("alert"), 0)

    def test_recovery_is_not_auto_resolved_by_default(self):
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO incident (incident_id, title, summary, status) VALUES (%s, %s, %s, %s)", ("inc", "title", "summary", "DETECTED"))
        MockWatcher.result.update({"correlated_incident_id": "inc", "is_recovery_signal": True})
        with patch.dict(os.environ, {}, clear=True):
            result = self.run_delivery()
        self.assertEqual(result["status"], "ingested_and_correlated")
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id = %s", ("inc",)).fetchone()[0], "DETECTED")
        self.assertEqual(self.count("audit_event"), 1)


if __name__ == "__main__":
    unittest.main()
