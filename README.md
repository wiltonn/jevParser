# jev-diff

Year-over-year diff for vehicle order-guide exports, judged with TypeSafe Jev. It runs as a
web app backed by a database, or from the command line against files.

## Web app

```bash
uv sync --extra web                 # Python side
npm --prefix web install && npm --prefix web run build
uv run jev-web import-cache         # optional: reuse the CLI's .jev-cache/judgments.json
uv run jev-web serve                # http://127.0.0.1:8000
```

The first start migrates the database (SQLite at `data/jev.db`), creates a local admin, the
General Motors and Ford catalogs, and the built-in GM (Excel) and Ford (PDF) rulesets. Then:

1. **Document library** — drop order-guide files (many at once). Each is stored once by content
   hash, identified (OEM, brand, model, year) from its filename and worksheet names, and waits
   for you to confirm. Confirming parses it with the ruleset that covers it.
2. **New comparison** — pick a model and two years for one comparison, or three or more for a
   timeline of adjacent pairs. It runs in the background; the page follows its progress.
3. **Comparison** — the report's `changes`, `sheets` and `pairing` views, with the lens tuner,
   plus parse warnings and exports (the self-contained HTML report, the annotated workbook).
4. **Admin → Rulesets** — every OEM- and year-specific rule is data: worksheet registry, footnote
   clause patterns, pairing tiers, availability lattice, the questions put to Jev (templated on
   `{old_year}`, `{new_year}`, `{oem}`…), lenses and detection. Rulesets are scoped by OEM,
   optionally brand/model, a year range and a format; the narrowest published one wins. Edit a
   draft, try clauses live, test it against stored documents, diff it, publish.

Settings (`JEV_` environment variables or `.env`): `JEV_DATA_DIR` (default `data`),
`JEV_DATABASE_URL` (default SQLite in the data dir; a Postgres URL works), `JEV_WORKERS`
(background job threads, default 1). For frontend development run `jev-web serve` and
`npm --prefix web run dev` (Vite proxies `/api`).

## Command line

```bash
uv run jev-diff compare \
    "docs/content/GMC/2026 GMC Yukon _ Denali Export.xlsx" \
    "docs/content/GMC/2027 GMC Yukon Export.xlsx" \
    --old-label 2026 --new-label 2027 \
    --judge -o report.html --annotate 2027-annotated.xlsx

xdg-open report.html          # or: explorer.exe report.html  on WSL
```

`report.html` is one self-contained file: open it from disk and
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
with no server and no new inference. Editing a question's wording in a ruleset re-asks only that
question — the cache is keyed per `(state, question id, question definition)`, not per request.

## V1 limitations

- **PDF guides are read by layout rules, written against Ford's Mustang guides.** The Ford
  ruleset (`jev_diff/rules/default_ford.py`) maps each page heading to a layout: equipment-group
  option matrices merge into one sheet with a column per equipment group; standard-equipment
  lists stay one sheet per series (higher series list only their additions); paint, seat-belt
  and stripe grids key on codes. Emissions, powertrain and Ford's own change summary are not
  compared. Another OEM's PDFs need their own ruleset, and a different page layout may need a new
  layout parser in `jev_diff/formats/pdf.py`. Where Ford prints a disclaimer as an unnumbered
  "Note:" after a list's last item, it attaches to that item — prose only, never ordering.
- **Sign-in is a stub.** Every request runs as the first administrator; the `X-Jev-User` header
  selects another user for testing roles.
- **The annotated workbook does not preserve embedded images or drawings.** openpyxl does not read
  workbook media, so re-saving drops it (17 wheel photographs on these files). Preserving it needs
  byte-level patching of the xlsx zip and is out of scope for V1. The run warns with the count, and
  the source workbook is never modified. Everything else — sheets, rows, values, formatting — is
  carried over, and the output validates as OOXML.
- The parser is deliberately GM-order-guide-aware. An unrecognised sheet shape fails the run rather
  than guessing; under-reporting is the failure mode that matters here.

`--ruleset FILE.json` runs with a ruleset exported from the web app (or from
`jev-diff rules export-default [--ruleset gm|ford-pdf]`); `jev-diff rules validate FILE.json`
checks one. PDF guides need a PDF ruleset, e.g.:

```bash
uv run jev-diff rules export-default --ruleset ford-pdf -o ford.json
uv run jev-diff compare docs/content/Ford/Mustang/2025-Mustang-Order-Guide.pdf \
    docs/content/Ford/Mustang/2026-Mustang-Order-Guide.pdf --ruleset ford.json --oem Ford --show
```

## Development

```bash
uv sync --extra web --extra dev
uv run python -m pytest tests/ -q      # core, parity goldens, rules, and the web API
npm --prefix web test                  # lens port parity against the Python implementation
```

`tests/test_parity.py` fingerprints every pipeline stage — parse, pairing, events, payload, the
judgment cache keys and the rendered report — against `tests/golden/parity.json`. Regenerate
only for an intentional behaviour change: `uv run python tests/snapshot.py --write`.

`TYPESAFE_API_KEY` is read from the environment, falling back to the `env` block of
`~/.claude/settings.json`. It is never written into the report or the changeset.
