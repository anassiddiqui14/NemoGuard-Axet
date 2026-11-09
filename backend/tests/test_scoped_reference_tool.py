"""Tests for incident-bound reference and email page retrieval."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain import incident_knowledge
from app.domain.agents.scoped_reference_tool import incident_tool_dispatch
from app.domain.agents.agent_tools import query_reviewed_email_evidence
from app.store.sqlite_database import SqliteDatabase


class ScopedReferenceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_paging_and_incident_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            db = SqliteDatabase(str(Path(directory) / "test.db"))
            db.init_schema()
            incident_knowledge.initialize(db)
            log_text = "header\r\nERROR " + "x" * 24000 + "end"
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO incident (incident_id,tenant_id,workspace_id,primary_job_id,title,status,severity,detected_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("one", "tenant", "workspace", "job", "Test", "PLAN_READY", "high", "now", "now", "now"),
                )
                conn.execute(
                    "INSERT INTO alert (alert_id,tenant_id,workspace_id,opened_ts,severity,alert_type,source_system,message,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    ("alert", "tenant", "workspace", "now", "high", "test", "Outlook", "failure", "open"),
                )
                conn.execute(
                    "INSERT INTO incident_alert (incident_id,alert_id,correlation_score,correlation_reasons_json,added_at) VALUES (%s,%s,%s,%s,%s)",
                    ("one", "alert", 1.0, "[]", "now"),
                )
                conn.execute(
                    "CREATE TABLE reviewed_alert_document (document_id TEXT PRIMARY KEY, alert_id TEXT, document_kind TEXT, title TEXT, content TEXT, submitted_by TEXT, created_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO reviewed_alert_document VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("doc", "alert", "EMAIL_ATTACHMENT", "error.log", log_text, "automatic-outlook-bridge", "now"),
                )
            sop_content = "a" * 99994 + "finish"
            sop = incident_knowledge.submit(db, "tenant", "workspace", "one", "job", "SOP", "SOP", sop_content, "operator")

            async def delegate(name, arguments):
                return "delegated"

            with patch("app.store.database_factory.get_database", return_value=db):
                dispatch = incident_tool_dispatch("one", delegate)
                parts = []
                offset = 0
                while offset is not None:
                    result = json.loads(await dispatch("read_incident_email_page", json.dumps({"document_id": "doc", "offset": offset})))
                    self.assertFalse(result["operator_reviewed"])
                    parts.append(result["content"])
                    offset = result["next_offset"]
                self.assertEqual("".join(parts), log_text)
                with patch("app.domain.agents.agent_tools.get_database", return_value=db):
                    match = json.loads(query_reviewed_email_evidence("one", "ERROR"))["matches"][0]
                self.assertEqual(match["character_offset"], len("header\r\n"))
                self.assertTrue(match["line_truncated"])
                page = json.loads(await dispatch("read_incident_email_page", json.dumps({
                    "document_id": "doc", "offset": match["character_offset"],
                })))
                self.assertTrue(page["content"].startswith("ERROR "))
                page = json.loads(await dispatch("read_incident_reference_page", json.dumps({"knowledge_id": sop, "offset": 0})))
                self.assertEqual(page["content"], sop_content)
                self.assertIsNone(page["next_offset"])
                self.assertIn("does not match", await dispatch("query_logs", '{"incident_id":"other"}'))
                self.assertIn("not available", await incident_tool_dispatch("other", delegate)(
                    "read_incident_email_page", '{"document_id":"doc","offset":0}'
                ))
                self.assertIn("Invalid", await dispatch("read_incident_email_page", '{"document_id":"doc","offset":true}'))


if __name__ == "__main__":
    unittest.main()
