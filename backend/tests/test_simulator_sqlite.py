"""Isolated simulator regression tests; no live webhook or model calls."""
import asyncio
import os
from pathlib import Path
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
