"""Read new messages from one classic Outlook folder and deliver them to NemoGuard.

This script sends email subjects and bodies to NemoGuard, which may send them
and existing incident context to its configured model. Attachments are excluded.
It never changes, moves, or deletes Outlook messages.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FOLDER_NAME = "NemoGuard Test Alerts"
MAX_BODY_CHARS = 100_000
ACCEPTED = {
    "ingested", "ingested_and_correlated", "ingested_and_incident_created",
    "ingested_and_incident_auto_resolved",
}


def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def message_key(store_id, entry_id):
    identity = canonical_json([store_id, entry_id]).encode("utf-8")
    return "outlook:" + hashlib.sha256(identity).hexdigest()


def validate_url(value):
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme != "http"
        or url.hostname not in ("localhost", "127.0.0.1", "::1")
        or url.username or url.password or url.fragment or url.query
        or not url.path
    ):
        raise ValueError("Use an HTTP localhost webhook URL without credentials, query, or fragment.")
    return value


def discover_designer_url(prefix, include_container=False):
    """Resolve exactly one explicitly selected running designer on localhost."""
    if not prefix or not prefix.startswith("deptapps-flows-designer-container-"):
        raise ValueError("An explicit Axet designer container name prefix is required.")
    result = subprocess.run(
        ["wsl", "docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    matches = []
    for line in result.stdout.splitlines():
        name, separator, ports = line.partition("\t")
        if separator and name.startswith(prefix):
            matches.append((name, ports))
    if len(matches) != 1:
        raise RuntimeError("Designer container is unavailable or ambiguous.")
    name, published_ports = matches[0]
    ports = re.findall(r"(?:^|,\s*)(?:127\.0\.0\.1|0\.0\.0\.0|172\.17\.0\.1):(\d+)->1880/tcp(?:,|$)", published_ports)
    if len(ports) != 1:
        raise RuntimeError("Designer must publish exactly one supported port.")
    base = f"http://127.0.0.1:{int(ports[0])}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(base + "/backend/api/status", timeout=5) as response:
        status = json.loads(response.read(4096))
    if not isinstance(status, dict):
        raise RuntimeError("Designer backend returned an invalid status.")
    if status.get("database") != "healthy" or status.get("orchestrator") != "healthy":
        raise RuntimeError("Designer backend is unhealthy.")
    if status.get("outlook_bridge_contract") != 1:
        raise RuntimeError("Designer backend lacks the required Outlook bridge contract.")
    url = base + "/backend/api/ingest/outlook-bridge"
    return (name, url) if include_container else url


def read_agent_secret(container, agent_cwd):
    """Read the configured agent's initial process environment into memory only."""
    if not agent_cwd.startswith("/usr/src/node-red/.node-red/python-envs/") or "\n" in agent_cwd:
        raise ValueError("Invalid agent directory.")
    code = """
import pathlib, sys
target = sys.stdin.readline().rstrip("\\n")
values = []
for proc in pathlib.Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if "python" not in (proc / "comm").read_text().lower():
            continue
        if str((proc / "cwd").resolve(strict=True)) != target:
            continue
        env = (proc / "environ").read_bytes().split(b"\\0")
        found = [entry.split(b"=", 1)[1] for entry in env
                 if entry.startswith(b"NEMOGUARD_WEBHOOK_SECRET=")]
        if len(found) != 1 or not found[0]:
            sys.exit(2)
        values.append(found[0])
    except FileNotFoundError:
        continue
    except (OSError, ValueError):
        sys.exit(2)
if not values or any(value != values[0] for value in values):
    sys.exit(2)
sys.stdout.buffer.write(values[0])
"""
    result = subprocess.run(
        ["wsl", "docker", "exec", "-i", "--user", "node-red", container, "python3", "-c", code],
        input=(agent_cwd + "\n").encode("utf-8"), capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("Agent secret unavailable or ambiguous.")
    try:
        secret = result.stdout.decode("ascii")
    except UnicodeError:
        raise RuntimeError("Agent secret is not valid for an HTTP header.") from None
    if not secret or any(ord(c) < 32 or ord(c) == 127 for c in secret):
        raise RuntimeError("Agent secret is not valid for an HTTP header.")
    return secret


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_payload(url, secret, key, payload, opener=None):
    request = urllib.request.Request(
        url, data=payload.encode("utf-8"), method="POST",
        headers={
            "Content-Type": "application/json",
            "X-NemoGuard-Webhook-Secret": secret,
            "Idempotency-Key": key,
        },
    )
    try:
        with (opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())).open(request, timeout=180) as response:
            result = json.loads(response.read(65537))
            if not isinstance(result, dict):
                return "retry", "invalid_response"
            status = result.get("status")
            if status in ACCEPTED or status == "ignored":
                return "done", status
            return "retry", "unconfirmed_response"
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 403, 404, 409):
            return "blocked", f"http_{exc.code}"
        return "retry", f"http_{exc.code}"
    except (OSError, ValueError, UnicodeError):
        return "retry", "network_or_response_error"


