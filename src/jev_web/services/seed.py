"""First-run data: a local admin, the sample catalog, and the GM ruleset."""

from __future__ import annotations

from importlib import resources

from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.rules import RuleSet

from ..db.models import Ruleset, User
from . import catalog
from .rulesets import create_ruleset

LOCAL_ADMIN = "admin@localhost"


def default_ruleset_json(name: str = "gm") -> str:
    from jev_diff.rules import SEED_FILES

    return resources.files("jev_diff.rules").joinpath(SEED_FILES[name]).read_text("utf-8")


def seed(session: Session) -> dict[str, int]:
    """Idempotent.  Returns counts of what was created."""
    created = {"users": 0, "rulesets": 0}
    if session.scalar(select(User).where(User.email == LOCAL_ADMIN)) is None:
        session.add(User(email=LOCAL_ADMIN, display_name="Local admin", role="admin"))
        created["users"] += 1

    gm = catalog.ensure_oem(session, "General Motors", prompt_name="GM")
    gm.slug = "gm"
    for brand_name, models in {"GMC": ["Yukon"], "Chevrolet": [], "Buick": [],
                               "Cadillac": []}.items():
        brand = catalog.ensure_brand(session, gm, brand_name)
        for model_name in models:
            model = catalog.ensure_model(session, brand, model_name)
            catalog.ensure_year(session, model, 2026, "Yukon / Denali")
            catalog.ensure_year(session, model, 2027)

    if session.scalar(select(Ruleset).where(Ruleset.oem_id == gm.id)) is None:
        rules = RuleSet.from_json(default_ruleset_json("gm"))
        create_ruleset(session, name=rules.name, oem_id=gm.id, rules=rules, fmt="xlsx",
                       description="Built-in rules for GM order-guide Excel exports.",
                       publish=True)
        created["rulesets"] += 1

    ford = catalog.ensure_oem(session, "Ford Motor Company", prompt_name="Ford")
    ford.slug = "ford"
    ford_brand = catalog.ensure_brand(session, ford, "Ford")
    mustang = catalog.ensure_model(session, ford_brand, "Mustang")
    catalog.ensure_year(session, mustang, 2025)
    catalog.ensure_year(session, mustang, 2026)
    if session.scalar(select(Ruleset).where(Ruleset.oem_id == ford.id)) is None:
        rules = RuleSet.from_json(default_ruleset_json("ford-pdf"))
        create_ruleset(session, name=rules.name, oem_id=ford.id, rules=rules, fmt="pdf",
                       description="Built-in rules for Ford PDF order guides, written "
                                   "against the 2025/2026 Mustang.",
                       publish=True)
        created["rulesets"] += 1
    session.flush()
    return created
