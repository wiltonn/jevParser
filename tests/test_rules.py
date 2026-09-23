"""Rulesets: validation, serialization, and proof that each section is live.

Parity (``test_parity.py``) shows the GM ruleset reproduces the old behaviour.
These tests show the converse: editing a section of the ruleset really changes
what the pipeline does, so no decision is still silently hard-coded.
"""

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import BOOKS
from jev_diff.rules import PromptContext, RuleSet, default_rules
from jev_diff.rules.model import ClassifyRules, QuestionTemplate, RelationPattern

GM = default_rules()
SEED = Path(__file__).resolve().parents[1] / "src" / "jev_diff" / "rules" / "gm_default.json"


def _edit(rules: RuleSet, **sections) -> RuleSet:
    """A modified copy, re-validated as an editor's save would be."""
    data = rules.model_dump(mode="json")
    for dotted, value in sections.items():
        node = data
        *path, last = dotted.split("__")
        for part in path:
            node = node[part]
        node[last] = value
    return RuleSet.model_validate(data)


def _unstructured(book) -> int:
    return sum(
        1 for s in book.sheets.values() for r in s.rows for c in r.values.values()
        for k in c.conditions if k.relation == "unstructured"
    )


# -- serialization -----------------------------------------------------------

def test_json_round_trip_is_lossless():
    again = RuleSet.from_json(GM.to_json())
    assert again == GM
    assert again.content_hash() == GM.content_hash()


def test_committed_seed_matches_built_in_rules():
    """``gm_default.json`` seeds the database; it must not drift from code.

    Regenerate with ``jev-diff rules export-default -o <path>``.
    """
    assert RuleSet.from_json(SEED.read_text(encoding="utf-8")) == GM


@pytest.mark.parametrize("name", ["gm", "ford-pdf"])
def test_every_seed_file_matches_its_built_in_ruleset(name):
    from jev_diff.rules import SEED_FILES, built_in

    path = SEED.parent / SEED_FILES[name]
    assert RuleSet.from_json(path.read_text(encoding="utf-8")) == built_in()[name]


def test_pdf_ruleset_requires_pdf_section():
    from jev_diff.rules import built_in

    body = built_in()["ford-pdf"].model_dump(mode="json")
    body["pdf"] = None
    with pytest.raises(ValidationError, match="needs a 'pdf' section"):
        RuleSet.model_validate(body)


def test_compound_tokens_take_the_most_available_class():
    from jev_diff.rules import built_in

    classify = built_in()["ford-pdf"].classify
    assert classify.class_of("O/--") == "optional"
    assert classify.class_of("S/O") == "standard"
    assert classify.class_of("I/--") == "package"
    assert classify.class_of("X/--") is None


def test_hash_ignores_relation_listing_order():
    shuffled = _edit(GM, clauses__ordering_relations=sorted(GM.clauses.ordering_relations,
                                                            reverse=True))
    assert shuffled.content_hash() == GM.content_hash()


# -- validation ------------------------------------------------------------------

def test_rejects_invalid_regex():
    with pytest.raises(ValidationError, match="invalid regular expression"):
        RelationPattern(relation="requires", pattern="^(unclosed")


def test_rejects_unknown_placeholder():
    with pytest.raises(ValidationError, match="unknown placeholder"):
        QuestionTemplate(type="noul", instructions="In {model_year} ...")


def test_rejects_incomplete_token_lattice():
    classes = dict(GM.classify.token_classes)
    del classes["A/D"]
    with pytest.raises(ValidationError, match="missing"):
        _edit(GM, classify__token_classes=classes)
    del classes[""]
    with pytest.raises(ValidationError, match="missing"):
        ClassifyRules(token_classes=classes)


def test_rejects_missing_question():
    questions = GM.model_dump(mode="json")["judge"]["questions"]
    del questions["order_risk"]
    with pytest.raises(ValidationError, match="missing question"):
        _edit(GM, judge__questions=questions)


def test_rejects_lens_over_unknown_question():
    with pytest.raises(ValidationError, match="unknown question"):
        _edit(GM, lenses={"x": {"weights": {"vibes": 1.0}}})


# -- every section is live ----------------------------------------------------

def test_clause_patterns_are_live(books):
    from jev_diff.parse.registry import parse_workbook

    anchored = [p for p in GM.model_dump(mode="json")["clauses"]["anchored"]
                if p["relation"] != "requires"]
    fallback = [p for p in GM.model_dump(mode="json")["clauses"]["fallback"]
                if p["relation"] != "requires"]
    edited = _edit(GM, clauses__anchored=anchored, clauses__fallback=fallback)
    book = parse_workbook(str(BOOKS["2027"]), "2027", edited)
    assert _unstructured(book) > _unstructured(books["2027"])


