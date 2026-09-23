"""Ruleset lifecycle and resolution.

A ruleset is a named line of versions.  Exactly one draft may be open at a
time; publishing freezes it.  Comparisons and parse runs record the version
they used, so a published version is never edited in place -- a change is a
new version, and old results stay explainable.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.rules import RuleSet

from ..db.models import (
    Brand,
    ModelYear,
    Oem,
    Ruleset,
    RulesetVersion,
    VehicleModel,
    utcnow,
)


class RulesetError(ValueError):
    pass


@functools.lru_cache(maxsize=32)
def _parse(content_hash: str, body_json: str) -> RuleSet:
    return RuleSet.model_validate_json(body_json)


def load_rules(version: RulesetVersion) -> RuleSet:
    """The ``RuleSet`` a version holds, parsed once per content hash."""
    import json

    return _parse(version.content_hash, json.dumps(version.body))


def validate_body(body: dict) -> RuleSet:
    try:
        return RuleSet.model_validate(body)
    except ValidationError as exc:
        raise RulesetError(_format_errors(exc)) from exc


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"])
        lines.append(f"{where}: {err['msg']}")
    return "\n".join(lines)


def validation_errors(body: dict) -> list[dict]:
    """Structured errors for the editor, keyed by path."""
    try:
        RuleSet.model_validate(body)
    except ValidationError as exc:
        return [{"path": [str(p) for p in e["loc"]], "message": e["msg"]}
                for e in exc.errors()]
    return []


def _new_version(ruleset: Ruleset, rules: RuleSet, *, status: str, user_id: int | None,
                 notes: str | None = None) -> RulesetVersion:
    number = max((v.version for v in ruleset.versions), default=0) + 1
    version = RulesetVersion(
        ruleset=ruleset, version=number, status=status,
        schema_version=rules.schema_version, body=rules.model_dump(mode="json"),
        content_hash=rules.content_hash(), notes=notes, created_by=user_id,
    )
    if status == "published":
        version.published_by, version.published_at = user_id, utcnow()
    return version


def create_ruleset(session: Session, *, name: str, oem_id: int, rules: RuleSet,
                   brand_id: int | None = None, model_id: int | None = None,
                   year_from: int | None = None, year_to: int | None = None,
                   fmt: str = "xlsx", priority: int = 0, description: str | None = None,
                   publish: bool = False, cloned_from: RulesetVersion | None = None,
                   user_id: int | None = None) -> Ruleset:
    if year_from and year_to and year_from > year_to:
        raise RulesetError("year_from is after year_to")
    ruleset = Ruleset(
        name=name, oem_id=oem_id, brand_id=brand_id, model_id=model_id,
        year_from=year_from, year_to=year_to, format=fmt, priority=priority,
        description=description, created_by=user_id,
        cloned_from_version_id=cloned_from.id if cloned_from else None,
    )
    session.add(ruleset)
    session.flush()
    session.add(_new_version(ruleset, rules, status="published" if publish else "draft",
                             user_id=user_id,
                             notes=f"cloned from {cloned_from.ruleset.name} "
                                   f"v{cloned_from.version}" if cloned_from else None))
    session.flush()
    return ruleset


def draft_of(ruleset: Ruleset) -> RulesetVersion | None:
    return next((v for v in ruleset.versions if v.status == "draft"), None)


def latest_published(ruleset: Ruleset) -> RulesetVersion | None:
    published = [v for v in ruleset.versions if v.status == "published"]
    return max(published, key=lambda v: v.version) if published else None


def open_draft(session: Session, ruleset: Ruleset, user_id: int | None) -> RulesetVersion:
    """The ruleset's draft, starting one from the latest version if needed."""
    existing = draft_of(ruleset)
    if existing:
        return existing
    base = latest_published(ruleset) or max(ruleset.versions, key=lambda v: v.version)
    version = _new_version(ruleset, load_rules(base), status="draft", user_id=user_id,
                           notes=f"from v{base.version}")
    session.add(version)
    session.flush()
    return version


def save_draft(session: Session, version: RulesetVersion, body: dict,
               notes: str | None = None) -> RulesetVersion:
    if version.status != "draft":
        raise RulesetError(f"v{version.version} is {version.status}; only a draft can change")
    rules = validate_body(body)
    version.body = rules.model_dump(mode="json")
    version.content_hash = rules.content_hash()
    version.schema_version = rules.schema_version
    if notes is not None:
        version.notes = notes
    return version


def publish(session: Session, version: RulesetVersion, user_id: int | None) -> RulesetVersion:
    if version.status != "draft":
        raise RulesetError(f"v{version.version} is already {version.status}")
    validate_body(version.body)
    version.status = "published"
    version.published_by, version.published_at = user_id, utcnow()
    return version


def archive(version: RulesetVersion) -> RulesetVersion:
    if version.status == "archived":
        raise RulesetError("already archived")
    version.status = "archived"
    return version


def diff(a: dict, b: dict, path: tuple = ()) -> list[dict]:
    """Leaf-level differences between two rule bodies, for review before publish."""
    out: list[dict] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            out.extend(diff(a.get(key), b.get(key), path + (key,)))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff(x, y, path + (i,)))
    elif a != b:
        out.append({"path": [str(p) for p in path], "old": a, "new": b})
    return out


# --------------------------------------------------------------------------
# Resolution.
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Candidate:
    ruleset: Ruleset
    version: RulesetVersion
    specificity: int

    def as_dict(self) -> dict:
        return {
            "ruleset_id": self.ruleset.id, "ruleset": self.ruleset.name,
            "version_id": self.version.id, "version": self.version.version,
            "specificity": ("model", "brand", "oem")[3 - self.specificity],
            "priority": self.ruleset.priority, "format": self.ruleset.format,
        }


def candidates(session: Session, *, oem_id: int, brand_id: int | None,
               model_id: int | None, years: list[int], formats: set[str]) -> list[Candidate]:
    """Published rulesets that apply, best first.

    A ruleset applies when its OEM matches, its brand/model (if set) match,
    every year falls in its range, and its format is ``any`` or the format of
    every document.  Ranked by how narrowly it is scoped, then priority, then
    the newest version.
    """
    out: list[Candidate] = []
    for rs in session.scalars(select(Ruleset).where(Ruleset.oem_id == oem_id)):
        if rs.brand_id is not None and rs.brand_id != brand_id:
            continue
        if rs.model_id is not None and rs.model_id != model_id:
            continue
        if any((rs.year_from and y < rs.year_from) or (rs.year_to and y > rs.year_to)
               for y in years):
            continue
        if rs.format != "any" and formats != {rs.format}:
            continue
        version = latest_published(rs)
        if version is None:
            continue
        specificity = 3 if rs.model_id else 2 if rs.brand_id else 1
        out.append(Candidate(rs, version, specificity))
    out.sort(key=lambda c: (-c.specificity, -c.ruleset.priority, -c.version.id))
    return out


def resolve_for_years(session: Session, years: list[ModelYear],
                      formats: set[str]) -> list[Candidate]:
    model: VehicleModel = years[0].model
    brand: Brand = model.brand
    oem: Oem = brand.oem
    return candidates(session, oem_id=oem.id, brand_id=brand.id, model_id=model.id,
                      years=[y.year for y in years], formats=formats)
