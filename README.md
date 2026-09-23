# jev-diff

Year-over-year diff for GM vehicle order-guide exports, judged with TypeSafe Jev.

```bash
uv run jev-diff compare \
    "docs/content/GMC/2026 GMC Yukon _ Denali Export.xlsx" \
    "docs/content/GMC/2027 GMC Yukon Export.xlsx" \
    --old-label 2026 --new-label 2027 \
    --judge -o report.html --annotate 2027-annotated.xlsx

xdg-open report.html          # or: explorer.exe report.html  on WSL
```

There is **no server**. `report.html` is one self-contained file: open it from disk and
the whole UI runs in the browser with no network. It has three views behind the nav —
`changes`, `sheets` and `pairing` — and every link in it is a plain `#/...` hash, so
views, filters and tuner settings can be bookmarked and shared as ordinary URLs.

`--judge` is optional. Without it the deterministic diff still runs and every change is
listed; the events are simply unranked, because the lens scores are what rank them.

## Why not just diff the cells

The digits in an availability cell are *references into a footnote table*, and the numbers are
unstable between model years while the meanings are what change. Two cases from the GMC exports
show the problem failing in opposite directions:

| RPO | Raw cell diff | Strip the digits | Truth |
| --- | --- | --- | --- |
| `5JL` | 5 changed cells | 0 changes | The "requires 20″ or larger wheels" constraint was **dropped** for 3 trims. Denali unchanged. |
| `SFE` | 1 changed cell (`■3`→`■2`) | 0 changes | Denali Ultimate **gained** a `(SPZ)` exclusion. Order-affecting. |

Neither is recoverable from the cell text. So conditions are resolved to their footnote *text*,
parsed into typed clauses, and compared as clauses — after which footnote renumbering is invisible
and a genuine change stands out.

## What is deterministic, and what Jev decides

Code owns parsing, footnote resolution, constraint parsing, the availability transition lattice,
all row pairing that a rule can settle, and every weight and threshold. Over these two files that
settles 661 of 677 rows and every condition delta but 25.

Jev is asked four things code cannot answer: are two unpaired rows the same entry, is a succession
link real, did a *reworded* constraint loosen or tighten, and how much does a change matter. A full
cold run is ~150 requests and about **$0.005**; reruns are free from cache.

There is deliberately **no cross-sheet pairing**: 76% of option codes are listed on more than one
worksheet in the same year, so matching across sheets manufactures phantom "moved" events.

## Tuning

Raw `score` / `probabilities` / `confidence` are persisted per question and never collapsed into a
single severity, so the report's weight, threshold and confidence controls re-rank in the browser
with no server and no new inference. Editing a question's wording in a profile re-asks only that
question — the cache is keyed per `(state, question id, question definition)`, not per request.

## V1 limitations

- **The annotated workbook does not preserve embedded images or drawings.** openpyxl does not read
  workbook media, so re-saving drops it (17 wheel photographs on these files). Preserving it needs
  byte-level patching of the xlsx zip and is out of scope for V1. The run warns with the count, and
  the source workbook is never modified. Everything else — sheets, rows, values, formatting — is
  carried over, and the output validates as OOXML.
- The parser is deliberately GM-order-guide-aware. An unrecognised sheet shape fails the run rather
  than guessing; under-reporting is the failure mode that matters here.

## Development

```bash
uv run --with openpyxl --with rapidfuzz --with pytest python -m pytest tests/ -q
```

`TYPESAFE_API_KEY` is read from the environment, falling back to the `env` block of
`~/.claude/settings.json`. It is never written into the report or the changeset.
