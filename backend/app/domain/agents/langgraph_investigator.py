from typing import TypedDict, Dict, Any, List
import asyncio
from langgraph.graph import StateGraph, END, START

from app.domain.agents.rca_agent import RCAAgent
from app.domain.agents.dependency_agent import DependencyAgent
from app.domain.agents.runbook_agent import RunbookAgent
from app.domain.agents.base_agent import BaseAgent
from app.domain.agents.agent_tools import _get_read_only_tools_schema, execute_tool_call

class InvestigationState(TypedDict):
    incident_id: str
    alerts: List[Dict[str, Any]]

    # Optional operator-provided guidance/confirmed-fact/rejection-feedback
    # context (e.g. from Commander Live or a rejected-plan resubmission),
    # carried through the graph so RCA (and, transitively, the Grounding
    # Critic) actually reason over it rather than it only ever being
    # persisted to the database. Empty string for a normal first-pass
    # triage -- this is purely additive and never required.
    feedback_context: str

    rca_result: Dict[str, Any]
    impact_result: Dict[str, Any]
    runbook_result: Dict[str, Any]
    
    critic_passed: bool
    # Explicit three-way safety-review outcome, distinct from the boolean
    # `critic_passed` above (which is retained for backward compatibility
    # with existing callers/consumers). "FAILED" means the Grounding
    # Critic actually evaluated the plan and rejected it (a genuine safety
    # judgment); "INCONCLUSIVE" means the critic could not complete its
    # review at all (LLM/tool-budget/transport failure) -- this must NEVER
    # be treated as equivalent to a real safety rejection, but a plan with
    # an inconclusive review must also never be treated as PASSED. See
    # GroundingCritic.analyze()/run_critic() below for how this is derived.
    critic_status: str  # "PASSED" | "FAILED" | "INCONCLUSIVE"
    critic_feedback: str
    final_plan: Dict[str, Any]

import os

# Structural (code-level, not just prompt-level) enforcement of the
# "staleness check before rerun" data-integrity policy. The LLM is asked to
# follow this ordering in its prompt (see RunbookAgent), but LLMs can still
# skip steps under pressure/ambiguity -- this function re-validates the
# ACTUAL plan steps that come back and forces passed=False if the policy
# was violated, regardless of what the LLM itself claimed.
LOCALSTACK_LAB_ENABLED = os.environ.get("NEMOGUARD_LOCALSTACK_LAB", "0") == "1"


def _plan_violates_data_integrity_policy(rca: Dict, steps: List[Dict]) -> str:
    """Returns a non-empty violation message if this looks like a
    write-job incident (RCA mentions a known lab write-target table or
    cause_type PARTIAL_WRITE) and the proposed steps rerun the job
    without a preceding check_table_staleness step. Empty string means no
    violation detected (either not a write-job incident, or the ordering
    is correct)."""
    if not LOCALSTACK_LAB_ENABLED:
        return ""

    finding = (rca.get("finding") or "").lower()
    cause_type = (rca.get("cause_type") or "").lower()
    write_table_mentioned = "order_events" in finding or cause_type == "partial_write"
    if not write_table_mentioned:
        return ""

    step_texts = [ (s.get("action") or "").lower() + " " + (s.get("tool") or "").lower() for s in steps ]
    has_staleness_check = any("staleness" in t or "check_table_staleness" in t for t in step_texts)
    has_rerun = any("rerun" in t or "re-run" in t for t in step_texts)

    if has_rerun and not has_staleness_check:
        return (
            "Plan reruns a job that writes to 'order_events' without a preceding "
            "check_table_staleness step. This risks double-writing rows from a prior "
            "partial write. A staleness check (and cleanup_partial_write if stale) MUST "
            "precede any rerun step for write-jobs."
        )
    return ""


