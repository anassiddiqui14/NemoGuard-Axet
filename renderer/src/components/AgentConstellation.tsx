import React, { useMemo } from 'react';
import { Cpu, Eye, GitBranch, BookOpen, Shield, CheckCircle } from 'lucide-react';
import { motion } from 'framer-motion';
import type { AgentEvent } from '../hooks/useIncidentEvents';

interface Props {
  status: string;
  events: AgentEvent[];
}

type AgentState = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'INCONCLUSIVE';

interface AgentDef {
  key: string;
  name: string;
  icon: React.ReactNode;
  actors: string[];
  completedOn: string[];
  failedOn?: string[];
  // Terminal, but neither a success nor a genuine rejection: the review
  // could not complete at all (LLM/tool/runtime error on the backend).
  // Currently only used by Safety (SAFETY_VALIDATION_INCONCLUSIVE) -- see
  // computeAgentState()'s explicit handling below, which must never
  // present this as if it were FAILED or COMPLETED.
  inconclusiveOn?: string[];
  idleDesc: string;
}

const AGENT_DEFS: AgentDef[] = [
  { key: 'watcher', name: 'Watcher', icon: <Eye className="w-4 h-4" />, actors: ['Watcher Agent', 'Watcher_Agent'], completedOn: ['ALERT_CORRELATED'], idleDesc: 'Classifying and correlating incoming alerts' },
  { key: 'rca', name: 'RCA', icon: <Cpu className="w-4 h-4" />, actors: ['RCA Agent', 'RCA_Agent'], completedOn: ['HYPOTHESIS_CREATED'], idleDesc: 'Investigating logs to isolate the root cause' },
  { key: 'impact', name: 'Impact', icon: <GitBranch className="w-4 h-4" />, actors: ['Impact Agent', 'Dependency_Agent'], completedOn: ['IMPACT_CALCULATED'], idleDesc: 'Calculating downstream and business impact' },
  { key: 'runbook', name: 'Runbook', icon: <BookOpen className="w-4 h-4" />, actors: ['Runbook Agent', 'Runbook_Agent'], completedOn: ['RUNBOOK_RETRIEVED'], idleDesc: 'Matching incident to approved recovery procedures' },
  { key: 'safety', name: 'Safety', icon: <Shield className="w-4 h-4" />, actors: ['Safety Agent', 'Grounding_Critic'], completedOn: ['SAFETY_VALIDATION_PASSED'], failedOn: ['SAFETY_VALIDATION_FAILED'], inconclusiveOn: ['SAFETY_VALIDATION_INCONCLUSIVE'], idleDesc: 'Validating plan grounding and blast radius' },
  { key: 'verifier', name: 'Verifier', icon: <CheckCircle className="w-4 h-4" />, actors: ['Verifier', 'Executor'], completedOn: ['VERIFICATION_PASSED', 'ACTION_COMPLETED'], failedOn: ['VERIFICATION_FAILED'], idleDesc: 'Independently confirming recovery after execution' },
];

// Phase 5/7: audit events that mark the boundary of the CURRENT
// investigation run. Only events at-or-after the latest one of these
// (for this incident) are considered "current" when deriving agent
// state -- this prevents a prior investigation/re-investigation's
// RCA/Impact/Runbook/Safety progression events from leaking into the
// current run's displayed state (e.g. showing an agent as COMPLETED
// from a stale earlier run while a brand new run is still in progress).
// Historical events are never deleted; this is purely a display-scoping
// rule applied client-side.
const RUN_BOUNDARY_EVENT_TYPES = ['TRIAGE_STARTED', 'REINVESTIGATION_TRIGGERED'];

function _findCurrentRunStartTimestamp(events: AgentEvent[]): string | null {
  let latest: string | null = null;
  for (const e of events) {
    if (RUN_BOUNDARY_EVENT_TYPES.includes(e.event_type)) {
      if (!latest || e.timestamp > latest) latest = e.timestamp;
    }
  }
  return latest;
}

