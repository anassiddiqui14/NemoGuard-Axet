"""Offline tests for the classic Outlook bridge."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import outlook_folder_bridge as bridge


class Collection:
    def __init__(self, values):
        self.values = values

    @property
    def Count(self):
        return len(self.values)

    def Item(self, index):
        return self.values[index - 1]


class Response:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return json.dumps(self.result).encode()


class Opener:
    def __init__(self, results):
        self.results = iter(results)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return Response(value)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.sqlite"
        self.url = "http://localhost:1234/backend/api/ingest/webhook"
        self.outbox = bridge.Outbox(self.path, "Test mailbox", bridge.FOLDER_NAME, self.url)

    def tearDown(self):
        self.outbox.close()
        self.temp.cleanup()

    @staticmethod
    def mail(identity, subject="Test alert", body="Synthetic failure"):
        return SimpleNamespace(Class=43, EntryID=identity, Subject=subject, Body=body)

    def test_baseline_then_new_message_and_retry_survive_restart(self):
        old = self.mail("old")
        folder = SimpleNamespace(Items=Collection([old]))
        self.assertEqual(bridge.scan_folder(folder, "store", self.outbox), ("baseline", 1))
        self.assertEqual(self.outbox.pending(), [])

        folder.Items.values.append(self.mail("new"))
        self.assertEqual(bridge.scan_folder(folder, "store", self.outbox), ("scanned", 1))
        self.assertEqual(bridge.scan_folder(folder, "store", self.outbox), ("scanned", 0))
        key, payload = self.outbox.pending()[0]
        self.outbox.record(key, "retry", "network_or_response_error")
        self.outbox.close()
        self.outbox = bridge.Outbox(self.path, "Test mailbox", bridge.FOLDER_NAME, self.url)
        self.assertEqual(self.outbox.pending(), [(key, payload)])
        self.outbox.record(key, "done", "ingested")
        self.assertEqual(self.outbox.pending(), [])
        self.assertIsNone(self.outbox.conn.execute(
            "SELECT payload FROM messages WHERE message_key=?", (key,)
        ).fetchone()[0])

    def test_attachments_survive_retry_and_unsupported_are_reported(self):
        class Attachment:
            def __init__(self, name, content):
                self.FileName, self.content, self.Size = name, content, len(content)

            def SaveAsFile(self, path):
                Path(path).write_bytes(self.content)

        self.outbox.baseline([])
        item = self.mail("new")
        item.Attachments = Collection([
            Attachment("full.log", b"start\n" + b"x" * 5000 + b"\nEND"),
            Attachment("image.png", b"binary"),
        ])
        self.assertEqual(bridge.scan_folder(SimpleNamespace(Items=Collection([item])), "store", self.outbox), ("scanned", 1))
        key, payload = self.outbox.pending()[0]
        data = json.loads(payload)
        self.assertTrue(data["attachments"][0]["text"].endswith("END"))
        self.assertEqual(data["omitted_attachments"], [{"name": "image.png", "reason": "unsupported_format"}])
        self.outbox.record(key, "retry", "network")
        self.outbox.close()
        self.outbox = bridge.Outbox(self.path, "Test mailbox", bridge.FOLDER_NAME, self.url)
        self.assertEqual(self.outbox.pending(), [(key, payload)])

    def test_oversized_attachment_blocks_delivery(self):
        self.outbox.baseline([])
        item = self.mail("large-attachment")
        item.Attachments = Collection([SimpleNamespace(FileName="large.log", Size=bridge.MAX_ATTACHMENT_BYTES + 1)])
        self.assertEqual(bridge.scan_folder(SimpleNamespace(Items=Collection([item])), "store", self.outbox), ("scanned", 0))
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(self.outbox.counts()["blocked"], 1)

    def test_transient_attachment_error_retries_without_blocking_other_messages(self):
        self.outbox.baseline([])
        first = self.mail("first")
        second = self.mail("second")
        folder = SimpleNamespace(Items=Collection([first, second]))
        with patch.object(bridge, "capture_attachments", side_effect=[OSError("temporary"), ([], [])]):
            self.assertEqual(bridge.scan_folder(folder, "store", self.outbox), ("scanned", 1))
        self.assertFalse(self.outbox.known(bridge.message_key("store", "first")))
        self.assertTrue(self.outbox.known(bridge.message_key("store", "second")))
        self.assertEqual(bridge.scan_folder(folder, "store", self.outbox), ("scanned", 1))
        self.assertEqual(len(self.outbox.pending()), 2)

    def test_no_baseline_when_enumeration_fails(self):
        folder = SimpleNamespace(Items=Collection([self.mail("")]))
        with self.assertRaises(RuntimeError):
            bridge.scan_folder(folder, "store", self.outbox)
        self.assertFalse(self.outbox.baselined())

    def test_limits_and_configuration_binding(self):
        self.assertFalse(self.outbox.add("empty", "Subject", " "))
        self.assertFalse(self.outbox.add("large", "Subject", "x" * (bridge.MAX_BODY_CHARS + 1)))
        self.assertEqual(self.outbox.counts()["blocked"], 2)
        with self.assertRaises(ValueError):
            bridge.Outbox(self.path, "Different mailbox", bridge.FOLDER_NAME, self.url)
        for url in ("https://example.com/a", "http://example.com/a", "http://localhost/a?x=y", "http://user@localhost/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bridge.validate_url(url)
        self.assertEqual(bridge.validate_url(self.url), self.url)

    def test_sender_retries_same_bytes_and_marks_ignored(self):
        key = bridge.message_key("store", "id")
        payload = bridge.canonical_json({"message": "synthetic"})
        auth_error = HTTPError(self.url, 401, "unauthorized", {}, io.BytesIO())
        opener = Opener([auth_error, {"status": "ignored"}])
        self.assertEqual(bridge.send_payload(self.url, "secret", key, payload, opener), ("retry", "http_401"))
        self.assertEqual(bridge.send_payload(self.url, "secret", key, payload, opener), ("done", "ignored"))
        self.assertEqual(opener.requests[0].data, opener.requests[1].data)
        self.assertEqual(opener.requests[0].get_header("Idempotency-key"), key)

    def test_gateway_size_rejection_blocks_and_preserves_payload(self):
        self.outbox.add("key", "Subject", "Body", [{"name": "job.log", "text": "log"}])
        key, payload = self.outbox.pending()[0]
        error = HTTPError(self.url, 413, "too large", {}, io.BytesIO())
        outcome, detail = bridge.send_payload(self.url, "secret", key, payload, Opener([error]))
        self.assertEqual((outcome, detail), ("blocked", "http_413"))
        self.outbox.record(key, outcome, detail)
        row = self.outbox.conn.execute(
            "SELECT status, payload FROM messages WHERE message_key=?", (key,)
        ).fetchone()
        self.assertEqual(row, ("blocked", payload))

    def test_heartbeat_sends_counts_only_and_tolerates_failure(self):
        class HeartbeatResponse(Response):
            status = 200

        class HeartbeatOpener:
            def __init__(self, fail=False):
                self.fail = fail
                self.request = None

            def open(self, request, timeout):
                self.request = request
                if self.fail:
                    raise OSError("offline")
                return HeartbeatResponse({})

        url = "http://localhost:1234/backend/api/ingest/outlook-bridge"
        opener = HeartbeatOpener()
        self.assertTrue(bridge.send_heartbeat(url, "secret", {"pending": 2, "blocked": 1, "baseline": 3, "done": 9}, opener))
        self.assertEqual(opener.request.full_url, "http://localhost:1234/backend/api/ingest/outlook-bridge-heartbeat")
        self.assertEqual(json.loads(opener.request.data), {"pending": 2, "blocked": 1, "baseline": 3})
        self.assertFalse(bridge.send_heartbeat(url, "secret", {}, HeartbeatOpener(fail=True)))

    def test_http_request_to_loopback_server(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                received["body"] = json.loads(self.rfile.read(length))
                received["key"] = self.headers.get("Idempotency-Key")
                received["secret"] = self.headers.get("X-NemoGuard-Webhook-Secret")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ingested"}).encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 5
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/webhook"
            result = bridge.send_payload(
                url, "test-secret", "outlook:test",
                bridge.canonical_json({"message": "synthetic"}),
            )
            thread.join(timeout=6)
            self.assertEqual(result, ("done", "ingested"))
            self.assertEqual(received, {
                "body": {"message": "synthetic"},
                "key": "outlook:test",
                "secret": "test-secret",
            })
        finally:
            thread.join(timeout=6)
            server.server_close()

    def test_designer_discovery_handles_port_changes(self):
        prefix = "deptapps-flows-designer-container-28477-"
        outputs = [
            prefix + "abc\t172.17.0.1:27223->1880/tcp, 172.17.0.1:33076->3307/tcp",
            prefix + "def\t172.17.0.1:31000->1880/tcp",
        ]
        opener = Opener([
            {"database": "healthy", "orchestrator": "available", "outlook_bridge_contract": 1, "outlook_bridge_attachments": True},
            {"database": "healthy", "orchestrator": "available", "outlook_bridge_contract": 1, "outlook_bridge_attachments": True},
        ])
        with patch.object(bridge.subprocess, "run", side_effect=[
            SimpleNamespace(stdout=output) for output in outputs
        ]), patch.object(bridge.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(bridge.discover_designer_url(prefix), "http://127.0.0.1:27223/backend/api/ingest/outlook-bridge")
            self.assertEqual(bridge.discover_designer_url(prefix), "http://127.0.0.1:31000/backend/api/ingest/outlook-bridge")

    def test_designer_discovery_fails_closed(self):
        prefix = "deptapps-flows-designer-container-28477-"
        ambiguous = prefix + "a\t127.0.0.1:1000->1880/tcp\n" + prefix + "b\t127.0.0.1:2000->1880/tcp"
        with patch.object(bridge.subprocess, "run", return_value=SimpleNamespace(stdout=ambiguous)):
            with self.assertRaises(RuntimeError):
                bridge.discover_designer_url(prefix)
        one = prefix + "a\t127.0.0.1:1000->1880/tcp"
        for status in (
            {"database": "unhealthy"},
            {"database": "healthy", "orchestrator": "healthy"},
        ):
            with patch.object(bridge.subprocess, "run", return_value=SimpleNamespace(stdout=one)), patch.object(
                bridge.urllib.request, "build_opener", return_value=Opener([status])
            ):
                with self.assertRaises(RuntimeError):
                    bridge.discover_designer_url(prefix)

    def test_agent_secret_lookup_fails_closed(self):
        cwd = "/usr/src/node-red/.node-red/python-envs/test/src/agent"
        with patch.object(bridge.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"test-secret")) as run:
            self.assertEqual(bridge.read_agent_secret("container", cwd), "test-secret")
            self.assertEqual(run.call_args.kwargs["input"], (cwd + "\n").encode())
            self.assertNotIn("test-secret", str(run.call_args.args))
        for result in (
            SimpleNamespace(returncode=2, stdout=b""),
            SimpleNamespace(returncode=0, stdout=b""),
            SimpleNamespace(returncode=0, stdout=b"bad\nheader"),
            SimpleNamespace(returncode=0, stdout=b"\xff"),
        ):
            with self.subTest(result=result), patch.object(bridge.subprocess, "run", return_value=result):
                with self.assertRaises(RuntimeError):
                    bridge.read_agent_secret("container", cwd)
        with self.assertRaises(ValueError):
            bridge.read_agent_secret("container", "/tmp/agent")

    def test_exact_folder_and_mailbox_selection(self):
        folder = SimpleNamespace(Name=bridge.FOLDER_NAME)
        root = SimpleNamespace(Folders=Collection([folder]))
        store = SimpleNamespace(
            DisplayName="Mailbox", StoreID="store",
            GetDefaultFolder=lambda folder_type: SimpleNamespace(Parent=root) if folder_type == 6 else None,
        )
        namespace = SimpleNamespace(Stores=Collection([store]))
        self.assertEqual(bridge.select_folder(namespace, "Mailbox"), (folder, "store"))
        with self.assertRaises(ValueError):
            bridge.select_folder(namespace, "Other")
        namespace.Stores.values.append(store)
        with self.assertRaises(ValueError):
            bridge.select_folder(namespace, "Mailbox")


if __name__ == "__main__":
    unittest.main()