# The LocalStack-specific data-integrity ordering policy (check staleness ->
# cleanup -> rerun -> verify) only makes sense when the LocalStack lab execution
# tools it references actually exist. In normal aXet deployments those tools are
# not registered, so instructing the critic to require them would either make it
# fabricate evidence or reject otherwise-sound plans for a capability that was
# never available in the first place. This section is built once at module import
# time (it only depends on the static LOCALSTACK_LAB_ENABLED flag) and interpolated
# into the critic's system prompt below.
if LOCALSTACK_LAB_ENABLED:
    _LOCALSTACK_POLICY_SECTION = """
DATA-INTEGRITY POLICY: if the RCA finding indicates the failing job writes to a database
table (e.g. "order_events"), the final plan's steps MUST check for a stale/partial write
(check_table_staleness) BEFORE any step that reruns the job, and clean up
(cleanup_partial_write) first if staleness was detected. If the runbook's proposed steps
violate this ordering, you MUST set "passed": false and explain the violation in "feedback",
and STILL reorder/fix the steps in "final_plan" so the corrected version follows the required
ordering (check -> cleanup if needed -> rerun -> verify) rather than passing through the
unsafe plan unchanged."""
else:
    _LOCALSTACK_POLICY_SECTION = """
LOCALSTACK/LAB CAPABILITY NOTICE: the LocalStack lab execution environment is NOT available
in this deployment. Tools such as check_table_staleness, cleanup_partial_write,
rerun_ingest_job, and verify_row_count_matches_expected do not exist here and MUST NOT be
added to, or required in, the final plan merely to satisfy the lab's data-integrity ordering
policy. When a remediation step cannot be executed through an available capability, keep it
as a clearly-labeled human/manual recovery instruction instead of inventing a tool call. The
mere absence of LocalStack capability must NOT, by itself, cause you to reject an otherwise
grounded and well-evidenced plan. Do not fabricate infrastructure evidence -- never claim you
checked table staleness, cleaned up a partial write, or verified a row count unless a real
tool call actually returned that result."""


class GroundingCritic(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Grounding_Critic",
            # Final safety/grounding gate before a plan reaches a human approver — route to the
            # stronger model since this is the last automated check standing between the AI's
            # output and a real recovery action.
            model="nvidia/nemotron-3-ultra-550b-a55b",
            system_prompt=f"""You are the Grounding Critic -- the final independent safety/verification
gate before a recovery plan reaches a human approver (spec: "Grounding and Safety Critic").

CRITICAL: you are a VERIFIER, not an actor. You have access to READ-ONLY diagnostic tools
(query_logs, get_cmdb_context, get_runbook, and when the LocalStack lab is enabled: real
CloudWatch/S3/Lambda/RDS/ECS/SQS/etc. observability tools, check_table_staleness,
verify_row_count_matches_expected, list_recent_changes). You do NOT have access to any
write/action tool (cleanup_partial_write, rerun jobs, etc.) -- you can only look, never act.

Use these tools to INDEPENDENTLY verify the RCA/Impact/Runbook agents' claims rather than
just trusting their text. For example: if the RCA claims a specific log line or CloudWatch
error, you may re-run query_logs or query_cloudwatch_logs yourself to confirm it's really
there. If a claim cannot be independently confirmed, treat it as weaker evidence and say so
in your feedback.
{_LOCALSTACK_POLICY_SECTION}

Once you have independently verified what you can, return ONLY valid JSON in this exact
format as your final response (do not wrap it in markdown):
{{
  "passed": true|false,
  "feedback": "Your critique here, including anything you independently verified or could not verify",
  "final_plan": {{
    "rationale": "Combined summary of RCA, impact and runbook",
    "expected_outcome": "System restored",
    "risk": "MEDIUM",
    "steps": [
       {{"action": "Step action", "tool": "manual", "risk": "LOW"}}
    ]
  }}
}}"""
        )
        
    async def analyze(self, incident_id: str, rca: Dict, impact: Dict, runbook: Dict, feedback_context: str = "") -> Dict:
        # When present, feedback_context carries the same operator-provided
        # guidance/confirmed-fact (or rejection-feedback) text that was fed
        # into RCA for this investigation run. Surfacing it to the critic
        # too ensures the final plan isn't accidentally validated as
        # "grounded" while silently contradicting an authoritative human
        # confirmed fact that RCA was asked to treat as ground truth.
        feedback_section = f"\n\n{feedback_context}\n" if feedback_context else ""
        prompt = f"""
        AUTHORITATIVE INCIDENT ID: {incident_id}

        RCA Result: {rca}
        IMPACT Result: {impact}
        RUNBOOK Result: {runbook}
        {feedback_section}
        Critique the findings for evidence grounding. Use your read-only tools to
        independently verify any specific factual claims you can (e.g. re-check a log line,
        a CloudWatch metric, or a table's staleness) before combining everything into a
        final recovery plan.

        IMPORTANT:
        - When calling `query_logs`, use exactly the AUTHORITATIVE INCIDENT ID supplied above.
        - Never invent, substitute, shorten, or use example incident IDs.
        - Example IDs appearing in tool schemas such as `INC-12345` are documentation examples
          only and must never be used as the actual query argument.
        - If operator-provided context above is present, verify the final_plan does not
          contradict an authoritative HUMAN CONFIRMED FACT; if the runbook/RCA plan conflicts
          with it, flag that in "feedback" and correct "final_plan" accordingly.
        """
        result = await self.call_llm_with_tools(prompt, _get_read_only_tools_schema(), execute_tool_call)

        # Structural re-check: don't just trust the LLM's own "passed" claim.
        final_plan = result.get("final_plan") or {}
        violation = _plan_violates_data_integrity_policy(rca, final_plan.get("steps") or [])
        if violation:
            result["passed"] = False
            existing_feedback = result.get("feedback") or ""
            result["feedback"] = (existing_feedback + " " if existing_feedback else "") + f"[POLICY ENFORCEMENT] {violation}"

        return result

