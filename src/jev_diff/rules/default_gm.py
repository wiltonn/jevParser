"""The GM order-guide ruleset, as it stood when these rules lived in code.

Every value here was moved verbatim from the module that used to own it, and
the parity tests (``tests/test_parity.py``) hold it there: rendered for 2026 ->
2027, the question templates must reproduce the old wording byte for byte,
because that wording is part of every judgment cache key.

``gm_default.json`` next to this file is generated from it
(``jev-diff rules export-default``) and seeds the database.
"""

from __future__ import annotations

from .model import (
    ClassifyRules,
    ClauseRules,
    DetectionRules,
    FeatureSheet,
    FlatSheet,
    GridSheet,
    JudgeRules,
    KeyedSheet,
    LensRule,
    PairingRules,
    QuestionTemplate,
    RelationPattern,
    RuleSet,
    SheetRegistry,
    StackedSheet,
    TierRule,
)

# --------------------------------------------------------------------------
# Sheets (formerly parse/registry.py).
# --------------------------------------------------------------------------

_SHEETS = SheetRegistry(
    sheets=(
        FeatureSheet(name="Standard Equipment"),
        FeatureSheet(name="Equipment Groups"),
        FeatureSheet(name="Interior"),
        FeatureSheet(name="Exterior"),
        FeatureSheet(name="Mechanical"),
        FeatureSheet(name="SEO Ship Thru"),
        FeatureSheet(name="OnStar SiriusXM Fleet Options"),
        # Wheels inserts an extra "Wheels" column, shifting everything right by one.
        FeatureSheet(name="Wheels", desc_col=4, first_axis_col=5),
        # Header rows differ per sheet and are pinned rather than sniffed,
        # because getting them wrong silently shifts every value.
        KeyedSheet(name="Specs", header_row=1),
        KeyedSheet(name="Dimensions", header_row=2),
        StackedSheet(name="Color and Trim"),
        GridSheet(name="Engine Axles"),
        GridSheet(name="Trailering Specs"),
        FlatSheet(name="All"),
    ),
    unknown_sheet="fatal",
    dictionary_sheet="All",
)

# --------------------------------------------------------------------------
# Clauses (formerly parse/constraints.py).
#
# Ranked longest-intent-first.  The first match wins, so more specific patterns
# ("not available at this time") precede more general ones ("not available with").
# Stage 1 anchors at the start of the clause, which is precise.  Stage 2 searches
# anywhere, because GM often leads with a parenthesised code or a time qualifier
# before stating the relation.
# --------------------------------------------------------------------------

def _p(relation: str, pattern: str, note: str = "") -> RelationPattern:
    return RelationPattern(relation=relation, pattern=pattern, note=note)


_CLAUSES = ClauseRules(
    code_regex=r"\(([0-9A-Z]{3})\)",
    clause_split_regex=r"(?<!\d)\.\s+(?=[A-Z(])",
    anchored=(
        _p("discontinued",       r"^no longer available\b"),
        _p("not_available_now",  r"^not available at this time\b"),
        _p("not_available_with", r"^not available (?:with|when|on|for)\b"),
        _p("order_type_only",    r"^available on the following order types\b"),
        _p("included_only_with", r"^(?:included and only available|required and only available|"
                                 r"available only|only available|included on orders)\b"),
        _p("requires",           r"^(?:requires|required with|required on|require|"
                                 r"must be ordered)\b"),
        _p("standard_with",      r"^standard (?:with|on)\b"),
        _p("excludes_content",   r"^does not include\b"),
        _p("included_with",      r"^(?:included with|included in|includes|also includes|"
                                 r"enables and includes|enables)\b"),
        _p("deleted_when",       r"^(?:deleted when|deletes|removes|removed when|removed with|"
                                 r"will delete)\b"),
        _p("replaced_by",        r"^replaced (?:by|with)\b"),
        _p("drivetrain_only",    r"^(?:2wd|4wd)\b"),
        _p("production_timing",  r"^(?:beginning|late availability|available beginning|effective)\b"),
        _p("upgradeable_to",     r"^(?:upgradeable|upgradable)\b"),
        _p("boilerplate",        r"^(?:always |terms and limitations|see dealer|important:|"
                                 r"message and data|service |for more|visit |subject to|do not use|"
                                 r"children are safer|read the vehicle|go to |available on select|"
                                 r"content varies|all fees|standard for the first|after the trial|"
                                 r"info at |not all vehicles|onstar |see |services vary|"
                                 r"terms apply|plan is non-|some features require|siriusxm|"
                                 r"safety services|trial subscription|will stop at)"),
    ),
    fallback=(
        _p("substitutable",      r"\bcan be substituted\b"),
        _p("not_available_with", r"\bnot (?:available|included)\b[^.]{0,40}?\b(?:with|when|on|for)\b",
           'e.g. "Not available as a free-flow with ..."'),
        _p("included_only_with", r"\b(?:included and only available|only available with)\b"),
        _p("included_with",      r"^when\b[^.]*?\bincludes\b",
           'e.g. "When (ATN) second row power bucket seats are ordered, includes (DCH) ..."'),
        _p("requires",           r"\b(?:requires|must be purchased|must be ordered)\b"),
        _p("deleted_when",       r"\bdeleted when\b"),
        _p("replaced_by",        r"\breplaced (?:by|with)\b"),
        _p("descriptive",        r"-specific\b|\bbody color\b|\bgloss black\b|^note that\b|"
                                 r"^allows a\b|\bwill come with\b"),
        _p("boilerplate",        r"\bsubject to change\b|\bterms and limitations\b|"
                                 r"\bpay attention while driving\b|\bvaries by subscription\b"),
    ),
    ordering_relations=(
        "requires", "not_available_with", "not_available_now", "included_only_with",
        "deleted_when", "replaced_by", "discontinued", "drivetrain_only",
        "standard_with", "substitutable", "upgradeable_to", "order_type_only",
        "excludes_content",
    ),
    prose_relations=("boilerplate", "descriptive", "production_timing"),
)