def send_heartbeat(url, secret, counts, opener=None):
    """Best-effort scan report; never interfere with email delivery."""
    payload = {key: int(counts.get(key, 0)) for key in ("pending", "blocked", "baseline")}
    request = urllib.request.Request(
        url.rsplit("/", 1)[0] + "/outlook-bridge-heartbeat",
        data=canonical_json(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-NemoGuard-Webhook-Secret": secret},
        method="POST",
    )
    try:
        with (opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())).open(request, timeout=5) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


class Outbox:
    def __init__(self, path, mailbox, folder, url):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages "
            "(message_key TEXT PRIMARY KEY, payload TEXT, status TEXT NOT NULL, detail TEXT)"
        )
        config = canonical_json([mailbox, folder, url])
        row = self.conn.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if row and row[0] != config:
            self.conn.close()
            raise ValueError("This state file belongs to a different mailbox, folder, or webhook URL.")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO settings VALUES ('config', ?)", (config,))

    def close(self):
        self.conn.close()

    def baselined(self):
        return self.conn.execute("SELECT 1 FROM settings WHERE key='baseline'").fetchone() is not None

    def baseline(self, keys):
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO messages (message_key, status, detail) VALUES (?, 'baseline', 'existing')",
                [(key,) for key in keys],
            )
            self.conn.execute("INSERT INTO settings VALUES ('baseline', 'complete')")

    def known(self, key):
        return self.conn.execute("SELECT 1 FROM messages WHERE message_key=?", (key,)).fetchone() is not None

    def add(self, key, subject, body):
        problem = None
        if len(subject) > 2000 or len(body) > MAX_BODY_CHARS:
            problem = "message_too_large"
        elif not body.strip():
            problem = "empty_body"
        if problem:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO messages VALUES (?, NULL, 'blocked', ?)",
                    (key, problem),
                )
            return False
        payload = canonical_json({
            "source": "Outlook email",
            "type": "Email alert",
            "monitor_name": subject or "(no subject)",
            "message": body,
        })
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, 'pending', NULL)",
                (key, payload),
            )
        return True

    def pending(self):
        return self.conn.execute(
            "SELECT message_key, payload FROM messages WHERE status='pending' ORDER BY rowid"
        ).fetchall()

    def record(self, key, outcome, detail):
        with self.conn:
            if outcome == "done":
                # Payload is no longer needed for retries after confirmed delivery.
                self.conn.execute(
                    "UPDATE messages SET status='done', detail=?, payload=NULL WHERE message_key=?",
                    (detail, key),
                )
            elif outcome == "blocked":
                self.conn.execute(
                    "UPDATE messages SET status='blocked', detail=? WHERE message_key=?",
                    (detail, key),
                )
            else:
                self.conn.execute(
                    "UPDATE messages SET detail=? WHERE message_key=?",
                    (detail, key),
                )

    def counts(self):
        return dict(self.conn.execute("SELECT status, COUNT(*) FROM messages GROUP BY status"))


def select_folder(namespace, mailbox, folder_name=FOLDER_NAME):
    matches = []
    for index in range(1, namespace.Stores.Count + 1):
        store = namespace.Stores.Item(index)
        if str(store.DisplayName).casefold() == mailbox.casefold():
            matches.append(store)
    if len(matches) != 1:
        raise ValueError("Mailbox name not found or ambiguous; choose an exact unique Outlook store name.")
    # Microsoft 365's mail root (IPM subtree) may be below the store root.
    # The parent of the store's default Inbox is the top-level mail-folder root.
    root = matches[0].GetDefaultFolder(6).Parent  # olFolderInbox
    folders = [
        root.Folders.Item(i) for i in range(1, root.Folders.Count + 1)
        if str(root.Folders.Item(i).Name).casefold() == folder_name.casefold()
    ]
    if len(folders) != 1:
        raise ValueError("The exact top-level test folder was not found or is ambiguous.")
    return folders[0], str(matches[0].StoreID)


def scan_folder(folder, store_id, outbox):
    # Enumerate fully before committing a baseline: a failed scan must not
    # silently classify an incomplete snapshot as the completed baseline.
    items = folder.Items
    snapshot = []
    for index in range(1, items.Count + 1):
        item = items.Item(index)
        if item.Class != 43:  # olMail
            continue
        entry_id = str(item.EntryID or "")
        if not entry_id:
            raise RuntimeError("Outlook returned a message without an EntryID.")
        snapshot.append((message_key(store_id, entry_id), item))
    if not outbox.baselined():
        outbox.baseline([key for key, _ in snapshot])
        return "baseline", len(snapshot)

    added = 0
    for key, item in snapshot:
        if outbox.known(key):
            continue
        # Outlook Body provides plain text; reading does not mark mail as read.
        subject = str(item.Subject or "")
        body = str(item.Body or "")
        if outbox.add(key, subject, body):
            added += 1
        else:
            print("A message had an empty body or exceeded the size limit and was blocked for review.")
    return "scanned", added


