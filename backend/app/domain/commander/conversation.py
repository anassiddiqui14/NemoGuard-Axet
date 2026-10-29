"""
Commander Live conversation.

Three explicit operator intents:
  ASK       -- read-only. Answers using current incident state/evidence,
               never mutates anything.
  GUIDE     -- persists as commander_guidance, increments the incident's
               guidance_revision. May trigger reinvestigation (deterministic
               rule, not purely LLM-decided).
  ADD_FACT  -- like GUIDE, but marked with stronger evidence authority
               (HUMAN_CONFIRMED_FACT vs HUMAN_OBSERVATION).

Conversation text is NEVER a valid approval or execution path -- approval
is still only ever performed through POST /plans/{plan_id}/approve with an
exact plan_hash match (see backend/app/api/main.py::approve_plan).
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.store.postgres_database import PostgresDatabase
from app.domain.commander.status_projector import StatusProjector
from app.domain.agents.commander_live_agent import CommanderLiveAgent


def _generate_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


VALID_GUIDANCE_TYPES = {
    "INVESTIGATION_DIRECTION", "HYPOTHESIS_FEEDBACK", "RECOVERY_CONSTRAINT",
    "EVIDENCE_REQUEST", "BUSINESS_CONTEXT", "MANUAL_EVIDENCE_REFERENCE",
}

# Deterministic classifier for whether guidance/a fact is materially
# state-changing enough to warrant flagging for reinvestigation.
# Intentionally simple and keyword-based rather than another LLM call --
# deterministic safety checks must make the final decision for plan
# invalidation, not the model itself.
_MATERIAL_KEYWORDS = (
    "not correct", "incorrect", "wrong", "actually", "instead",
    "already", "partial", "stale", "duplicate", "do not", "don't",
    "confirmed", "rule out", "ruled out", "contradicts", "unsafe",
    "reinvestigate", "re-investigate", "investigate again", "look again",
    "check again", "re-run", "rerun the investigation",
)


def _is_materially_state_changing(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in _MATERIAL_KEYWORDS)


# Fire-and-forget background re-investigation tasks (triggered by GUIDE/
# ADD_FACT) must keep a reference somewhere, otherwise asyncio may garbage
# -collect a Task that is still running, silently cancelling it. This
# module-level set is the simplest possible fix: add on create, discard on
# completion via a done-callback. Deliberately NOT a new task queue/
# framework (no Celery/Temporal/FastAPI BackgroundTasks) -- just keeping a
# reference alive for the lifetime of the task.
_BACKGROUND_TASKS: set = set()


class IncidentNotFoundError(Exception):
    pass


class CommanderConversation:
    def __init__(self, db: Optional[PostgresDatabase] = None):
        self.db = db or PostgresDatabase(
            os.environ.get("POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db")
        )
        self.projector = StatusProjector(self.db)
        self.agent = CommanderLiveAgent()

    def _load_context(self, incident_id: str) -> Dict[str, Any]:
        """Assembles the same real evidence/hypothesis/plan context an
        operator would see in the workspace, for the LLM to ground its
        response in -- never fabricated, always read live from the DB.

        Hypotheses are scoped to the LATEST investigation's batch only
        (all hypotheses saved by a single save_agent_findings() call share
        one created_at timestamp): without this, ranking "ORDER BY
        confidence DESC" across every historical investigation would mix
        hypotheses from unrelated past triage runs together with the
        current one. The latest plan is selected by plan_version (the
        authoritative revision ordering), not created_at, since a plan
        revision may be persisted in the same batch/moment as an earlier
        one in edge cases.
        """
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT status, actual_root_cause, rca_confidence FROM incident WHERE incident_id = %s",
                (incident_id,),
            )
            status_row = cursor.fetchone()
            status = status_row[0] if status_row else "UNKNOWN"

            cursor = conn.execute(
                """
                SELECT statement, cause_type, confidence
                FROM hypothesis
                WHERE incident_id = %s
                  AND created_at = (
                      SELECT MAX(created_at)
                      FROM hypothesis
                      WHERE incident_id = %s
                  )
                ORDER BY rank_no ASC
                LIMIT 5
                """,
                (incident_id, incident_id),
            )
            hypotheses = [{"statement": r[0], "cause_type": r[1], "confidence": r[2]} for r in cursor.fetchall()]

            cursor = conn.execute(
                "SELECT action_plan_id, status, overall_risk FROM action_plan WHERE incident_id = %s "
                "ORDER BY plan_version DESC, created_at DESC LIMIT 1",
                (incident_id,),
            )
            plan_row = cursor.fetchone()
            plan = (
                {"action_plan_id": plan_row[0], "status": plan_row[1], "overall_risk": plan_row[2]}
                if plan_row else {}
            )

        return {"status": status, "hypotheses": hypotheses, "plan": plan}

    def _log_audit(self, incident_id: str, actor: str, event_type: str, summary: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_event (audit_event_id, incident_id, actor_type, actor_id, event_type, event_summary, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (_generate_id("AUD"), incident_id, "HUMAN", actor, event_type, summary, datetime.now(timezone.utc).isoformat()),
            )

    def _require_incident(self, incident_id: str, tenant_id: str) -> None:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM incident WHERE incident_id = %s AND tenant_id = %s", (incident_id, tenant_id),
            )
            if not cursor.fetchone():
                raise IncidentNotFoundError(incident_id)

    def _get_current_revision(self, incident_id: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT current_revision FROM incident_guidance_revision WHERE incident_id = %s", (incident_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else 0

    def _increment_revision(self, incident_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO incident_guidance_revision (incident_id, current_revision, updated_at)
                VALUES (%s, 1, %s)
                ON CONFLICT (incident_id) DO UPDATE SET
                    current_revision = incident_guidance_revision.current_revision + 1,
                    updated_at = EXCLUDED.updated_at
                """,
                (incident_id, now),
            )
            cursor = conn.execute(
                "SELECT current_revision FROM incident_guidance_revision WHERE incident_id = %s", (incident_id,),
            )
            return cursor.fetchone()[0]

    # ------------------------------------------------------------------
    # Conversation history
    # ------------------------------------------------------------------

    def get_messages(self, incident_id: str, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns up to `limit` messages for this incident in chronological
        (ASC) order. Selects the NEWEST `limit` rows first (DESC), then
        re-orders that page ASC -- otherwise, once a conversation exceeds
        `limit` messages, a plain "ORDER BY created_at ASC LIMIT N" would
        return only the OLDEST messages and new messages would silently
        disappear from both the UI and the context fed to the LLM."""
        self._require_incident(incident_id, tenant_id)
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT message_id, role, intent, content, created_by, created_at, evidence_refs_json, guidance_revision
                FROM (
                    SELECT message_id, role, intent, content, created_by, created_at, evidence_refs_json, guidance_revision
                    FROM incident_message
                    WHERE incident_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) AS recent
                ORDER BY created_at ASC
                """,
                (incident_id, limit),
            )
            cols = [c[0] for c in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        for r in rows:
            if r.get("evidence_refs_json"):
                try:
                    r["evidence_refs"] = json.loads(r["evidence_refs_json"])
                except Exception:
                    r["evidence_refs"] = []
        return rows

    def _add_message(
        self, incident_id: str, role: str, content: str, *, intent: Optional[str] = None,
        created_by: Optional[str] = None, evidence_refs: Optional[List[str]] = None,
        guidance_id: Optional[str] = None, guidance_revision: Optional[int] = None,
    ) -> str:
        message_id = _generate_id("MSG")
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO incident_message (
                    message_id, incident_id, role, intent, content, created_by, created_at,
                    evidence_refs_json, guidance_id, guidance_revision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message_id, incident_id, role, intent, content, created_by, now,
                    json.dumps(evidence_refs or []), guidance_id, guidance_revision,
                ),
            )
        return message_id

    # ------------------------------------------------------------------
    # ASK -- read-only
    # ------------------------------------------------------------------

    async def ask(self, incident_id: str, tenant_id: str, question: str, asked_by: str) -> Dict[str, Any]:
        """Read-only: a REAL LLM call (CommanderLiveAgent), grounded in the
        incident's current evidence/hypothesis/plan state, with read-only
        tool access for anything not already in that context. Never
        mutates incident state or guidance_revision."""
        self._require_incident(incident_id, tenant_id)
        # Load prior turns BEFORE persisting this new message, so the
        # history passed to the LLM doesn't include the question it's
        # about to answer twice.
        history = self.get_messages(incident_id, tenant_id)
        self._add_message(incident_id, "OPERATOR", question, intent="ASK", created_by=asked_by)

        context = self._load_context(incident_id)
        answer = await self.agent.ask(incident_id, context, question, history=history)

        self._add_message(incident_id, "COMMANDER", answer, evidence_refs=[h["statement"] for h in context["hypotheses"]])

        return {
            "answer": answer,
            "incident_state": context["status"],
            "hypotheses": context["hypotheses"],
            "guidance_revision": self._get_current_revision(incident_id),
        }

    # ------------------------------------------------------------------
    # GUIDE / ADD_FACT -- state-changing, persisted, revision-tracked
    # ------------------------------------------------------------------

    async def guide(
        self, incident_id: str, tenant_id: str, text: str, submitted_by: str, *,
        guidance_type: str = "INVESTIGATION_DIRECTION", is_fact: bool = False,
    ) -> Dict[str, Any]:
        """Persists operator guidance/fact as a first-class, auditable
        record and increments guidance_revision. Determines (via a
        deterministic keyword classifier, not an LLM) whether this looks
        materially state-changing enough to flag for reinvestigation, and
        if so, actually triggers it in the background.

        The exact final commander_ack text (including, for material
        guidance, the "I'm re-investigating now" addendum) is computed
        BEFORE it is persisted/returned, so the durable transcript and the
        HTTP response never disagree -- there is exactly one COMMANDER
        message persisted per guide() call, not two.
        """
        self._require_incident(incident_id, tenant_id)
        if guidance_type not in VALID_GUIDANCE_TYPES:
            guidance_type = "INVESTIGATION_DIRECTION"

        new_revision = self._increment_revision(incident_id)
        guidance_id = _generate_id("GUID")
        now = datetime.now(timezone.utc).isoformat()
        authority = "HUMAN_CONFIRMED_FACT" if is_fact else "HUMAN_OBSERVATION"
        is_material = _is_materially_state_changing(text)

        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO commander_guidance (
                    guidance_id, incident_id, created_at, created_by, source_channel,
                    guidance_type, authority, text, guidance_revision, triggered_reinvestigation
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    guidance_id, incident_id, now, submitted_by, "UI",
                    guidance_type, authority, text, new_revision, int(is_material),
                ),
            )

        self._add_message(
            incident_id, "OPERATOR", text,
            intent="ADD_FACT" if is_fact else "GUIDE",
            created_by=submitted_by, guidance_id=guidance_id, guidance_revision=new_revision,
        )

        event_type = "CONFIRMED_FACT_ADDED" if is_fact else "HUMAN_GUIDANCE_RECEIVED"
        self._log_audit(
            incident_id, submitted_by, event_type,
            f"Operator {'confirmed a fact' if is_fact else 'provided guidance'} (revision {new_revision}): {text}",
        )

        # Real LLM-generated acknowledgment, grounded in the incident's
        # current context and the specific text the operator provided --
        # not a fixed template. The deterministic is_material classification
        # above is passed in as a fact for the model to reference, but the
        # classification decision itself was never delegated to the LLM.
        context = self._load_context(incident_id)
        commander_ack = await self.agent.react_to_guidance(incident_id, context, text, is_fact, is_material)

        # Perform the actual decision to re-run the investigation graph.
        # Runs in the background (fire-and-forget via asyncio.create_task,
        # with a module-level reference kept so the task cannot be
        # garbage-collected mid-flight) so guide()/the HTTP response
        # returns immediately; the re-investigation result shows up later
        # as its own PLAN_REVISED audit event / Commander Pulse update /
        # chat message.
        reinvestigation_triggered = False
        if is_material:
            from app.domain.orchestrator import IncidentOrchestrator

            async def _run_reinvestigation():
                try:
                    result = await IncidentOrchestrator(self.db.db_path).reinvestigate_from_commander_guidance(
                        incident_id, text, is_fact, guidance_id,
                    )
                    print(f"Commander-triggered reinvestigation for {incident_id}: {result}")
                    # Explicit chat-visible outcome message -- previously the
                    # ONLY signal that re-investigation had started/finished
                    # was the audit_event/SSE stream, which most operators
                    # were not watching during a chat-triggered request. Never
                    # echo raw internal exception text/result["error"] into
                    # the chat transcript -- detailed errors stay server-side
                    # in logs only; the operator sees a generic, safe message.
                    status = result.get("status")
                    if status == "skipped":
                        outcome = (
                            f"I looked into re-investigating, but the incident is currently "
                            f"{result.get('reason', 'not in a state I can safely re-investigate right now')}. "
                            "I'll hold off until it reaches a plan-review checkpoint."
                        )
                    elif "error" in result:
                        outcome = (
                            "Re-investigation could not complete because of an internal error. "
                            "The previous plan remains available for review."
                        )
                    elif result.get("plan_id"):
                        # Phase 12: a plan whose safety review was
                        # INCONCLUSIVE (the critic could not complete its
                        # evaluation, as opposed to a genuine FAILED
                        # verdict) must be worded so the operator never
                        # reads it as "Safety rejected this" -- the plan
                        # was never auto-approved either way, but the
                        # distinction matters for how the operator should
                        # read the situation.
                        plan_version = result.get("plan_version")
                        outcome = (
                            f"Re-investigation completed and a revised plan was generated "
                            f"({result['plan_id']}), but the automated safety review could not complete. "
                            "The plan remains available for manual review and has not been approved."
                        ) if result.get("critic_status") == "INCONCLUSIVE" else (
                            f"Re-investigation complete. I've produced a revised recovery plan "
                            f"({result['plan_id']}) based on your input -- check the Recovery plan tab to review it."
                        )
                    else:
                        outcome = "Re-investigation finished, but I wasn't able to produce a revised plan. Please check the incident's audit log for details."
                    self._add_message(incident_id, "COMMANDER", outcome)
                except Exception as e:
                    # Full detail stays server-side; the chat transcript gets
                    # a generic, operator-safe message only.
                    print(f"Commander-triggered reinvestigation for {incident_id} failed: {e}")
                    try:
                        self._add_message(
                            incident_id, "COMMANDER",
                            "Re-investigation could not complete because of an internal error. "
                            "The previous plan remains available for review.",
                        )
                    except Exception:
                        pass

            task = asyncio.create_task(_run_reinvestigation())
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)
            reinvestigation_triggered = True
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE commander_guidance SET triggered_reinvestigation = 1 WHERE guidance_id = %s",
                    (guidance_id,),
                )
            # Tell the operator NOW (synchronously, before the background
            # task even starts) that re-investigation is beginning -- this
            # is what makes the chat box show something is happening
            # immediately rather than going quiet while the real
            # multi-agent pipeline runs. No duration/time estimate and no
            # claim of success before it actually completes.
            commander_ack += (
                " I'm re-investigating now with this information included and will post the "
                "result here when the new analysis finishes."
            )

        # Persist the FINAL commander_ack exactly once -- this is what the
        # durable transcript and the HTTP response both reflect; there is
        # no earlier persisted copy that would otherwise disagree with it.
        self._add_message(incident_id, "COMMANDER", commander_ack)

        return {
            "guidance_id": guidance_id,
            "guidance_revision": new_revision,
            "is_material": is_material,
            "authority": authority,
            "commander_response": commander_ack,
            "reinvestigation_triggered": reinvestigation_triggered,
        }
