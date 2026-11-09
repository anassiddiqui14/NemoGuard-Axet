# Outlook Alert Inbox

The Alert Inbox retains accepted Outlook bridge emails before AI classification. It displays the most recent 200 records to operators and administrators in the configured tenant and workspace.

## Configuration and access

The Outlook bridge uses `NEMOGUARD_WEBHOOK_SECRET` for delivery and heartbeat requests. The server assigns incoming messages to `NEMOGUARD_OUTLOOK_TENANT_ID` and `NEMOGUARD_OUTLOOK_WORKSPACE_ID`, defaulting to `default_tenant` and `default_workspace`. These values must match the intended users' tenant and workspace. Do not change them without planning how existing records and webhook identities will be handled.

Only users with the operator or admin role in the matching tenant and workspace can read email bodies, review them, promote ignored emails, or view bridge reports. Email bodies are stored in the application database and can contain sensitive information. Protect database access and backups and establish an appropriate retention policy before production use. Inbox-specific deletion and retention automation are not yet implemented.

## Processing

Accepted emails are stored before the Watcher runs. A failed classification remains visible as `failed`; the existing bridge keeps the message in its local pending outbox and retries delivery. Ignored messages remain visible as `needs_review`. Successful alerts and incidents continue to use the existing webhook idempotency receipt.

An operator can record an assessment and reason without changing the AI outcome. For an ignored email, an operator can explicitly promote it to one alert with a severity and reason. Promotion preserves the original AI outcome and creates an alert only: it does not create or link an incident, start investigation, or execute a plan. The email remains unverified external evidence.

Operators can retry failed or unfinished deliveries using the original stored payload and idempotency key. A retry can create an alert or incident and start triage, and the bridge may retry concurrently. Attempts are audited. Records created before original-payload storage was added may not support inbox retry.

Operators can move an alert's correlation from one existing incident to another using `POST /api/v2/incidents/{incident_id}/alerts/{alert_id}/recorrelate` (also available from the incident workspace's Alerts tab), providing a required reason. This updates only the `incident_alert` link and writes an audited `ALERT_RECORRELATED` event on both the source and target incidents. It does NOT copy, move, or revise any evidence, hypotheses, or plans already generated against the source incident — those remain a historical record of what was investigated under the original correlation. Triggering a fresh investigation against the target incident (if needed) is a separate, deliberate operator action (e.g. Start Triage).

## Bridge reporting

The updated bridge sends a best-effort report after a folder scan containing local pending, blocked, and baseline counts. The backend records the report receipt time. These values describe a snapshot from one bridge, not complete production coverage or current service health. Multiple bridges using the same tenant and workspace overwrite one another's latest report. A missing or old report does not establish why the bridge stopped.

The first bridge scan baselines existing messages without sending them. Messages removed before a scan and items blocked locally, including oversized or empty messages, are not represented as inbox emails. Counts of blocked or baselined messages do not include their contents. The updated bridge can capture supported UTF-8 text attachments and report unsupported files as omitted. The local changes are not deployed; live Outlook, gateway limits, and agent behavior remain unverified. Captured attachment text is retained with the original delivery and is unverified external evidence. See `OUTLOOK_FOLDER_BRIDGE.md` for limits and local storage risks.

The inbox refreshes periodically, but the timestamp and warning must be checked before relying on a report. The list is limited to 200 entries and currently has no pagination.

## Deployment verification

Deploy the updated backend and frontend, then restart the updated local bridge only after confirming the aXet gateway forwards the inbox and heartbeat routes. Confirm access using an authorized operator and a test message; verify that the message appears once, a classification failure remains pending for retry, and a heartbeat report appears. Do not use production-sensitive emails for testing without authorization. The local bridge changes are not deployed by pushing only backend files.

PostgreSQL behavior, concurrent delivery and promotion scenarios, browser interactions, and the aXet gateway must be verified in the target environment before relying on the inbox operationally.