function computeAgentState(def: AgentDef, events: AgentEvent[], incidentStatus: string): { state: AgentState; lastMessage?: string } {
  const s = incidentStatus.toUpperCase();
  const isResolved = s === 'RESOLVED';

  // Scope to the current investigation run only (Phase 5/7): if a run
  // boundary event exists, ignore anything strictly before it so a prior
  // run's completion events can't make the CURRENT run's agents look
  // already finished.
  const runStart = _findCurrentRunStartTimestamp(events);
  const scopedEvents = runStart ? events.filter((e) => e.timestamp >= runStart) : events;

  const relevant = scopedEvents.filter((e) => def.actors.includes(e.source));
  if (relevant.length === 0) {
    // Once the incident is resolved, no agent should still be shown as
    // running/queued — previously agents with no emitted events for this
    // incident (e.g. Watcher on a fast auto-recovered incident) fell through
    // to QUEUED here, but that's fine; the real bug is below where agents
    // *with* events but no explicit completion event stayed RUNNING forever.
    if (isResolved) return { state: 'COMPLETED', lastMessage: def.idleDesc };

    // Phase 7: derive stage from REAL audit progress of the CURRENT run,
    // not merely from incident.status == INVESTIGATING. Previously every
    // non-verifier agent was unconditionally marked RUNNING for the
    // entire INVESTIGATING phase -- including when no triage had
    // actually started at all (e.g. Temporal unavailable and nothing was
    // scheduled). RCA is RUNNING only until its own HYPOTHESIS_CREATED
    // appears (handled in the `relevant.length > 0` branch below), and
    // Impact/Runbook/Safety stay QUEUED until their real prerequisites
    // (RCA, then both Impact+Runbook) have actually completed in the
    // CURRENT run -- never inferred as active purely from incident.status.
    const isInvestigatingPhase = ['INVESTIGATING', 'TRIAGING', 'CORRELATING'].includes(s);
    const isExecPhase = ['EXECUTING', 'VERIFYING'].includes(s);
    if (def.key === 'verifier' && isExecPhase) return { state: 'RUNNING' };

    // Watcher/correlation happens BEFORE an investigation run starts and
    // is never rerun during RCA/reinvestigation -- so once a current-run
    // boundary (TRIAGE_STARTED/REINVESTIGATION_TRIGGERED) exists at all,
    // Watcher's job for this run is implicitly already done, regardless
    // of whether its own ALERT_CORRELATED event (if any) falls before
    // that boundary and was therefore excluded from scopedEvents. This is
    // derived purely from run-boundary/lifecycle semantics -- it does NOT
    // reintroduce old Watcher events into scopedEvents. If no run
    // boundary exists yet (e.g. the incident is still CORRELATING and
    // triage hasn't started), Watcher legitimately has no completed work
    // for this run and remains RUNNING.
    if (def.key === 'watcher') {
      if (runStart) return { state: 'COMPLETED', lastMessage: def.idleDesc };
      return { state: 'RUNNING' };
    }

    if (isInvestigatingPhase) {
      const rcaCompleted = scopedEvents.some(
        (e) => AGENT_DEFS.find((d) => d.key === 'rca')?.actors.includes(e.source) && e.event_type === 'HYPOTHESIS_CREATED'
      );
      // Current-run completion of the two parallel branches that must
      // BOTH finish before Safety can start -- mirrors the backend
      // LangGraph ordering (RCA -> {Impact, Runbook} -> Safety) without
      // touching that graph itself. A branch counts as "complete" only
      // via its own terminal event inside scopedEvents (handled in the
      // `relevant.length > 0` path for that branch); from Safety's own
      // relevant.length===0 vantage point here, both must be checked
      // directly against scopedEvents.
      const impactCompleted = scopedEvents.some(
        (e) => AGENT_DEFS.find((d) => d.key === 'impact')?.actors.includes(e.source) && e.event_type === 'IMPACT_CALCULATED'
      );
      const runbookCompleted = scopedEvents.some(
        (e) => AGENT_DEFS.find((d) => d.key === 'runbook')?.actors.includes(e.source) && e.event_type === 'RUNBOOK_RETRIEVED'
      );

      if (def.key === 'rca') {
        // RCA is the first real agent to run once a triage run has
        // actually started (i.e. scopedEvents is non-empty because a
        // run-boundary event exists) OR, for backward compatibility with
        // runs that predate TRIAGE_STARTED existing, as soon as the
        // incident is in an investigating phase at all.
        return { state: 'RUNNING' };
      }
      if ((def.key === 'impact' || def.key === 'runbook') && rcaCompleted) {
        return { state: 'RUNNING' };
      }
      if (def.key === 'safety' && impactCompleted && runbookCompleted) {
        // Both parallel branches finished and Safety has no terminal
        // event of its own yet (that's this `relevant.length === 0`
        // branch) -- Safety is now actively running.
        return { state: 'RUNNING' };
      }
      // Impact/Runbook before RCA completes, and Safety until both
      // Impact and Runbook complete, remain QUEUED rather than falsely
      // showing as active.
      return { state: 'QUEUED' };
    }
    return { state: 'QUEUED' };
  }
  const last = relevant[relevant.length - 1];
  // Explicit INCONCLUSIVE handling (terminal, but neither success nor a
  // genuine rejection) -- checked BEFORE failedOn/completedOn so an
  // inconclusive Safety review can never fall through and be mistaken
  // for either. Must stay distinct from RUNNING too (it has its own
  // terminal event, so it's not still in progress).
  if (def.inconclusiveOn?.includes(last.event_type)) {
    // Neutral operator-facing wording -- must never read as "Safety
    // validation failed" / "safety concern found" / "rejected by
    // Safety". The backend's own event_summary for this event type may
    // already be neutral, but the display text here is intentionally
    // hardcoded rather than trusting `last.message` verbatim, so this
    // component's own wording guarantee doesn't depend on backend text
    // staying neutral forever.
    return { state: 'INCONCLUSIVE', lastMessage: 'Safety review could not complete — manual review required' };
  }
  if (def.failedOn?.includes(last.event_type)) return { state: 'FAILED', lastMessage: last.message };
  if (def.completedOn.includes(last.event_type)) return { state: 'COMPLETED', lastMessage: last.message };
  // The agent's last recorded event wasn't an explicit "completed" event type
  // (this happens for auto-resolved incidents where the pipeline short-circuits
  // and never emits every agent's terminal event). If the incident has already
  // resolved, treat every agent as COMPLETED rather than leaving it stuck on
  // RUNNING indefinitely.
  if (isResolved) return { state: 'COMPLETED', lastMessage: last.message };
  return { state: 'RUNNING', lastMessage: last.message };
}

