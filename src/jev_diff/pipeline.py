"""One comparison, end to end: the single entry point for the CLI and the web app.

parse (x2) -> align -> classify -> judge (optional, two passes) -> payload.

Progress is reported as ``progress(stage, fraction)`` with ``fraction`` in
[0, 1] over the whole run, and ``cancelled()`` is polled between stages and as
judgments land, so a long judged run can be stopped without losing what it
already paid for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .align.pairing import BookAlignment, align_books
from .classify import ChangeEvent, build_events, consolidate
from .formats import adapter_for, detect_format
from .judge.cache import JudgmentStore
from .judge.client import ApiConfig
from .judge.run import Cancelled, Judgments, lenses_from, run
from .model.canonical import Warning_, Workbook
from .render.payload import build
from .rules.context import PromptContext
from .rules.default_gm import DEFAULT_GM
from .rules.model import RuleSet

ProgressFn = Callable[[str, float], None]

#: Stage -> (start, end) share of the whole run.
STAGES: dict[str, tuple[float, float]] = {
    "parse_old": (0.00, 0.10),
    "parse_new": (0.10, 0.20),
    "align": (0.20, 0.25),
    "classify": (0.25, 0.30),
    "judge_identity": (0.30, 0.55),
    "judge_materiality": (0.55, 0.95),
    "payload": (0.95, 1.00),
}


class ParseFailed(RuntimeError):
    """A workbook produced fatal warnings; reporting would under-report."""

    def __init__(self, warnings: list[Warning_]):
        self.warnings = warnings
        super().__init__(f"{len(warnings)} fatal parse problem(s)")


@dataclass(slots=True)
class ComparisonResult:
    book_a: Workbook
    book_b: Workbook
    alignment: BookAlignment
    events: list[ChangeEvent]
    judgments: Judgments | None
    payload: dict[str, Any]
    warnings: list[Warning_] = field(default_factory=list)

    @property
    def fatal(self) -> list[Warning_]:
        return [w for w in self.warnings if w.severity == "fatal"]


def parse_document(path: str, label: str, rules: RuleSet | None = None,
                   fmt: str | None = None) -> Workbook:
    """Parse one order guide with the adapter for its format."""
    rules = rules or DEFAULT_GM
    return adapter_for(fmt or detect_format(path)).parse(path, label, rules)


def compare(path_a: str, path_b: str, *, label_a: str, label_b: str,
            rules: RuleSet | None = None, context: PromptContext | None = None,
            store: JudgmentStore | None = None, judge: bool = False,
            workers: int = 8, api: ApiConfig | None = None,
            books: tuple[Workbook, Workbook] | None = None,
            allow_fatal: bool = False,
            progress: ProgressFn | None = None,
            cancelled: Callable[[], bool] | None = None) -> ComparisonResult:
    rules = rules or DEFAULT_GM
    context = context or PromptContext(old_year=label_a, new_year=label_b)

    def report(stage: str, within: float = 0.0) -> None:
        if progress:
            start, end = STAGES[stage]
            progress(stage, start + (end - start) * max(0.0, min(1.0, within)))

    def check() -> None:
        if cancelled and cancelled():
            raise Cancelled("cancelled")

    if books is not None:
        book_a, book_b = books
    else:
        report("parse_old")
        book_a = parse_document(path_a, label_a, rules)
        check()
        report("parse_new")
        book_b = parse_document(path_b, label_b, rules)
    warnings = book_a.warnings + book_b.warnings
    fatal = [w for w in warnings if w.severity == "fatal"]
    if fatal and not allow_fatal:
        raise ParseFailed(fatal)
    check()

    report("align")
    alignment = align_books(book_a, book_b, rules)
    report("classify")
    events = consolidate(build_events(book_a, book_b, alignment, rules))
    check()

    judgments: Judgments | None = None
    if judge:
        if store is None:
            raise ValueError("a judged comparison needs a judgment store")

        def on_judge(stage: str, done: int, total: int) -> None:
            report(stage, done / total if total else 1.0)

        judgments = run(alignment, events, store, workers=workers, rules=rules,
                        context=context, api=api, progress=on_judge,
                        cancelled=cancelled)

    report("payload")
    payload = build(book_a, book_b, alignment, events, judgments, lenses_from(rules))
    report("payload", 1.0)
    return ComparisonResult(book_a, book_b, alignment, events, judgments, payload,
                            warnings)
