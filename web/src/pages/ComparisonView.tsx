import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ErrorNote, Html, JobLine, Loading, PageHead, Stats, Status } from "../components/ui";
import { DS, type NavItem } from "../design";
import { api } from "../lib/api";
import { human, when } from "../lib/format";
import { isActive, useAction, useApi, useJob } from "../lib/hooks";
import { band, lensValue, type Band, type LensSettings } from "../lib/lens";
import type { Comparison, Job, Payload, PEvent, PSheet } from "../lib/types";

export default function ComparisonView() {
  const { id } = useParams();
  const qc = useQueryClient();
  const c = useApi<Comparison>(["comparison", id], `/comparisons/${id}`, {
    refetchInterval: (d) => (isActive(d?.status) ? 1000 : false),
  });
  const done = c.data?.status === "succeeded";
  const payload = useQuery<Payload>({
    queryKey: ["payload", id, c.data?.finished_at],
    queryFn: () => api.get<Payload>(`/comparisons/${id}/payload`),
    enabled: done, staleTime: Infinity,
  });
  const rerun = useAction(() => api.post(`/comparisons/${id}/rerun`), [["comparison", id]]);
  const cancel = useAction((jobId: number) => api.post(`/jobs/${jobId}/cancel`), [["comparison", id]]);

  if (!c.data) return <div className="page"><Loading /></div>;
  const d = c.data;
  const base = `/comparisons/${id}`;
  const title = `${d.old.year} → ${d.new.year} ${d.brand} ${d.model}`;

  const head = (
    <PageHead title={title}
      crumbs={[["Comparisons", "/comparisons"], [`${d.brand} ${d.model}`, `/catalog/models/${d.model_id}`],
        ...(d.timeline_id ? [["timeline", `/timelines/${d.timeline_id}`] as [string, string]] : [])]}
      sub={<>{d.ruleset.name} v{d.ruleset.version} · {d.options.judge ? "judged" : "not judged"} · <Status value={d.status} /> {d.finished_at && `· ${when(d.finished_at)}`}</>}
      actions={<>
        {isActive(d.status) && d.job && <button className="btn danger" onClick={() => cancel.mutate(d.job!.id)}>Cancel</button>}
        {!isActive(d.status) && <button className="btn" onClick={() => rerun.mutate(undefined)}>Re-run</button>}
      </>} />
  );

  if (!done) {
    return (
      <div className="page">
        {head}
        <div className="panel">
          <JobLine job={d.job} />
          {d.status === "failed" && <div className="banner error" style={{ marginTop: 12 }}>{d.error}</div>}
          {d.status === "cancelled" && <p className="sub" style={{ marginTop: 12 }}>Cancelled. Judgments received before cancelling are cached, so a re-run resumes where this stopped.</p>}
          <p className="sub" style={{ marginTop: 12 }}>
            {d.old.filename} → {d.new.filename}
          </p>
        </div>
        <ErrorNote error={rerun.error || cancel.error} />
      </div>
    );
  }
  if (!payload.data) return <div className="page wide">{head}<Loading /></div>;
  const p = payload.data;

  const tabs = [
    { id: "changes", label: "changes", count: p.events.length },
    { id: "sheets", label: "sheets", count: p.sheets.length },
    { id: "pairing", label: "pairing", count: p.summary.residue + p.summary.judged },
    { id: "warnings", label: "warnings", count: p.warnings.length },
    { id: "exports", label: "exports" },
    { id: "run", label: "run details" },
  ];

  return (
    <div className="page wide">
      {head}
      {!!d.judge_errors?.length && <div className="banner error">{d.judge_errors.length} judgment request(s) failed; affected changes are unranked. First: {d.judge_errors[0]}</div>}
      <TabNav base={base} tabs={tabs} />
      <Routes>
        <Route index element={<Navigate to="changes" replace />} />
        <Route path="changes/:eid?" element={<Changes p={p} base={base} />} />
        <Route path="sheets/:slug?/:rid?" element={<Sheets p={p} base={base} />} />
        <Route path="pairing" element={<Pairing p={p} />} />
        <Route path="warnings" element={<Warnings p={p} />} />
        <Route path="exports" element={<Exports c={d} lenses={Object.keys(p.lenses)} onDone={() => qc.invalidateQueries({ queryKey: ["exports", id] })} />} />
        <Route path="run" element={<RunDetails c={d} p={p} />} />
      </Routes>
    </div>
  );
}

