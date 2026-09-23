from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import audit, current_user, get_session, require_admin
from ..db.models import (
    Brand,
    Comparison,
    Document,
    ModelYear,
    Oem,
    Timeline,
    User,
    VehicleModel,
)
from ..services import catalog
from . import serialize as S

router = APIRouter(tags=["catalog"])


class OemIn(BaseModel):
    name: str = Field(min_length=1)
    prompt_name: str | None = None


class NameIn(BaseModel):
    name: str = Field(min_length=1)


class YearIn(BaseModel):
    year: int = Field(ge=1900, le=2200)
    variant_label: str | None = None


@router.get("/catalog")
def tree(session: Session = Depends(get_session, scope="function"), _: User = Depends(current_user)):
    doc_counts = dict(session.execute(
        select(Document.model_year_id, func.count()).where(
            Document.detection_status == "confirmed").group_by(Document.model_year_id)).all())
    out = []
    for oem in session.scalars(select(Oem).order_by(Oem.name)):
        out.append({
            "id": oem.id, "slug": oem.slug, "name": oem.name, "prompt_name": oem.prompt_name,
            "brands": [{
                "id": b.id, "slug": b.slug, "name": b.name,
                "models": [{
                    "id": m.id, "slug": m.slug, "name": m.name,
                    "years": [{"id": y.id, "year": y.year, "variant_label": y.variant_label,
                               "documents": doc_counts.get(y.id, 0)} for y in m.years],
                } for m in b.models],
            } for b in oem.brands],
        })
    return out


@router.post("/oems", status_code=201)
def create_oem(body: OemIn, session: Session = Depends(get_session, scope="function"),
               user: User = Depends(require_admin)):
    if catalog.find_oem(session, body.name):
        raise HTTPException(409, "that OEM exists")
    oem = catalog.ensure_oem(session, body.name, body.prompt_name)
    audit(session, user, "create", "oem", oem.id, name=oem.name)
    return {"id": oem.id}


@router.patch("/oems/{oem_id}")
def update_oem(oem_id: int, body: OemIn, session: Session = Depends(get_session, scope="function"),
               user: User = Depends(require_admin)):
    oem = session.get(Oem, oem_id) or _404("OEM")
    oem.name = body.name
    if body.prompt_name:
        oem.prompt_name = body.prompt_name
    audit(session, user, "update", "oem", oem.id, name=oem.name, prompt_name=oem.prompt_name)
    return {"id": oem.id}


@router.post("/oems/{oem_id}/brands", status_code=201)
def create_brand(oem_id: int, body: NameIn, session: Session = Depends(get_session, scope="function"),
                 user: User = Depends(current_user)):
    oem = session.get(Oem, oem_id) or _404("OEM")
    brand = catalog.ensure_brand(session, oem, body.name)
    audit(session, user, "create", "brand", brand.id, name=brand.name)
    return {"id": brand.id}


@router.post("/brands/{brand_id}/models", status_code=201)
def create_model(brand_id: int, body: NameIn, session: Session = Depends(get_session, scope="function"),
                 user: User = Depends(current_user)):
    brand = session.get(Brand, brand_id) or _404("brand")
    model = catalog.ensure_model(session, brand, body.name)
    audit(session, user, "create", "model", model.id, name=model.name)
    return {"id": model.id}


@router.post("/models/{model_id}/years", status_code=201)
def create_year(model_id: int, body: YearIn, session: Session = Depends(get_session, scope="function"),
                user: User = Depends(current_user)):
    model = session.get(VehicleModel, model_id) or _404("model")
    my = catalog.ensure_year(session, model, body.year, body.variant_label)
    if body.variant_label is not None:
        my.variant_label = body.variant_label
    audit(session, user, "create", "model_year", my.id, year=my.year)
    return {"id": my.id}


@router.get("/models/{model_id}")
def overview(model_id: int, session: Session = Depends(get_session, scope="function"),
             _: User = Depends(current_user)):
    model = session.get(VehicleModel, model_id) or _404("model")
    years = []
    for y in model.years:
        docs = session.scalars(select(Document).where(Document.model_year_id == y.id)
                               .order_by(Document.is_primary.desc(),
                                         Document.uploaded_at.desc())).all()
        years.append({"id": y.id, "year": y.year, "variant_label": y.variant_label,
                      "documents": [S.document(d) for d in docs]})
    comparisons = session.scalars(select(Comparison).where(Comparison.model_id == model.id)
                                  .order_by(Comparison.id.desc())).all()
    timelines = session.scalars(select(Timeline).where(Timeline.model_id == model.id)
                                .order_by(Timeline.id.desc())).all()
    return {
        "id": model.id, "name": model.name, "slug": model.slug,
        "brand": {"id": model.brand.id, "name": model.brand.name},
        "oem": {"id": model.brand.oem.id, "name": model.brand.oem.name},
        "years": years,
        "comparisons": [S.comparison(c) for c in comparisons],
        "timelines": [{"id": t.id, "name": t.name, "created_at": S.ts(t.created_at),
                       "steps": len(t.comparisons)} for t in timelines],
    }


@router.get("/model-years/{year_id}")
def year_detail(year_id: int, session: Session = Depends(get_session, scope="function"),
                _: User = Depends(current_user)):
    my = session.get(ModelYear, year_id) or _404("model year")
    return S.model_year(my)


def _404(what: str):
    raise HTTPException(404, f"no such {what}")