const STATE_STYLES: Record<AgentState, { ring: string; bg: string; text: string; glow: string }> = {
  QUEUED: { ring: 'ring-white/[0.06]', bg: 'bg-white/[0.02]', text: 'text-text-muted', glow: '' },
  RUNNING: { ring: 'ring-agent-active/40', bg: 'bg-gradient-to-br from-agent-active/15 to-primary/5', text: 'text-agent-active', glow: 'shadow-lg shadow-agent-active/20' },
  COMPLETED: { ring: 'ring-healthy/30', bg: 'bg-healthy/[0.06]', text: 'text-healthy', glow: '' },
  FAILED: { ring: 'ring-critical/40', bg: 'bg-critical/[0.08]', text: 'text-critical', glow: '' },
  // Neutral/warning treatment -- deliberately NOT the same red/critical
  // styling as FAILED. An inconclusive safety review is "manual review
  // required", not "Safety rejected this plan" -- the visual language
  // must not imply a genuine rejection occurred.
  INCONCLUSIVE: { ring: 'ring-warning/40', bg: 'bg-warning/[0.08]', text: 'text-warning', glow: '' },
};

/*
 * UI MODERNIZATION (spec Section 43/44 -- "NemoGuard Investigation"
 * branded agent loader): the grid-card AgentConstellation above already
 * satisfies the spec's core requirement -- truthful, SSE-driven agent
 * state with zero fabricated progress -- but Section 43's specific
 * mockup shows a compact vertical checklist (✓/●/○ per step) rather than
 * a card grid. Rather than duplicate the real state computation
 * (computeAgentState) in a second component, this reuses the exact same
 * function and just renders it in the checklist layout the spec
 * describes, for contexts (e.g. a narrow sidebar or a modal) where the
 * grid doesn't fit.
 */
