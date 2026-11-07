# NemoGuard V22 — Product and Design Specification

> **Status:** Approved product direction  
> **Purpose:** Establish the V22 overview dashboard, shell, interaction model, visual system, data strategy, and phased delivery order.

---

## 1. Design principle

The reference reel is **design inspiration, not product definition**.

NemoGuard V22 should adopt the reel’s calm, premium visual language while remaining an AI-assisted incident-response and production-operations product.

The reference succeeds through:

- a narrow persistent navigation rail;
- large softly rounded cards;
- a warm off-white canvas;
- pale mint and cream surfaces;
- dark forest-green navigation and hero areas;
- restrained orange CTA emphasis;
- information density without visual clutter;
- almost no hard dividers;
- consistent spacing and corner radius;
- quiet, smooth transitions instead of dramatic animation.

NemoGuard must not introduce fictional gateway-security concepts merely because they appear in a generic dashboard concept. The following are **not V22 product features** unless NemoGuard later becomes an inline AI security gateway:

- Protected Requests
- Blocked Threats
- generic AI Models
- arbitrary compliance percentages

The production overview must answer five questions immediately:

1. **What is broken?**
2. **What needs my attention?**
3. **What are the agents doing?**
4. **What action is waiting on a human?**
5. **Is the platform healthy?**

---

## 2. Application shell

### Navigation rail

Use a fixed **72–76 px dark-green navigation rail** on desktop.

The collapsed state is the default visual state. Existing user preference for expanded/collapsed navigation remains supported.

Navigation should map directly to existing routes:

| Label | Route |
|---|---|
| Overview | `/app` |
| Incidents | `/app/incidents` |
| Alert Inbox | `/app/alert-inbox` |
| Agent Operations | `/app/agent-operations` |
| Intelligence | `/app/intelligence` |
| Scenario Cockpit | `/app/scenario-cockpit` |
| Admin | `/app/admin` |
| Settings | `/app/settings` |

Command Center remains accessible from Overview and Incident detail workflows.

### Active state

- The selected item uses a mint circular/pill surface.
- Use Framer Motion `layoutId` for one shared selection indicator that glides vertically between icons.
- Hover/focus reveals a small route-name tooltip.
- Mobile continues to use the existing navigation drawer pattern.

---

## 3. Top header

The header should feel almost invisible rather than becoming a traditional heavy enterprise toolbar.

### Header content

**Left**

- NemoGuard wordmark
- small environment/state label

**Center/right**

- global search
- environment label/selector
- shared date range
- notifications
- user menu

### Global search

Search should eventually include:

- incident ID and title;
- alerts;
- evidence;
- RCA text;
- action plans;
- sources/systems;
- agent runs.

Results must deep-link to the appropriate Incident Workspace or existing product surface.

### Environment

If only one usable environment exists, render:

> **Production**

Do not make it appear switchable until multiple environments are real.

### Date range

Provide a shared overview range:

- 7d
- 14d
- 30d
- 90d

All historical KPI calculations and overview charts use the same selected range.

---

## 4. Theme and semantic tokens

V22 uses the light, desaturated feel of the reel rather than the dark SOC concept proposed earlier.

```text
canvas              #F3F6F2
surface             #FCFDFB
surface-muted       #EDF3EE
surface-mint        #E1F3EC

brand-deep          #073F32
brand               #0C6F58
brand-medium        #15876D
brand-mint          #8FE4C9

text-primary        #13231E
text-secondary      #62716B
text-muted          #8C9994

border              rgba(15, 63, 50, 0.10)

success             #18A566
warning             #E5A52B
critical            #DE565E
info                #3777D6

warm-accent         #FF9C55
```

### Color rules

- Forest green is the brand anchor.
- Mint is used for selection, low-emphasis emphasis, positive operational states, and calm focus surfaces.
- Orange is **not** a brand color. Use it only for one or two high-value CTAs.
- Status colors must remain semantic and never be the sole means of communicating state.

---

## 5. Cards, elevation, and spacing

Avoid excessive glassmorphism. The visual quality should come from quiet dimensional surfaces, not transparent panels everywhere.

### Card defaults

```text
border radius: 22–26px
border: 1px subtle gray-green
shadow: very soft
background: mostly opaque white
```

### Elevation levels

| Level | Purpose |
|---|---|
| Level 0 | Flat background containers |
| Level 1 | Standard dashboard cards |
| Level 2 | Interactive/selected cards, drawers, menus, and popovers |

Use consistent spacing and avoid hard section dividers.

---

## 6. Typography

Use a single modern sans-serif family. Do not mix display fonts.

| Usage | Size / weight |
|---|---|
| Page / hero title | 28–34 px / 650–700 |
| Section title | 18–20 px / 650 |
| KPI value | 32–42 px / 700 |
| Card title | 14–16 px / 600 |
| Body | 13–14 px |
| Supporting text | 11–13 px |

