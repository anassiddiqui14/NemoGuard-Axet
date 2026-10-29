"""
Shared triage scheduling path (Phases 1-4 of the aXet reliability patch).

ONE reusable entry point -- `schedule_triage()` -- is used by BOTH:
  - the manual "Start Triage" button (POST /api/v2/incidents/{id}/triage)
  - the webhook ingestion path (POST /api/v2/ingest/webhook), when Watcher
    creates/correlates a new incident

so there is exactly one place that knows how to:
  - use Temporal when it's available (QUEUED_TEMPORAL), now INCLUDING the
    actual `start_workflow()` call and its failure handling (previously
    the caller in main.py made this call separately, unprotected -- a
    `start_workflow()` exception would propagate straight to the global
    FastAPI exception handler, leaving the incident permanently stuck in
    INVESTIGATING with no restoration).
  - run IncidentOrchestrator.triage_incident() directly in a worker thread
    when Temporal is absent (QUEUED_DIRECT) -- `triage_incident()` is a
    synchronous function that internally calls `asyncio.run()`, which
    would raise if called directly from this already-running async event
    loop; `asyncio.to_thread()` runs it on a plain OS thread that has no
    pre-existing event loop, so this is safe.
  - prevent two concurrent triage runs for the SAME incident, using the
    database itself (`SELECT ... FOR UPDATE`) as the authoritative,
    cross-process concurrency guard -- NOT a global Python lock, which
    would not help once/if this runs across more than one worker process.
  - keep a reference to every direct-triage background task so it cannot
    be garbage-collected mid-flight, and restore the incident's prior
    status if the background run fails (exception OR a normal return with
    saved_plan=False) -- without ever overwriting a newer legitimate
    status some other operation already moved the incident to, via a
    SINGLE atomic `UPDATE ... WHERE status = 'INVESTIGATING'` rather than
    a separate SELECT-then-UPDATE (which has a race window between the
    read and the write).

No Temporal/Celery/Redis/Kafka or other new infrastructure is introduced.
LangGraph's existing agent ordering (RCA -> {Impact, Runbook} -> Critic) is
completely untouched by this module.
"""
import asyncio
import os
from typing import Optional

from app.store.postgres_database import PostgresDatabase
from app.domain.enums import IncidentState
from app.domain.orchestrator import IncidentOrchestrator


def _db_url() -> str:
    return os.environ.get(
        "POSTGRES_URL", "postgresql://nemoguard:nemoguard_password@postgres:5432/nemoguard_db"
    )


# Fire-and-forget direct-triage background tasks, keyed by incident_id so a
# given incident's task can be inspected/discarded by id. A reference must
# be kept somewhere or asyncio may silently cancel a still-running task
# mid-flight; the module-level dict is the simplest fix (add on schedule,
# discard via an IDENTITY-SAFE done-callback on completion -- see
# _register_background_task() below). The DATABASE status transition
# (see schedule_triage()) remains the real cross-process dedup mechanism --
# this dict is purely for task lifetime / local introspection and does NOT
# need to be consulted to decide whether a triage is "already running" (a
# second worker process wouldn't share this dict at all).
_BACKGROUND_TRIAGE_TASKS: dict = {}

# Statuses that mean "an investigation or execution is already actively in
# flight for this incident" -- a new triage request must be rejected
# while any of these are current, rather than silently starting a second,
# concurrent investigation of the same incident.
_ACTIVE_STATUSES = {
    IncidentState.INVESTIGATING.value,
    IncidentState.EXECUTING.value,
    IncidentState.VERIFYING.value,
}


def _register_background_task(incident_id: str, task: "asyncio.Task") -> None:
    """
    Stores `task` under `incident_id` and arranges for it to be removed
    from `_BACKGROUND_TRIAGE_TASKS` on completion -- but ONLY if the dict
    still maps `incident_id` to THIS exact task object at that point
    (identity check). Without this check, if the dict entry for
    `incident_id` were ever overwritten by a newer task (e.g. a
    hypothetical future caller that bypasses the DB-level dedup guard),
    the OLD task's completion callback would incorrectly pop the NEW
    task's entry, losing the reference to a still-running task. This is
    defense-in-depth: the DB-level dedup in schedule_triage() is expected
    to prevent this situation from arising in practice, but the cleanup
    itself should not silently assume that invariant always holds.
    """
    _BACKGROUND_TRIAGE_TASKS[incident_id] = task

    def _on_done(completed_task: "asyncio.Task") -> None:
        if _BACKGROUND_TRIAGE_TASKS.get(incident_id) is completed_task:
            _BACKGROUND_TRIAGE_TASKS.pop(incident_id, None)

    task.add_done_callback(_on_done)


