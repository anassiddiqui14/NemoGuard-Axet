"""Tests for the reviewed-alert sender without network access."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("reviewed_sender", ROOT / "scripts/send_reviewed_alert.py")
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"status":"ingested"}'


class SenderTests(unittest.TestCase):
    def test_retry_uses_same_key_and_headers(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alert.json"
            path.write_text(json.dumps({"message": "fabricated"}), encoding="utf-8")
            args = ["sender", str(path), "--url", "http://localhost:5927/backend/api/ingest/webhook"]
            with patch.object(sys, "argv", args), patch.object(sender.getpass, "getpass", return_value="test-secret"), patch.object(sender.urllib.request, "build_opener", return_value=Opener()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sender.main(), 0)
                self.assertEqual(sender.main(), 0)
        self.assertEqual(requests[0].get_header("Idempotency-key"), requests[1].get_header("Idempotency-key"))
        self.assertEqual(requests[0].get_header("X-nemoguard-webhook-secret"), "test-secret")

    def test_redirect_not_followed(self):
        self.assertIsNone(sender.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
