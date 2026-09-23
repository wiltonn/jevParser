"""Job type -> handler.  Each takes a ``JobContext`` and returns a JSON result."""

from __future__ import annotations

from typing import Any, Callable

from ..db import SessionLocal
from ..db.models import Document, ParseRun, RulesetVersion
from .queue import JobContext


def parse_document(ctx: JobContext) -> dict[str, Any]:
    from ..services.documents import execute_parse

    with SessionLocal() as session:
        run = session.get(ParseRun, ctx.params["parse_run_id"])
        ctx.progress("parse", 0.1, force=True)
        execute_parse(session, run)
        return {"parse_run_id": run.id, "status": run.status,
                "fatal": run.fatal_count, "warnings": run.warn_count}


def run_comparison(ctx: JobContext) -> dict[str, Any]:
    from ..services.comparisons import execute

    return execute(ctx)


def export(ctx: JobContext) -> dict[str, Any]:
    from ..services.exports import execute

    return execute(ctx)


def test_ruleset(ctx: JobContext) -> dict[str, Any]:
    """Dry-run a ruleset version against a document, and optionally a pair.

    Reports what an editor needs before publishing: parse warnings, the
    relation histogram with the clauses no pattern matched, and -- given a
    second document -- how event counts move against the published baseline.
    Nothing is persisted except this job's result.
    """
    from jev_diff.pipeline import compare, parse_document

    from ..services.blobstore import blob_path
    from ..services.documents import label_for, summarize, unstructured_examples
    from ..services.rulesets import latest_published, load_rules

    p = ctx.params
    with SessionLocal() as session:
        version = session.get(RulesetVersion, p["ruleset_version_id"])
        rules = load_rules(version)
        baseline = latest_published(version.ruleset)
        baseline_rules = load_rules(baseline) if baseline and baseline.id != version.id else None
        doc = session.get(Document, p["document_id"])
        pair = session.get(Document, p["pair_document_id"]) if p.get("pair_document_id") else None
        doc_path, doc_label, doc_fmt = str(blob_path(doc.blob_sha256)), label_for(doc), doc.format
        pair_info = (str(blob_path(pair.blob_sha256)), label_for(pair), pair.format) if pair else None

    ctx.progress("parse", 0.1, force=True)
    book = parse_document(doc_path, doc_label, rules, fmt=doc_fmt)
    sheets, histogram = summarize(book)
    out: dict[str, Any] = {
        "sheets": sheets,
        "relations": histogram,
        "unstructured": unstructured_examples(book),
        "warnings": [{"kind": w.kind, "severity": w.severity, "where": w.where,
                      "message": w.message} for w in book.warnings[:200]],
        "fatal": sum(1 for w in book.warnings if w.severity == "fatal"),
    }
    if baseline_rules is not None:
        _, base_hist = summarize(parse_document(doc_path, doc_label, baseline_rules, fmt=doc_fmt))
        out["baseline_relations"] = base_hist
    ctx.check()

    if pair_info:
        ctx.progress("compare", 0.5, force=True)
        # Order the pair oldest first by label.
        a, b = sorted([(doc_path, doc_label, doc_fmt), pair_info], key=lambda x: x[1])

        def counts(r) -> dict[str, Any]:
            res = compare(a[0], b[0], label_a=a[1], label_b=b[1], rules=r, allow_fatal=True)
            kinds: dict[str, int] = {}
            for e in res.events:
                kinds[e.kind] = kinds.get(e.kind, 0) + 1
            tiers: dict[str, int] = {}
            for p_ in res.alignment.pairs:
                tiers[str(p_.tier)] = tiers.get(str(p_.tier), 0) + 1
            return {"events": len(res.events), "kinds": kinds, "tiers": tiers,
                    "residue": len(res.alignment.residue),
                    "constraint_judgments": sum(len(e.needs_constraint_judgment)
                                                for e in res.events)}

        out["comparison"] = {"labels": [a[1], b[1]], "draft": counts(rules)}
        if baseline_rules is not None:
            ctx.check()
            out["comparison"]["baseline"] = counts(baseline_rules)
    return out


HANDLERS: dict[str, Callable[[JobContext], dict[str, Any]]] = {
    "parse_document": parse_document,
    "run_comparison": run_comparison,
    "export": export,
    "test_ruleset": test_ruleset,
}
