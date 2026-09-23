"""Command line entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .align.pairing import align_books
from .classify import build_events, consolidate
from .parse.registry import parse_workbook


def _encode(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: v for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, frozenset):
        return sorted(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"not serializable: {type(obj).__name__}")


def _report(book) -> None:
    total = sum(len(s.rows) for s in book.sheets.values())
    print(f"{book.label}: {len(book.sheets)} sheets, {total} rows", file=sys.stderr)
    for sheet in book.sheets.values():
        images = f"  images={len(sheet.images)}" if sheet.images else ""
        print(
            f"  {sheet.kind:<15} {sheet.name:<31} "
            f"rows={len(sheet.rows):>4} cols={len(sheet.columns):>2} "
            f"footnotes={len(sheet.footnotes):>2}{images}",
            file=sys.stderr,
        )


def cmd_parse(args: argparse.Namespace) -> int:
    book = parse_workbook(args.workbook, args.label or Path(args.workbook).stem[:4])
    _report(book)

    fatal = [w for w in book.warnings if w.severity == "fatal"]
    for warning in book.warnings:
        print(f"  {warning.severity.upper()} [{warning.kind}] "
              f"{warning.where}: {warning.message}", file=sys.stderr)

    if args.json:
        payload = {
            "path": book.path,
            "label": book.label,
            "sheets": {
                name: {
                    "kind": sheet.kind,
                    "columns": [dataclasses.asdict(c) for c in sheet.columns],
                    "footnotes": sheet.footnotes,
                    "images": [dataclasses.asdict(i) for i in sheet.images],
                    "rows": [dataclasses.asdict(r) for r in sheet.rows],
                }
                for name, sheet in book.sheets.items()
            },
            "warnings": [dataclasses.asdict(w) for w in book.warnings],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2, default=_encode))
        print(f"wrote {args.json}", file=sys.stderr)

    if fatal:
        print(f"\n{len(fatal)} fatal parse problem(s); refusing to under-report.",
              file=sys.stderr)
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    book_a = parse_workbook(args.old, args.old_label or Path(args.old).stem[:4])
    book_b = parse_workbook(args.new, args.new_label or Path(args.new).stem[:4])

    fatal = [w for w in book_a.warnings + book_b.warnings if w.severity == "fatal"]
    for warning in book_a.warnings + book_b.warnings:
        print(f"  {warning.severity.upper()} [{warning.kind}] {warning.where}: "
              f"{warning.message}", file=sys.stderr)
    if fatal:
        print(f"{len(fatal)} fatal parse problem(s); refusing to under-report.",
              file=sys.stderr)
        return 1

    alignment = align_books(book_a, book_b)
    events = consolidate(build_events(book_a, book_b, alignment))

    tiers: dict[int, int] = {}
    for sheet in alignment.sheets.values():
        for tier, count in sheet.tier_counts().items():
            tiers[tier] = tiers.get(tier, 0) + count
    dispositions = {k: len(v) for k, v in alignment.by_disposition().items() if v}

    print(f"parsed   {len(book_a.sheets)} sheets  "
          f"{sum(len(s.rows) for s in book_a.sheets.values())} -> "
          f"{sum(len(s.rows) for s in book_b.sheets.values())} rows")
    print(f"paired   {sum(tiers.values())} rows  "
          + "  ".join(f"tier{t}={n}" for t, n in sorted(tiers.items())))
    print(f"residue  " + "  ".join(f"{k}={v}" for k, v in dispositions.items()))
    print(f"review   {len(alignment.review_candidates)} rows need a judgment call")

    kinds: dict[str, int] = {}
    for event in events:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
    print(f"events   {len(events)}  " + "  ".join(
        f"{k}={v}" for k, v in sorted(kinds.items())))
    print(f"         {sum(1 for e in events if e.order_affecting)} order-affecting; "
          f"{sum(len(e.needs_constraint_judgment) for e in events)} clauses need "
          f"a constraint judgment")

    if args.judge:
        from .judge.cache import JudgmentCache
        from .judge.run import DEFAULT_LENSES, band, lens_value, run

        cache = JudgmentCache(args.cache)
        judgments = run(alignment, events, cache, workers=args.workers)
        spend = judgments.usage["input_tokens"] * 0.042 / 1_000_000
        print(f"judged   {judgments.usage['requests']} requests  "
              f"{judgments.usage['input_tokens']:,} input tokens  ~${spend:.4f}  "
              f"(cache {judgments.cache_hits} hit / {judgments.cache_misses} miss)")
        for err in judgments.errors[:5]:
            print(f"  ERROR {err}", file=sys.stderr)

        outcomes: dict[str, int] = {}
        for value in judgments.pairings.values():
            outcomes[value["outcome"]] = outcomes.get(value["outcome"], 0) + 1
        if outcomes:
            print("pairing  " + "  ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))

        lens = DEFAULT_LENSES[args.lens]
        bands: dict[str, int] = {}
        for event in events:
            bands[band(event, lens)] = bands.get(band(event, lens), 0) + 1
        print(f"lens     {args.lens}: " + "  ".join(
            f"{k}={v}" for k, v in sorted(bands.items())))

        if args.annotate:
            from .render.xlsx_annotate import annotate, media_count

            lost = media_count(args.new)
            stats = annotate(
                args.new, args.annotate, events,
                bands={i: band(e, lens) for i, e in enumerate(events)},
                values={i: round(lens_value(e, lens), 2) for i, e in enumerate(events)},
            )
            print(f"annotated {args.annotate}  "
                  f"{stats['cells_filled']} cells filled, "
                  f"{stats['log_rows']} change-log rows")
            if lost:
                print(f"  NOTE  {lost} embedded image(s) are not carried into the "
                      f"annotated copy. Preserving workbook media is not supported "
                      f"in V1; the original file is unchanged.", file=sys.stderr)

        if args.out:
            from .render.html import render as render_html
            from .render.payload import build as build_payload

            payload = build_payload(book_a, book_b, alignment, events, judgments)
            Path(args.out).write_text(render_html(payload), encoding="utf-8")
            print(f"report   {args.out}")

        print()
        ranked = sorted(events, key=lambda e: -lens_value(e, lens))
        for event in ranked:
            verdict = band(event, lens)
            if verdict == "cosmetic" and not args.show:
                continue
            also = f" (+{','.join(event.also_on)})" if event.also_on else ""
            print(f"  [{verdict:8}] {lens_value(event, lens):4.2f}  {event.kind:12} "
                  f"{event.code or '-':5} {event.description[:44]}")
            if args.show and event.detail:
                print(f"              {event.detail[:150]}")
            print(f"              {event.sheet}{also}")
        return 0

    if args.show:
        print()
        for event in events:
            if event.kind in ("constraint", "availability", "identity"):
                also = f" (+{','.join(event.also_on)})" if event.also_on else ""
                print(f"  [{event.kind:12}] {event.sheet}{also} "
                      f"{event.code or '-'}: {event.description[:40]}")
                if event.detail:
                    print(f"        {event.detail[:160]}")

    if args.json:
        payload = {
            "old": book_a.label, "new": book_b.label,
            "tiers": tiers, "dispositions": dispositions,
            "events": [dataclasses.asdict(e) for e in events],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2, default=_encode))
        print(f"\nwrote {args.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jev-diff",
        description="Year-over-year diff for GM order-guide exports.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="parse one workbook into the canonical model")
    p.add_argument("workbook")
    p.add_argument("--label", help="model year label (default: inferred)")
    p.add_argument("--json", help="write the canonical model to this path")
    p.set_defaults(func=cmd_parse)

    c = sub.add_parser("compare", help="diff two workbooks")
    c.add_argument("old")
    c.add_argument("new")
    c.add_argument("--old-label")
    c.add_argument("--new-label")
    c.add_argument("--json", help="write the changeset to this path")
    c.add_argument("--show", action="store_true", help="list the change events")
    c.add_argument("--judge", action="store_true",
                   help="run the Jev judgments and rank by lens")
    c.add_argument("--lens", default="ordering", choices=("ordering", "content"),
                   help="which materiality lens ranks the report")
    c.add_argument("--cache", default=".jev-cache/judgments.json",
                   help="judgment cache path")
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("-o", "--out", help="write the self-contained HTML report here")
    c.add_argument("--annotate", metavar="XLSX",
                   help="write an annotated copy of the newer workbook "
                        "(V1: embedded images are not preserved)")
    c.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