Use tabular numerals for counters, KPI values, timestamps, and durations where visual stability matters.

---

## 7. Motion system

Motion exists to communicate state, not as decoration.

### Page transitions

The persistent rail and shell remain stationary. Only page content changes:

```text
opacity: 0 → 1
translateY: 6px → 0
scale: 0.992 → 1
duration: 180–240ms
```

A very subtle wash/crossfade may be used during major route changes.

### Dashboard load

Animate only top-level card containers:

```text
card 1     0ms
card 2    35ms
card 3    70ms
card 4   105ms
```

Do not stagger every child element.

### Interactions

**Interactive cards**

```text
translateY(-2px)
small shadow increase
slightly darker border
duration: 120–150ms
```

**Click feedback**

```text
scale 0.985
duration: about 80ms
```

**Navigation**

- Use one shared Framer Motion active indicator.
- The mint selection bubble physically travels between destinations.

**Drawers**

```text
translateX(24px) → 0
opacity: 0 → 1
duration: 220–280ms
```

**Charts**

- First load: line draws once, area fades in, tooltip fades/scales.
- Live updates: animate only the new data point.
- Never replay a full chart animation for normal refreshes.

**Live events**

- A new event gets a mint-tinted background that fades to normal in about 1.2 seconds.
- Critical events may receive a brief red edge highlight.
- Do not continuously pulse critical items.

### Accessibility

Respect `prefers-reduced-motion`.

Reduced-motion mode removes translation, scaling, and chart drawing while retaining restrained opacity transitions.

---

## 8. Overview information architecture

Use a 12-column content grid.

```text
Row 1:  Commander Brief (8) | Action Queue (4)
Row 2:  Agent Fleet (4)     | Four KPIs (8)
Row 3:  Incident & Alert Activity (8) | System Health (4)
Row 4:  Recent Investigations (7) | Live Operations Feed (5)
```

A floating Commander Live button remains in the lower-right corner.

---

## 9. Commander Brief

Commander Brief replaces generic hero copy and becomes a core NemoGuard surface.

### Example

> **Production is stable with 2 incidents requiring attention**
>
> 17 alerts were processed in the last 4 hours. Two incidents remain open. INC-1042 is awaiting approval for a medium-risk recovery plan. No critical integration failures detected.

### Actions

- Primary: **Open Command Center**
- Secondary: **Ask Commander**

### Inputs

The brief is derived from:

- open incidents and severity;
- agent findings;
- pending action plans;
- system health;
- execution/verification state;
- existing Commander Live / Commander Pulse capabilities.

### Runtime behavior

- Do **not** force an LLM request on every overview load.
- Cache the latest Commander Pulse and show its freshness, for example: `Updated 2 min ago`.
- When inference is unavailable, construct a deterministic operational summary from current authoritative state.

---

## 10. Action Queue

Action Queue replaces the reel’s calendar because human action is more valuable than a decorative calendar in NemoGuard.

### Example actions

| State | Example |
|---|---|
| Approval required | INC-1042 · Restart Airflow workflow · Medium risk |
| Investigation stalled | INC-1037 · RCA Agent · Waiting 11 min |
| Verification failed | INC-1029 · Manual review required |
| Connector unhealthy | Datadog ingestion · Last successful event 18 min ago |

Each item deep-links to its existing product workspace.

Action Queue answers:

> **What does a human need to do next?**

---

## 11. Agent Fleet

Agent Fleet must show actual NemoGuard agent runtime projections rather than decorative avatars.

### Agents

- Watcher
- RCA
- Impact / Dependency
- Runbook
- Grounding / Safety
- Commander

### Agent data

Each agent should expose:

```text
status
last activity
current incident
runs today
failure count
median duration
```

Compact state:

```text
RCA Agent
● Working
INC-1042
```

Clicking an agent routes to:

```text
/app/agent-operations?agent=rca
```

### Status semantics

| Status | Meaning |
|---|---|
| Idle | No current work |
| Working | Active agent run |
| Degraded | Recent repeated failures or timeouts |
| Unavailable | Provider or required dependency unavailable |

Status must represent operational behavior, not merely whether a Python object exists.

---

## 12. KPI definitions

### Open Incidents

Replaces Protected Requests.

```text
Open Incidents
7
2 critical · 3 high
↓ 3 vs previous period
```

- Click: Incidents filtered to `state=open`.
- Sparkline: open incident count across the selected range.

### High-Priority Alerts

Replaces Blocked Threats.

```text
High-Priority Alerts
18
+4 today
```

Definition: critical/high alerts in the selected period that:

- generated an incident;
- joined an existing incident; or
- remain awaiting correlation/triage.

- Click: Alert Inbox with the appropriate filters.

### Verified Recoveries