# --------------------------------------------------------------------------
# Pairing (formerly align/pairing.py).
# --------------------------------------------------------------------------

_PAIRING = PairingRules(tiers=(
    TierRule(id=1, key="exact", reason="same codes, same description"),
    TierRule(id=2, key="code_set_and_description",
             reason="same code set (either column), same description"),
    # The free win: catches every orderable <-> reference-only column swap.
    TierRule(id=3, key="code_set",
             reason="same code set (either column), description changed"),
    TierRule(id=4, key="description", reason="no code in either year, same description"),
    TierRule(id=5, key="unique_description",
             reason="description unique in both sheets and unchanged; "
                    "a code was assigned or removed",
             unique_only=True),
))

# --------------------------------------------------------------------------
# Classification (formerly classify.py).
# --------------------------------------------------------------------------

_CLASSIFY = ClassifyRules(
    token_classes={
        "S": "standard",
        "■": "package",
        "□": "package",
        "A": "optional",
        "A/D": "optional",
        "D": "optional",
        "--": "unavailable",
        "": "unavailable",
        "*": "optional",
        "<code>": "optional",
    },
    rewrite_similarity=60.0,
)

# --------------------------------------------------------------------------
# Judgment questions (formerly judge/questions.py).
#
# Criteria levels describe concrete situations and stand on their own, and no
# question's wording presupposes another's answer: questions in a request are
# answered in parallel and cannot see each other.
# --------------------------------------------------------------------------

