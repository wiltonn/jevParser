"""The schema.

Four areas:

*   **Catalog** -- OEM -> brand -> model -> model year.  The year a document
    belongs to is a row here, not a label typed on a command line.
*   **Documents** -- uploaded order guides, stored once by content hash, and
    their parse runs.  A parse is cached per (document, ruleset version).
*   **Rules** -- rulesets scoped by OEM (optionally brand/model), a model-year
    range and a source format, each with immutable published versions.
*   **Comparisons** -- runs of the pipeline, their persisted results, and the
    jobs that execute them.

Enum-like columns are plain strings so a Postgres move needs no type migration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JSONType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, **kw)


# --------------------------------------------------------------------------
# People and settings.
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    #: ``admin`` manages rulesets, users and settings; ``analyst`` runs comparisons.
    role: Mapped[str] = mapped_column(String(20), default="analyst")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSONType)
    at: Mapped[datetime] = _ts(index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: Plain JSON, or a Fernet token string when ``is_secret``.
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONType)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = _ts(onupdate=utcnow)


# --------------------------------------------------------------------------
# Catalog.
# --------------------------------------------------------------------------

class Oem(Base):
    __tablename__ = "oems"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    #: How judgment questions name the manufacturer ("GM").
    prompt_name: Mapped[str] = mapped_column(String(64))

    brands: Mapped[list[Brand]] = relationship(back_populates="oem", order_by="Brand.name")


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("oem_id", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))

    oem: Mapped[Oem] = relationship(back_populates="brands")
    models: Mapped[list[VehicleModel]] = relationship(back_populates="brand",
                                                      order_by="VehicleModel.name")


class VehicleModel(Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("brand_id", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))

    brand: Mapped[Brand] = relationship(back_populates="models")
    years: Mapped[list[ModelYear]] = relationship(back_populates="model",
                                                  order_by="ModelYear.year")


class ModelYear(Base):
    __tablename__ = "model_years"
    __table_args__ = (UniqueConstraint("model_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    #: How the guide titles itself when it differs from the model ("Yukon / Denali").
    variant_label: Mapped[str | None] = mapped_column(String(128))

    model: Mapped[VehicleModel] = relationship(back_populates="years")


# --------------------------------------------------------------------------
# Documents.
# --------------------------------------------------------------------------

class Blob(Base):
    """File content, stored once however many times it is uploaded."""

    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(128))
    storage_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _ts()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(16))
    model_year_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_years.id", ondelete="SET NULL"), index=True)
    #: Ranked catalog guesses: [{oem, brand, model, year, confidence, source}].
    detection: Mapped[dict | None] = mapped_column(JSONType)
    #: ``pending`` until someone confirms which model year this is.
    detection_status: Mapped[str] = mapped_column(String(16), default="pending")
    #: The document a comparison uses for its model year by default.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = _ts()

    blob: Mapped[Blob] = relationship()
    model_year: Mapped[ModelYear | None] = relationship()
    parse_runs: Mapped[list[ParseRun]] = relationship(
        back_populates="document", order_by="ParseRun.id.desc()",
        cascade="all, delete-orphan")


class ParseRun(Base):
    """One document parsed under one ruleset version."""

    __tablename__ = "parse_runs"
    __table_args__ = (UniqueConstraint("document_id", "ruleset_version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    ruleset_version_id: Mapped[int] = mapped_column(ForeignKey("ruleset_versions.id"))
    #: ``queued`` | ``running`` | ``ok`` | ``warnings`` | ``failed``
    status: Mapped[str] = mapped_column(String(16), default="queued")
    #: [{name, kind, rows, columns, footnotes, images}]
    sheet_summary: Mapped[list | None] = mapped_column(JSONType)
    #: relation -> clause count, including ``unstructured``.
    relation_histogram: Mapped[dict | None] = mapped_column(JSONType)
    fatal_count: Mapped[int] = mapped_column(Integer, default=0)
    warn_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="parse_runs")
    ruleset_version: Mapped[RulesetVersion] = relationship()
    warnings: Mapped[list[ParseWarning]] = relationship(
        back_populates="parse_run", cascade="all, delete-orphan")


class ParseWarning(Base):
    __tablename__ = "parse_warnings"

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_run_id: Mapped[int] = mapped_column(ForeignKey("parse_runs.id", ondelete="CASCADE"),
                                              index=True)
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    where: Mapped[str] = mapped_column(String(255))

    parse_run: Mapped[ParseRun] = relationship(back_populates="warnings")


# --------------------------------------------------------------------------
# Rules.
# --------------------------------------------------------------------------

class Ruleset(Base):
    """A named line of rule versions and the documents it applies to."""

    __tablename__ = "rulesets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    oem_id: Mapped[int] = mapped_column(ForeignKey("oems.id"))
    #: Narrower scopes win over wider ones when several rulesets apply.
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id"))
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    #: ``xlsx`` | ``pdf`` | ``any``
    format: Mapped[str] = mapped_column(String(16), default="xlsx")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    cloned_from_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("ruleset_versions.id", use_alter=True, name="fk_ruleset_cloned_from"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()

    oem: Mapped[Oem] = relationship()
    brand: Mapped[Brand | None] = relationship()
    model: Mapped[VehicleModel | None] = relationship()
    versions: Mapped[list[RulesetVersion]] = relationship(
        back_populates="ruleset", foreign_keys="RulesetVersion.ruleset_id",
        order_by="RulesetVersion.version")


class RulesetVersion(Base):
    """One version of a ruleset's rules.  Immutable once published."""

    __tablename__ = "ruleset_versions"
    __table_args__ = (UniqueConstraint("ruleset_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ruleset_id: Mapped[int] = mapped_column(ForeignKey("rulesets.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    #: ``draft`` | ``published`` | ``archived``
    status: Mapped[str] = mapped_column(String(16), default="draft")
    schema_version: Mapped[int] = mapped_column(Integer)
    #: The serialized ``jev_diff.rules.RuleSet``.  Plain JSON, never JSONB:
    #: mapping order is meaningful (the first lens is the default) and JSONB
    #: would re-sort it.
    body: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ruleset: Mapped[Ruleset] = relationship(back_populates="versions",
                                            foreign_keys=[ruleset_id])


# --------------------------------------------------------------------------
# Comparisons.
# --------------------------------------------------------------------------

class Timeline(Base):
    """Three or more model years compared as a chain of adjacent pairs."""

    __tablename__ = "timelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"))
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()

    model: Mapped[VehicleModel] = relationship()
    comparisons: Mapped[list[Comparison]] = relationship(
        back_populates="timeline", order_by="Comparison.timeline_position")