Replaces Policy Compliance.

```text
Verified Recoveries
23
92% successful
23 verified · 2 failed verification
```

Definition: recovery executions that completed and passed independent verification.

A recovery does not count simply because an API/action succeeded.

- Click: Intelligence or Command Center filtered to completed recovery actions.

### Median Triage Time

Replaces a duplicated Active Incidents card.

```text
Median Triage Time
2m 18s
↓ 34 sec
```

Definition:

```text
incident detected/created
→
triage decision completed
```

- Click: Intelligence with triage-time context.
- Future Intelligence metrics: MTTA, MTTR, automation rate, and human-touch rate.

---

## 13. Incident and Alert Activity

Rename a generic threat chart to **Incident & Alert Activity**.

### Series

- primary: Alerts ingested
- secondary: Incidents created
- optional toggle: Incidents resolved

### Tooltip example

```text
Nov 6

Alerts              42
Incidents created    7
Resolved             5
Critical             1
```

Clicking a data point opens a compact filtered drawer, for example:

> Show the 7 incidents created on Nov 6.

The chart must support investigation rather than simply decorate the dashboard.

---

## 14. System Health

System Health must only display real signals.

### Initial checks

- Database
- Orchestrator
- Policy Engine
- Temporal / execution mode
- aXet inference
- API

### Later checks

- Outlook bridge
- Datadog
- Airflow
- MCP integrations

### Example

```text
Database
● Healthy
PostgreSQL · 23 ms

Workflow
● Direct fallback
Temporal disabled

Inference
● Healthy
aXet · configured model

API Gateway
● Healthy
```

Temporal/direct-fallback state is especially important because it changes execution and recovery semantics.

Clicking the card opens an expanded health drawer with timestamps and safe error details.

---

## 15. Recent Investigations

Replace generic analyses with incident-grounded investigation cards.

```text
INC-1042
Airflow pipeline failure
RCA complete
Confidence 94%
3 min ago
```

```text
INC-1039
S3 source arrival delay
Plan ready
Approval required
12 min ago
```

```text
INC-1034
Datadog anomaly
Resolved
Verified
28 min ago
```

Inputs:

- incidents;
- evidence;
- RCA findings;
- plans;
- status projections.

Clicking a card opens the relevant Command Center incident workspace.

---

## 16. Live Operations Feed

Live Operations Feed is the home for NemoGuard’s authenticated streaming architecture.

### Example events

```text
10:31  Alert received
       Datadog · Airflow SLA breach

10:31  Watcher Agent classified alert
       Confidence 0.97

10:32  Incident INC-1042 created

10:33  RCA Agent completed investigation

10:34  Recovery plan requires approval
```

### Filters

- All
- Incidents
- Agents
- Approvals
- Execution
- System

Each item deep-links to the related incident where applicable.

Retain authenticated streaming fetch rather than replacing it with native `EventSource`, since authorization/aXet behavior requires request headers.

---

## 17. Notifications

Notifications are distinct from the Live Operations Feed.

- **Feed:** system-wide operational timeline.
- **Notifications:** information requiring or relevant to the current user.

Examples:

- approval requested;
- assigned incident;
- action rejected;
- verification failed;
- critical source disconnected;
- policy changed.

Unread state must persist.

---

## 18. Commander Live

The floating button is **Commander Live**, not a generic AI Assistant.

It opens the right-side Commander drawer.

### Example prompts

- What needs my attention?
- Why is INC-1042 classified high severity?
- Summarize the RCA.
- What recovery is proposed?
- What evidence supports this conclusion?
- What happened in the last hour?

### Context passed to Commander

```text
current environment
selected date range
current filters
currently selected incident
```

Commander can recommend actions but must **never bypass** capability policy, approval, or execution controls. Existing externalized policy configuration and admin APIs remain authoritative.

---

## 19. Functional data architecture

Adopt the following frontend division of responsibility:

```text
TanStack Query
    REST and server state

Existing authenticated streaming fetch
    live SSE state

React local state
    visual/UI state only
```

No Redux is required.

### Target feature structure

```text
renderer/src/features/overview/

api/
  overviewApi.ts

hooks/
  useOverview.ts
  useSystemHealth.ts
  useActionQueue.ts
  useCommanderPulse.ts
  useLiveOperations.ts

components/
  CommanderBrief.tsx
  ActionQueue.tsx
  AgentFleet.tsx
  MetricCard.tsx
  ActivityChart.tsx
  RecentInvestigations.tsx
  SystemHealth.tsx
  LiveOperationsFeed.tsx

types/
  overview.ts
```

Existing design-system primitives must be extended and reused rather than styling each overview card independently.

---

## 20. Overview API strategy

Do not make twenty independent browser requests when Overview loads.

Create/evolve a single projection:

```text
GET /api/v2/overview?range=30d
```

