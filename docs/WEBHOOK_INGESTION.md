# Webhook alert ingestion

## Status and safety

These changes must be tested and deployed before use. The existing live
deployment may still run older code without webhook authentication. Do not
expose that endpoint to the internet. This integration does not use Outlook.

The watcher sends the supplied alert and active-incident context to the
configured LLM. Obtain authorization for that data flow and avoid sending
credentials, personal data, or sensitive infrastructure details that your
organization does not permit the model to receive. The watcher can classify
a valid alert as noise; monitor `ignored` responses and evaluate it using
representative test alerts before relying on it for production monitoring.

## Axet deployment

The observed flow used a Git-backed Python agent and a reverse-proxy gateway.
Editing files on Windows does **not** update that Git-backed runtime. Publish
the reviewed changes to the agent's configured Git repository and branch,
then redeploy/restart the agent according to your Axet environment. Do not
assume the Docker Compose configuration applies to the Axet agent.

Configure a strong random `NEMOGUARD_WEBHOOK_SECRET` in the backend agent's
environment using your approved secret mechanism. Configure the same secret
for any simulator or forwarder process that sends alerts. Never commit it or
paste it into a flow, ticket, or chat. Without it, the updated endpoint returns
503. `NEMOGUARD_WEBHOOK_AUTO_RESOLVE` defaults to off; setting it to `true`
allows LLM-classified recovery messages to resolve correlated incidents.

On the observed designer, the gateway exposed `/backend/api`, mapping it to
the backend's `/api/v2`. The local route is therefore:

```
POST http://localhost:20456/backend/api/ingest/webhook
```

Confirm the mapping and backend startup after redeployment; a status response
alone does not prove the updated webhook is deployed. `localhost` is not
reachable from external monitoring services. Use an organization-approved
HTTPS ingress with network restrictions, request size/rate limits and TLS;
verify any gateway authentication and header forwarding requirements. Do not
expose the Axet designer or unrelated API routes. A shared secret alone is
not a substitute for network controls, replay controls, or provider-specific
signature validation.

## Sender contract

Send a JSON object with these headers:

- `Content-Type: application/json`
- `X-NemoGuard-Webhook-Secret: <configured secret>`
- `Idempotency-Key: <stable delivery ID>`

Choose a globally unique key for each distinct event, including a sender
namespace. Retry the **same** event with the same key and equivalent JSON.
Using the same key with a different JSON payload returns 409. Keys are
required, ASCII, and at most 200 characters. Do not generate a new key for
each retry. Providers that cannot add these headers need a trusted adapter
that authenticates their native signature and supplies these headers.

Example payload (synthetic, sending it can create an incident):

```json
{
  "source": "Datadog",
  "type": "Monitor Alert",
  "monitor_name": "Synthetic pipeline failure",
  "message": "Test pipeline failed its health check",
  "tags": ["service:test-pipeline", "severity:high"]
}
```

After deployment, test against an isolated/non-production backend using a
sender configured with the secret in its environment or secret store. Check
for 401 with a wrong secret, 400 for a missing key, and a duplicate result
with the same alert ID on retry. Do not log request headers or the secret.
The webhook may invoke an LLM and start incident triage; even a synthetic test
can create database records.

## Response and reliability

- `503`: no backend secret configured.
- `401`: invalid secret.
- `400`: missing/invalid idempotency key.
- `409`: key already used for a different payload.
- `500`: classification or processing failed; retry with the same key.
- A 2xx response can be `ignored` (classified as noise), `ingested`,
  `ingested_and_correlated`, or `ingested_and_incident_created`. Inspect
  the response status rather than treating every 2xx as an incident.

Delivery receipts and alert/incident writes share a database transaction.
Retries with the same key return the stored ingestion result and
`duplicate: true`, even after a backend restart, provided the database is
persistent. Concurrent requests may both invoke the watcher, though only one
receipt and alert should commit. Do not delete receipts while a sender may
still retry. The receipt stores the ingestion result, not the later scheduling
result.

**Important limitation:** triage scheduling is not transactional with the
receipt. If the process stops after the incident commits but before
scheduling, a retry is recognized as a duplicate and does not reschedule
triage. Monitor incidents stuck in `INVESTIGATING` and investigate them
with an administrator: the manual triage endpoint may reject that state as
already active. Do not blindly reset incident state while work may still be
running. A durable outbox/recovery worker is needed for automatic crash
recovery. A 2xx does not guarantee completed triage.

The simulator and LocalStack forwarders now require the shared secret.
Queue deliveries use the SQS message ID and leave failed requests queued.
Their immutable payload omits the old database-derived run ID when it is not
explicitly present in the original alarm; this can reduce log correlation.
The export importer generates new payloads and IDs on a fresh invocation, so
re-running an import can create new alerts.

## Verification

Run the dependency-free isolated SQLite tests:

```
python backend/tests/test_webhook_isolated.py -v
```

These tests mock the watcher and extract the ingestion method from source;
they include a concurrent retry test using SQLite, but do not test FastAPI
authentication, the actual LLM, PostgreSQL concurrency, Axet header
forwarding, or automatic triage scheduling. Those require additional
integration and deployment testing.