class LangGraphInvestigator:
    def __init__(self):
        self.rca_agent = RCAAgent()
        self.dep_agent = DependencyAgent()
        self.runbook_agent = RunbookAgent()
        self.critic = GroundingCritic()
        self.graph = self._build_graph()
        
    def _build_graph(self):
        workflow = StateGraph(InvestigationState)
        
        # Define nodes
        async def run_rca(state: InvestigationState):
            res = await self.rca_agent.analyze(state["incident_id"], feedback_context=state.get("feedback_context", ""))
            return {"rca_result": res}
            
        async def run_impact(state: InvestigationState):
            # Impact analysis is only meaningful once we actually know the root cause — running
            # this in parallel with RCA (as before) meant the Impact Agent was guessing blindly
            # off a placeholder string like "Investigating incident X" instead of the real
            # finding. We now run RCA first and feed its concrete finding into the Impact Agent,
            # so impact/blast-radius reasoning is grounded in actual evidence rather than generic
            # incident metadata.
            rca_finding = state.get("rca_result", {}).get("finding") or f"Investigating incident {state['incident_id']}"
            res = await self.dep_agent.analyze(rca_finding)
            return {"impact_result": res}
            
        async def run_runbook(state: InvestigationState):
            # Likewise, runbook selection should be informed by the confirmed root cause (not
            # just the raw alert list) so the retrieved procedure actually matches the failure
            # mode instead of a keyword-only match against alert text.
            rca_finding = state.get("rca_result", {}).get("finding", "")
            res = await self.runbook_agent.analyze(state.get("alerts", []), rca_finding=rca_finding)
            return {"runbook_result": res}
            
        async def run_critic(state: InvestigationState):
            rca = state.get("rca_result", {}) or {}
            impact = state.get("impact_result", {}) or {}
            runbook = state.get("runbook_result", {}) or {}

            res = await self.critic.analyze(
                state["incident_id"], rca, impact, runbook,
                feedback_context=state.get("feedback_context", ""),
            )

            final_plan = res.get("final_plan") or {}

            # Three-way safety-review outcome (Phase 11): distinguish a
            # GENUINE safety rejection (the critic actually evaluated the
            # plan and returned "passed": false) from an INCONCLUSIVE
            # review (the critic call itself failed/errored -- LLM
            # timeout, malformed response even after the forced final
            # synthesis turn, tool/runtime exception -- so no real safety
            # judgment was ever rendered). An inconclusive review must
            # NEVER be reported as "the critic found this unsafe"; it must
            # also never be treated as PASSED. Both FAILED and
            # INCONCLUSIVE keep critic_passed=False for backward
            # compatibility with any existing boolean consumers, but
            # critic_status lets callers (orchestrator audit logging,
            # Commander messaging) react correctly to the difference.
            critic_errored = "error" in res
            if critic_errored:
                critic_status = "INCONCLUSIVE"
                critic_feedback = (
                    res.get("feedback", "")
                    or "Grounding Critic could not complete its safety review (LLM/tool error); "
                       "no safety verdict was rendered. Plan was synthesized from raw agent findings as a fallback."
                )
            elif res.get("passed", False):
                critic_status = "PASSED"
                critic_feedback = res.get("feedback", "")
            else:
                critic_status = "FAILED"
                critic_feedback = res.get("feedback", "") or "Grounding Critic evaluated the plan and rejected it."

            # The critic call can fail outright (LLM error, malformed/truncated JSON — see
            # BaseAgent.call_llm_json) or return valid JSON but with an empty/missing
            # final_plan. Either way we'd otherwise silently persist a plan with a blank
            # rationale and zero steps, which looks like a successful triage but gives the
            # operator nothing to approve or execute. If final_plan has no usable steps,
            # fall back to synthesizing one directly from the runbook agent's own
            # recommended steps (which are already evidence-grounded) plus the RCA finding,
            # so there's always a concrete, actionable plan even if the critic step itself
            # degraded.
            if not final_plan.get("steps"):
                fallback_steps = runbook.get("steps") or []
                final_plan = {
                    "rationale": final_plan.get("rationale") or rca.get("finding") or runbook.get("finding") or "Automated recovery formulation based on agent findings.",
                    "expected_outcome": final_plan.get("expected_outcome") or "System restored to normal operating state.",
                    "risk": final_plan.get("risk") or "MEDIUM",
                    "steps": fallback_steps,
                }

            return {
                "critic_passed": critic_status == "PASSED",
                "critic_status": critic_status,
                "critic_feedback": critic_feedback,
                "final_plan": final_plan
            }
            
        # Add nodes
        workflow.add_node("rca", run_rca)
        workflow.add_node("impact", run_impact)
        workflow.add_node("runbook", run_runbook)
        workflow.add_node("critic", run_critic)
        
        # Edges: RCA must complete first since impact/runbook grounding depends on its finding
        # (see comments on run_impact/run_runbook above). Impact and runbook then run in
        # parallel off the same confirmed root cause before both feed into the critic.
        workflow.add_edge(START, "rca")
        workflow.add_edge("rca", "impact")
        workflow.add_edge("rca", "runbook")
        
        workflow.add_edge("impact", "critic")
        workflow.add_edge("runbook", "critic")
        
        workflow.add_edge("critic", END)
        
        return workflow.compile()
        
    async def investigate(
        self, incident_id: str, alerts: List[Dict] = None, audit_callback=None,
        feedback_context: str = "",
    ):
        if alerts is None:
            alerts = []
        state = {
            "incident_id": incident_id,
            "alerts": alerts,
            "feedback_context": feedback_context,
            "rca_result": {},
            "impact_result": {},
            "runbook_result": {},
            "critic_passed": False,
            "critic_status": "INCONCLUSIVE",
            "critic_feedback": "",
            "final_plan": {}
        }
        
        final_state = dict(state)
        async for event in self.graph.astream(state):
            for node_name, updates in event.items():
                # Merge updates into our running state
                final_state.update(updates)
                
                # Fire live audit logs
                if audit_callback:
                    if node_name == "rca":
                        audit_callback(incident_id, "RCA Agent", "HYPOTHESIS_CREATED", "RCA Agent isolated root cause based on execution logs.")
                    elif node_name == "impact":
                        audit_callback(incident_id, "Impact Agent", "IMPACT_CALCULATED", "Impact Agent identified downstream dependencies from CMDB.")
                    elif node_name == "runbook":
                        audit_callback(incident_id, "Runbook Agent", "RUNBOOK_RETRIEVED", "Runbook Agent matched incident to standard recovery steps.")
                    elif node_name == "critic":
                        # Three-way audit emission: a genuine FAILED verdict
                        # (the critic actually evaluated and rejected the
                        # plan) is reported distinctly from an INCONCLUSIVE
                        # review (the critic could not complete its
                        # evaluation at all) -- the latter must never be
                        # logged/worded as if the critic found a problem.
                        critic_status = updates.get("critic_status", "INCONCLUSIVE")
                        if critic_status == "PASSED":
                            audit_callback(incident_id, "Safety Agent", "SAFETY_VALIDATION_PASSED", "Safety Agent validated plan parameters and verified blast radius is contained.")
                        elif critic_status == "FAILED":
                            audit_callback(incident_id, "Safety Agent", "SAFETY_VALIDATION_FAILED", f"Safety Agent rejected plan: {updates.get('critic_feedback')}")
                        else:
                            audit_callback(incident_id, "Safety Agent", "SAFETY_VALIDATION_INCONCLUSIVE", f"Safety Agent could not complete its review: {updates.get('critic_feedback')}")

        return final_state