function TabNav({ base, tabs }: { base: string; tabs: { id: string; label: string; count?: number }[] }) {
  const { "*": rest } = useParams();
  const current = (rest ?? "").split("/")[0] || "changes";
  const [params] = useSearchParams();
  const keep = params.toString() ? `?${params}` : "";
  return (
    <Html className="tabs" html={DS().Nav(tabs.map((t) => ({
      id: t.id, label: t.label, count: t.count,
      // Lens and filter settings carry across tabs, as in the report.
      href: `${base}/${t.id}${t.id === "changes" || t.id === "sheets" ? keep : ""}`,
    })), current)} />
  );
}

/* ------------------------------------------------------------------------ */
/* Changes: the report's main view, with the lens tuner.                     */
/* ------------------------------------------------------------------------ */

const DEFAULT_BANDS: Band[] = ["material", "review"];

function useViewState(p: Payload) {
  const [params, setParams] = useSearchParams();
  const lensNames = Object.keys(p.lenses);
  const lensName = params.get("lens") && p.lenses[params.get("lens")!] ? params.get("lens")! : lensNames[0];
  const base = p.lenses[lensName];
  const lens: LensSettings = {
    weights: Object.fromEntries(Object.entries(base.weights).map(([q, w]) =>
      [q, params.has(`w.${q}`) ? Number(params.get(`w.${q}`)) : w])),
    bands: {
      material: params.has("m") ? Number(params.get("m")) : base.bands.material,
      review: params.has("r") ? Number(params.get("r")) : base.bands.review,
    },
    confidence_floor: params.has("c") ? Number(params.get("c")) : base.confidence_floor,
  };
  const bands = new Set<string>(params.has("b") ? params.get("b")!.split(",").filter(Boolean) : DEFAULT_BANDS);
  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k); else next.set(k, v);
    }
    setParams(next, { replace: true });
  };
  return {
    lensName, lensNames, lens, bands, set,
    q: params.get("q") ?? "", sheet: params.get("sheet") ?? "", trim: params.get("trim") ?? "", kind: params.get("kind") ?? "",
    onlyChanged: params.get("changed") === "1",
    params,
  };
}

function haystack(e: PEvent): string {
  const parts = [e.code ?? "", e.description, e.detail ?? ""];
  for (const d of e.deltas ?? []) {
    parts.push(...(d.added ?? []), ...(d.dropped ?? []));
    for (const pair of d.reworded ?? []) parts.push(...pair);
  }
  return parts.join(" \u0000 ").toLowerCase();
}

