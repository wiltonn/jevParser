"""The Ford PDF order-guide ruleset, written against the 2025/2026 Mustang guides.

It shares GM's clause vocabulary -- both OEMs write "Requires ...", "Not
available with ..." -- and adds Ford's own phrasings.  What differs is
structural: Ford guides are PDFs laid out per series, with an availability
vocabulary of S / O / I / P, compound cells such as ``O/-`` (the parts differ
by body style), and codes of two to four characters.

``ford_pdf.json`` next to this file is generated from it
(``jev-diff rules export-default --ruleset ford-pdf``) and seeds the database.
"""

from __future__ import annotations

from .default_gm import DEFAULT_GM
from .model import (
    ClassifyRules,
    ClauseRules,
    DetectionRules,
    PdfRules,
    PdfSection,
    QuestionTemplate,
    RelationPattern,
    RuleSet,
    SheetRegistry,
)


def _p(relation: str, pattern: str, note: str = "") -> RelationPattern:
    return RelationPattern(relation=relation, pattern=pattern, note=note)


_GM = DEFAULT_GM.clauses

_CLAUSES = ClauseRules(
    # Ford codes are two to four characters and are often listed together:
    # "(60G)", "(642)", "(G1)", "(DW, 2W)".  A two-character code may be all
    # letters (paint "AE"); a longer one must hold a digit, which keeps
    # "(AEB)" and "(WAFAC)" out.
    code_regex=r"[(,]\s*((?=[0-9A-Z]*\d)[0-9A-Z]{3,4}|[0-9A-Z]{2})(?=\s*[,)])",
    clause_split_regex=_GM.clause_split_regex,
    anchored=(
        _p("package_content", r"^package includes\b",
           "a bulleted item of a package; added by the PDF reader"),
        _p("price_note", r"^extra charge\b"),
        _p("limited_to", r"^available (?:on|with|for|in)\b",
           'e.g. "Available on Convertible only"'),
        _p("replaces", r"^replaces\b"),
        _p("standard_with", r"^standard (?:to|for)\b"),
        _p("boilerplate", r"^(?:refer to|this feature is ordered|select option|ford connectivity|"
                          r"modem activation|the ford app|trial length|details:)"),
        *_GM.anchored,
    ),
    fallback=(
        _p("required_option", r"\bis a required option\b"),
        *_GM.fallback,
        _p("boilerplate", r"\bevolving technology\b|\bconnected services\b|\bdata usage\b|"
                          r"\bsubscription\b|\bdata speeds\b"),
    ),
    ordering_relations=(*_GM.ordering_relations, "limited_to", "replaces", "required_option"),
    prose_relations=(*_GM.prose_relations, "price_note"),
)

_TOKENS = {
    "S": "S", "O": "O", "I": "I", "P": "P",
    "-": "--", "--": "--", "––": "--", "–": "--", "—": "--",
    "◼": "◼", "■": "◼",
}

_CLASSIFY = ClassifyRules(
    token_classes={
        "S": "standard",
        "I": "package",
        "P": "package",
        "O": "optional",
        "◼": "optional",
        "--": "unavailable",
        "": "unavailable",
        "<code>": "optional",
    },
    rewrite_similarity=60.0,
    composite_separator="/",
)

_SECTIONS = (
    PdfSection(heading=r"national discount packages", layout="ignore"),
    PdfSection(heading=r"^major product changes$", layout="ignore"),
    PdfSection(heading=r"^standard equipment$", layout="feature_list",
               sheet="{series} – Standard Equipment"),
    PdfSection(heading=r"^equipment group$", layout="option_matrix", sheet="Equipment Groups"),
    PdfSection(heading=r"^functional equipment$", layout="label_grid", sheet="Rear Axle Ratios"),
    PdfSection(heading=r"^color & trim availability$", layout="code_grid", sheet="Color & Trim"),
    PdfSection(heading=r"^seat belt color availability$", layout="code_grid",
               sheet="Seat Belt Colors"),
    PdfSection(heading=r"^stripe offerings$", layout="code_grid", sheet="Stripe Offerings"),
    PdfSection(heading=r"^emissions$", layout="ignore"),
    PdfSection(heading=r"^powertrain$", layout="ignore"),
)

_PDF = PdfRules(
    sections=_SECTIONS,
    series_names={
        "ECOBOOST": "EcoBoost", "ECOBOOST PREMIUM": "EcoBoost Premium",
        "GT": "GT", "GT PREMIUM": "GT Premium", "DARK HORSE": "Dark Horse",
    },
    tokens=_TOKENS,
    ignore_rows=(
        r"^\(this section is for wbdo users only\)",
        r"^\(s\) standard",
        # A footnote printed inside a table: "1 Not available with ...".  One
        # space, then prose -- a label with a superscript is "2\nRace Red".
        r"^\d{1,2} [A-Z][a-z]+ [a-z]",
    ),
)

_questions = dict(DEFAULT_GM.judge.questions)
_questions["alignment"] = QuestionTemplate(
    type="score",
    instructions=(
        "Two rows from the same section of a {oem} vehicle order guide, one from "
        "the {old_year} model year (`{pair_old}`) and one from {new_year} (`{pair_new}`). How do "
        "they relate as entries in the order guide?"
    ),
    criteria=DEFAULT_GM.judge.questions["alignment"].criteria,
)

_JUDGE = DEFAULT_GM.judge.model_copy(update={
    "questions": _questions,
    "token_legend": {
        "S": "standard equipment",
        "O": "optional, ordered separately",
        "I": "included in the equipment group",
        "P": "included with the Performance Package",
        "◼": "available",
        "--": "not available",
        "X/Y": "differs by body style or configuration; each part applies to one",
    },
})

DEFAULT_FORD_PDF = RuleSet(
    name="Ford order guide (PDF)",
    format="pdf",
    sheets=SheetRegistry(sheets=(), unknown_sheet="warn", dictionary_sheet="All codes"),
    clauses=_CLAUSES,
    pairing=DEFAULT_GM.pairing,
    classify=_CLASSIFY,
    judge=_JUDGE,
    lenses=DEFAULT_GM.lenses,
    detection=DetectionRules(
        filename_patterns=(
            r"^(?P<year>(?:19|20)\d{2})[-_ ](?P<model>[A-Za-z0-9 ]+?)[-_ ]Order[-_ ]Guide",
        ),
        default_brand="Ford",
        fingerprint_sheets=("Equipment Groups", "Color & Trim", "Stripe Offerings"),
    ),
    pdf=_PDF,
)
