# NemoGuard V22 — Implementation Plan

> **Companion documents**
>
> - Product/design direction: [`nemoguard-v22-product-design-spec.md`](./nemoguard-v22-product-design-spec.md)
> - Current-state dependency analysis: [`current-state-dependency-analysis.md`](./current-state-dependency-analysis.md)

## Scope and delivery strategy

V22 is delivered in vertical increments. Each increment must preserve existing routes, authorization behavior, incident workflows, and the clear separation between Production and Scenario Cockpit.

| Release | Primary outcome | Backend risk |
|---|---|---|
| V22.1 | Visual foundation and Overview layout using existing data | Low |
| V22.2 | Authoritative overview projection and real operational KPIs | Medium |
| V22.3 | Unified authenticated live operations state | Medium |
| V22.4 | Commander intelligence layer | Medium |
| V22.5 | Platform/integration visibility | Medium |
| V22.6 | V22 system applied across remaining product surfaces | Incremental |

---

# V22.1 — Visual foundation

## Goal

Make the application visibly V22 before introducing new backend projections. Retain current endpoint behavior and existing fetch hooks for this release.

## File-level implementation plan

### 1. Semantic visual foundation

**Modify:** `renderer/src/index.css`

1. Add V22 semantic colors:
   - canvas, surface, muted surface, mint surface;
   - forest-green brand scale;
   - dark readable text hierarchy;
   - semantic success/warning/critical/info colors;
   - orange warm-accent token.
2. Add reusable V22 surface utilities:
   - standard card;
   - selected/elevated card;
   - quiet interactive card;
   - warm CTA button;
   - status dot;
   - lightweight content-enter animation.
3. Add V22 shadow/radius variables and use soft opaque surfaces in the new Overview.
4. Define a locally scoped dark-theme compatibility strategy:
   - do not restyle every existing product screen in this change;
   - make the Overview V22 surface explicit while shared tokens are upgraded;
   - retain user-controlled dark mode until V22.6 defines dark equivalents for all pages.
5. Reduce nonessential animated/decorative effects on V22 surfaces.
6. Retain and validate global reduced-motion behavior.

### 2. App shell and top header

**Modify:** `renderer/src/components/shell/AppShell.tsx`

1. Refine top bar into a quiet V22 header:
   - compact wordmark;
   - `Production` environment label, rendered non-interactive;
   - date-range control placeholder/local state;
   - existing functional search palette;
   - existing notifications and user menu.
2. Preserve:
   - mobile drawer;
   - command palette behavior;
   - theme toggle;
   - shell-level toaster;
   - keyboard accessibility.
3. Move page-specific titles out of the global header.
4. Ensure header does not become a heavy bordered enterprise toolbar.

### 3. Navigation rail

**Modify:** `renderer/src/components/shell/GlobalNavRail.tsx`

1. Make collapsed desktop width 74 px.
2. Use a solid deep forest-green rail.
3. Replace line-style active state with an animated mint circular/pill selection surface.
4. Continue using Framer Motion `layoutId`, preserving existing tooltip behavior and expanded mode.
5. Preserve all current routes from `useNavItems`.

### 4. Overview page layout and current-data adaptation

**Rewrite:** `renderer/src/pages/ProductionOverviewPage/ProductionOverviewPage.tsx`

Build the V22 12-column visual layout using data currently available from:

- `GET /api/v2/incidents?state=open`;
- `GET /api/v2/alerts`;
- `GET /api/v2/status`.

V22.1 panels:

| Panel | V22.1 source | V22.1 behavior |
|---|---|---|
| Commander Brief | deterministic derivation from fetched incidents/status | no LLM request |
| Action Queue | open incidents/criticalities and current limitations | no fake actions |
| Agent Fleet | explicit planned/projection placeholder | must clearly state runtime projection arrives in V22.3 |
| Open Incidents | incident feed | real |
| High-Priority Alerts | alert feed where severity exists | real only when available; otherwise transparent unavailable state |
| Verified Recoveries | no current overview-ready source | explicit unavailable state, no invented metric |
| Median Triage Time | no current overview-ready source | explicit unavailable state, no invented metric |
| Incident & Alert Activity | current feed data | a truthful current-snapshot representation; historical API deferred to V22.2 |
| System Health | `/api/v2/status` | real API/database/orchestrator/policy/workflow fields |
| Recent Investigations | incidents feed | real links to Command Center |
| Live Operations Feed | no cross-incident stream currently exposed | explicit V22.3 live-streaming state, not fake activity |

Every panel must have loading, live, stale, empty, and error behavior where applicable.

### 5. Reusable V22 Overview components

**Create:** `renderer/src/features/overview/`

Initial structure:

```text
features/overview/
├── components/
│   ├── ActionQueue.tsx
│   ├── AgentFleetPreview.tsx
│   ├── CommanderBrief.tsx
│   ├── MetricCard.tsx
│   ├── RecentInvestigations.tsx
│   ├── SystemHealth.tsx
│   └── V22Surface.tsx
├── types/
│   └── overview.ts
└── utils/
    └── overviewPresentation.ts
```

The V22.1 components are presentation-focused. Do not introduce TanStack Query until the V22.2 overview projection exists.

### 6. Verification

1. Run TypeScript build.
2. Run frontend lint.
3. Run the Vite development server.
4. Validate:
   - desktop collapsed rail;
   - expanded rail;
   - mobile drawer;
   - Overview at desktop and tablet widths;
   - loading/error/freshness behavior;
   - keyboard navigation and focus;
   - reduced-motion behavior;
   - existing route navigation and command palette.
5. Capture updated visual screenshots for product review.

## V22.1 acceptance criteria

