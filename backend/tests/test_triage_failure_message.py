"""Fail-closed direct-triage failure reporting."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain import triage_scheduler


class TriageFailureMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_plan_restores_status_and_reports_safe_fallback_unavailable(self):
        for restored in (True, False):
            with self.subTest(restored=restored):
                orchestrator = MagicMock()
                with patch.object(triage_scheduler, "_get_db", return_value=object()), \
                     patch.object(triage_scheduler, "IncidentOrchestrator", return_value=orchestrator), \
                     patch.object(triage_scheduler.asyncio, "to_thread", new=AsyncMock(return_value={"saved_plan": False})), \
                     patch.object(triage_scheduler, "_restore_if_still_investigating", return_value=restored):
                    await triage_scheduler._run_direct_triage("synthetic", "DETECTED")
                args = orchestrator._log_audit.call_args.args
                self.assertEqual(args[2], "TRIAGE_FAILED")
                self.assertIn("safe fallback plan was unavailable", args[3])
                if restored:
                    self.assertIn("restored to its prior status", args[3])
                else:
                    self.assertIn("no restoration was applied", args[3])


if __name__ == "__main__":
    unittest.main()