def _restore_if_still_investigating(db: PostgresDatabase, incident_id: str, previous_status: str) -> bool:
    """
    Atomically restores `incident.status` to `previous_status` in a
    SINGLE `UPDATE ... WHERE status = 'INVESTIGATING'` statement -- not a
    separate SELECT followed by an UPDATE, which would leave a race
    window between reading the status and writing it back (another
    operation could change the status in between, and the blind UPDATE
    would then overwrite that newer legitimate state). Using `WHERE
    status = 'INVESTIGATING'` as the predicate means this UPDATE only
    takes effect if the row is *still* exactly INVESTIGATING at the exact
    moment the UPDATE executes -- a normal SQL UPDATE's WHERE clause is
    evaluated atomically against the current row by the database engine
    itself, so there is no window for a concurrent writer to slip in
    between a "check" and a "write" the way there would be with two
    separate statements.

    Returns True if the row was actually restored (i.e. it was still
    INVESTIGATING), False if it had already moved on to something else
    (e.g. a human cancelled it while this ran) -- callers use this to
    make the audit-event text accurately reflect whether restoration
    actually happened.
    """
    with db.get_connection() as conn:
        cursor = conn.execute(
            "UPDATE incident SET status = %s WHERE incident_id = %s AND status = %s",
            (previous_status, incident_id, IncidentState.INVESTIGATING.value),
        )
        return cursor.rowcount > 0


async def _run_direct_triage(incident_id: str, previous_status: str) -> None:
    """
    Runs IncidentOrchestrator.triage_incident() (synchronous, uses
    asyncio.run() internally) on a worker thread via asyncio.to_thread(),
    which is safe because that thread has no event loop of its own for
    asyncio.run() to conflict with.

    Treats BOTH of the following as failure and attempts to restore the
    incident's prior (pre-triage) status rather than leaving it stuck in
    INVESTIGATING:
      Case A: triage_incident() raises an exception.
      Case B: triage_incident() returns normally but
              result.get("saved_plan") is False (both the LangGraph path
              AND its deterministic triage_fallback() failed).
    The restore is atomic and conditional (_restore_if_still_investigating)
    so it never overwrites a newer legitimate state; the audit message
    explicitly states whether the restoration actually happened or
    whether the incident had already moved on by the time this ran.
    """
    db = PostgresDatabase(_db_url())
    orchestrator = IncidentOrchestrator(_db_url())
    try:
        result = await asyncio.to_thread(orchestrator.triage_incident, incident_id)
        if not result or not result.get("saved_plan"):
            restored = _restore_if_still_investigating(db, incident_id, previous_status)
            restoration_note = (
                "Incident restored to its prior status for manual review."
                if restored
                else "Incident status had already changed before restoration could occur; no restoration was applied."
            )
            orchestrator._log_audit(
                incident_id, "SYSTEM", "TRIAGE_FAILED",
                "Direct triage completed without producing a usable recovery plan "
                "(both the primary investigation and its deterministic fallback failed). "
                f"{restoration_note}",
            )
    except Exception as e:
        # Full detail stays server-side in logs only; the audit event gets
        # a generic, operator-safe summary (never raw exception text).
        print(f"Direct triage for {incident_id} raised an exception: {e}")
        restored = _restore_if_still_investigating(db, incident_id, previous_status)
        restoration_note = (
            "Incident restored to its prior status for manual review."
            if restored
            else "Incident status had already changed before restoration could occur; no restoration was applied."
        )
        try:
            orchestrator._log_audit(
                incident_id, "SYSTEM", "TRIAGE_FAILED",
                f"Triage could not complete because of an internal error. {restoration_note}",
            )
        except Exception:
            pass


