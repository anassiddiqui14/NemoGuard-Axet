"""Approval and reference submission checks using temporary SQLite data."""
import concurrent.futures
import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["JWT_SECRET"] = "isolated-approval-test-secret-at-least-32-bytes"
os.environ["AXET_FLOWS_AUTH"] = "false"
os.environ["OTEL_SDK_DISABLED"] = "true"

from fastapi.testclient import TestClient

import app.api.main as api
import app.api.incident_knowledge as knowledge_api
from app.api.auth import create_access_token
from app.domain import incident_knowledge
from app.domain.orchestrator import IncidentOrchestrator
from app.store.sqlite_database import SqliteDatabase


def headers(role):
    token = create_access_token({
        "sub": "test-user", "roles": [role],
        "tenant_id": "tenant-a", "workspace_id": "workspace-a",
    })
    return {"Authorization": "Bearer " + token}


class KnowledgeApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(str(Path(self.tmp.name) / "test.db"))
        self.db.init_schema()
        incident_knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute("CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO incident (incident_id,tenant_id,workspace_id,title,status,severity,detected_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("incident", "tenant-a", "workspace-a", "Test", "PLAN_READY", "high", "now", "now", "now"),
            )
            conn.execute(
                "INSERT INTO agent_run (agent_run_id,incident_id,agent_name,objective,status) VALUES (%s,%s,%s,%s,%s)",
                ("agent", "incident", "agent", "test", "COMPLETED"),
            )
            conn.execute(
                "INSERT INTO action_plan (action_plan_id,incident_id,agent_run_id,plan_version,status,overall_risk,rationale,expected_outcome,rollback_summary,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("plan", "incident", "agent", 1, "PENDING_APPROVAL", "LOW", "Review", "Expected", "Rollback", "now"),
            )
        self.patches = [
            patch.object(api, "get_database", return_value=self.db),
            patch.object(knowledge_api, "get_database", return_value=self.db),
            patch.object(api, "temporal_client", None),
            patch.object(IncidentOrchestrator, "execute_plan", return_value=None),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(api.app)
        response = self.client.get("/api/v2/incidents/incident/plans", headers=headers("commander"))
        self.assertEqual(response.status_code, 200, response.text)
        self.plan_hash = response.json()[0]["plan_hash"]

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def approve(self):
        return self.client.post(
            "/api/v2/incidents/incident/plans/plan/approve",
            headers=headers("commander"),
            json={"decision": "approve", "plan_hash": self.plan_hash},
        )

    def submit(self):
        return self.client.post(
            "/api/v2/incidents/incident/knowledge",
            headers=headers("operator"),
            json={"kind": "DEPENDENCY", "job_key": "job-1", "title": "Dependency", "content": "Job B depends on job A."},
        )

    def test_submission_invalidates_pending_plan_and_blocks_approval(self):
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.approve().status_code, 409)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM action_plan WHERE action_plan_id=%s", ("plan",)).fetchone()[0], "NEEDS_REVIEW")
            self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("incident",)).fetchone()[0], "PLAN_READY")

    def test_active_investigation_cannot_approve_or_accept_reference(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='INVESTIGATING' WHERE incident_id=%s", ("incident",))
        self.assertEqual(self.approve().status_code, 409)
        self.assertEqual(self.submit().status_code, 400)
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident_knowledge").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("incident",)).fetchone()[0], "INVESTIGATING")

    def test_concurrent_submission_and_approval_cannot_both_succeed(self):
        barrier = threading.Barrier(2, timeout=10)

        def invoke(method):
            barrier.wait()
            return method().status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            submission = executor.submit(invoke, self.submit)
            approval = executor.submit(invoke, self.approve)
            submission_status = submission.result(timeout=40)
            approval_status = approval.result(timeout=40)
        self.assertIn((submission_status, approval_status), ((201, 409), (400, 200)))
        with self.db.get_connection() as conn:
            status = conn.execute("SELECT status FROM action_plan WHERE action_plan_id=%s", ("plan",)).fetchone()[0]
        self.assertEqual(status, "NEEDS_REVIEW" if submission_status == 201 else "APPROVED")


    def test_stale_findings_do_not_overwrite_executing_or_terminal_incident(self):
        from app.domain import orchestrator as orchestrator_module

        payload = {
            "hypotheses": [], "evidence": [], "impacts": [],
            "action_plan": {"rationale": "Stale", "steps": []},
        }
        with patch.object(orchestrator_module, "get_database", return_value=self.db):
            for status in ("EXECUTING", "VERIFYING", "RESOLVED", "FAILED", "CANCELLED"):
                with self.db.get_connection() as conn:
                    conn.execute("UPDATE incident SET status=%s WHERE incident_id=%s", (status, "incident"))
                result = IncidentOrchestrator().save_agent_findings("incident", payload, critic_status="PASSED")
                self.assertIn("error", result)
                with self.db.get_connection() as conn:
                    self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("incident",)).fetchone()[0], status)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM action_plan WHERE incident_id=%s", ("incident",)).fetchone()[0], 1)

    def test_feedback_does_not_revise_plan_after_approval(self):
        from app.domain import orchestrator as orchestrator_module
        from app.domain.agents import base_agent

        class FeedbackAgent:
            def __init__(self, **kwargs):
                pass

            async def call_llm_json(self, prompt):
                with self.db.get_connection() as conn:
                    conn.execute("UPDATE incident SET status='EXECUTING' WHERE incident_id=%s", ("incident",))
                    conn.execute("UPDATE action_plan SET status='APPROVED' WHERE action_plan_id=%s", ("plan",))
                return {"rationale": "Revised", "steps": []}

        FeedbackAgent.db = self.db
        with patch.object(orchestrator_module, "get_database", return_value=self.db), \
             patch.object(base_agent, "BaseAgent", FeedbackAgent):
            result = asyncio.run(IncidentOrchestrator().triage_feedback("incident", "Revise"))
        self.assertIn("error", result)
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT status,plan_version,rationale FROM action_plan WHERE action_plan_id=%s",
                ("plan",),
            ).fetchone()
        self.assertEqual(tuple(row), ("APPROVED", 1, "Review"))

    def test_concurrent_reinvestigation_and_approval_cannot_both_succeed(self):
        from app.domain import orchestrator as orchestrator_module
        from app.domain.agents import langgraph_investigator

        barrier = threading.Barrier(2, timeout=10)

        class Investigator:
            async def investigate(self, *args, **kwargs):
                await asyncio.sleep(0.05)
                return {
                    "rca_result": {"finding": "Unknown", "confidence": 0.1},
                    "impact_result": {"impacts": []},
                    "runbook_result": {"steps": []},
                    "critic_status": "INCONCLUSIVE",
                    "final_plan": {"rationale": "Review", "steps": [
                        {"action": "Manual review", "tool": "manual", "risk": "LOW"}
                    ]},
                }

        def approve():
            barrier.wait()
            return self.approve().status_code

        def reinvestigate():
            barrier.wait()
            return asyncio.run(IncidentOrchestrator().reinvestigate_from_commander_guidance(
                "incident", "Re-investigate", False, "guidance-test"
            ))

        with patch.object(orchestrator_module, "get_database", return_value=self.db), \
             patch.object(langgraph_investigator, "LangGraphInvestigator", Investigator):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                approval = executor.submit(approve)
                investigation = executor.submit(reinvestigate)
                approval_status = approval.result(timeout=40)
                investigation_result = investigation.result(timeout=40)

        if approval_status == 200:
            self.assertEqual(investigation_result.get("status"), "skipped")
        else:
            self.assertEqual(approval_status, 409)
            self.assertEqual(investigation_result.get("status"), "success")
            with self.db.get_connection() as conn:
                old_status = conn.execute(
                    "SELECT status FROM action_plan WHERE action_plan_id=%s", ("plan",)
                ).fetchone()[0]
            self.assertEqual(old_status, "SUPERSEDED")


if __name__ == "__main__":
    unittest.main()
