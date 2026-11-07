# Classic Outlook test-folder bridge

This local Windows test utility polls the top-level **NemoGuard Test Alerts**
folder of an explicitly selected classic Outlook mailbox. It submits newly
observed messages' plain-text subject and body to NemoGuard; attachments are
not included. It does not intentionally mark, move, or delete messages.

## Before starting

- Use only messages you are authorized to send to NemoGuard and its configured
  AI model. Existing incident context may also be sent to the model. The
  endpoint may create incidents and start investigation.
- Keep classic Outlook running with the desired account signed in and synced.
  Organization policies may block Outlook automation or show security prompts.
- Keep the Axet designer/backend running. This first version only supports
  an HTTP loopback webhook. The designer port can change after a restart;
  discovery mode finds the selected running designer using `wsl docker ps`
  and checks `/backend/api/status` before delivery.
- The Python agent's environment does not automatically become the Windows
  bridge's environment. For this local designer, the opt-in
  `--secret-from-agent` option reads the agent's initial process environment
  through Docker and holds the webhook secret in memory. This requires local
  Docker access and permission to inspect the agent process environment; use
  only if allowed by your organization. If the agent directory changes, process
  environments are inaccessible, or matching agents have different secrets,
  delivery pauses with messages pending. An approved secret manager or a
  dedicated sender credential is preferable for a production integration.
- Alternatively, obtain the matching `NEMOGUARD_WEBHOOK_SECRET` through your
  approved secret-management process. The bridge reads it from its own
  environment or prompts without echoing. Do not put it in chat, a command line,
  a screenshot, or source control.
- Use a Windows account and computer approved to hold the content: the local
  SQLite state file stores pending subjects and bodies **unencrypted**. It
  relies on the file system's inherited permissions; the script does not
  enforce a private ACL. SQLite may retain deleted text in database pages or
  journals. Protect and dispose of it under your organization's policy.

## Double-click launcher

After deploying the compatible backend, open classic Outlook and double-click
**Launch NemoGuard Bridge.vbs** in the repository root. The launcher prefers
the mailbox associated with the existing bridge queue, or Outlook's default
mailbox if no queue exists. If neither can be identified, select a mailbox.
The bridge verifies that the test folder exists before monitoring. Keep the
launcher and Outlook open; use **Stop monitoring** or close it to stop.

The launcher checks the backend contract before starting. Until the backend
changes are deployed and the designer is restarted, monitoring stays paused.
The launcher uses installation-specific Axet container and agent identifiers;
it may require reconfiguration if those change.

## Command-line alternative

From the repository root in PowerShell, with classic Outlook open:

1. List the Outlook store display names. This lists names, not message content:

   ```powershell
   .\.venv\Scripts\python.exe scripts\outlook_folder_bridge.py --list-mailboxes
   ```

2. Start the bridge using the exact store name and the unique designer
   container prefix for your Axet instance:

   ```powershell
   .\.venv\Scripts\python.exe scripts\outlook_folder_bridge.py --mailbox "EXACT OUTLOOK STORE NAME" --axet-designer-prefix "deptapps-flows-designer-container-28477-" --secret-from-agent "/usr/src/node-red/.node-red/python-envs/373027f335ebf4cc/src/c7e2cde01005ddac"
   ```

   Discovery and agent secret retrieval require WSL and Docker access.
   Omit `--secret-from-agent` to use the bridge environment or hidden prompt
   instead. The agent directory is specific to this installation and may
   change after redeployment. The default scan interval is 30 seconds; use `--interval 10` for a
   10-second interval. Discovery checks health, not cryptographic identity:
   use only on a trusted machine and trusted local designer.

3. Wait for `Baseline complete`. Messages already present at the first
   successful scan are recorded as existing and are **not sent**. Only after
   that, copy or move an authorized test message into the folder.

4. Watch the console. `Delivery: done (ingested...)` confirms the webhook
   accepted an alert, not necessarily that an incident was created or triage
   completed. `done (ignored)` means the Watcher classified it as noise.
   Inspect NemoGuard for the actual incident and investigation state.

Press `Ctrl+C` to stop. The bridge does not automatically start on login.

## Reliability and troubleshooting

- While the bridge is stopped or Outlook is offline, messages still in the
  folder when it resumes can be detected. A message removed before a scan can
  be missed. Polling and Outlook synchronization are not real-time
  guarantees; do not use this test tool as the sole production monitor.
- A network error, 401, 429, or server failure leaves the same payload and
  idempotency key pending for retry. For a wrong secret, stop and restart the
  bridge with the correct one. A 400, 403, 404, or 409 blocks that message for
  manual investigation; no automatic retry/unblock interface exists yet.
- Empty-body and oversized messages are blocked; attachments are never
  inspected. The console reports counts and outcomes, not message contents.
- The default state is `%LOCALAPPDATA%\NemoGuard\outlook-bridge.sqlite`.
  Preserve it across restarts: deleting it resets the baseline and retry
  history. State is bound to the mailbox, folder, and selected designer
  prefix in discovery mode, so a port change does not invalidate the queue.
  If using `--url` instead, the exact URL is part of the state binding. Do not
  delete the state file to work around a mismatch: pending deliveries may be
  lost. There is no state migration interface yet.
- Outlook EntryIDs can change on moves or copies. Copying the same email
  again may therefore be treated as a new delivery. A delivery receipt also
  does not guarantee triage was scheduled or completed.
- A successfully delivered message is removed from the pending payload
  column, but that does not securely erase its previous database bytes.

For offline tests, run:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_outlook_folder_bridge -v
```