_QUESTIONS = {
    "alignment": QuestionTemplate(
        type="score",
        instructions=(
            "Two rows from the same worksheet of a {oem} vehicle order guide, one from "
            "the {old_year} model year (`{pair_old}`) and one from {new_year} (`{pair_new}`). How do "
            "they relate as entries in the order guide?"
        ),
        criteria=(
            "The rows describe unrelated equipment: a buyer reading one would not "
            "expect the other. For example one is a seating package and the other "
            "is a wheel.",
            "The rows describe equipment in the same subsystem, and one may be a "
            "successor, a variant, a renamed version, or a package that absorbed "
            "the other. A reader could reasonably argue either that these are one "
            "entry or two.",
            "The rows are the same order-guide entry for the same equipment. Any "
            "difference in wording is how the same equipment was described, not "
            "different equipment.",
        ),
    ),
    "succession": QuestionTemplate(
        type="noul",
        instructions=(
            "`{pair_old}` names equipment that is no longer offered, and `{pair_new}` names "
            "the equipment that replaced it in the same position in the lineup."
        ),
    ),
    "absorbed": QuestionTemplate(
        type="noul",
        instructions=(
            "The equipment listed in `{pair_old}` is now bundled inside the package "
            "described in `{pair_new}` rather than being offered on its own."
        ),
    ),
    "direction": QuestionTemplate(
        type="score",
        instructions=(
            "`{cond_old}` and `{cond_new}` are the ordering constraints "
            "attached to the same option for the same trim level. Considering which "
            "vehicle configurations can be ordered, how does the {new_year} constraint "
            "compare?"
        ),
        criteria=(
            "Configurations that could be ordered in {old_year} can no longer be ordered "
            "in {new_year}: the option now requires something extra, or is now blocked by "
            "a combination that used to be allowed.",
            "The same configurations can be ordered under both constraints. The "
            "wording differs, or a referenced option was renamed or renumbered, but "
            "no configuration changes from orderable to un-orderable or the reverse.",
            "Configurations that could not be ordered in {old_year} can be ordered in "
            "{new_year}: a prerequisite was dropped, or an additional way to get the "
            "option was added.",
        ),
    ),
    "description_kind": QuestionTemplate(
        type="choice",
        instructions=(
            "Only the wording describing this option changed between the model "
            "years. What kind of edit was it?"
        ),
        criteria={
            "marketing": "Promotional phrasing was added, dropped or rewritten, "
                         "with no change to what the equipment is or does.",
            "correction": "A specific factual detail was corrected or made more "
                          "precise, such as a measurement, a capacity or a name.",
            "capability": "The described capability of the equipment changed: it "
                          "now does more, less, or something different.",
            "housekeeping": "An editorial change with no reader-visible meaning, "
                            "such as punctuation, ordering or a reference marker.",
        },
    ),
    "order_risk": QuestionTemplate(
        type="score",
        instructions=(
            "A dealer or fleet configurator has a saved {old_year} build sheet and is "
            "re-ordering the same vehicle for {new_year}. What happens to that order "
            "because of `change`?"
        ),
        criteria=(
            # Level 0 must cover a *loosened* restriction as well as a pure
            # wording change.  Tying it to "only the wording differs" left a
            # lifted constraint with no fitting level, and the answers came back
            # split between this level and the most severe one.
            "The same order succeeds and builds the same vehicle. Either nothing "
            "changed but the wording, or a restriction was lifted so configurations "
            "that were blocked before can now be ordered.",
            "The order still succeeds, but the vehicle differs from what was "
            "expected: an item is now bundled into a package, installed by the "
            "dealer rather than the factory, or fitted as standard rather than "
            "ordered separately.",
            "The order is rejected or has to be rebuilt: an option code no longer "
            "exists, has become reference-only, or now conflicts with another code "
            "on the order.",
        ),
    ),
    "content_churn": QuestionTemplate(
        type="score",
        instructions=(
            "A marketing team maintains web pages, brochures and spec tables built "
            "from the {old_year} order guide. What must they change because of `change`?"
        ),
        criteria=(
            "Nothing. No published sentence or table cell derived from this row "
            "changes.",
            "A published table cell or availability footnote changes, but no prose "
            "is rewritten.",
            "Published prose must be rewritten, or a feature must be added to or "
            "removed from a page, because the equipment or its described capability "
            "changed.",
        ),
    ),
}

_JUDGE = JudgeRules(
    questions=_QUESTIONS,
    token_legend={
        "S": "standard equipment",
        "A": "available as a separate order",
        "A/D": "available, dealer-installed",
        "■": "included in an equipment group",
        "--": "not available",
    },
    alignment_outcomes=("unlink", "review", "pair"),
    constraint_outcomes=("tightened", "equivalent", "loosened"),
    confidence_floor=0.75,
    similarity_floor=50.0,
)

# --------------------------------------------------------------------------
# Lenses (formerly judge/run.py).
# --------------------------------------------------------------------------

_LENSES = {
    "ordering": LensRule(weights={"order_risk": 1.0, "content_churn": 0.0}),
    "content": LensRule(weights={"order_risk": 0.2, "content_churn": 1.0}),
}

DEFAULT_GM = RuleSet(
    name="GM order guide (xlsx)",
    format="xlsx",
    sheets=_SHEETS,
    clauses=_CLAUSES,
    pairing=_PAIRING,
    classify=_CLASSIFY,
    judge=_JUDGE,
    lenses=_LENSES,
    detection=DetectionRules(
        filename_patterns=(
            r"^(?P<year>(?:19|20)\d{2})\s+(?P<brand>GMC|Chevrolet|Buick|Cadillac)\s+"
            r"(?P<model>[A-Za-z0-9 -]+?)(?:\s*_.*)?\s+Export",
        ),
        fingerprint_sheets=(
            "Standard Equipment", "Equipment Groups", "Interior", "Exterior",
            "Mechanical", "Color and Trim", "All",
        ),
    ),
)
