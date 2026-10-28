"""
Compatibility bridge: maps EXISTING free-text action_step rows (tool_name /
action_type strings already being produced by the RCA/Runbook/Commander
agents' prompts) into typed ActionIntent objects the Plan Compiler can
consume.

This lets the generic execution engine work TODAY against the plans
already being generated, without first having to rewrite every agent
prompt to emit structured ActionIntent JSON (that prompt-level migration
is tracked separately — see docs/IMPLEMENTATION_PLAN_FROM_GPT_SPEC.md).

Mapping is deterministic and keyword-based; anything unrecognized safely
falls through to the ops.manual_step capability via plan_compiler's
default fallback (never silently dropped, never executed as a guess).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .models import ActionIntent

# LocalStack lab execution tools (check_table_staleness, cleanup_partial_write,
# rerun_ingest_job, verify_row_count_matches_expected, and the write-job rerun
# capability) only exist when the LocalStack lab is actually available. In normal
# aXet deployments none of these are registered, so intents that resolve to a
# lab-only capability must be deliberately downgraded to MANUAL rather than being
# compiled against a capability that doesn't exist.
LOCALSTACK_LAB_ENABLED = os.environ.get("NEMOGUARD_LOCALSTACK_LAB", "0") == "1"

_LAB_ONLY_INTENT_TYPES = {
    "CHECK_TABLE_STALENESS",
    "CLEANUP_PARTIAL_WRITE",
    "RERUN_WRITE_JOB",
    "RERUN_INGEST_JOB",
    "VERIFY_ROW_COUNT",
}


# Exact tool_name matches take priority over fuzzy text matching, since
# agents frequently name their tool_name field after the literal
# capability they intend (e.g. "check_table_staleness",
# "cleanup_partial_write") -- this is a far more reliable signal than
# scanning the free-text action_type/reasoning, which can legitimately
# mention multiple related concepts (e.g. a staleness-check step's
# rationale text mentioning "partial writes" as context) without that
# meaning the step's actual TOOL is the other capability.
_EXACT_TOOL_NAME_MAP = {
    "check_table_staleness": "CHECK_TABLE_STALENESS",
    "cleanup_partial_write": "CLEANUP_PARTIAL_WRITE",
    "idempotent_rerun_order_events_job": "RERUN_WRITE_JOB",
    "rerun_ingest_job": "RERUN_INGEST_JOB",
    "verify_row_count_matches_expected": "VERIFY_ROW_COUNT",
}


def _keyword_intent_type(tool_name: str, action_type: str) -> str:
    normalized_tool_name = (tool_name or "").strip().lower()
    if normalized_tool_name in _EXACT_TOOL_NAME_MAP:
        return _EXACT_TOOL_NAME_MAP[normalized_tool_name]

    text = f"{tool_name} {action_type}".lower()
    if "cleanup" in text or "clean up" in text:
        return "CLEANUP_PARTIAL_WRITE"
    if "order_events" in text or ("rerun" in text and "order" in text):
        return "RERUN_WRITE_JOB"
    if "staleness" in text or "stale" in text:
        return "CHECK_TABLE_STALENESS"
    if "verify" in text and "row" in text:
        return "VERIFY_ROW_COUNT"
    if "rerun" in text or "retry" in text or "re-run" in text or "re-trigger" in text or "trigger" in text:
        return "RERUN_INGEST_JOB"
    return "MANUAL"  # deliberately unmapped -> ops.manual_step fallback


def action_step_to_intent(action_step: Dict[str, Any], incident_run_id: str = "", target_resource_id: str = "") -> ActionIntent:
    """
    action_step: a dict with at least action_type, tool_name, parameters_json
    (as already stored in the action_step table).
    """
    tool_name = action_step.get("tool_name", "") or ""
    action_type = action_step.get("action_type", "") or ""
    intent_type = _keyword_intent_type(tool_name, action_type)

    try:
        parameters = json.loads(action_step.get("parameters_json") or "{}")
    except Exception:
        parameters = {}

    # Lab-only capabilities (check_table_staleness, cleanup_partial_write,
    # rerun_ingest_job, verify_row_count_matches_expected, and the write-job
    # rerun capability) do not exist outside the LocalStack lab. Rather than
    # compiling an intent against a capability that was never registered,
    # degrade it to MANUAL so an operator still sees exactly what was
    # proposed (via `reason`) without the engine inventing an execution
    # target or silently dropping the step.
    if intent_type in _LAB_ONLY_INTENT_TYPES and not LOCALSTACK_LAB_ENABLED:
        intent_type = "MANUAL"

    # Only the run_id-scoped lab capabilities get run_id defaulted — this is
    # safe regardless of LocalStack availability since it's just plumbing
    # the already-known incident run_id through, not inventing a resource.
    if intent_type in ("CHECK_TABLE_STALENESS", "CLEANUP_PARTIAL_WRITE", "RERUN_WRITE_JOB", "VERIFY_ROW_COUNT", "RERUN_INGEST_JOB"):
        parameters.setdefault("run_id", incident_run_id or parameters.get("run_id", ""))

    # table_name must NEVER be silently defaulted to "order_events" for a
    # generic incident/resource — that was guessing a specific lab table for
    # potentially unrelated production tables. The LocalStack lab only
    # actually supports "order_events"; any other explicit table name
    # (e.g. "customer_profile") must NOT bypass this allowlist by being
    # passed straight through as if it were executable. A table-oriented
    # LocalStack capability may only stay executable when the supported
    # table is explicitly "order_events" -- either because
    # parameters["table_name"] already says so, or because the action/tool
    # text explicitly mentions "order_events" and no other explicit table
    # was given. Any other explicit table, or no explicit supported table
    # at all, degrades to MANUAL rather than guessing or silently executing
    # against an unsupported table.
    if intent_type in ("CHECK_TABLE_STALENESS", "CLEANUP_PARTIAL_WRITE", "RERUN_WRITE_JOB", "VERIFY_ROW_COUNT"):
        explicit_table_name = parameters.get("table_name")
        text = f"{tool_name} {action_type}".lower()
        if explicit_table_name == "order_events":
            pass  # explicitly supported table -> keep the lab intent
        elif not explicit_table_name and "order_events" in text:
            parameters["table_name"] = "order_events"
        else:
            # Either an explicit but unsupported table (e.g. "customer_profile"),
            # or no explicit supported table at all -- never silently execute,
            # never guess.
            intent_type = "MANUAL"

    if intent_type == "MANUAL":
        # A MANUAL intent is a human/operator action, never a concrete AWS or
        # database resource. Deriving target metadata from the generic
        # fallback below would make it look like it targets an
        # AWS_LAMBDA_FUNCTION, which is misleading and could be mistaken for
        # an executable capability.
        final_target_resource_type = "HUMAN_ACTION"
        final_target_resource_id = parameters.get("table_name") or parameters.get("run_id") or "unspecified"
    else:
        final_target_resource_type = "POSTGRES_TABLE" if "TABLE" in intent_type or "WRITE" in intent_type else "AWS_LAMBDA_FUNCTION"
        final_target_resource_id = target_resource_id or parameters.get("table_name") or parameters.get("run_id") or "unknown"

    return ActionIntent(
        intent_type=intent_type,
        target_resource_type=final_target_resource_type,
        target_resource_id=final_target_resource_id,
        reason=action_type or tool_name,
        parameters=parameters,
        evidence_ids=[],
        expected_effect=action_type,
    )


def action_steps_to_intents(action_steps: List[Dict[str, Any]], incident_run_id: str = "") -> List[ActionIntent]:
    return [action_step_to_intent(s, incident_run_id=incident_run_id) for s in action_steps]
