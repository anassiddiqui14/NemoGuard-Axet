"""Classify a watcher diagnostic failure without printing credentials or model output.

Run inside the Axet container via its agent virtual environment, with the
same approved environment as the backend. This makes one LLM call and
does not invoke the webhook or write to the database.
"""
import asyncio
import contextlib
import io
import re

from app.domain.agents.watcher_agent import WatcherAgent

PAYLOAD = {
    "source": "Datadog",
    "type": "Monitor Alert",
    "monitor_name": "[HIGH] fictional_orders_batch failure",
    "message": "The fictional_orders_batch data pipeline has failed for 3 consecutive runs due to a database connection timeout. Downstream reports may be delayed.",
    "tags": ["service:fictional_orders_batch", "severity:high", "env:staging"],
    "run_id": "AXET-WEBHOOK-CHECK-20261103-01",
}


def main():
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(WatcherAgent().analyze(PAYLOAD, []))
        if not isinstance(result, dict):
            category = "invalid response type"
        elif "error" not in result:
            category = "classification returned"
        else:
            error = str(result["error"]).lower()
            match = re.search(r"\b(?:status code|error code|http)\s*[:=]?\s*(401|403|429)\b", error)
            if match:
                print("Model HTTP status:", match.group(1))
            if "not valid json" in error:
                category = "invalid JSON response"
            elif "401" in error or "403" in error or "authentication" in error:
                category = "authentication or authorization error"
            elif "429" in error:
                category = "rate limited"
            elif "timeout" in error or "timed out" in error:
                category = "timeout"
            elif "connection" in error:
                category = "connection error"
            else:
                category = "other model error"
        print("Diagnostic:", category)
    except Exception as exc:
        print("Diagnostic could not complete; exception type:", type(exc).__name__)


if __name__ == "__main__":
    main()
