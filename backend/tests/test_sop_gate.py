"""Tests for the pre-investigation SOP gate."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.domain import incident_knowledge as knowledge
from app.domain import triage_scheduler
from app.store.sqlite_database import SqliteDatabase


class SopGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = SqliteDatabase(str(Path(self.temp.name) / "gate.db"))
        self.db.init_schema()
        knowledge.initialize(self.db)
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO incident (incident_id,tenant_id,workspace_id,primary_job_id,"
                "title,status,severity,detected_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                ("one", "tenant", "workspace", "job-1", "Test", "INVESTIGATING",
                 "high", "now", "now", "now"),
            )

    async def test_pause_and_resume_after_explicit_decision(self):
        with patch.object(triage_scheduler, "_get_db", return_value=self.db), \
             patch.object(triage_scheduler, "get_db_backend_name", return_value="sqlite"), \
             patch.object(triage_scheduler, "_start_temporal_workflow", new_callable=AsyncMock) as start:
            first = await triage_scheduler.schedule_triage_for_new_incident(object(), "one", "DETECTED")
            self.assertEqual(first["status"], "AWAITING_SOP")
            start.assert_not_awaited()
            with self.db.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM incident WHERE incident_id=%s", ("one",)).fetchone()[0], "DETECTED")
            again = await triage_scheduler.schedule_triage(object(), "one")
            self.assertEqual(again["status"], "AWAITING_SOP")
            with self.db.get_connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident_message WHERE incident_id=%s AND intent='SOP_REQUEST'", ("one",)).fetchone()[0], 1)
            self.assertTrue(knowledge.confirm_no_sop(self.db, "tenant", "workspace", "one", "operator"))
            resumed = await triage_scheduler.schedule_triage(object(), "one")
            self.assertEqual(resumed["status"], "QUEUED_TEMPORAL")
            start.assert_awaited_once()

    async def test_pause_preserves_existing_review_checkpoint(self):
        with patch.object(triage_scheduler, "_get_db", return_value=self.db), \
             patch.object(triage_scheduler, "get_db_backend_name", return_value="sqlite"), \
             patch.object(triage_scheduler, "_start_temporal_workflow", new_callable=AsyncMock) as start:
            for status in ("PLAN_READY", "AWAITING_APPROVAL", "MANUAL_ACTION_REQUIRED"):
                with self.subTest(status=status):
                    with self.db.get_connection() as conn:
                        conn.execute("UPDATE incident SET status=%s WHERE incident_id=%s", (status, "one"))
                    result = await triage_scheduler.schedule_triage(object(), "one")
                    self.assertEqual(result["status"], "AWAITING_SOP")
                    with self.db.get_connection() as conn:
                        actual = conn.execute(
                            "SELECT status FROM incident WHERE incident_id=%s", ("one",)
                        ).fetchone()[0]
                    self.assertEqual(actual, status)
            start.assert_not_awaited()

    async def test_sop_is_not_hidden_by_newer_dependencies(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='DETECTED' WHERE incident_id=%s", ("one",))
        sop_id = knowledge.submit(
            self.db, "tenant", "workspace", "one", "job-1",
            "SOP", "Procedure", "Check the service.", "operator",
        )
        with self.db.get_connection() as conn:
            for index in range(21):
                conn.execute(
                    "INSERT INTO incident_knowledge "
                    "(knowledge_id,tenant_id,workspace_id,incident_id,job_key,kind,title,content,status,submitted_by,submitted_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (f"dep-{index}", "tenant", "workspace", "one", "job-1",
                     "DEPENDENCY", "Dependency", "Details", "PENDING", "operator",
                     f"9999-01-01T00:00:{index:02d}Z"),
                )
        context = knowledge.context_for_incident(self.db, "one")
        self.assertTrue(context["items_truncated"])
        self.assertIn(sop_id, [item["knowledge_id"] for item in context["items"]])
        with patch.object(triage_scheduler, "_get_db", return_value=self.db), \
             patch.object(triage_scheduler, "get_db_backend_name", return_value="sqlite"), \
             patch.object(triage_scheduler, "_start_temporal_workflow", new_callable=AsyncMock) as start:
            result = await triage_scheduler.schedule_triage(object(), "one")
            self.assertEqual(result["status"], "QUEUED_TEMPORAL")
            start.assert_awaited_once()

    async def test_submitted_sop_allows_scheduling(self):
        with self.db.get_connection() as conn:
            conn.execute("UPDATE incident SET status='DETECTED' WHERE incident_id=%s", ("one",))
        knowledge.submit(self.db, "tenant", "workspace", "one", "job-1", "SOP", "Procedure", "Check the service.", "operator")
        with patch.object(triage_scheduler, "_get_db", return_value=self.db), \
             patch.object(triage_scheduler, "get_db_backend_name", return_value="sqlite"), \
             patch.object(triage_scheduler, "_start_temporal_workflow", new_callable=AsyncMock) as start:
            result = await triage_scheduler.schedule_triage(object(), "one")
            self.assertEqual(result["status"], "QUEUED_TEMPORAL")
            start.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
