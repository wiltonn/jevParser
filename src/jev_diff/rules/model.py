"""The ruleset: every OEM- and year-specific decision the pipeline makes, as data.

Until this existed those decisions were module constants -- which sheet names
exist and how each is shaped, the clause vocabulary, the pairing tiers, the
availability lattice, the wording of every question put to Jev, and the lenses.
Pulling them into one validated document is what lets an administrator keep a
ruleset per OEM and model-year range without a code change.

What stays in code is *mechanism*: how a feature matrix is walked, what a
pairing key function computes, how directions are derived from classes.  A
ruleset chooses among mechanisms and parameterises them; it cannot add new ones.
That boundary keeps an edited ruleset from ever being able to run arbitrary
logic, and keeps the editor a form rather than a programming environment.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import string
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

SheetKind = Literal["feature_matrix", "keyed_rows", "stacked_matrix", "grid", "flat_lookup"]
AvailabilityClass = Literal["standard", "package", "optional", "unavailable"]
PairingKey = Literal[
    "exact", "code_set_and_description", "code_set", "description", "unique_description",
]

#: Every token the Excel cell parser can produce.  An xlsx ruleset's lattice
#: must classify each one; a PDF ruleset declares its own vocabulary.
TOKEN_VALUES = ("S", "A", "--", "D", "A/D", "■", "□", "*", "", "<code>")

#: Tokens every format can produce: an empty cell, and a cell holding a code.
BASE_TOKENS = ("", "<code>")

_CLASS_RANK = {"standard": 3, "package": 2, "optional": 1, "unavailable": 0}

#: Question ids the pipeline reads answers from.  Wording is data; which
#: question feeds which decision is code.
QUESTION_IDS = (
    "alignment", "succession", "absorbed", "direction",
    "description_kind", "order_risk", "content_churn",
)

#: Placeholders a question template may use.
PLACEHOLDERS = frozenset({
    "old_year", "new_year", "oem", "brand", "model",
    "pair_old", "pair_new", "cond_old", "cond_new",
})
STATE_KEY_PLACEHOLDERS = frozenset({"old_year", "new_year"})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
    return pattern


def placeholders_in(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}


# --------------------------------------------------------------------------
# Sheets.  Column and row numbers are 1-based, as a spreadsheet user reads them.
# --------------------------------------------------------------------------

class _SheetBase(_Frozen):
    #: Exact worksheet name, or a regular expression when ``match="regex"``.
    name: str
    match: Literal["exact", "regex"] = "exact"

    @model_validator(mode="after")
    def _regex_compiles(self):
        if self.match == "regex":
            _check_regex(self.name)
        return self


class FeatureSheet(_SheetBase):
    """An availability matrix: codes and a description, then one column per trim."""

    kind: Literal["feature_matrix"] = "feature_matrix"
    header_row: int = 3
    orderable_col: int = 1
    ref_col: int = 2
    desc_col: int = 3
    first_axis_col: int = 4


class KeyedSheet(_SheetBase):
    """A label column plus value columns, e.g. Specs and Dimensions."""

    kind: Literal["keyed_rows"] = "keyed_rows"
    header_row: int = 1
    label_col: int = 1
    first_axis_col: int = 2


class StackedSheet(_SheetBase):
    """Several sub-tables stacked vertically, each with its own header row."""

    kind: Literal["stacked_matrix"] = "stacked_matrix"
    #: A row whose first cell (lower-cased) is one of these starts a sub-table.
    header_keywords: tuple[str, ...] = (
        "decor level", "exterior solid paint", "exterior paint", "exterior premium paint",
    )
    label_col: int = 1
    code_col: int = 3
    seat_col: int = 4
    first_axis_col: int = 5


class GridSheet(_SheetBase):
    """Multi-level merged headers keyed by a carried-forward first column."""

    kind: Literal["grid"] = "grid"
    #: The header row is the first whose first cell reads this (lower-cased).
    header_label: str = "model"
    search_rows: int = 5
    first_axis_col: int = 2


class FlatSheet(_SheetBase):
    """A ``code -> description`` dictionary for the model year."""

    kind: Literal["flat_lookup"] = "flat_lookup"
    start_row: int = 2
    code_col: int = 1
    desc_col: int = 2


SheetRule = Annotated[
    Union[FeatureSheet, KeyedSheet, StackedSheet, GridSheet, FlatSheet],
    Field(discriminator="kind"),
]


class SheetRegistry(_Frozen):
    sheets: tuple[SheetRule, ...]
    #: What to do with a worksheet no rule names.  ``fatal`` refuses to report,
    #: which is the safe default: an unparsed sheet under-reports silently.
    unknown_sheet: Literal["fatal", "warn", "skip"] = "fatal"
    #: The per-year code dictionary that arbitrates add-versus-relocate.  It is
    #: never itself diffed.
    dictionary_sheet: str | None = "All"

    def rule_for(self, name: str) -> SheetRule | None:
        for rule in self.sheets:
            if rule.match == "exact" and rule.name == name:
                return rule
        for rule in self.sheets:
            if rule.match == "regex" and re.fullmatch(rule.name, name):
                return rule
        return None

    @model_validator(mode="after")
    def _unique_names(self):
        seen = set()
        for rule in self.sheets:
            if rule.match == "exact":
                if rule.name in seen:
                    raise ValueError(f"sheet {rule.name!r} is registered twice")
                seen.add(rule.name)
        return self


# --------------------------------------------------------------------------
# Footnote clauses.
# --------------------------------------------------------------------------

class RelationPattern(_Frozen):
    relation: str
    pattern: str
    note: str = ""

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        return _check_regex(v)


@functools.lru_cache(maxsize=64)
def _compile_patterns(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, re.Pattern], ...]:
    return tuple((name, re.compile(pat)) for name, pat in pairs)


@functools.lru_cache(maxsize=64)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern)


class ClauseRules(_Frozen):
    #: An RPO code as cited inside prose.  Group 1 is the code.
    code_regex: str = r"\(([0-9A-Z]{3})\)"
    #: Sentence boundary between clauses.
    clause_split_regex: str = r"(?<!\d)\.\s+(?=[A-Z(])"
    #: Stage 1: matched against the start of the lower-cased clause, first wins.
    anchored: tuple[RelationPattern, ...]
    #: Stage 2: searched anywhere, first wins.
    fallback: tuple[RelationPattern, ...]
    #: Relations that restrict or permit an order (the ordering lens).
    ordering_relations: tuple[str, ...]
    #: Relations that are prose only (content lens only, never judged).
    prose_relations: tuple[str, ...]

    @field_validator("ordering_relations", "prose_relations")
    @classmethod
    def _sorted(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        # Stored sorted so the serialized form, and so the content hash, does
        # not depend on the order an editor happened to list them in.
        return tuple(sorted(set(v)))

    @property
    def ordering(self) -> frozenset[str]:
        return frozenset(self.ordering_relations)

    @property
    def prose(self) -> frozenset[str]:
        return frozenset(self.prose_relations)

    @field_validator("code_regex", "clause_split_regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        return _check_regex(v)

    @field_validator("code_regex")
    @classmethod
    def _has_group(cls, v: str) -> str:
        if re.compile(v).groups < 1:
            raise ValueError("code_regex must capture the code in group 1")
        return v

    @property
    def code_re(self) -> re.Pattern:
        return _compile(self.code_regex)

    @property
    def clause_re(self) -> re.Pattern:
        return _compile(self.clause_split_regex)

    @property
    def compiled_anchored(self):
        return _compile_patterns(tuple((p.relation, p.pattern) for p in self.anchored))

    @property
    def compiled_fallback(self):
        return _compile_patterns(tuple((p.relation, p.pattern) for p in self.fallback))

    @property
    def relations(self) -> frozenset[str]:
        return frozenset(p.relation for p in self.anchored + self.fallback)


# --------------------------------------------------------------------------
# Pairing, classification.
# --------------------------------------------------------------------------

class TierRule(_Frozen):
    #: Reported on every pair and used in the UI; keep stable once published.
    id: int
    key: PairingKey
    reason: str
    #: Pair a key only when it occurs exactly once on each side.
    unique_only: bool = False
    enabled: bool = True


class PairingRules(_Frozen):
    tiers: tuple[TierRule, ...]

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [t.id for t in self.tiers]
        if len(set(ids)) != len(ids):
            raise ValueError("tier ids must be unique")
        return self


class ClassifyRules(_Frozen):
    #: Availability token -> class.  Direction is computed between classes.
    token_classes: dict[str, AvailabilityClass]
    #: How alike two same-shape clauses must read to count as a rewrite.
    rewrite_similarity: float = 60.0
    #: Separator of a compound token such as Ford's ``O/-`` (optional on one
    #: body style, not available on the other).  A compound token's class is
    #: the most available of its parts; a change between two tokens of the
    #: same class is a fulfilment shift, so ``O`` -> ``O/-`` is still reported.
    composite_separator: str | None = None

    @field_validator("token_classes")
    @classmethod
    def _base_tokens(cls, v: dict[str, str]) -> dict[str, str]:
        missing = [t for t in BASE_TOKENS if t not in v]
        if missing:
            raise ValueError(f"token_classes must classify every token; missing {missing}")
        return v

    def class_of(self, token: str) -> str | None:
        """The class of a token, compound tokens included; None if unknown."""
        if token in self.token_classes:
            return self.token_classes[token]
        sep = self.composite_separator
        if sep and sep in token:
            parts = [self.token_classes.get(p) for p in token.split(sep)]
            if all(parts):
                return max(parts, key=lambda c: _CLASS_RANK[c])
        return None


# --------------------------------------------------------------------------
# Judgment questions and lenses.
# --------------------------------------------------------------------------

class QuestionTemplate(_Frozen):
    type: Literal["score", "noul", "choice"]
    instructions: str
    criteria: tuple[str, ...] | dict[str, str] | None = None

    def texts(self) -> list[str]:
        out = [self.instructions]
        if isinstance(self.criteria, dict):
            out.extend(self.criteria.values())
        elif self.criteria:
            out.extend(self.criteria)
        return out

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "score" and not isinstance(self.criteria, tuple):
            raise ValueError("a score question needs a list of criteria levels")
        if self.type == "choice" and not isinstance(self.criteria, dict):
            raise ValueError("a choice question needs a mapping of option -> criterion")
        for text in self.texts():
            unknown = placeholders_in(text) - PLACEHOLDERS
            if unknown:
                raise ValueError(f"unknown placeholder(s) {sorted(unknown)} in {text[:60]!r}")
        return self


class StateKeys(_Frozen):
    """Names of the year-specific fields in request state."""

    pair_old: str = "row_{old_year}"
    pair_new: str = "row_{new_year}"
    cond_old: str = "condition_{old_year}"
    cond_new: str = "condition_{new_year}"

    @model_validator(mode="after")
    def _placeholders(self):
        for value in (self.pair_old, self.pair_new, self.cond_old, self.cond_new):
            unknown = placeholders_in(value) - STATE_KEY_PLACEHOLDERS
            if unknown:
                raise ValueError(f"unknown placeholder(s) {sorted(unknown)} in {value!r}")
        return self


class JudgeRules(_Frozen):
    questions: dict[str, QuestionTemplate]
    state_keys: StateKeys = StateKeys()
    #: Glossary of availability tokens sent with every materiality request.
    token_legend: dict[str, str]
    alignment_outcomes: tuple[str, str, str] = ("unlink", "review", "pair")
    constraint_outcomes: tuple[str, str, str] = ("tightened", "equivalent", "loosened")
    #: Below this, even a confident-looking pairing goes to review.
    confidence_floor: float = 0.75
    #: Below this description similarity, two residue rows are not asked about.
    similarity_floor: float = 50.0

    @field_validator("questions")
    @classmethod
    def _all_questions(cls, v: dict[str, QuestionTemplate]) -> dict[str, QuestionTemplate]:
        missing = [q for q in QUESTION_IDS if q not in v]
        if missing:
            raise ValueError(f"missing question(s) {missing}")
        unknown = [q for q in v if q not in QUESTION_IDS]
        if unknown:
            raise ValueError(f"unknown question id(s) {unknown}")
        return v


class LensRule(_Frozen):
    weights: dict[str, float]
    bands: dict[str, float] = {"material": 1.5, "review": 0.5}
    confidence_floor: float = 0.75

    @field_validator("bands")
    @classmethod
    def _bands(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != {"material", "review"}:
            raise ValueError("bands must define exactly 'material' and 'review'")
        return v


# --------------------------------------------------------------------------
# Detection and the document.
# --------------------------------------------------------------------------

class DetectionRules(_Frozen):
    #: Filename regexes; named groups ``year``, ``brand`` and ``model`` are read.
    filename_patterns: tuple[str, ...] = ()
    #: The brand to suggest when a filename does not name one ("Ford").
    default_brand: str | None = None
    #: Worksheet names whose presence identifies this OEM's format.
    fingerprint_sheets: tuple[str, ...] = ()

    @field_validator("filename_patterns")
    @classmethod
    def _compiles(cls, v):
        for p in v:
            _check_regex(p)
        return v


# --------------------------------------------------------------------------
# PDF layouts.
# --------------------------------------------------------------------------

PdfLayout = Literal["option_matrix", "feature_list", "code_grid", "label_grid", "ignore"]


class PdfSection(_Frozen):
    """One kind of page in a PDF order guide, recognised by its heading.

    Layouts:

    ``option_matrix``
        Rows are options (description, code, notes, package contents); columns
        are equipment groups.  Every series' pages merge into one sheet whose
        columns are all the groups, so an option moving between series shows.
    ``feature_list``
        A bulleted list with category headings, one sheet per series.
    ``code_grid``
        Rows keyed by a code (paint, seat belt) against columns identified by
        codes in the header rows (interior trim, stripe).  Pages merge.
    ``label_grid``
        Rows keyed by their label against columns named by the header rows.
    ``ignore``
        Recognised and deliberately not compared (prose, change summaries).
    """

    #: Regex searched (case-insensitively) in the page's heading lines.
    heading: str
    layout: PdfLayout
    #: Sheet name; ``{series}`` is replaced for per-series layouts.
    sheet: str = ""

    @field_validator("heading")
    @classmethod
    def _compiles(cls, v: str) -> str:
        return _check_regex(v)

    @model_validator(mode="after")
    def _needs_sheet(self):
        if self.layout != "ignore" and not self.sheet:
            raise ValueError(f"section {self.heading!r} needs a sheet name")
        if self.layout == "feature_list" and "{series}" not in self.sheet:
            raise ValueError("a feature_list sheet name must contain {series}")
        return self


class PdfRules(_Frozen):
    sections: tuple[PdfSection, ...]
    #: How many lines at the top of a page may hold its heading.
    heading_lines: int = 5
    #: Regex with a ``series`` group, searched in the heading lines.
    series_pattern: str = r"^(?:\d\d/\d\d/\d\d\s+)?\d{4}\s+\S+\s*(?P<series>.*?)\s*PROPRIETARY"
    #: Series as printed (upper-cased, marks removed) -> display name.
    series_names: dict[str, str] = {}
    #: Cell text -> canonical token.  Keys are matched after trimming.
    tokens: dict[str, str]
    #: Splits a compound cell (``O/-/I``) into tokens.
    composite_separator: str = "/"
    #: An option, paint or trim code standing alone in a cell.
    code_regex: str = r"^(?:[0-9A-Z]{2,4}|\*\*\*)$"
    #: An equipment-group code in a header or entities cell (group 1).
    group_code_regex: str = r"\b(\d{3}A)\d?\b"
    #: Characters marking "new for this model year"; stripped for comparison.
    new_marker: str = "\uf0ab"
    #: Rows whose first cell matches any of these are not data.
    ignore_rows: tuple[str, ...] = ()
    #: Build a code -> description dictionary sheet from every coded row, so
    #: unpaired codes can be told apart as retired, introduced or moved.
    synthesize_dictionary: bool = True
    #: Severity of a cell no token matches.
    unparsed_severity: Literal["fatal", "warn"] = "warn"

    @field_validator("series_pattern", "code_regex", "group_code_regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        return _check_regex(v)

    @field_validator("ignore_rows")
    @classmethod
    def _rows_compile(cls, v):
        for p in v:
            _check_regex(p)
        return v

    def section_for(self, heading: list[str]) -> PdfSection | None:
        for section in self.sections:
            rx = _compile_ci(section.heading)
            if any(rx.search(line) for line in heading):
                return section
        return None


@functools.lru_cache(maxsize=256)
def _compile_ci(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


class RuleSet(_Frozen):
    schema_version: int = SCHEMA_VERSION
    name: str
    format: Literal["xlsx", "pdf"] = "xlsx"
    sheets: SheetRegistry
    clauses: ClauseRules
    pairing: PairingRules
    classify: ClassifyRules
    judge: JudgeRules
    lenses: dict[str, LensRule]
    detection: DetectionRules = DetectionRules()
    pdf: PdfRules | None = None

    @model_validator(mode="after")
    def _format_sections(self):
        if self.format == "pdf" and self.pdf is None:
            raise ValueError("a pdf ruleset needs a 'pdf' section describing its page layouts")
        classes = self.classify
        if self.format == "xlsx":
            missing = [t for t in TOKEN_VALUES if t not in classes.token_classes]
            if missing:
                raise ValueError(f"token_classes must classify every token; missing {missing}")
        if self.pdf is not None:
            unknown = sorted({t for t in self.pdf.tokens.values()
                              if classes.class_of(t) is None})
            if unknown:
                raise ValueError(f"token_classes must classify every pdf token; "
                                 f"missing {unknown}")
        return self

    @model_validator(mode="after")
    def _lens_questions(self):
        for name, lens in self.lenses.items():
            unknown = [q for q in lens.weights if q not in QUESTION_IDS]
            if unknown:
                raise ValueError(f"lens {name!r} weights unknown question(s) {unknown}")
        return self

    # -- serialization ----------------------------------------------------

    def to_json(self, *, indent: int | None = 2) -> str:
        # Key order is kept, not sorted: mapping order is meaningful (the first
        # lens is the default, choice criteria are presented in order).
        return json.dumps(self.model_dump(mode="json"), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "RuleSet":
        return cls.model_validate_json(text)

    def content_hash(self) -> str:
        """Identity of the rules, independent of formatting and key order."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                               ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
