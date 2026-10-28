"""
Axet .flows entrypoint shim.

The Axet `.flows` Python startup/stop tooling (setup_python_app.sh /
setup_python_app_stop.sh) expects the FastAPI ASGI app to be importable as
`app.main:app` relative to the selected backend application directory
(`app/backend`, i.e. this file's parent `backend/`).

NemoGuard's actual FastAPI application object is defined in
`backend/app/api/main.py` (ported from the original `src/api/main.py`).
This module simply re-exports that same `app` object under the path Axet's
generic startup scripts expect, so `uvicorn app.main:app` keeps working
unmodified inside the .flows runtime.
"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[1]

if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from .api.main import app  # noqa: F401

# Axet's generic Support page / deployment tooling checks a conventional
# "/backend/api/health" endpoint after deployment (see HOW-TO-START.md /
# DEPLOY-TO-AXET.md §9 "First thing to test"). NemoGuard's own health/status
# endpoint lives at "/api/v2/status" with a different response shape, so we
# add a thin alias here rather than changing the original NemoGuard contract.
@app.get("/backend/api/health")
@app.post("/backend/api/health")
async def _axet_health_alias():
    return {"statusCode": 200, "status": "ok"}

# Mount Scenario Cockpit simulator inside the aXet-hosted FastAPI process.
from simulator_backend.main import app as simulator_app
app.mount('/api/v2/sim', simulator_app)

