# Replay exported Outlook alerts

Use only authorized data in a non-production test. The watcher sends alert
content and active incident context to the configured model and may print
model output in logs. Masking is best-effort, not a privacy guarantee.

## Portal

In NemoGuard, open **Scenario Cockpit → Email Alert Replay**. Enter the
webhook secret, select one Outlook `.msg` file (maximum 5 MB), and choose
**Preview email**. Review and redact the subject and body, then confirm
authorization and select **Send reviewed alert**.

Preview uploads the complete file, including attachments, to NemoGuard for
parsing; attachments are not included in the replay payload. No automatic
redaction is performed. The preview does not call the LLM or create an alert.
Sending does both when the watcher accepts the message; inspect the displayed
result because it may instead be ignored or correlated.

To retry an uncertain delivery, select the same file and use exactly the
same reviewed text. Editing either changes the idempotency key and may create
a second alert. Identical files with identical reviewed text share a key.
Keep the secret out of screenshots and flow exports. The current Axet
Python-agent requirements must include `extract-msg` and the agent must
be restarted after installing it. Access requires an operator or admin
account and the webhook secret.

## Command-line alternative: prepare and review

Export Outlook messages as `.msg` files into a local folder. In PowerShell,
from the project root, install the importer dependencies if needed:

```powershell
python -m pip install extract-msg httpx
```

Preview one alert without sending it (replace the source path):

```powershell
python scripts/import_real_alerts.py --source "C:\path\to\exported-alerts" --out-dir "C:\path\to\reviewed-alerts" --dry-run --limit 1
```

Inspect the generated JSON in a text editor. Remove or redact any confidential
or personal data, credentials, links, and identifiers that are not authorized
for the model. Review every field. The JSON and original `.msg` files remain
sensitive; store and dispose of them under your organization's policy.

## Send the reviewed JSON

With the Axet designer running and the webhook secret configured, send one
reviewed JSON file (replace its path):

```powershell
python scripts/send_reviewed_alert.py "C:\path\to\reviewed-alerts\YOUR-FILE.json" --url "http://localhost:5927/backend/api/ingest/webhook"
```

Enter the webhook secret at the hidden prompt; do not paste it into chat or
include it in the command. The Axet designer port can change after restart;
substitute the current port. Do not use the importer `--send` option after
review: it regenerates the output rather than necessarily sending the reviewed
file.

The sender hashes the canonical JSON for its delivery key. Retrying the same
file uses the same key and should return `duplicate: true`. Identical payloads
are treated as the same delivery; edits make a new key. Do not edit a file
between an uncertain delivery and its retry.

Inspect the returned `status` and `incident_id`: `ignored` is not an alert;
`ingested` is not necessarily a new incident; correlation may attach an alert
to an existing incident. A network timeout or 500 can leave delivery uncertain:
retry the exact same file, then inspect NemoGuard before doing anything else.
Never treat a 2xx alone as proof that triage completed.

The Axet-managed model token is currently captured at agent startup and can
expire, causing classification failures until the session is renewed and the
agent is restarted. The webhook secret in Python-agent flow JSON may be
exportable in plaintext; use only a disposable test secret there. The local
designer URL is not an approved public ingress.