- V22 shell and Overview use the light calm canvas, forest rail, mint selection, rounded opaque cards, and restrained motion.
- Existing Overview data remains truthful—no invented health, event, recovery, or agent data.
- Current routes and auth behavior still work.
- Existing non-Overview pages remain usable.
- App build and lint pass.

---

# V22.2 — Real Overview projection

## Goal

Replace V22.1 unavailable/snapshot panels with one authoritative dashboard projection and meaningful historical data.

## Backend API work

**Modify:** `backend/app/api/main.py` initially, then extract into an overview router during API modularization.

**Create:** a response schema/module adjacent to the API router when route extraction begins.

Target:

```http
GET /api/v2/overview?range=7d|14d|30d|90d
```

Response:

```ts
type OverviewResponse = {
  generatedAt: string;
  summary: {
    openIncidents: Metric;
    highPriorityAlerts: Metric;
    verifiedRecoveries: Metric;
    medianTriageTime: Metric;
  };
  actionQueue: ActionQueueItem[];
  activitySeries: ActivityPoint[];
  recentInvestigations: Investigation[];
  systemHealth: HealthItem[];
};
```

## Data-definition work required before coding

1. Define severity/status mappings for high-priority alerts.
2. Define verified recovery from execution plus independent verification records.
3. Define triage completion event/state and data-quality fallbacks.
4. Define a stable action-queue priority model.
5. Define date range semantics, timezone, and prior-period comparison.
6. Add indexes/query tests for historical aggregation.

## Frontend work

1. Install and configure TanStack Query.
2. Add query provider in app entry composition.
3. Create:
   - `features/overview/api/overviewApi.ts`
   - `features/overview/hooks/useOverview.ts`
   - `features/overview/types/overview.ts`
4. Replace V22.1 local polling with query cache/refetch settings.
5. Make each metric and chart route to filtered existing detail screens.

## Acceptance criteria

- Overview is loaded with one projection request plus Commander Pulse if needed.
- Every metric has a written, testable definition.
- Selected date range drives metrics and activity series.
- No browser dashboard-load waterfall of unrelated requests.

---

# V22.3 — Live Operations

## Goal

Make operational state feel live without polling every panel.

## Backend work

1. Define an authenticated operations-stream contract:
   - event ID;
   - timestamp;
   - event category;
   - related incident/entity;
   - minimal patch/revalidation hint.
2. Reuse established authenticated streaming-fetch strategy.
3. Add safe heartbeat, reconnect guidance, and event replay/last-event strategy as feasible.
4. Do not expose cross-tenant data.

## Frontend work

1. Create a reusable authenticated stream client:
   - reconnection/backoff;
   - document visibility awareness;
   - stale/disconnected status;
   - cleanup on unmount.
2. Patch or invalidate TanStack Query overview data by event type.
3. Build:
   - Live Operations Feed;
   - agent runtime state projections;
   - notification unread state;
   - mint event-arrival highlight.
4. Show data freshness globally and per important panel.

## Acceptance criteria

- Live feed never simulates production events.
- Stream failure has clear stale/reconnecting language.
- One event updates only affected data rather than replaying every chart.

---

# V22.4 — Commander intelligence layer

## Goal

Integrate existing Commander capabilities into Overview without making inference a blocking dependency.

## Work

1. Add Commander Brief projection/caching endpoint or durable cached store.
2. Build deterministic fallback summary based on overview state.
3. Add Commander Live floating button/drawer using existing Commander APIs.
4. Pass only relevant context:
   - environment;
   - date range;
   - current filters;
   - selected incident.
5. Preserve policy and approval boundaries. Commander may recommend but must not execute outside existing governed flows.

## Acceptance criteria

- Overview initial render never waits for LLM inference.
- Commander shows last-updated/fallback state.
- Existing capability policy, human approval, and execution restrictions cannot be bypassed.

---

# V22.5 — Platform visibility

## Goal

Expose truthful dependencies and execution conditions that affect operational confidence.

## Work

1. Extend `/api/v2/status` or create a detailed authenticated health projection.
2. Show:
   - API;
   - database;
   - orchestrator;
   - policy engine;
   - Temporal vs direct fallback;
   - inference provider;
   - integrations as implemented.
3. Build a System Health details drawer with timestamps and safe errors.
4. Add Outlook bridge, Datadog, Airflow, and MCP status only when actual probes/contracts exist.

## Acceptance criteria

- Every health item comes from a real runtime signal.
- Workflow execution mode is prominent and clear.
- Failure details are safe and do not leak credentials/internal connection strings.

---

# V22.6 — Extend the system across product

## Recommended redesign sequence

1. Incidents
2. Command Center
3. Agent Operations
4. Intelligence
5. Admin/governance
6. Settings

For each surface:

1. map the workflow and existing APIs;
2. reuse V22 tokens/surfaces/motion;
3. preserve role/tenant/approval behavior;
4. introduce loading, empty, stale, and error states;
5. run visual, accessibility, and workflow regression checks.

---

# Cross-cutting work

## Quality and accessibility

- Preserve visible keyboard focus.
- Ensure semantic status has text/icon support, not color only.
- Respect `prefers-reduced-motion`.
- Verify contrast for mint and orange on light surfaces.
- Add route/page error boundaries as data complexity expands.

## Platform hardening

Track separately from V22 visual changes:

- consolidate Python dependency declarations;
- lock/pin dependencies;
- use `npm ci`;
- replace mutable container image tags;
- reconcile legacy Pipeline Copilot/NVIDIA configuration references;
- incrementally split `backend/app/api/main.py` into routers;
- validate policy configuration in CI.

## Metrics and product review

Collect before/after baseline measures:

- initial overview loading time;
- API request count per overview load;
- time to identify action-required incidents;
- time to triage;
- number of operator clicks to incident workspace;
- accessibility/lighthouse regressions;
- bundle size and code-splitting results.