export const InvestigationChecklist: React.FC<Props> = ({ status, events }) => {
  const s = status?.toUpperCase() || '';
  const agents = AGENT_DEFS.map((def) => {
    const { state, lastMessage } = computeAgentState(def, events, s);
    return { ...def, state, lastMessage: lastMessage || def.idleDesc };
  });

  return (
    <div className="rounded-xl p-4 bg-white/[0.02] ring-1 ring-white/[0.05]">
      <div className="text-[11px] font-bold text-text-muted uppercase tracking-wider mb-3">NemoGuard Investigation</div>
      <div className="space-y-2">
        {agents.map((agent) => {
          const marker =
            agent.state === 'COMPLETED' ? '✓'
              : agent.state === 'FAILED' ? '✕'
              : agent.state === 'INCONCLUSIVE' ? '!'
              : agent.state === 'RUNNING' ? '●'
              : '○';
          const markerClass =
            agent.state === 'COMPLETED'
              ? 'text-healthy'
              : agent.state === 'FAILED'
                ? 'text-critical'
                : agent.state === 'INCONCLUSIVE'
                  ? 'text-warning'
                  : agent.state === 'RUNNING'
                    ? 'text-agent-active'
                    : 'text-text-muted';
          return (
            <div key={agent.key} className="flex items-start gap-2.5">
              <span className={`font-mono text-[12px] font-bold flex-shrink-0 w-4 text-center ${markerClass} ${agent.state === 'RUNNING' ? 'animate-pulse' : ''}`}>
                {marker}
              </span>
              <div className="min-w-0 flex-1">
                <span className={`text-[12px] ${agent.state === 'QUEUED' ? 'text-text-muted' : 'text-text-secondary'}`}>{agent.lastMessage}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export const AgentConstellation: React.FC<Props> = ({ status, events }) => {
  const s = status?.toUpperCase() || '';

  const agents = useMemo(() => {
    return AGENT_DEFS.map((def) => {
      const { state, lastMessage } = computeAgentState(def, events, s);
      return { ...def, state, lastMessage: lastMessage || def.idleDesc };
    });
  }, [events, s]);

  const activeCount = agents.filter((a) => a.state === 'RUNNING').length;

  return (
    <div className="flex flex-col h-full">
      <div className="grid grid-cols-3 gap-2.5 content-start">
        {agents.map((agent, idx) => {
          const style = STATE_STYLES[agent.state];
          const isRunning = agent.state === 'RUNNING';
          return (
            <motion.div
              key={agent.key}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: idx * 0.05 }}
              className={`relative rounded-xl p-3 ring-1 ${style.ring} ${style.bg} ${style.glow} flex flex-col items-center text-center transition-all duration-300`}
            >
              {isRunning && (
                <motion.div
                  className="absolute inset-0 rounded-xl ring-2 ring-agent-active/50"
                  animate={{ opacity: [0.6, 0.1, 0.6] }}
                  transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
                />
              )}
              <div className={`w-9 h-9 rounded-lg flex items-center justify-center mb-2 ${style.text} ${agent.state === 'RUNNING' ? 'bg-agent-active/20' : agent.state === 'COMPLETED' ? 'bg-healthy/15' : agent.state === 'FAILED' ? 'bg-critical/15' : agent.state === 'INCONCLUSIVE' ? 'bg-warning/15' : 'bg-white/[0.04]'}`}>
                {agent.icon}
              </div>
              <div className="text-[11.5px] font-semibold text-text-primary mb-0.5">{agent.name}</div>
              <div className={`text-[9px] font-bold uppercase tracking-wide ${style.text}`}>{agent.state}</div>
              <div className="text-[9.5px] text-text-muted mt-1.5 line-clamp-2 leading-tight break-words w-full" title={agent.lastMessage}>
                {agent.lastMessage}
              </div>
            </motion.div>
          );
        })}
      </div>
      <div className="flex justify-between items-center pt-3 mt-3 border-t border-white/[0.05] text-[11px]">
        <span className="text-text-muted font-medium">Coordinated agents</span>
        <span className={`font-semibold px-2.5 py-1 rounded-full text-[10px] ${activeCount > 0 ? 'bg-agent-active/15 text-agent-active' : 'bg-white/[0.03] text-text-muted'}`}>
          {activeCount} active / {agents.length} total
        </span>
      </div>
    </div>
  );
};