async def _start_temporal_workflow(temporal_client, incident_id: str) -> None:
    """
    Isolated so both `schedule_triage()` (existing incident) and
    `schedule_triage_for_new_incident()` (freshly-created incident) call
    the exact same `start_workflow()` invocation -- there is now only
    ONE place in the whole codebase that constructs this call. Raises on
    failure; callers are responsible for their own (different) restore
    semantics, since an existing incident's `previous_status` differs
    from a freshly-created incident's fallback status.
    """
    from app.domain.workflows.incident_workflow import IncidentLifecycleWorkflow
    await temporal_client.start_workflow(
        IncidentLifecycleWorkflow.run,
        incident_id,
        id=f"incident-{incident_id}",
        task_queue="incident-task-queue",
    )


def _log_scheduling_failure_audit(incident_id: str) -> None:
    """
    Emits a safe, generic TRIAGE_FAILED audit event for a Temporal
    scheduling failure (start_workflow() itself raised) -- never includes
    raw exception text; detailed exception information stays in
    server-side print() logs only (see the caller's own print() call
    immediately before this).
    """
    try:
        IncidentOrchestrator(_db_url())._log_audit(
            incident_id, "SYSTEM", "TRIAGE_FAILED",
            "Triage could not be scheduled because of an internal error "
            "while starting the investigation workflow.",
        )
    except Exception:
        pass


