from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import audit, current_user, get_session
from ..db.models import Comparison, Document, ParseRun, ParseWarning, RulesetVersion, User
from ..services import documents as svc
from ..services.blobstore import blob_path
from . import serialize as S

router = APIRouter(tags=["documents"])


class ConfirmIn(BaseModel):
    model_year_id: int | None = None
    oem: str | None = None
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    make_primary: bool | None = None


class ReparseIn(BaseModel):
    ruleset_version_id: int | None = None


@router.post("/documents", status_code=201)
def upload(files: list[UploadFile] = File(...), session: Session = Depends(get_session, scope="function"),
           user: User = Depends(current_user)):
    out, errors = [], []
    for f in files:
        try:
            doc = svc.ingest(session, f.file, f.filename or "upload", user.id)
        except svc.DocumentError as exc:
            errors.append({"filename": f.filename, "error": str(exc)})
            continue
        audit(session, user, "upload", "document", doc.id, filename=doc.original_filename)
        out.append(S.document(doc, detail=True))
    return {"documents": out, "errors": errors}


@router.get("/documents")
def list_documents(status: str | None = None, model_year_id: int | None = None,
                   q: str | None = None, session: Session = Depends(get_session, scope="function"),
                   _: User = Depends(current_user)):
    query = select(Document).order_by(Document.uploaded_at.desc())
    if status:
        query = query.where(Document.detection_status == status)
    if model_year_id:
        query = query.where(Document.model_year_id == model_year_id)
    if q:
        query = query.where(Document.original_filename.ilike(f"%{q}%"))
    return [S.document(d) for d in session.scalars(query.limit(500))]


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, session: Session = Depends(get_session, scope="function"),
                 _: User = Depends(current_user)):
    return S.document(_doc(session, doc_id), detail=True)


@router.post("/documents/{doc_id}/confirm")
def confirm(doc_id: int, body: ConfirmIn, session: Session = Depends(get_session, scope="function"),
            user: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    svc.confirm(session, doc, **body.model_dump(), user_id=user.id)
    audit(session, user, "confirm", "document", doc.id, model_year_id=doc.model_year_id)
    return S.document(doc, detail=True)


@router.post("/documents/{doc_id}/reject")
def reject(doc_id: int, session: Session = Depends(get_session, scope="function"),
           user: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    doc.detection_status, doc.model_year_id, doc.is_primary = "rejected", None, False
    audit(session, user, "reject", "document", doc.id)
    return S.document(doc)


@router.post("/documents/{doc_id}/primary")
def make_primary(doc_id: int, session: Session = Depends(get_session, scope="function"),
                 user: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    if doc.model_year_id is None:
        raise HTTPException(400, "confirm the document's model year first")
    svc.set_primary(session, doc)
    audit(session, user, "primary", "document", doc.id)
    return S.document(doc)


@router.post("/documents/{doc_id}/reparse")
def reparse(doc_id: int, body: ReparseIn, session: Session = Depends(get_session, scope="function"),
            user: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    version = session.get(RulesetVersion, body.ruleset_version_id) \
        if body.ruleset_version_id else None
    job = svc.queue_parse(session, doc, version, user_id=user.id)
    return S.job(job)


@router.get("/documents/{doc_id}/download")
def download(doc_id: int, session: Session = Depends(get_session, scope="function"),
             _: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    return FileResponse(blob_path(doc.blob_sha256), media_type=doc.blob.mime,
                        filename=doc.original_filename)


@router.delete("/documents/{doc_id}", status_code=204)
def delete(doc_id: int, session: Session = Depends(get_session, scope="function"),
           user: User = Depends(current_user)):
    doc = _doc(session, doc_id)
    used = session.scalar(select(Comparison.id).where(
        or_(Comparison.old_document_id == doc.id, Comparison.new_document_id == doc.id)))
    if used:
        raise HTTPException(409, "a comparison uses this document; delete it first")
    audit(session, user, "delete", "document", doc.id, filename=doc.original_filename)
    session.delete(doc)


@router.get("/parse-runs/{run_id}/warnings")
def warnings(run_id: int, session: Session = Depends(get_session, scope="function"),
             _: User = Depends(current_user)):
    if session.get(ParseRun, run_id) is None:
        raise HTTPException(404, "no such parse run")
    rows = session.scalars(select(ParseWarning).where(ParseWarning.parse_run_id == run_id)
                           .order_by(ParseWarning.id).limit(2000))
    return [{"kind": w.kind, "severity": w.severity, "message": w.message, "where": w.where}
            for w in rows]


def _doc(session: Session, doc_id: int) -> Document:
    doc = session.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "no such document")
    return doc
