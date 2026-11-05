"""Isolated simulator regression tests; no live webhook or model calls."""
import asyncio
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.store.sqlite_database import SqliteDatabase
import simulator_backend.main as simulator


class Response:
    is_success = True
    status_code = 200

    def json(self):
        return {"status": "ingested_and_incident_created"}


class AsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, *args, **kwargs):
        return Response()


class FakeAgent:
    def __init__(self, **kwargs):
        pass

    async def call_llm_json(self, prompt):
        return {
            "failure_logs": [{"level": "ERROR", "component": "worker", "message": "failed"}],
            "webhook_payloads": [{"source": "Datadog", "message": "failed"}],
            "business_assets": [{"asset_name": "Report", "asset_type": "Dashboard", "owner": "Ops", "depends_on_service": "worker"}],
            "runbooks": [{"service_name": "worker", "title": "Recovery"}],
        }


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.temp.name) / "test.db"))
        self.db.init_schema()
        self.env = patch.dict(os.environ, {"NEMOGUARD_WEBHOOK_SECRET": "test-secret"})
        self.env.start()
        self.factory = patch.object(simulator, "get_database", return_value=self.db)
        self.factory.start()

    def tearDown(self):
        self.factory.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_scripted_scenarios_use_sqlite(self):
        with patch.object(simulator.httpx, "post", return_value=Response()) as send:
            for scenario in ("SCHEMA_REGRESSION", "OOM_CRASH", "CASCADING_FAILURE"):
                result = simulator.simulate_failure(scenario, "http://127.0.0.1:8200")
                self.assertTrue(result["outcomes"])
                self.assertTrue(all(o["status"] == "ingested_and_incident_created" for o in result["outcomes"]))
            self.assertEqual(send.call_count, 9)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM execution").fetchone()[0], 3)
            self.assertGreater(conn.execute("SELECT count(*) FROM log_event").fetchone()[0], 3)

    def _seed_reset_data(self):
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO incident (incident_id, title, status, severity, detected_at, created_at, updated_at) VALUES ('i', 'Incident', 'NEW', 'high', 'now', 'now', 'now')")
            conn.execute("INSERT INTO alert (alert_id, opened_ts, severity, alert_type, message) VALUES ('a', 'now', 'high', 'failure', 'test')")
            conn.execute("INSERT INTO incident_alert (incident_id, alert_id, correlation_score, correlation_reasons_json, added_at) VALUES ('i', 'a', 1, '[]', 'now')")
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL REFERENCES alert(alert_id))")
            conn.execute("INSERT INTO reviewed_alert_document VALUES ('d', 'a')")
            conn.execute("CREATE TABLE webhook_delivery (delivery_key TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO webhook_delivery VALUES ('receipt')")
            conn.execute("INSERT INTO incident_message (message_id, incident_id, role, content, created_at) VALUES ('m', 'i', 'user', 'message', 'now')")
            conn.execute("INSERT INTO agent_run (agent_run_id, incident_id, agent_name, objective, status) VALUES ('ar', 'i', 'agent', 'test', 'done')")
            conn.execute("INSERT INTO agent_step (agent_step_id, agent_run_id, sequence_no, step_type, summary, status, started_at) VALUES ('as', 'ar', 1, 'test', 'test', 'done', 'now')")
            conn.execute("INSERT INTO tool_call (tool_call_id, agent_run_id, agent_step_id, tool_name, risk_level, arguments_json, status, started_at) VALUES ('tc', 'ar', 'as', 'test', 'low', '{}', 'done', 'now')")
            conn.execute("INSERT INTO evidence (evidence_id, incident_id, tool_call_id, evidence_type, source_system, title, excerpt, collected_at) VALUES ('e', 'i', 'tc', 'test', 'test', 'test', 'test', 'now')")
            conn.execute("INSERT INTO action_plan (action_plan_id, incident_id, agent_run_id, plan_version, status, overall_risk, rationale, expected_outcome, rollback_summary, created_at) VALUES ('p', 'i', 'ar', 1, 'draft', 'low', 'test', 'test', 'test', 'now')")
            conn.execute("INSERT INTO action_step (action_step_id, action_plan_id, sequence_no, action_type, tool_name, risk_level, requires_approval, parameters_json, preconditions_json, expected_postconditions_json, status) VALUES ('s', 'p', 1, 'test', 'test', 'low', 1, '{}', '[]', '[]', 'draft')")
            conn.execute("INSERT INTO approval (approval_id, incident_id, action_plan_id, requested_at, expires_at, decision, plan_hash) VALUES ('ap', 'i', 'p', 'now', 'later', 'pending', 'hash')")
            conn.execute("INSERT INTO action_execution (action_execution_id, action_step_id, approval_id, idempotency_key, status) VALUES ('ae', 's', 'ap', 'key', 'done')")
            conn.execute("INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at) VALUES ('incident-audit', 'i', 'user', 'u', 'test', 'test', 'now')")
            conn.execute("INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at) VALUES ('global-audit', NULL, 'user', 'u', 'test', 'test', 'now')")
            conn.execute("INSERT INTO job (job_id, job_name, platform, domain, stage, schedule, criticality, default_duration_sec, owner_team, retry_policy) VALUES ('j', 'job', 'test', 'test', 'test', 'daily', 1, 1, 'ops', 'none')")
            conn.execute("INSERT INTO execution (run_id, job_id, scheduled_ts, start_ts, status, incident_id) VALUES ('r', 'j', 'now', 'now', 'failed', 'i')")
            conn.execute("INSERT INTO platform_user (user_id, email, password_hash) VALUES ('u', 'u@example.com', 'hash')")

    def test_reset_clears_incidents_and_preserves_unrelated_data(self):
        self._seed_reset_data()
        self.assertEqual(simulator.reset_database()["status"], "success")
        self.assertEqual(simulator.reset_database()["status"], "success")
        with self.db.get_connection() as conn:
            for table in ("incident", "alert", "incident_alert", "reviewed_alert_document", "incident_message", "agent_run", "agent_step", "tool_call", "evidence", "action_plan", "action_step", "approval", "action_execution"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            for table in ("job", "execution", "platform_user", "webhook_delivery", "audit_event"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1, table)
            self.assertIsNone(conn.execute("SELECT incident_id FROM execution").fetchone()[0])
            self.assertEqual(conn.execute("SELECT audit_event_id FROM audit_event").fetchone()[0], "global-audit")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_reset_route_uses_configured_database(self):
        self._seed_reset_data()

        async def call_route():
            transport = simulator.httpx.ASGITransport(app=simulator.app)
            async with simulator.httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/reset")

        response = asyncio.run(call_route())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM alert").fetchone()[0], 0)

    def test_reset_rolls_back_if_unexpected_dependency_blocks_deletion(self):
        self._seed_reset_data()
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE blocker (incident_id TEXT NOT NULL REFERENCES incident(incident_id))")
            conn.execute("INSERT INTO blocker VALUES ('i')")
        with self.assertRaises(sqlite3.IntegrityError):
            simulator.reset_database()
        with self.db.get_connection() as conn:
            for table in ("incident", "alert", "incident_alert", "reviewed_alert_document", "incident_message"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1, table)
            self.assertEqual(conn.execute("SELECT incident_id FROM execution").fetchone()[0], "i")

    def test_ignored_and_server_errors_are_not_success(self):
        response = Response()
        response.json = lambda: {"status": "ignored"}
        self.assertEqual(simulator._delivery_status(response), "ignored")
        response.is_success = False
        response.status_code = 500
        self.assertEqual(simulator._delivery_status(response), "unknown")

    def test_mounted_port_and_unconfirmed_delivery(self):
        request = SimpleNamespace(scope={"root_path": "/api/v2/sim", "server": ("127.0.0.1", 8341)})
        self.assertEqual(simulator._scenario_api_base(request), "http://127.0.0.1:8341")
        with patch.object(simulator, "simulate_failure", return_value={
            "run_id": "test-run", "outcomes": [{"status": "ignored"}]
        }):
            with self.assertRaises(simulator.HTTPException) as error:
                asyncio.run(simulator.trigger_scenario(simulator.ScenarioRequest(), request))
        self.assertEqual(error.exception.status_code, 502)

    def test_ai_scenario_uses_sqlite(self):
        from backend.app.domain.agents import base_agent
        async def collect():
            return [line async for line in simulator.generate_and_inject_ai("failure", "http://127.0.0.1:8200")]
        with patch.object(base_agent, "BaseAgent", FakeAgent), patch.object(simulator.httpx, "AsyncClient", AsyncClient), patch.object(simulator.asyncio, "sleep", return_value=None):
            lines = asyncio.run(collect())
        self.assertIn("Successfully injected AI incident", "".join(lines))
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM execution").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM business_asset").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM runbook").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