async def schedule_triage(temporal_client, incident_id: str) -> dict:
    """
    The ONE reusable triage-scheduling entry point for an EXISTING
    incident that may already be mid-lifecycle -- used by the manual
    /triage route. (A brand-new incident created by the webhook path has
    its own entry point, schedule_triage_for_new_incident(), below --
    see that function's docstring for why the same-incident dedup check
    performed here would incorrectly reject a just-created incident.)

    Phase 2 (race-safe same-incident deduplication): performs an atomic
    check-and-set against the database -- `SELECT status ... FOR UPDATE`
    followed by the status transition, inside a single transaction/
    connection -- so that even if this process runs with multiple workers,
    two near-simultaneous calls for the SAME incident cannot both observe
    a non-active status and both proceed. The second call's `FOR UPDATE`
    blocks until the first transaction commits, then sees the
    already-updated (active) status and is correctly rejected. Different
    incidents are never serialized against each other -- this lock is
    scoped to exactly one row.

    This function is `async def` specifically so that the Temporal
    `start_workflow()` call can be awaited and its failure handled INSIDE
    this function (not left to the caller) -- if Temporal accepts the
    scheduling decision (status flipped to INVESTIGATING) but then
    `await start_workflow()` itself raises (e.g. Temporal server
    unreachable mid-call, task queue misconfigured), an ATTEMPT is made
    to atomically restore `previous_status`; the returned dict's
    `"restored"` key truthfully reflects whether that attempt actually
    succeeded (the row might already have moved on to something else by
    the time the restore ran), so the API layer never claims a
    restoration happened when it did not.

    Returns one of:
      {"accepted": True,  "incident_id": ..., "status": "QUEUED_TEMPORAL"}
      {"accepted": True,  "incident_id": ..., "status": "QUEUED_DIRECT"}
      {"accepted": False, "incident_id": ..., "status": "TRIAGE_ALREADY_ACTIVE"}
      {"accepted": False, "incident_id": ..., "status": "INCIDENT_NOT_FOUND"}
      {"accepted": False, "incident_id": ..., "status": "TEMPORAL_START_FAILED", "restored": True|False}
    """
    db = PostgresDatabase(_db_url())
    with db.get_connection() as conn:
        cursor = conn.execute(
            "SELECT status FROM incident WHERE incident_id = %s FOR UPDATE",
            (incident_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"accepted": False, "incident_id": incident_id, "status": "INCIDENT_NOT_FOUND"}

        previous_status = row[0]
        if previous_status in _ACTIVE_STATUSES:
            return {"accepted": False, "incident_id": incident_id, "status": "TRIAGE_ALREADY_ACTIVE"}

        conn.execute(
            "UPDATE incident SET status = %s WHERE incident_id = %s",
            (IncidentState.INVESTIGATING.value, incident_id),
        )
    # The transaction above has committed (PostgresDatabase.get_connection()
    # commits on clean exit), releasing the row lock -- any concurrent
    # caller for this SAME incident_id will now see INVESTIGATING and be
    # rejected; a caller for a DIFFERENT incident_id was never blocked.

    if temporal_client:
        try:
            await _start_temporal_workflow(temporal_client, incident_id)
            return {"accepted": True, "incident_id": incident_id, "status": "QUEUED_TEMPORAL"}
        except Exception as e:
            # Full detail stays server-side in logs only; the audit event
            # (_log_scheduling_failure_audit) gets a generic, operator-safe
            # summary -- never raw exception text.
            print(f"schedule_triage: Temporal start_workflow() failed for {incident_id}: {e}")
            restored = _restore_if_still_investigating(db, incident_id, previous_status)
            _log_scheduling_failure_audit(incident_id)
            return {
                "accepted": False, "incident_id": incident_id,
                "status": "TEMPORAL_START_FAILED", "restored": restored,
            }

    # No Temporal: run the real investigation directly, in the background,
    # via a tracked asyncio.Task so the HTTP request returns immediately
    # (matching the existing Temporal-queued UX) while the task itself
    # cannot be garbage-collected mid-flight.
    task = asyncio.create_task(_run_direct_triage(incident_id, previous_status))
    _register_background_task(incident_id, task)

    return {"accepted": True, "incident_id": incident_id, "status": "QUEUED_DIRECT"}


async def schedule_triage_for_new_incident(temporal_client, incident_id: str, fallback_status: str) -> dict:
    """
    Triage-scheduling entry point for a BRAND-NEW incident that the
    webhook ingestion path (process_webhook()) has ALREADY inserted with
    status INVESTIGATING. There is nothing to deduplicate against for an
    incident that did not exist a moment ago, so this intentionally does
    NOT run schedule_triage()'s same-incident "already active" dedup
    check -- that check would incorrectly reject the very incident this
    call is meant to schedule, since process_webhook() already set its
    status to INVESTIGATING before this is ever called.

    `fallback_status` is the status to restore the incident to if
    scheduling fails (Temporal start_workflow() raises) or if the direct
    triage path itself fails -- the caller (ingest_webhook()) passes the
    incident's actual pre-INVESTIGATING lifecycle state
    (IncidentState.DETECTED.value), not a hardcoded guess.

    Unifies what was previously a split, duplicated code path: this is
    now the ONLY place that calls Temporal's start_workflow() for a
    webhook-created incident, with the exact same try/except + atomic
    conditional restore + safe audit event as schedule_triage() uses for
    an existing incident -- previously the webhook's Temporal branch
    called start_workflow() directly, unprotected, with no restoration on
    failure.

    Returns one of:
      {"accepted": True,  "incident_id": ..., "status": "QUEUED_TEMPORAL"}
      {"accepted": True,  "incident_id": ..., "status": "QUEUED_DIRECT"}
      {"accepted": False, "incident_id": ..., "status": "TEMPORAL_START_FAILED", "restored": True|False}
    """
    if temporal_client:
        try:
            await _start_temporal_workflow(temporal_client, incident_id)
            return {"accepted": True, "incident_id": incident_id, "status": "QUEUED_TEMPORAL"}
        except Exception as e:
            print(f"schedule_triage_for_new_incident: Temporal start_workflow() failed for {incident_id}: {e}")
            db = PostgresDatabase(_db_url())
            restored = _restore_if_still_investigating(db, incident_id, fallback_status)
            _log_scheduling_failure_audit(incident_id)
            return {
                "accepted": False, "incident_id": incident_id,
                "status": "TEMPORAL_START_FAILED", "restored": restored,
            }

    # No Temporal: same tracked direct-background triage path used today
    # for an existing incident, just entered via a different function so
    # the dedup check above is correctly skipped for a just-created row.
    task = asyncio.create_task(_run_direct_triage(incident_id, fallback_status))
    _register_background_task(incident_id, task)

    return {"accepted": True, "incident_id": incident_id, "status": "QUEUED_DIRECT"}