### Conceptual response

```text
generatedAt

summary
  openIncidents
  highPriorityAlerts
  verifiedRecoveries
  medianTriageTime

actionQueue
agents
activitySeries
recentInvestigations
systemHealth
```

Commander Pulse remains separate because it may invoke inference and has different caching needs.

Detailed workflows continue using existing incident and alert APIs.

---

## 21. Live-state architecture

```text
REST overview snapshot
        ↓
render dashboard
        ↓
connect authenticated operations stream
        ↓
patch affected cached state
```

### Patch examples

```text
incident.created
    → Open Incidents +1
    → activity chart update
    → live feed entry

plan.approval_required
    → Action Queue +1
    → notification badge +1

execution.verified
    → Verified Recoveries +1
    → incident state update

agent.run.started
    → Agent Fleet status = Working
```

This provides a live operations console feel without polling every widget every two seconds.

---

## 22. Required component states

Every dashboard tile must support the following states independently:

1. **Loading** — skeleton matching final geometry.
2. **Live** — normal fresh data.
3. **Stale** — last-known data plus freshness warning.
4. **Empty** — intentional operational zero state.
5. **Error** — contained error and retry action.

Example:

```text
System Health
Unable to refresh
Last checked 4 min ago
Retry
```

A single failed panel must not blank the entire overview.

---

## 23. Data freshness

Every important panel should expose freshness.

Examples:

```text
Live · updated 8 sec ago
Reconnecting…
Showing last known data · 3 min old
```

Production UI must never imply that unavailable data is live.

---

## 24. Production versus Scenario Cockpit

Maintain a visible separation.

### Production Overview

- real incidents;
- real alerts;
- real agent activity;
- real health;
- real approvals;
- real executions.

### Scenario Cockpit

- synthetic incidents;
- simulated alerts;
- test recoveries;
- evaluation results.

Simulation may intentionally generate activity; Production must not.

---

## 25. Explicit exclusions

Do not carry forward the following without genuine matching product capabilities:

- Protected Requests
- Blocked Threats
- unexplained Policy Compliance percentages
- calendar as a primary Overview feature
- random AI-model cards
- fake health indicators
- fake real-time activity in Production

---

## 26. Implementation order

### V22.1 — Visual foundation

Implement:

- navigation rail;
- header;
- design tokens;
- card system;
- typography;
- motion tokens;
- new 12-column grid.

Use existing data and current endpoints unchanged.

**Goal:** achieve the V22 visual system with minimal backend risk.

### V22.2 — Real Overview

Implement:

- Open Incidents;
- High-Priority Alerts;
- Verified Recoveries;
- Median Triage Time;
- Incident & Alert Activity;
- Action Queue.

**Goal:** remove demo/fake numbers.

### V22.3 — Live Operations

Implement:

- SSE consolidation;
- Live Operations Feed;
- agent runtime state;
- notifications;
- live KPI cache updates.

### V22.4 — Intelligence layer

Implement:

- Commander Brief;
- Commander Live drawer;
- Recent Investigations;
- confidence/evidence projections.

### V22.5 — Platform visibility

Implement:

- System Health;
- integration health;
- Temporal/direct-mode status;
- provider health;
- data freshness.

### V22.6 — Apply V22 across the product

Then redesign, in order:

1. Incidents
2. Command Center
3. Agent Operations
4. Intelligence
5. Admin/governance
6. Settings

Do not redesign all product surfaces simultaneously. Overview establishes the system first.

---

## 27. End-state layout

```text
┌─ Rail ─┬─────────────────────────────────────────────────┐
│        │ NemoGuard        Search        Prod   User       │
│  ◉     │                                                   │
│  ○     │ ┌──────── Commander Brief ───────┐ ┌Action Queue┐│
│  ○     │ │ Production requires attention  │ │ 3 actions  ││
│  ○     │ └────────────────────────────────┘ └────────────┘│
│  ○     │                                                   │
│  ○     │ ┌ Agent Fleet ┐ ┌Open┐ ┌Alerts┐ ┌Recov┐ ┌Triage┐ │
│        │ └─────────────┘ └────┘ └──────┘ └─────┘ └──────┘ │
│        │                                                   │
│        │ ┌──── Incident & Alert Activity ──┐ ┌ Health ───┐│
│        │ │              chart               │ │ services  ││
│        │ └──────────────────────────────────┘ └───────────┘│
│        │                                                   │
│        │ ┌ Recent Investigations ┐ ┌ Live Operations ────┐│
│        │ │ RCA / Plan / Status   │ │ streaming events    ││
│        │ └───────────────────────┘ └─────────────────────┘│
│        │                                             ◉ Cmdr│
└────────┴───────────────────────────────────────────────────┘
```

## Final V22 standard

The UI should look calm.

The underlying application should feel alive.
