# NemoGuard V22 — Leadership Demo Guide

## Demo objective

Show the long-term vision without overstating current integration access:

> NemoGuard can already become the place a production-support team checks first to understand what is happening, what needs attention, what the system knows, what it is missing, and what should happen next. Broader integrations and autonomous action come after that understanding is proven.

Use a sanitized/approved forwarded production alert for the live path. Use Scenario Lab only for synthetic action demonstrations.

## Recommended 12–15 minute flow

### 1. Start on Production Overview

Open **Production Overview** and spend ~60 seconds here.

Call out:

- Commander Brief
- Shift Brief
- Needs Attention
- What NemoGuard Needs / Investigation Coverage
- real KPIs
- System Health
- Agent Activity
- Recent Investigations
- Live Operations

Suggested narrative:

> "The goal is not another monitoring dashboard. This is the operator's starting point. NemoGuard reduces everything it currently knows into what requires human attention and, just as importantly, tells us where it does not have enough context."

### 2. Explain the current approved live boundary

Show **Alert Inbox** / mailbox status.

Suggested narrative:

> "I do not yet have approval to connect NemoGuard directly to every production application. The approved live intake today is my organizational mailbox. I can forward production-system alerts into a dedicated folder, and NemoGuard treats that as an untrusted source rather than privileged telemetry."

Important trust point:

- forwarded email is source-reported evidence
- it is retained/reviewable
- retries are idempotent
- email-only evidence does not become authority to change production

### 3. Forward one real approved alert

Forward a sanitized alert into the monitored Outlook folder.

Show it arriving in **Alert Inbox**.

If timing is unpredictable during a presentation, pre-stage one new item immediately before the demo and keep a known persisted incident as the fallback.

### 4. Show classification and correlation

Explain that Watcher:

- decides whether the message is a valid operational alert
- normalizes severity/source/type/message
- compares against active incidents
- correlates where appropriate
- creates a new incident for sufficiently important unmatched signals

Do not claim deterministic source integration when the source is email.

### 5. Open the incident Command Center

Show:

- lifecycle state
- correlated alert(s)
- evidence
- Agent Constellation
- RCA hypotheses
- impact/dependency findings when available
- runbook grounding when available
- Investigation Coverage

Suggested narrative:

> "The interesting part is not that an LLM generated an answer. The interesting part is that NemoGuard separates the claim from the evidence and exposes what would make the investigation stronger."

### 6. Focus on "What NemoGuard Needs"

This is the self-improvement glimpse.

Examples the UI may surface:

- independent runtime evidence unavailable
- no job/run identifier
- no dependency context
- no runbook
- owner unknown
- RCA requested additional evidence

Suggested narrative:

> "Today self-improvement means self-assessment, not self-modifying code. NemoGuard identifies the gaps that constrained this investigation so the team knows exactly which integration, runbook, ownership metadata, or verification signal would increase its usefulness."

### 7. Use Commander Live

Ask questions such as:

- What do we know?
- What evidence supports the current RCA?
- What don't we know?
- What should I inspect next?

Where useful, add operator guidance/facts and show that this becomes persisted operational context rather than an ephemeral chat turn.

### 8. Show the safety boundary

If the incident came only from forwarded email, explain that NemoGuard will not treat that as sufficient independent authority to approve a production recovery.

Suggested narrative:

> "The system is intentionally more willing to say 'I don't know' than to manufacture confidence. Email is useful for awareness and investigation, but it is not enough evidence to let the system act on production."

### 9. Open Scenario Lab for the future-action glimpse

Make the transition explicit:

> "This next screen is not production data. It is the isolated lab used to demonstrate the later maturity stage."

Use a known scenario to demonstrate the architecture:

signal → incident → investigation → proposed plan → policy/approval → execution → verification → resolve/rollback.

The point is the governed lifecycle, not the synthetic scenario itself.

### 10. Return to Production Overview

Close with:

> "The near-term objective is to make this the central system for production support awareness. As we earn approval to connect richer read-only sources, the coverage score improves and NemoGuard learns which recommendations are consistently correct. Only after we have evidence of that reliability do we graduate selected low-risk capabilities toward automated action."

## Demo fallback plan

If live mailbox ingestion is delayed:

1. use an already-retained Alert Inbox item;
2. open the persisted incident it created;
3. show the complete investigation/audit trail;
4. state clearly that the live bridge normally supplies the same path;
5. use Scenario Lab only for action-oriented behavior.

Never fabricate a production event to save a live demo.

## Leadership questions to be ready for

### "Is this connected to all our production systems?"

No. Current approved live intake is forwarded alert email. Direct integrations are a planned, approval-dependent expansion.

### "Can it restart jobs automatically?"

The architecture supports governed capabilities and independent verification, but current production access is intentionally restricted. Scenario Lab demonstrates the action lifecycle safely.

### "How does it improve over time?"

V22 begins with measurable self-assessment: evidence coverage, missing context, operator corrections, and verified recovery outcomes. Later versions can use those measurements to propose better correlation rules, runbooks, and autonomy candidates.

### "How do you prevent hallucinated actions?"

NemoGuard separates evidence from claims, uses an independent grounding/safety critic, requires policy/approval for governed actions, hashes plans to prevent stale approvals, and verifies outcomes before marking a recovery successful.

### "What would you need next?"

Read-only approved integrations that improve the gaps NemoGuard itself identifies: logs/job history, dependency/ownership data, change/deployment history, approved runbooks, and independent verification signals.
