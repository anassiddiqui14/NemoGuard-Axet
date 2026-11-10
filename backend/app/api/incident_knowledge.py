"""Authenticated incident knowledge submission and review endpoints."""
from datetime import datetime, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
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


class ContributionDraft(BaseModel):
    job_key: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    message_ids: list[str] = Field(min_length=1, max_length=100)
    confirmed: bool


class Review(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    note: str = Field(default="", max_length=2000)


class DocumentEdit(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    job_key: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=2000)


class DocumentArchive(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


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
    with db.get_connection() as conn:
        decision = conn.execute(
            "SELECT decision FROM incident_sop_decision WHERE incident_id=%s AND tenant_id=%s AND workspace_id=%s",
            (incident_id, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
    return JSONResponse({
        **context,
        "sop_decision": decision[0] if decision else None,
        "submissions": knowledge.list_items(db, current_user.tenant_id, current_user.workspace_id, incident_id),
    }, headers={"Cache-Control": "no-store"})


@router.get("/incidents/{incident_id}/knowledge/contributions")
def list_contributions(
    incident_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(require_incident_operator),
):
    """List operator messages as unverified source material for human review."""
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT m.message_id,m.intent,m.content,m.created_by,m.created_at FROM incident_message m "
            "JOIN incident i ON i.incident_id=m.incident_id "
            "WHERE i.incident_id=%s AND i.tenant_id=%s AND i.workspace_id=%s AND m.role='OPERATOR' "
            "ORDER BY m.created_at ASC,m.message_id ASC LIMIT %s OFFSET %s",
            (incident_id, current_user.tenant_id, current_user.workspace_id, limit + 1, offset),
        ).fetchall()
    items = [
        {"message_id": row[0], "intent": row[1], "content": row[2],
         "created_by": row[3], "created_at": str(row[4])}
        for row in rows[:limit]
    ]
    return JSONResponse({
        "items": items,
        "next_offset": offset + limit if len(rows) > limit else None,
        "notice": "Operator messages are unverified and are not approved SOP steps.",
    }, headers={"Cache-Control": "no-store"})


@router.post("/incidents/{incident_id}/knowledge/contribution-drafts", status_code=201)
def create_contribution_draft(
    incident_id: str, req: ContributionDraft,
    current_user: User = Depends(require_incident_operator),
):
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    if not req.confirmed:
        raise HTTPException(status_code=400, detail="Confirm the proposed procedure before submission.")
    try:
        item_id = knowledge.submit_contribution_draft(
            db, current_user.tenant_id, current_user.workspace_id, incident_id,
            req.job_key, req.title, req.content, current_user.user_id, req.message_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"knowledge_id": item_id, "status": "PENDING"}


@router.post("/incidents/{incident_id}/knowledge/no-sop")
def confirm_no_sop(incident_id: str, current_user: User = Depends(require_incident_operator)):
    db = get_database()
    require_scoped_incident(db, incident_id, current_user)
    if not knowledge.confirm_no_sop(db, current_user.tenant_id, current_user.workspace_id,
                                    incident_id, current_user.user_id):
        raise HTTPException(status_code=409, detail="The incident is not awaiting an SOP decision.")
    return {"status": "NO_SOP"}


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


@router.get("/admin/incident-knowledge-library")
def knowledge_library(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(require_role("admin")),
):
    db = get_database()
    with db.get_connection() as conn:
        cur = conn.execute(
            "SELECT knowledge_id,incident_id,job_key,kind,title,content,status,"
            "submitted_by,submitted_at,reviewed_by,reviewed_at,review_note "
            "FROM incident_knowledge WHERE tenant_id=%s AND workspace_id=%s "
            "ORDER BY submitted_at DESC,knowledge_id DESC LIMIT %s OFFSET %s",
            (current_user.tenant_id, current_user.workspace_id, limit + 1, offset),
        )
        names = [column[0] for column in cur.description]
        rows = cur.fetchall()
    return JSONResponse({
        "items": [dict(zip(names, row)) for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }, headers={"Cache-Control": "no-store"})


@router.put("/admin/incident-knowledge/{knowledge_id}")
def edit_knowledge(knowledge_id: str, req: DocumentEdit, current_user: User = Depends(require_role("admin"))):
    try:
        updated = knowledge.manage_item(
            get_database(), current_user.tenant_id, current_user.workspace_id,
            knowledge_id, current_user.user_id, req.reason,
            title=req.title, job_key=req.job_key, content=req.content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Document not found in this workspace.")
    return {"knowledge_id": knowledge_id, "status": "PENDING"}


@router.post("/admin/incident-knowledge/{knowledge_id}/archive")
def archive_knowledge(knowledge_id: str, req: DocumentArchive, current_user: User = Depends(require_role("admin"))):
    try:
        updated = knowledge.manage_item(
            get_database(), current_user.tenant_id, current_user.workspace_id,
            knowledge_id, current_user.user_id, req.reason, archive=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Document not found in this workspace.")
    return {"knowledge_id": knowledge_id, "status": "ARCHIVED"}


@router.get("/admin/incident-knowledge/{knowledge_id}/sources")
def knowledge_sources(knowledge_id: str, current_user: User = Depends(require_role("admin"))):
    db = get_database()
    with db.get_connection() as conn:
        item = conn.execute(
            "SELECT incident_id FROM incident_knowledge WHERE knowledge_id=%s AND tenant_id=%s AND workspace_id=%s",
            (knowledge_id, current_user.tenant_id, current_user.workspace_id),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="Document not found in this workspace.")
        rows = conn.execute(
            "SELECT m.message_id,m.intent,m.content,m.created_by,m.created_at "
            "FROM incident_knowledge_source s JOIN incident_message m ON m.message_id=s.message_id "
            "WHERE s.knowledge_id=%s AND m.incident_id=%s AND m.role='OPERATOR' "
            "ORDER BY m.created_at,m.message_id",
            (knowledge_id, item[0]),
        ).fetchall()
    return JSONResponse({"items": [
        {"message_id": row[0], "intent": row[1], "content": row[2],
         "created_by": row[3], "created_at": str(row[4])}
        for row in rows
    ]}, headers={"Cache-Control": "no-store"})


@router.post("/admin/incident-knowledge/{knowledge_id}/review")
def review_knowledge(knowledge_id: str, req: Review, current_user: User = Depends(require_role("admin"))):
    if "\x00" in req.note:
        raise HTTPException(status_code=400, detail="Review notes must not contain NUL characters.")
    try:
        updated = knowledge.review(
            get_database(), current_user.tenant_id, current_user.workspace_id,
            knowledge_id, req.decision, current_user.user_id, req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Pending submission not found in this workspace.")
    return {"knowledge_id": knowledge_id, "status": req.decision}
