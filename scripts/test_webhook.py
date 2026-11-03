"""Send one synthetic webhook. WARNING: this can create an incident."""
import argparse
import json
import os
import sys

import httpx


def main():
    parser = argparse.ArgumentParser(
        description="Send a synthetic alert; this may create an incident and start triage."
    )
    parser.add_argument(
        "--url", required=True,
        help="Full webhook URL, e.g. http://localhost:20456/backend/api/ingest/webhook",
    )
    parser.add_argument(
        "--idempotency-key", required=True,
        help="Stable unique event ID; reuse it when retrying this exact payload.",
    )
    args = parser.parse_args()
    secret = os.environ.get("NEMOGUARD_WEBHOOK_SECRET", "")
    if not secret:
        print("Set NEMOGUARD_WEBHOOK_SECRET in the environment first.", file=sys.stderr)
        return 2

    payload = {
        "source": "Synthetic test",
        "type": "Monitor Alert",
        "monitor_name": "Synthetic pipeline failure",
        "message": "Test pipeline failed its health check.",
        "tags": ["service:test-pipeline", "severity:high"],
    }
    try:
        response = httpx.post(
            args.url,
            json=payload,
            headers={
                "X-NemoGuard-Webhook-Secret": secret,
                "Idempotency-Key": args.idempotency_key,
            },
            timeout=120.0,
        )
        print(f"HTTP {response.status_code}")
        try:
            print(json.dumps(response.json(), indent=2))
        except ValueError:
            print("Response was not JSON.")
        return 0 if response.is_success else 1
    except httpx.RequestError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