def list_mailboxes(namespace):
    for index in range(1, namespace.Stores.Count + 1):
        print(namespace.Stores.Item(index).DisplayName)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-mailboxes", action="store_true", help="List Outlook mailbox names without reading messages.")
    parser.add_argument("--mailbox", help="Exact Outlook mailbox/store display name.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--url", help="Local NemoGuard Outlook bridge endpoint, including /backend/api/ingest/outlook-bridge.")
    target.add_argument("--axet-designer-prefix", help="Exact unique prefix of the Axet designer container name; discovers the local port.")
    parser.add_argument("--secret-from-agent", metavar="AGENT_CWD", help="Opt in to reading the running agent's secret from its exact container working directory.")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (minimum 5).")
    parser.add_argument(
        "--state", type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "NemoGuard" / "outlook-bridge.sqlite",
        help="Private local SQLite state file. Pending messages can contain sensitive email text.",
    )
    args = parser.parse_args(argv)
    if not args.list_mailboxes:
        if not args.mailbox or not (args.url or args.axet_designer_prefix) or args.interval < 5:
            parser.error("--mailbox and either --url or --axet-designer-prefix are required; --interval must be at least 5 seconds.")
        if args.secret_from_agent and not args.axet_designer_prefix:
            parser.error("--secret-from-agent requires --axet-designer-prefix.")
        if args.url:
            try:
                validate_url(args.url)
            except ValueError as exc:
                parser.error(str(exc))
        elif not args.axet_designer_prefix.startswith("deptapps-flows-designer-container-"):
            parser.error("Use an explicit Axet designer container name prefix.")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        print("Install pywin32 in the selected Python environment before running this bridge.", file=sys.stderr)
        return 2

    pythoncom.CoInitialize()
    outbox = None
    lock_file = None
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        if args.list_mailboxes:
            list_mailboxes(namespace)
            return 0
        folder, store_id = select_folder(namespace, args.mailbox)
        secret = None if args.secret_from_agent else (
            os.environ.get("NEMOGUARD_WEBHOOK_SECRET") or getpass.getpass("NemoGuard webhook secret (input hidden): ")
        )
        if not args.secret_from_agent and not secret:
            print("No secret entered; bridge not started.", file=sys.stderr)
            return 2

        # Prevent two bridge processes from sending the same queue concurrently.
        import msvcrt
        args.state.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(str(args.state) + ".lock", "a+b")
        try:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("Another bridge is already using this state file.", file=sys.stderr)
            return 2

        target_identity = args.url or "axet-designer:" + args.axet_designer_prefix
        outbox = Outbox(args.state, args.mailbox, FOLDER_NAME, target_identity)
        print("Monitoring the dedicated test folder. Attachments are excluded.")
        print("Pending message text is stored locally in the state file until delivery is confirmed.")
        while True:
            try:
                try:
                    if args.axet_designer_prefix:
                        container, url = discover_designer_url(args.axet_designer_prefix, include_container=True)
                    else:
                        status_url = args.url.rsplit("/", 2)[0] + "/status"
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                        with opener.open(status_url, timeout=5) as response:
                            status = json.loads(response.read(4096))
                        if (not args.url.endswith("/ingest/outlook-bridge")
                                or not isinstance(status, dict)
                                or status.get("outlook_bridge_contract") != 1
                                or status.get("database") != "healthy"
                                or status.get("orchestrator") != "healthy"):
                            raise RuntimeError("Incompatible backend.")
                        url = args.url
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    print("Axet designer unavailable or unverified; messages remain pending.", file=sys.stderr)
                    time.sleep(args.interval)
                    continue
                phase, count = scan_folder(folder, store_id, outbox)
                if phase == "baseline":
                    print(f"Baseline complete: {count} existing messages were not sent.")
                elif count:
                    print(f"Queued {count} new message(s).")
                counts = outbox.counts()
                if not secret and args.secret_from_agent:
                    try:
                        secret = read_agent_secret(container, args.secret_from_agent)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                        secret = None
                if secret and not send_heartbeat(url, secret, counts):
                    print("Bridge heartbeat unavailable; monitoring continues.", file=sys.stderr)
                pending = outbox.pending()
                if pending and args.secret_from_agent:
                    try:
                        secret = read_agent_secret(container, args.secret_from_agent)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                        print("Agent credential unavailable; messages remain pending.", file=sys.stderr)
                        time.sleep(args.interval)
                        continue
                for key, payload in pending:
                    outcome, detail = send_payload(url, secret, key, payload)
                    outbox.record(key, outcome, detail)
                    print(f"Delivery: {outcome} ({detail}).")
                    if outcome != "done":
                        break
                print(f"Bridge status: {outbox.counts()}")
            except Exception as exc:
                # No message bodies, addresses, credentials, or exception text in logs.
                print(f"Outlook scan failed ({type(exc).__name__}); will retry.", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nBridge stopped.")
        return 0
    except Exception as exc:
        print(f"Bridge could not start: {type(exc).__name__}. Check mailbox name and Outlook availability.", file=sys.stderr)
        return 1
    finally:
        if outbox:
            outbox.close()
        if lock_file:
            lock_file.close()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