def test_sheet_registry_is_live():
    from jev_diff.parse.registry import parse_workbook

    sheets = [s for s in GM.model_dump(mode="json")["sheets"]["sheets"]
              if s["name"] != "Wheels"]
    fatal = parse_workbook(str(BOOKS["2027"]), "2027",
                           _edit(GM, sheets__sheets=sheets))
    assert "Wheels" not in fatal.sheets
    assert any(w.kind == "unknown_sheet" and w.severity == "fatal" for w in fatal.warnings)

    skipped = parse_workbook(str(BOOKS["2027"]), "2027",
                             _edit(GM, sheets__sheets=sheets, sheets__unknown_sheet="skip"))
    assert "Wheels" not in skipped.sheets
    assert not any(w.kind == "unknown_sheet" for w in skipped.warnings)


def test_pairing_tiers_are_live(books):
    from jev_diff.align.pairing import align_books

    tiers = GM.model_dump(mode="json")["pairing"]["tiers"]
    for t in tiers:
        if t["id"] == 3:
            t["enabled"] = False
    edited = _edit(GM, pairing__tiers=tiers)
    baseline = align_books(books["2026"], books["2027"])
    changed = align_books(books["2026"], books["2027"], edited)
    base_tiers = Counter(p.tier for p in baseline.pairs)
    new_tiers = Counter(p.tier for p in changed.pairs)
    assert base_tiers[3] > 0 and new_tiers[3] == 0
    assert len(changed.residue) > len(baseline.residue)


def test_token_lattice_is_live(books, alignment):
    from jev_diff.classify import build_events, consolidate

    # Treat "included in an equipment group" as a separate option: the corpus's
    # A <-> ■ moves stop being package shifts.
    classes = dict(GM.classify.token_classes, **{"■": "optional"})
    edited = _edit(GM, classify__token_classes=classes)
    base = consolidate(build_events(books["2026"], books["2027"], alignment))
    changed = consolidate(build_events(books["2026"], books["2027"], alignment, edited))

    def directions(events):
        return Counter(d.direction for e in events for d in e.deltas if d.changed)

    assert directions(base)["package_shift"] > 0
    assert directions(changed)["package_shift"] < directions(base)["package_shift"]


def test_ordering_relations_are_live(books, alignment):
    from jev_diff.classify import build_events, consolidate

    edited = _edit(GM, clauses__ordering_relations=[])
    events = consolidate(build_events(books["2026"], books["2027"], alignment, edited))
    base = consolidate(build_events(books["2026"], books["2027"], alignment))
    assert sum(len(e.needs_constraint_judgment) for e in base) > 0
    assert sum(len(e.needs_constraint_judgment) for e in events) == 0


def test_questions_render_for_any_year_pair():
    from jev_diff.judge.questions import build_questions

    qs = build_questions(GM.judge, PromptContext("2025", "2026", oem="GM"))
    assert (qs.pair_old, qs.pair_new) == ("row_2025", "row_2026")
    assert (qs.cond_old, qs.cond_new) == ("condition_2025", "condition_2026")
    text = qs["alignment"]["instructions"]
    assert "2025 model year (`row_2025`)" in text and "2027" not in text
    assert "saved 2025 build sheet" in qs["order_risk"]["instructions"]
    assert all("2027" not in str(q) for q in qs.questions.values())


def test_lenses_are_live():
    from jev_diff.judge.run import lenses_from

    edited = _edit(GM, lenses={"risk_only": {"weights": {"order_risk": 1.0},
                                             "bands": {"material": 1.0, "review": 0.2}}})
    lenses = lenses_from(edited)
    assert list(lenses) == ["risk_only"]
    assert lenses["risk_only"].bands["material"] == 1.0


def test_pipeline_with_seed_file_matches_built_in(books):
    """The CLI's ``--ruleset gm_default.json`` path is the built-in behaviour."""
    from jev_diff.pipeline import compare
    from jev_diff.rules import load

    stages = []
    a = compare(str(BOOKS["2026"]), str(BOOKS["2027"]), label_a="2026", label_b="2027",
                books=(books["2026"], books["2027"]),
                progress=lambda stage, f: stages.append((stage, f)))
    b = compare(str(BOOKS["2026"]), str(BOOKS["2027"]), label_a="2026", label_b="2027",
                rules=load(SEED), books=(books["2026"], books["2027"]))
    assert a.payload == b.payload
    fractions = [f for _, f in stages]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