function Changes({ p, base }: { p: Payload; base: string }) {
  const { eid } = useParams();
  const v = useViewState(p);
  const [q, setQ] = useState(v.q);
  useEffect(() => setQ(v.q), [v.q]);
  useEffect(() => { const t = setTimeout(() => q !== v.q && v.set({ q }), 200); return () => clearTimeout(t); }, [q]); // eslint-disable-line

  const hay = useMemo(() => new Map(p.events.map((e) => [e.eid, haystack(e)])), [p]);
  const trims = useMemo(() => [...new Set(p.sheets.flatMap((s) => s.trims.new.map((t) => t.name)))], [p]);
  const kinds = useMemo(() => [...new Set(p.events.map((e) => e.kind))].sort(), [p]);

  const scored = p.events.map((e) => ({ e, band: band(e.judgments, v.lens), value: lensValue(e.judgments, v.lens) }));
  const counts: Record<string, number> = { material: 0, review: 0, cosmetic: 0, unjudged: 0 };
  scored.forEach((r) => counts[r.band]++);
  const needle = v.q.trim().toLowerCase();
  const visible = scored.filter((r) =>
    (v.bands.has(r.band) || r.band === "unjudged") &&
    (!needle || hay.get(r.e.eid)!.includes(needle)) &&
    (!v.sheet || r.e.sheet === v.sheet || r.e.also_on.includes(v.sheet)) &&
    (!v.kind || r.e.kind === v.kind) &&
    (!v.trim || r.e.deltas.some((d) => d.trim === v.trim && d.changed)),
  ).sort((a, b) => b.value - a.value);
  const linked = eid ? scored.find((r) => r.e.eid === eid) : undefined;
  const hiddenLink = linked && !visible.some((r) => r.e.eid === eid);

  useEffect(() => {
    if (eid) document.getElementById(`e-${eid}`)?.scrollIntoView({ block: "center" });
  }, [eid, p]);

  const ds = DS();
  const card = (r: typeof scored[number], extra = "") =>
    ds.EventCard(r.e, r.band, r.value, p.old, p.new)
      .replace('<article class="card ev"', `<article id="e-${ds.esc(r.e.eid)}" class="card ev${extra}${r.e.eid === eid ? " linked" : ""}"`);

  const facts = [
    v.bands.size === 3 ? "" : [...v.bands].join(", "),
    v.sheet && `sheet ${v.sheet}`, v.trim && `trim ${v.trim}`, v.kind && `kind ${human(v.kind)}`, v.q && `"${v.q}"`,
  ];
  const lensLabel = (n: string) => (n === "ordering" ? "ordering accuracy" : n === "content" ? "content ops" : human(n));
  const clear = () => v.set({ q: null, sheet: null, trim: null, kind: null, b: null });
  const spend = (p.summary.input_tokens * 0.042) / 1e6;

  return (
    <>
      <Stats items={[
        [`${p.summary.rows_old}→${p.summary.rows_new}`, "rows"],
        [p.summary.paired, "paired"],
        [p.events.length, "changes"],
        [`${p.summary.multi_sheet_codes}/${p.summary.codes}`, "codes on 2+ sheets"],
        [p.summary.requests, "jev requests"],
        [`$${spend.toFixed(4)}`, "spend"],
      ]} />
      <div className="card tuner">
        <div className="trow">
          <label>Lens</label>
          {v.lensNames.map((n) => (
            <button key={n} data-lens={n} className={n === v.lensName ? "on" : ""}
              onClick={() => { const next: Record<string, string | null> = { lens: n === v.lensNames[0] ? null : n, m: null, r: null, c: null };
                Object.keys(p.lenses[v.lensName].weights).forEach((q) => { next[`w.${q}`] = null; }); v.set(next); }}>{lensLabel(n)}</button>
          ))}
          <span className="where">{Object.entries(p.summary.tiers).sort().map(([t, n]) => `tier ${t}: ${n}`).join(" · ")}</span>
        </div>
        <div className="trow">
          {Object.entries(v.lens.weights).map(([qid, w]) => (
            <Slider key={qid} label={human(qid)} max={1} value={w} onChange={(x) => v.set({ [`w.${qid}`]: String(x) })} />
          ))}
          <Slider label="material ≥" max={2} value={v.lens.bands.material} onChange={(x) => v.set({ m: String(x) })} />
          <Slider label="review ≥" max={2} value={v.lens.bands.review} onChange={(x) => v.set({ r: String(x) })} />
          <Slider label="min confidence" max={1} value={v.lens.confidence_floor} onChange={(x) => v.set({ c: String(x) })} />
        </div>
      </div>
      <div className="card bar">
        <input className="q-in" type="search" value={q} placeholder="code, description or condition text" onChange={(e) => setQ(e.target.value)} />
        <select value={v.sheet} onChange={(e) => v.set({ sheet: e.target.value })}>
          <option value="">all sheets</option>{p.sheets.map((s) => <option key={s.name}>{s.name}</option>)}
        </select>
        <select value={v.trim} onChange={(e) => v.set({ trim: e.target.value })}>
          <option value="">all trims</option>{trims.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={v.kind} onChange={(e) => v.set({ kind: e.target.value })}>
          <option value="">all kinds</option>{kinds.map((k) => <option key={k} value={k}>{human(k)}</option>)}
        </select>
      </div>
      <div className="bands">
        {(["material", "review", "cosmetic"] as const).map((b) => (
          <Html as="span" key={b} html={ds.BandPill(b, counts[b], v.bands.has(b))} onClick={() => {
            const next = new Set(v.bands); if (next.has(b)) next.delete(b); else next.add(b);
            const list = [...next].sort();
            v.set({ b: list.join(",") === [...DEFAULT_BANDS].sort().join(",") ? null : list.join(",") || "none" });
          }} />
        ))}
        {counts.unjudged > 0 && <Html as="span" html={ds.BandPill("unjudged", counts.unjudged)} />}
      </div>
      <div className="crumb">
        {hiddenLink ? <><b>1</b> shown by link · filters would hide it</> :
          <>showing <b>{visible.length}</b> of <b>{p.events.length}</b>{facts.filter(Boolean).map((f) => ` · ${f}`)}</>}
        {(facts.some(Boolean) || hiddenLink) && <> &nbsp;<a href="#" onClick={(e) => { e.preventDefault(); clear(); }}>clear</a></>}
      </div>
      {hiddenLink && linked && <Html html={card(linked, " linked")} />}
      {visible.length ? (
        <Html html={visible.map((r) => card(r)).join("")} onClick={(e) => {
          const art = (e.target as HTMLElement).closest("article[id^='e-']");
          if (art && !(e.target as HTMLElement).closest("a")) {
            history.replaceState(null, "", `${base}/changes/${art.id.slice(2)}${location.search}`);
          }
        }} />
      ) : <div className="card empty">Nothing matches {facts.filter(Boolean).join(" · ") || "the current bands"}.</div>}
    </>
  );
}

function Slider({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (v: number) => void }) {
  return (
    <div className="ctl">
      <label>{label}</label>
      <input type="range" min={0} max={max} step={0.05} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <output>{value.toFixed(2)}</output>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* Sheets: every row of every worksheet, linked to the change it produced.   */
/* ------------------------------------------------------------------------ */

function Sheets({ p, base }: { p: Payload; base: string }) {
  const { slug, rid } = useParams();
  const v = useViewState(p);
  const nav = useNavigate();
  const sheet: PSheet = p.sheets.find((s) => s.slug === slug) ?? p.sheets[0];
  const ds = DS();
  const [q, setQ] = useState(v.q);
  useEffect(() => { const t = setTimeout(() => q !== v.q && v.set({ q }), 200); return () => clearTimeout(t); }, [q]); // eslint-disable-line
  useEffect(() => { if (rid) document.getElementById(`r-${rid}`)?.scrollIntoView({ block: "center" }); }, [rid, slug]);

  const groups: [string, PSheet["shape"]][] = [["feature sheets", "feature"], ["spec sheets", "keyed"], ["ratings", "grid"]];
  const items: NavItem[] = [];
  const keep = v.params.toString() ? `?${v.params}` : "";
  for (const [label, shape] of groups) {
    const list = p.sheets.filter((s) => s.shape === shape);
    if (!list.length) continue;
    items.push({ group: label });
    list.forEach((s) => items.push({ id: s.slug, label: s.name, count: s.counts.changed, href: `${base}/sheets/${s.slug}${keep}` }));
  }
  const rows = sheet.rows.new.length ? sheet.rows.new : sheet.rows.old;
  const needle = v.q.trim().toLowerCase();
  const shown = rows.filter((r) => (!v.onlyChanged || r.eid) && (!needle ||
    [r.code ?? "", r.desc, ...Object.values(r.v).map((c) => (c.c ?? []).join(" "))].join(" ").toLowerCase().includes(needle)));
  const table = sheet.shape === "keyed" ? ds.KeyedTable(sheet, p.old, p.new)
    : sheet.shape === "grid" ? ds.GridTable(sheet, p.old, p.new)
    : ds.SheetTable(sheet, shown, sheet.trims.new, { hit: rid ?? null });
  const html = (ds.AxisLineup(sheet, p.old, p.new) + table).replaceAll('href="#/changes/e/', `href="${base}/changes/`);

  return (
    <>
      <Stats items={[
        [`${sheet.counts.rows_old}→${sheet.counts.rows_new}`, "rows"], [sheet.counts.paired, "paired"],
        [sheet.counts.changed, "changed"], [sheet.trims.new.length, "trims"],
      ]} />
      <div className="card bar">
        <input className="q-in" type="search" value={q} placeholder="code, description or condition text" onChange={(e) => setQ(e.target.value)} />
        <label className="chk"><input type="checkbox" checked={v.onlyChanged} onChange={(e) => v.set({ changed: e.target.checked ? "1" : null })} /> only changed</label>
      </div>
      <div className="crumb">showing <b>{shown.length}</b> of <b>{rows.length}</b>{v.onlyChanged && " · only changed"}{v.q && ` · "${v.q}"`}</div>
      <div className="rail">
        <Html html={ds.Nav(items, sheet.slug, { vertical: true })} />
        <Html className="rail-body card ev" html={html} onClick={(e) => {
          const tr = (e.target as HTMLElement).closest("tr[id^='r-']");
          if (tr && !(e.target as HTMLElement).closest("a")) nav(`${base}/sheets/${sheet.slug}/${tr.id.slice(2)}${keep}`, { replace: true });
        }} />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------ */

function Pairing({ p }: { p: Payload }) {
  const order = ["needs_review", "retired", "introduced", "moved_off_sheet", "moved_onto_sheet", "listing_removed", "listing_added"];
  const by: Record<string, Payload["residue"]> = {};
  p.residue.forEach((r) => (by[r.disposition] ??= []).push(r));
  return (
    <>
      <Stats items={[[p.summary.residue, "unpaired rows"], [p.summary.needs_review, "need a judgment"],
        [p.summary.judged, "identity judgments"], [Object.keys(p.summary.tiers).length, "tiers used"]]} />
      <p className="sub">Rows the deterministic tiers could not pair, and why each candidate was matched or rejected.
        Everything but <span className="mono">needs review</span> was settled by the option dictionary alone.</p>
      {order.filter((d) => by[d]).map((d) => (
        <div key={d}>
          <div className="grp-h">{human(d)} · {by[d].length}</div>
          {by[d].map((r) => (
            <div className="card unp" key={r.uid} id={`u-${r.uid}`}>
              <span className="code">{r.code ?? "—"}</span><span className="desc">{r.description}</span>
              <span className="where">{r.sheet} · {r.year}</span>
            </div>
          ))}
        </div>
      ))}
      <h2>Identity judgments</h2>
      <div className="card ev" style={{ overflowX: "auto" }}>
        <table className="plain">
          <thead><tr><th>Outcome</th><th>Score</th><th>Conf.</th><th>Succession</th><th>Absorbed</th><th>Candidate</th></tr></thead>
          <tbody>{p.pairings.map((x) => (
            <tr key={x.jid} className="jrow"><td>{x.outcome}</td>
              <td className="mono">{(x.score ?? 0).toFixed(2)}</td><td className="mono">{(x.confidence ?? 0).toFixed(2)}</td>
              <td className="mono">{(x.succession ?? 0).toFixed(2)}</td><td className="mono">{(x.absorbed ?? 0).toFixed(2)}</td>
              <td>{x.key.replace(/^pair:/, "").slice(0, 110)}</td></tr>
          ))}</tbody>
        </table>
        {!p.pairings.length && <p className="sub">No identity judgments: the comparison ran without Jev, or nothing was similar enough to ask about.</p>}
      </div>
    </>
  );
}

function Warnings({ p }: { p: Payload }) {
  if (!p.warnings.length) return <div className="card empty">Both documents parsed without warnings.</div>;
  return (
    <div className="card ev" style={{ overflowX: "auto" }}>
      <table className="plain"><tbody>{p.warnings.map((w, i) => (
        <tr key={i}><td><Status value={w.severity === "fatal" ? "fatal" : "warnings"} /></td><td className="mono">{w.kind}</td>
          <td className="mono">{w.where}</td><td>{w.message}</td></tr>
      ))}</tbody></table>
    </div>
  );
}

function Exports({ c, lenses, onDone }: { c: Comparison; lenses: string[]; onDone: () => void }) {
  const list = useApi<{ id: number; kind: string; lens: string | null; filename: string; created_at: string }[]>(["exports", String(c.id)], `/comparisons/${c.id}/exports`);
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId, [["exports", String(c.id)]]);
  const [lens, setLens] = useState(lenses[0]);
  const start = useAction((body: { kind: string; lens?: string }) => api.post<Job>(`/comparisons/${c.id}/exports`, body));
  useEffect(() => { if (job.data && !isActive(job.data.status)) onDone(); }, [job.data?.status]); // eslint-disable-line
  const xlsxOk = c.new.filename.toLowerCase().endsWith(".xlsx");
  return (
    <>
      <div className="cols-2">
        <div className="panel">
          <h3>HTML report</h3>
          <p className="sub">One self-contained file with every view, the lens tuner included. Works offline; share it with anyone.</p>
          <button className="btn primary" disabled={start.isPending || isActive(job.data?.status)}
            onClick={() => start.mutate({ kind: "html" }, { onSuccess: (j) => setJobId(j.id) })}>Build report</button>
        </div>
        <div className="panel">
          <h3>Annotated workbook</h3>
          <p className="sub">A copy of the {c.new.year} workbook with changed cells filled by direction and a Change Log sheet ranked by the chosen lens. Embedded images are not carried over.</p>
          <div className="form-row">
            <select value={lens} onChange={(e) => setLens(e.target.value)}>{lenses.map((l) => <option key={l}>{l}</option>)}</select>
            <button className="btn primary" disabled={!xlsxOk || start.isPending || isActive(job.data?.status)}
              onClick={() => start.mutate({ kind: "xlsx", lens }, { onSuccess: (j) => setJobId(j.id) })}>Build workbook</button>
          </div>
          {!xlsxOk && <p className="err">Needs an Excel document for {c.new.year}.</p>}
        </div>
      </div>
      {job.data && <div className="panel"><JobLine job={job.data} /></div>}
      <ErrorNote error={start.error} />
      <h2>Built</h2>
      {list.data?.length ? (
        <div className="card ev" style={{ padding: 0 }}><table className="plain"><tbody>
          {list.data.map((e) => (
            <tr key={e.id}><td><a href={`/api/exports/${e.id}/download`}>{e.filename}</a></td><td className="mono">{e.kind}</td><td>{when(e.created_at)}</td></tr>
          ))}
        </tbody></table></div>
      ) : <p className="sub">Nothing built yet.</p>}
    </>
  );
}

function RunDetails({ c, p }: { c: Comparison; p: Payload }) {
  return (
    <div className="cols-2">
      <div className="panel">
        <h3>Inputs</h3>
        <dl className="kv">
          <dt>{c.old.year}</dt><dd><Link to={`/library/${c.old.document_id}`}>{c.old.filename}</Link></dd>
          <dt>{c.new.year}</dt><dd><Link to={`/library/${c.new.document_id}`}>{c.new.filename}</Link></dd>
          <dt>Ruleset</dt><dd><Link to={`/admin/rulesets/${c.ruleset.id}/versions/${c.ruleset.version_id}`}>{c.ruleset.name} v{c.ruleset.version}</Link></dd>
          <dt>Judged</dt><dd>{c.options.judge ? `yes, ${c.options.workers} workers` : "no"}</dd>
          <dt>Started</dt><dd>{when(c.created_at)}</dd>
          <dt>Finished</dt><dd>{when(c.finished_at)}</dd>
        </dl>
      </div>
      <div className="panel">
        <h3>Judgments</h3>
        <dl className="kv">
          <dt>Requests sent</dt><dd>{c.usage?.requests ?? 0}</dd>
          <dt>Input tokens</dt><dd>{(c.usage?.input_tokens ?? 0).toLocaleString()}</dd>
          <dt>Cache</dt><dd>{c.cache ? `${c.cache.hits} hit / ${c.cache.misses} miss` : "—"}</dd>
          <dt>Pairing tiers</dt><dd>{Object.entries(p.summary.tiers).map(([t, n]) => `${t}: ${n}`).join(" · ")}</dd>
          <dt>Order-affecting</dt><dd>{c.summary?.order_affecting} of {p.events.length}</dd>
        </dl>
      </div>
    </div>
  );
}
