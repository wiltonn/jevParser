"""OEM -> brand -> model -> year, with find-or-create by name."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Brand, ModelYear, Oem, VehicleModel


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x"


def find_oem(session: Session, name: str) -> Oem | None:
    return session.scalar(select(Oem).where(
        (func.lower(Oem.name) == name.lower()) | (Oem.slug == slugify(name))))


def find_brand(session: Session, oem_id: int | None, name: str) -> Brand | None:
    q = select(Brand).where(
        (func.lower(Brand.name) == name.lower()) | (Brand.slug == slugify(name)))
    if oem_id is not None:
        q = q.where(Brand.oem_id == oem_id)
    return session.scalar(q)


def find_model(session: Session, brand_id: int, name: str) -> VehicleModel | None:
    return session.scalar(select(VehicleModel).where(
        VehicleModel.brand_id == brand_id,
        (func.lower(VehicleModel.name) == name.lower())
        | (VehicleModel.slug == slugify(name))))


def ensure_oem(session: Session, name: str, prompt_name: str | None = None) -> Oem:
    oem = find_oem(session, name)
    if oem is None:
        oem = Oem(slug=slugify(name), name=name, prompt_name=prompt_name or name)
        session.add(oem)
        session.flush()
    return oem


def ensure_brand(session: Session, oem: Oem, name: str) -> Brand:
    brand = find_brand(session, oem.id, name)
    if brand is None:
        brand = Brand(oem_id=oem.id, slug=slugify(name), name=name)
        session.add(brand)
        session.flush()
    return brand


def ensure_model(session: Session, brand: Brand, name: str) -> VehicleModel:
    model = find_model(session, brand.id, name)
    if model is None:
        model = VehicleModel(brand_id=brand.id, slug=slugify(name), name=name)
        session.add(model)
        session.flush()
    return model


def ensure_year(session: Session, model: VehicleModel, year: int,
                variant_label: str | None = None) -> ModelYear:
    my = session.scalar(select(ModelYear).where(ModelYear.model_id == model.id,
                                                ModelYear.year == year))
    if my is None:
        my = ModelYear(model_id=model.id, year=year, variant_label=variant_label)
        session.add(my)
        session.flush()
    return my


def year_label(my: ModelYear) -> str:
    return str(my.year)
