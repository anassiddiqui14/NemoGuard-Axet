# NemoGuard V22 — Known Limitations

V22 is intentionally honest about what is operational today versus what belongs to the longer-term vision.

## 1. Live production integration scope

The approved live intake path is forwarded alert email through the Outlook bridge. NemoGuard is **not** directly integrated with every production monitoring, scheduler, data platform, ticketing system, or observability source.

This means some investigations will correctly report missing independent evidence, dependency context, runbooks, ownership, or execution identifiers.

## 2. Forwarded email is not authoritative telemetry

Email-derived evidence is intentionally treated as low-authority/unverified external data. A plan associated with reviewed email evidence cannot simply be approved as though the email independently proved the condition.

This is a safety property, not a defect.

## 3. Production autonomous remediation is not the current goal

The governed capability framework exists, but richer executable capabilities are primarily demonstrated in Scenario Lab / LocalStack. V22 does not add unauthorized write access to real production systems.

## 4. Self-improvement is assessment, not self-modification

"What NemoGuard Needs" and Investigation Coverage identify missing context and evidence. V22 does not autonomously edit its own code, prompts, policies, integrations, or production configuration.

A future learning loop should use reviewed human corrections and verified outcomes before proposing changes.

## 5. Coverage score measures data presence, not AI correctness

The V22 Investigation Coverage score answers whether important categories of context exist. It is not a calibrated probability that the RCA is correct.

True confidence calibration requires historical human-confirmed outcomes and evaluation datasets.

## 6. Agent Activity is not process-health monitoring

The Agent Activity panel is derived from persisted audit events. It shows recent observed work, not a heartbeat proving that a Python process/model endpoint is healthy at that instant.

## 7. Some health states are intentionally UNKNOWN

Database and Temporal connectivity can be actively observed. Policy and aXet inference may only be known to be configured unless an active safe probe is performed.

V22 displays those as unknown/configured instead of falsely green.

## 8. Overview live refresh is snapshot polling

Production Overview refreshes the deterministic operations snapshot periodically. Per-incident SSE remains available for detailed live incident activity, but the overview is not yet driven by one cross-incident event bus.

## 9. Admin future surfaces are withheld

The repository contained UI for Model Settings/Evaluation, Teams/Routes, Incident Memory, False Resolutions, and Adoption Readiness without matching backend contracts in this repository state.

V22 removes these from the primary product navigation/Admin experience rather than presenting non-functional controls to leadership. They can return once their contracts exist.

## 10. Database target for aXet Cloud is still pending implementation

The current repository supports PostgreSQL and SQLite through the database abstraction.

The aXet team has explicitly advised against SQLite on `/internal-storage-files/files` for durable application persistence because of locking/journaling behavior and recommended the Flow-provided MySQL-compatible service instead (internal application port 3306; external client port 3307).

A production-grade `mysql` backend has **not** been implemented in V22 because the final internal hostname/credential/database contract still needs to be confirmed before wiring credentials into application code.

Do not treat `/tmp` SQLite as durable cloud storage.

## 11. aXet Python Gateway cold-start race remains external

The application-side FastAPI routing was proven healthy. The installed aXet Python Gateway currently has a hard-coded 60-second agent/Uvicorn discovery wait; on a cold deployment it can time out before the Python Agent becomes ready and then fail to retry, leaving `/backend/api` unregistered.

Restarting the Gateway after the Agent is ready successfully registers the proxy. The aXet team is investigating whether this timeout can be configured or requires a platform change.

This is outside the NemoGuard application code.

## 12. Local/full build validation constraints in this review environment

The uploaded repository contained `renderer/node_modules` produced on macOS. The review container is Linux, so Vite/Rolldown cannot find its Linux native optional binding. TypeScript compilation passes, but `vite build` cannot complete against that copied `node_modules` tree.

Before building on another platform:

```bash
cd renderer
rm -rf node_modules
npm ci
npm run build
```

(Use the platform-equivalent removal command on Windows.)

The review environment also lacks `openai` and `psycopg2` and has no package-network access, so tests importing those dependencies cannot be collected here. Dependency-light and focused V22 tests were run separately.

## 13. Historical documentation contains legacy provider references

Older architecture/status/spec documents under `docs/` still describe the pre-aXet NVIDIA/Nemotron era. Active agent code now routes through the aXet `BaseAgent` integration, and V22 active configuration no longer requires `NVIDIA_API_KEY`.

Treat historical documents as snapshots of prior development states unless they are explicitly updated for V22.
