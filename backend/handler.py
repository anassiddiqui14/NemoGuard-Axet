"""Node-RED message handler for python-agent (reverse-proxy / bridge mode).

In reverse-proxy mode the agent still loads this entrypoint via the NDJSON
bridge and requires one of: handle_message, run, main, or process.
HTTP traffic is served separately by uvicorn from app.main:app.
"""

from typing import Any, Dict


def handle_message(msg: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    """Handle messages from Node-RED flows (inject / wiring), not HTTP."""
    payload = msg.get("payload", {})
    return {
        "status": "ok",
        "received_payload": payload,
        "node_id": node_id,
        "source": "ax-seed-architecture-backend",
    }
