"""Authenticated incident knowledge submission and review endpoints."""
from datetime import datetime, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.auth import User, get_current_user, require_role
from app.domain import incident_knowledge as knowledge
from app.store.database_factory import get_database

router = APIRouter(prefix="/api/v2")


class Submission(BaseModel):
    kind: Literal["SOP", "DEPENDENCY"]
    job_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=100_000)
    filename: str = Field(default="", max_length=255)
    file_base64: str = Field(default="", max_length=2_796_204)


class Review(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    note: str = Field(default="", max_length=2000)


class WorkspaceAssignment(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def require_incident_operator(current_user: User = Depends(get_current_user)):
    if not set(current_user.roles).intersection({"admin", "operator", "commander"}):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user


@router.post("/admin/incidents/{incident_id}/assign-workspace")
def assign_workspace(incident_id: str, req: WorkspaceAssignment, current_user: User = Depends(require_role("admin"))):
    reason = req.reason.strip()
    if not reason or "\x00" in reason:
        raise HTTPException(status_code=400, detail="A valid assignment reason is required.")
    if not current_user.workspace_id or not current_user.workspace_id.strip():
        raise HTTPException(status_code=400, detail="Your account has no assigned workspace.")
    db = get_database()
    now = datetime.now(timezone.utc).isoformat()
    with db.get_connection() as conn:
        result = conn.execute(
            "UPDATE incident SET workspace_id=%s, updated_at=%s "
            "WHERE incident_id=%s AND tenant_id=%s AND workspace_id IS NULL",
            (current_user.workspace_id, now, incident_id, current_user.tenant_id),
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=409, detail="Incident is not available for assignment.")
        conn.execute(
            "INSERT INTO audit_event (audit_event_id,incident_id,tenant_id,workspace_id,"
            "actor_type,actor_id,event_type,event_summary,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            ("AUD-" + uuid.uuid4().hex, incident_id, current_user.tenant_id,
             current_user.workspace_id, "HUMAN", current_user.user_id,
             "WORKSPACE_ASSIGNED", "Workspace assigned by administrator: " + reason, now),
        )
    return {"status": "assigned", "workspace_id": current_user.workspace_id}


def require_scoped_incident(db, incident_id, user):
    # Unlike legacy incident access, do not treat NULL workspace as belonging
    # to every workspace: persisted knowledge must never cross this boundary.
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id FROM incident WHERE incident_id=%s AND tenant_id=%s",
            (incident_id, user.tenant_id),
        ).fetchone()
    if not row or (row[0] is not None and row[0] != user.workspace_id):
        raise HTTPException(status_code=404, detail="Incident not found in this workspace.")
    if row[0] is None:
        raise HTTPException(
            status_code=409,
            detail="This incident has no assigned workspace. An administrator must establish its ownership before incident references can be used.",
        )


@router.get("/incidents/{incident_id}/knowledge")
def incident_knowledge(incident_id: str, current_user: User = Depends(get_current_user)):
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    context = knowledge.context_for_incident(db, incident_id)
    return JSONResponse({
        **context,
        "submissions": knowledge.list_items(db, current_user.tenant_id, current_user.workspace_id, incident_id),
    }, headers={"Cache-Control": "no-store"})


@router.post("/incidents/{incident_id}/knowledge", status_code=201)
def submit_knowledge(incident_id: str, req: Submission, current_user: User = Depends(require_incident_operator)):
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    try:
        if req.kind == "SOP":
            if not req.filename or not req.file_base64:
                raise ValueError("Select a .txt, .docx or .pdf SOP document.")
            content = knowledge.extract_document(req.filename, req.file_base64)
        else:
            content = req.content
        item_id = knowledge.submit(
            db, current_user.tenant_id, current_user.workspace_id,
            incident_id, req.job_key, req.kind, req.title, content, current_user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"knowledge_id": item_id, "status": "PENDING"}


@router.post("/incidents/{incident_id}/knowledge/reinvestigate")
async def reinvestigate_knowledge(incident_id: str, current_user: User = Depends(require_incident_operator)):
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    from app.domain.commander.conversation import CommanderConversation, IncidentNotFoundError
    try:
        return await CommanderConversation().guide(
            incident_id, current_user.tenant_id,
            "Please re-investigate using the submitted incident references.",
            current_user.user_id, guidance_type="INVESTIGATION_DIRECTION", is_fact=False,
        )
    except IncidentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Incident not found in this workspace.") from exc


@router.get("/admin/incident-knowledge")
def pending_knowledge(current_user: User = Depends(require_role("admin"))):
    return JSONResponse({"items": knowledge.list_items(
        get_database(), current_user.tenant_id, current_user.workspace_id, status="PENDING"
    )}, headers={"Cache-Control": "no-store"})


@router.post("/admin/incident-knowledge/{knowledge_id}/review")
def review_knowledge(knowledge_id: str, req: Review, current_user: User = Depends(require_role("admin"))):
    if "\x00" in req.note:
        raise HTTPException(status_code=400, detail="Review notes must not contain NUL characters.")
    updated = knowledge.review(
        get_database(), current_user.tenant_id, current_user.workspace_id,
        knowledge_id, req.decision, current_user.user_id, req.note,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Pending submission not found in this workspace.")
    return {"knowledge_id": knowledge_id, "status": req.decision}
