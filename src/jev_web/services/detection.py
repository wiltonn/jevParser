"""Guess which OEM, model and model year an uploaded order guide is.

Two signals, from every published ruleset for the file's format:

*   the ruleset's filename patterns (named groups ``year``, ``brand``,
    ``model``), and
*   how many of the ruleset's fingerprint sheet names the file contains.

The result is a ranked list of suggestions.  Nothing is assigned until a person
confirms: a wrong year silently turns a comparison inside out.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.formats import adapter_for

from ..db.models import Ruleset
from . import catalog
from .rulesets import latest_published, load_rules

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def detect(session: Session, path: str, filename: str, fmt: str) -> dict:
    adapter = adapter_for(fmt)
    stem = Path(filename).stem
    suggestions: list[dict] = []
    all_sheets: set[str] = set()

    for ruleset in session.scalars(select(Ruleset)):
        if ruleset.format not in (fmt, "any"):
            continue
        version = latest_published(ruleset)
        if version is None:
            continue
        rules = load_rules(version)
        # A PDF's sections only have names under a ruleset, so ask per ruleset.
        try:
            sheet_names = set(adapter.sheet_names(path, rules))
        except Exception:                               # unreadable: no fingerprint
            sheet_names = set()
        all_sheets |= sheet_names
        fingerprint = set(rules.detection.fingerprint_sheets)
        sheet_score = (len(fingerprint & sheet_names) / len(fingerprint)) if fingerprint else 0.0

        fields: dict[str, str] = {}
        for pattern in rules.detection.filename_patterns:
            m = re.search(pattern, stem, re.IGNORECASE)
            if m:
                fields = {k: v.strip() for k, v in m.groupdict().items() if v}
                break
        if not fields:
            y = _YEAR.search(stem)
            if y:
                fields["year"] = y.group(1)
        if sheet_score == 0 and not fields:
            continue

        oem = ruleset.oem
        if "brand" not in fields and rules.detection.default_brand:
            fields["brand"] = rules.detection.default_brand
        brand = catalog.find_brand(session, oem.id, fields["brand"]) if "brand" in fields else ruleset.brand
        model = None
        if brand is not None and "model" in fields:
            model = catalog.find_model(session, brand.id, fields["model"])
        model = model or ruleset.model
        year = int(fields["year"]) if "year" in fields else None
        model_year_id = None
        if model is not None and year is not None:
            model_year_id = next((y.id for y in model.years if y.year == year), None)

        # Filename fields that resolve to catalog entries count for more than
        # ones that would have to be created.
        name_score = (0.2 * ("year" in fields) + 0.15 * ("brand" in fields)
                      + 0.15 * ("model" in fields)
                      + 0.1 * (model_year_id is not None))
        confidence = round(min(1.0, 0.4 * sheet_score + name_score + 0.0), 3)
        suggestions.append({
            "ruleset_id": ruleset.id,
            "oem_id": oem.id, "oem": oem.name,
            "brand_id": brand.id if brand else None,
            "brand": brand.name if brand else fields.get("brand"),
            "model_id": model.id if model else None,
            "model": model.name if model else fields.get("model"),
            "year": year,
            "model_year_id": model_year_id,
            "confidence": confidence,
            "sheet_match": round(sheet_score, 3),
            "source": "+".join(s for s, on in (("filename", bool(fields)),
                                               ("sheets", sheet_score > 0)) if on),
        })

    suggestions.sort(key=lambda s: -s["confidence"])
    return {"sheet_names": sorted(all_sheets), "suggestions": suggestions}
