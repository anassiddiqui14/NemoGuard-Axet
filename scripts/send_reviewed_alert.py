"""Send one previously reviewed JSON alert to a local NemoGuard webhook.

This can create an incident and send the payload to the configured LLM.
"""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Reviewed JSON file to send")
    parser.add_argument("--url", required=True, help="Full local HTTP or HTTPS webhook URL")
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if url.username or url.password or url.fragment or not (
        url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))
    ):
        parser.error("Use an HTTPS URL or an HTTP localhost URL without embedded credentials.")
    try:
        data = args.file.read_bytes()
        payload = json.loads(data.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Cannot read JSON file: {exc}", file=sys.stderr)
        return 2

    # Canonicalize exactly as the API does. Identical payloads deliberately
    # share a delivery key. Use a different payload for a distinct event.
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    key = "reviewed:" + hashlib.sha256(canonical).hexdigest()
    secret = getpass.getpass("Webhook secret (input hidden): ")
    if not secret:
        print("No secret entered.", file=sys.stderr)
        return 2
    req = urllib.request.Request(
        args.url,
        data=canonical,
        headers={
            "Content-Type": "application/json",
            "X-NemoGuard-Webhook-Secret": secret,
            "Idempotency-Key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=180) as response:
            result = json.loads(response.read())
            if not isinstance(result, dict):
                raise ValueError("Unexpected response")
            print(f"HTTP {response.status}")
            print(json.dumps({k: result[k] for k in ("status", "alert_id", "incident_id", "duplicate", "triage_scheduling_status") if k in result}, indent=2))
            if result.get("status") not in ("ingested", "ingested_and_correlated", "ingested_and_incident_created", "ingested_and_incident_auto_resolved"):
                print("The webhook did not confirm ingestion.", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: delivery not confirmed. Retry the same reviewed file.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Delivery outcome unknown ({type(exc).__name__}). Retry the same reviewed file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