class Comparison(Base):
    __tablename__ = "comparisons"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    old_model_year_id: Mapped[int] = mapped_column(ForeignKey("model_years.id"))
    new_model_year_id: Mapped[int] = mapped_column(ForeignKey("model_years.id"))
    old_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    new_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    ruleset_version_id: Mapped[int] = mapped_column(ForeignKey("ruleset_versions.id"))
    #: {judge: bool, workers: int}
    options: Mapped[dict] = mapped_column(JSONType, default=dict)
    #: ``queued`` | ``running`` | ``succeeded`` | ``failed`` | ``cancelled``
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    timeline_id: Mapped[int | None] = mapped_column(ForeignKey("timelines.id"))
    timeline_position: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts(index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model: Mapped[VehicleModel] = relationship()
    old_model_year: Mapped[ModelYear] = relationship(foreign_keys=[old_model_year_id])
    new_model_year: Mapped[ModelYear] = relationship(foreign_keys=[new_model_year_id])
    old_document: Mapped[Document] = relationship(foreign_keys=[old_document_id])
    new_document: Mapped[Document] = relationship(foreign_keys=[new_document_id])
    ruleset_version: Mapped[RulesetVersion] = relationship()
    timeline: Mapped[Timeline | None] = relationship(back_populates="comparisons")
    result: Mapped[ComparisonResult | None] = relationship(
        back_populates="comparison", cascade="all, delete-orphan", uselist=False)


class ComparisonResult(Base):
    __tablename__ = "comparison_results"

    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE"), primary_key=True)
    #: zlib-compressed JSON of the pipeline payload -- the same structure the
    #: HTML report embeds, and what the UI renders.
    payload_z: Mapped[bytes] = mapped_column(LargeBinary)
    summary: Mapped[dict] = mapped_column(JSONType)
    usage: Mapped[dict | None] = mapped_column(JSONType)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSONType)
    ruleset_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()

    comparison: Mapped[Comparison] = relationship(back_populates="result")


class ChangeEventRow(Base):
    """Events flattened for search across comparisons and timelines."""

    __tablename__ = "change_events"
    __table_args__ = (
        UniqueConstraint("comparison_id", "eid"),
        Index("ix_change_events_code", "code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    eid: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    sheet: Mapped[str] = mapped_column(String(128))
    also_on: Mapped[list] = mapped_column(JSONType, default=list)
    code: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    disposition: Mapped[str | None] = mapped_column(String(32))
    order_affecting: Mapped[bool] = mapped_column(Boolean)
    #: Lens values at save time, for sorting server-side.
    lens_values: Mapped[dict | None] = mapped_column(JSONType)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    #: ``html`` | ``xlsx``
    kind: Mapped[str] = mapped_column(String(8))
    lens: Mapped[str | None] = mapped_column(String(64))
    blob_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    filename: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _ts()


# --------------------------------------------------------------------------
# Jobs and the judgment cache.
# --------------------------------------------------------------------------

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_id", "status", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: ``parse_document`` | ``run_comparison`` | ``test_ruleset`` | ``export``
    type: Mapped[str] = mapped_column(String(32))
    #: ``queued`` | ``running`` | ``succeeded`` | ``failed`` | ``cancelled``
    status: Mapped[str] = mapped_column(String(16), default="queued")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType)
    stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    comparison_id: Mapped[int | None] = mapped_column(
        ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Judgment(Base):
    """One cached answer, keyed exactly as ``jev_diff.judge.cache`` keys it."""

    __tablename__ = "judgments"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    question_id: Mapped[str | None] = mapped_column(String(64), index=True)
    answer: Mapped[dict] = mapped_column(JSONType)
    #: ``api`` | ``json_import``
    source: Mapped[str] = mapped_column(String(16), default="api")
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
