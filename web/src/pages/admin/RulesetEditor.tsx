import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ErrorNote, JobLine, Loading, PageHead, Status } from "../../components/ui";
import { api } from "../../lib/api";
import { human, when } from "../../lib/format";
import { isActive, useAction, useApi, useJob } from "../../lib/hooks";
import type { CatalogOem, Doc, Job, Ruleset, RulesetVersion } from "../../lib/types";
import { scopeText } from "./Rulesets";

/* A ruleset version's body is edited as plain JSON in local state and saved
   whole; the server validates it with the same pydantic model the pipeline
   loads, so the editor never has to re-implement a rule. */

type Body = any;
type Err = { path: string[]; message: string };

const TABS = ["sheets", "clauses", "pairing", "lattice", "questions", "lenses", "detection", "json"] as const;
type Tab = (typeof TABS)[number] | "test" | "diff";

const QUESTION_HELP: Record<string, string> = {
  alignment: "Asked about pairs of leftover rows: are they the same order-guide entry?",
  succession: "Asked with alignment: did the new row replace the old one?",
  absorbed: "Asked with alignment: was the old row bundled into the new package?",
  direction: "Asked about a reworded ordering condition: did it tighten or loosen?",
  description_kind: "Asked about wording-only changes: what kind of edit was it?",
  order_risk: "Materiality for the ordering lens: what happens to a saved order?",
  content_churn: "Materiality for the content lens: what must published material change?",
};
const PLACEHOLDERS = ["{old_year}", "{new_year}", "{oem}", "{brand}", "{model}", "{pair_old}", "{pair_new}", "{cond_old}", "{cond_new}"];
const TOKENS = ["S", "A", "--", "D", "A/D", "■", "□", "*", "", "<code>"];
const CLASSES = ["standard", "package", "optional", "unavailable"];
const PAIR_KEYS = ["exact", "code_set_and_description", "code_set", "description", "unique_description"];
const SHEET_KINDS: Record<string, [string, string][]> = {
  feature_matrix: [["header_row", "header row"], ["orderable_col", "orderable code col"], ["ref_col", "reference code col"], ["desc_col", "description col"], ["first_axis_col", "first trim col"]],
  keyed_rows: [["header_row", "header row"], ["label_col", "label col"], ["first_axis_col", "first value col"]],
  stacked_matrix: [["label_col", "label col"], ["code_col", "code col"], ["seat_col", "seat col"], ["first_axis_col", "first trim col"]],
  grid: [["search_rows", "search rows"], ["first_axis_col", "first value col"]],
  flat_lookup: [["start_row", "first data row"], ["code_col", "code col"], ["desc_col", "description col"]],
};

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));

export default function RulesetEditor() {
  const { id, vid } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const rs = useApi<Ruleset>(["ruleset", id], `/rulesets/${id}`);
  const versions = rs.data?.versions ?? [];
  const current = vid ? Number(vid)
    : (versions.find((v) => v.status === "draft") ?? versions.find((v) => v.status === "published") ?? versions[0])?.id;
  const version = useApi<RulesetVersion>(["ruleset-version", current], current ? `/ruleset-versions/${current}` : null);
  const [body, setBody] = useState<Body | null>(null);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<Err[]>([]);
  const [tab, setTab] = useState<Tab>("sheets");
  const [notes, setNotes] = useState("");

  useEffect(() => {
    if (version.data?.body) { setBody(clone(version.data.body)); setDirty(false); setErrors([]); setNotes(version.data.notes ?? ""); }
  }, [version.data?.id, version.data?.content_hash]); // eslint-disable-line

  // Validate as you type, server-side, with the real model.
  useEffect(() => {
    if (!dirty || !body) return;
    const t = setTimeout(() => {
      api.post<{ errors: Err[] }>("/ruleset-versions/validate", { body }).then((r) => setErrors(r.errors)).catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [body, dirty]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ruleset", id] });
    qc.invalidateQueries({ queryKey: ["ruleset-version"] });
    qc.invalidateQueries({ queryKey: ["rulesets"] });
  };
  const openDraft = useAction(() => api.post<RulesetVersion>(`/rulesets/${id}/draft`));
  const save = useAction(() => api.put<RulesetVersion>(`/ruleset-versions/${current}`, { body, notes }));
  const publish = useAction(() => api.post(`/ruleset-versions/${current}/publish`));
  const discard = useAction(() => api.del(`/ruleset-versions/${current}`));
  const archive = useAction((vId: number) => api.post(`/ruleset-versions/${vId}/archive`));

  if (!rs.data || !version.data || !body) return <div className="page"><Loading /></div>;
  const r = rs.data;
  const v = version.data;
  const editable = v.status === "draft";
  const update = (fn: (b: Body) => void) => {
    if (!editable) return;
    const next = clone(body);
    fn(next);
    setBody(next);
    setDirty(true);
  };
  const errorsFor = (prefix: string) => errors.filter((e) => e.path.join(".").startsWith(prefix));

  const doSave = () => save.mutate(undefined, {
    onSuccess: () => { setDirty(false); refresh(); },
    onError: (e: any) => { if (e.body?.errors) setErrors(e.body.errors); },
  });

  return (
    <div className="page wide">
      <PageHead title={r.name} crumbs={[["Rulesets", "/admin/rulesets"], [r.oem, null]]}
        sub={<>{scopeText(r)} · priority {r.priority} · viewing <b>v{v.version}</b> <Status value={v.status} /> · <span className="hash">{v.content_hash.slice(0, 12)}</span></>}
        actions={<>
          <a className="btn" href={`/api/ruleset-versions/${v.id}/export`}>Export JSON</a>
          {!editable && !r.has_draft && <button className="btn primary" onClick={() => openDraft.mutate(undefined, {
            onSuccess: (d) => { refresh(); nav(`/admin/rulesets/${id}/versions/${d.id}`); } })}>Edit (new draft)</button>}
          {!editable && r.has_draft && <Link className="btn primary" to={`/admin/rulesets/${id}/versions/${versions.find((x) => x.status === "draft")!.id}`}>Go to draft</Link>}
          {editable && <>
            <button className="btn danger" disabled={versions.length === 1} onClick={() => {
              if (confirm("Discard this draft? Unsaved and saved edits are lost.")) discard.mutate(undefined, { onSuccess: () => { refresh(); nav(`/admin/rulesets/${id}`); } });
            }}>Discard draft</button>
            <button className="btn" disabled={!dirty || save.isPending || errors.length > 0} onClick={doSave}>{save.isPending ? "Saving…" : dirty ? "Save draft" : "Saved"}</button>
            <button className="btn primary" disabled={dirty || errors.length > 0 || publish.isPending} onClick={() => {
              if (confirm(`Publish v${v.version}? Published versions cannot be edited; new comparisons will use it.`)) publish.mutate(undefined, { onSuccess: refresh });
            }}>Publish</button>
          </>}
        </>} />
      <ErrorNote error={openDraft.error || save.error || publish.error || discard.error || archive.error} />
      {editable ? (
        <div className="form-row" style={{ marginBottom: 14 }}>
          <div className="field" style={{ flex: 1 }}><label>Change notes</label>
            <input type="text" value={notes} onChange={(e) => { setNotes(e.target.value); setDirty(true); }} placeholder="What this draft changes, and why" /></div>
        </div>
      ) : <div className="banner">v{v.version} is {v.status}. {v.status === "published" ? "It is read-only; open a draft to change the rules." : ""}</div>}
      {errors.length > 0 && (
        <div className="banner error"><b>{errors.length} problem{errors.length > 1 ? "s" : ""}</b> — fix before saving:
          <ul style={{ margin: "6px 0 0" }}>{errors.slice(0, 8).map((e, i) => <li key={i}><span className="mono">{e.path.join(".")}</span>: {e.message}</li>)}</ul></div>
      )}

      <div className="split">
        <div>
          <nav className="nav tabs">
            {[...TABS, "diff", "test"].map((t) => (
              <a key={t} className={`nav-i${tab === t ? " on" : ""}`} href="#" onClick={(e) => { e.preventDefault(); setTab(t as Tab); }}>
                {t === "json" ? "raw JSON" : t === "test" ? "test" : t}
                {errorsFor(t === "lattice" ? "classify" : t === "questions" ? "judge" : t === "json" || t === "test" || t === "diff" ? "\u0000" : t).length > 0 && <span className="err"> ●</span>}
              </a>
            ))}
          </nav>
          <fieldset disabled={!editable} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
            {tab === "sheets" && <SheetsTab body={body} update={update} />}
            {tab === "clauses" && <ClausesTab body={body} update={update} />}
            {tab === "pairing" && <PairingTab body={body} update={update} />}
            {tab === "lattice" && <LatticeTab body={body} update={update} />}
            {tab === "questions" && <QuestionsTab body={body} update={update} />}
            {tab === "lenses" && <LensesTab body={body} update={update} />}
            {tab === "detection" && <DetectionTab body={body} update={update} />}
            {tab === "json" && <JsonTab body={body} onApply={(b) => { setBody(b); setDirty(true); }} />}
          </fieldset>
          {tab === "diff" && <DiffTab versionId={v.id} />}
          {tab === "test" && <TestTab versionId={v.id} dirty={dirty} />}
        </div>
        <aside>
          <ScopePanel r={r} onSaved={refresh} />
          <div className="panel">
            <h3>Versions</h3>
            <table className="plain"><tbody>
              {versions.map((x) => (
                <tr key={x.id} className="link" onClick={() => nav(`/admin/rulesets/${id}/versions/${x.id}`)}>
                  <td><b>v{x.version}</b>{x.id === v.id && " ◂"}</td><td><Status value={x.status} /></td>
                  <td className="sub">{when(x.published_at ?? x.created_at)}</td>
                  <td>{x.status === "published" && x.id !== versions.find((y) => y.status === "published")?.id &&
                    <button className="btn sm" onClick={(e) => { e.stopPropagation(); archive.mutate(x.id, { onSuccess: refresh }); }}>archive</button>}</td>
                </tr>
              ))}
            </tbody></table>
            {v.notes && <p className="sub" style={{ marginBottom: 0 }}>{v.notes}</p>}
          </div>
        </aside>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */

type TabProps = { body: Body; update: (fn: (b: Body) => void) => void };

function Section({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return <div className="panel"><h3>{title}</h3>{hint && <p className="sub" style={{ marginTop: -4 }}>{hint}</p>}{children}</div>;
}

function Num({ value, onChange, width = 70, step }: { value: number; onChange: (n: number) => void; width?: number; step?: number }) {
  return <input type="number" value={value} step={step} style={{ width }} onChange={(e) => onChange(Number(e.target.value))} />;
}

function move<T>(list: T[], from: number, to: number): T[] {
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function SheetsTab({ body, update }: TabProps) {
  const reg = body.sheets;
  return (
    <>
      <Section title="Worksheets" hint="Each worksheet the guide contains, and the shape that parses it. Column and row numbers count from 1, as in the spreadsheet. A name can be a regular expression when the exact title varies.">
        <div style={{ overflowX: "auto" }}>
          <table className="plain">
            <thead><tr><th>Name</th><th>Match</th><th>Kind</th><th>Layout</th><th></th></tr></thead>
            <tbody>{reg.sheets.map((s: any, i: number) => (
              <tr key={i}>
                <td><input type="text" value={s.name} onChange={(e) => update((b) => { b.sheets.sheets[i].name = e.target.value; })} /></td>
                <td><select value={s.match} onChange={(e) => update((b) => { b.sheets.sheets[i].match = e.target.value; })}>
                  <option value="exact">exact</option><option value="regex">regex</option></select></td>
                <td><select value={s.kind} onChange={(e) => update((b) => { b.sheets.sheets[i] = { name: s.name, match: s.match, kind: e.target.value }; })}>
                  {Object.keys(SHEET_KINDS).map((k) => <option key={k} value={k}>{human(k)}</option>)}</select></td>
                <td>
                  <div className="mini-grid">
                    {SHEET_KINDS[s.kind].map(([k, label]) => (
                      <label className="mini" key={k}><em>{label}</em>
                        <Num value={s[k] ?? 1} width={52} onChange={(n) => update((b) => { b.sheets.sheets[i][k] = n; })} /></label>
                    ))}
                    {s.kind === "stacked_matrix" && <div className="field" style={{ minWidth: 260 }}><label>sub-table header keywords</label>
                      <input type="text" value={(s.header_keywords ?? []).join(", ")} onChange={(e) => update((b) => {
                        b.sheets.sheets[i].header_keywords = e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean); })} /></div>}
                    {s.kind === "grid" && <div className="field"><label>header label</label>
                      <input type="text" value={s.header_label ?? "model"} style={{ width: 100 }} onChange={(e) => update((b) => { b.sheets.sheets[i].header_label = e.target.value; })} /></div>}
                  </div>
                </td>
                <td><button className="btn sm danger" onClick={() => update((b) => { b.sheets.sheets.splice(i, 1); })}>×</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <button className="btn" style={{ marginTop: 10 }} onClick={() => update((b) => { b.sheets.sheets.push({ name: "New sheet", match: "exact", kind: "feature_matrix" }); })}>Add worksheet</button>
      </Section>
      <Section title="Unknown worksheets and the dictionary">
        <div className="form-row">
          <div className="field"><label>A worksheet no rule names</label>
            <select value={reg.unknown_sheet} onChange={(e) => update((b) => { b.sheets.unknown_sheet = e.target.value; })}>
              <option value="fatal">stops the comparison (safest)</option><option value="warn">is skipped with a warning</option><option value="skip">is skipped silently</option>
            </select></div>
          <div className="field"><label>Code dictionary sheet</label>
            <input type="text" value={reg.dictionary_sheet ?? ""} onChange={(e) => update((b) => { b.sheets.dictionary_sheet = e.target.value || null; })} placeholder="none" /></div>
        </div>
        <p className="sub" style={{ margin: "8px 0 0" }}>The dictionary sheet settles whether an unpaired code was retired, introduced, or moved between sheets. It is never itself diffed.</p>
      </Section>
    </>
  );
}

function PatternList({ list, relations, onChange }: { list: any[]; relations: string[]; onChange: (l: any[]) => void }) {
  const [drag, setDrag] = useState<number | null>(null);
  return (
    <>
      {list.map((p, i) => (
        <div key={i} className={`pattern-row${drag === i ? " dragging" : ""}`} draggable
          onDragStart={() => setDrag(i)} onDragEnd={() => setDrag(null)}
          onDragOver={(e) => { e.preventDefault(); if (drag !== null && drag !== i) { onChange(move(list, drag, i)); setDrag(i); } }}>
          <span className="drag" title="drag to reorder; the first match wins">⋮⋮</span>
          <input type="text" list="relations" value={p.relation} onChange={(e) => onChange(list.map((x, j) => (j === i ? { ...x, relation: e.target.value } : x)))} />
          <input type="text" value={p.pattern} title={p.note || undefined} onChange={(e) => onChange(list.map((x, j) => (j === i ? { ...x, pattern: e.target.value } : x)))} />
          <button className="btn sm danger" onClick={() => onChange(list.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <datalist id="relations">{relations.map((r) => <option key={r} value={r} />)}</datalist>
      <button className="btn sm" onClick={() => onChange([...list, { relation: "", pattern: "", note: "" }])}>Add pattern</button>
    </>
  );
}

function ClausesTab({ body, update }: TabProps) {
  const c = body.clauses;
  const relations = useMemo(() => [...new Set([...c.anchored, ...c.fallback].map((p: any) => p.relation))].filter(Boolean).sort() as string[], [c]);
  const toggle = (key: "ordering_relations" | "prose_relations", rel: string) => update((b) => {
    const set = new Set<string>(b.clauses[key]);
    if (set.has(rel)) set.delete(rel); else set.add(rel);
    b.clauses[key] = [...set].sort();
  });
  return (
    <>
      <Section title="Stage 1 — anchored patterns" hint="Matched against the start of each lower-cased footnote clause. First match wins, so put specific phrases above general ones.">
        <PatternList list={c.anchored} relations={relations} onChange={(l) => update((b) => { b.clauses.anchored = l; })} />
      </Section>
      <Section title="Stage 2 — fallback patterns" hint="Searched anywhere in the clause when no anchored pattern matched. A clause neither stage matches is 'unstructured'.">
        <PatternList list={c.fallback} relations={relations} onChange={(l) => update((b) => { b.clauses.fallback = l; })} />
      </Section>
      <Section title="What each relation means" hint="Ordering relations restrict or permit an order and feed the ordering lens; a reworded one is sent to Jev. Prose relations can never change what is orderable.">
        <table className="plain">
          <thead><tr><th>Relation</th><th>Ordering</th><th>Prose</th></tr></thead>
          <tbody>{relations.map((rel) => (
            <tr key={rel}><td className="mono">{rel}</td>
              <td><input type="checkbox" checked={c.ordering_relations.includes(rel)} onChange={() => toggle("ordering_relations", rel)} /></td>
              <td><input type="checkbox" checked={c.prose_relations.includes(rel)} onChange={() => toggle("prose_relations", rel)} /></td></tr>
          ))}</tbody>
        </table>
      </Section>
      <Section title="Codes and clause boundaries">
        <div className="form-grid">
          <div className="field"><label>RPO code (group 1 is the code)</label>
            <input type="text" className="mono" value={c.code_regex} onChange={(e) => update((b) => { b.clauses.code_regex = e.target.value; })} /></div>
          <div className="field"><label>Clause boundary</label>
            <input type="text" className="mono" value={c.clause_split_regex} onChange={(e) => update((b) => { b.clauses.clause_split_regex = e.target.value; })} /></div>
        </div>
      </Section>
      <ClauseProbe clauses={c} />
    </>
  );
}

function ClauseProbe({ clauses }: { clauses: any }) {
  const [text, setText] = useState("Requires (L84) 5.3L EcoTec3 V8 engine. Not available with (SPZ) Black wheel locks, LPO.");
  const [out, setOut] = useState<any[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      api.post<any[]>("/rules/clause-probe", { text, clauses }).then((r) => { setOut(r); setErr(null); }).catch(setErr);
    }, 300);
    return () => clearTimeout(t);
  }, [text, clauses]);
  return (
    <Section title="Try it" hint="Paste footnote text; it is classified with the rules above exactly as the parser will, including unsaved edits.">
      <textarea rows={3} style={{ width: "100%" }} value={text} onChange={(e) => setText(e.target.value)} />
      <ErrorNote error={err} />
      {out && <table className="plain probe-out" style={{ marginTop: 8 }}>
        <thead><tr><th>Clause</th><th>Relation</th><th>Codes</th></tr></thead>
        <tbody>{out.map((r, i) => (
          <tr key={i}><td>{r.clause}</td>
            <td className={r.relation === "unstructured" ? "err mono" : "mono"}>{r.relation}{r.ordering && <> <span className="chip accent">ordering</span></>}{r.prose && <> <span className="chip">prose</span></>}</td>
            <td className="mono">{r.codes.join(", ")}</td></tr>
        ))}</tbody>
      </table>}
    </Section>
  );
}

function PairingTab({ body, update }: TabProps) {
  const tiers = body.pairing.tiers;
  return (
    <Section title="Pairing tiers" hint="Rows are paired across years by these keys, in order, each tier taking only what the earlier ones left. The tier number is reported on every pair, so keep it stable once published.">
      <table className="plain">
        <thead><tr><th>Order</th><th>Tier</th><th>Key</th><th>Reason shown to users</th><th>Unique only</th><th>On</th></tr></thead>
        <tbody>{tiers.map((t: any, i: number) => (
          <tr key={i}>
            <td style={{ whiteSpace: "nowrap" }}>
              <button className="btn sm ghost" disabled={i === 0} onClick={() => update((b) => { b.pairing.tiers = move(b.pairing.tiers, i, i - 1); })}>↑</button>
              <button className="btn sm ghost" disabled={i === tiers.length - 1} onClick={() => update((b) => { b.pairing.tiers = move(b.pairing.tiers, i, i + 1); })}>↓</button>
            </td>
            <td><Num value={t.id} width={54} onChange={(n) => update((b) => { b.pairing.tiers[i].id = n; })} /></td>
            <td><select value={t.key} onChange={(e) => update((b) => { b.pairing.tiers[i].key = e.target.value; })}>
              {PAIR_KEYS.map((k) => <option key={k} value={k}>{human(k)}</option>)}</select></td>
            <td><input type="text" value={t.reason} style={{ width: "100%" }} onChange={(e) => update((b) => { b.pairing.tiers[i].reason = e.target.value; })} /></td>
            <td><input type="checkbox" checked={t.unique_only} onChange={(e) => update((b) => { b.pairing.tiers[i].unique_only = e.target.checked; })} /></td>
            <td><input type="checkbox" checked={t.enabled} onChange={(e) => update((b) => { b.pairing.tiers[i].enabled = e.target.checked; })} /></td>
          </tr>
        ))}</tbody>
      </table>
    </Section>
  );
}

function LatticeTab({ body, update }: TabProps) {
  const cls = body.classify;
  return (
    <>
      <Section title="Availability classes" hint="Each cell token belongs to one class. A change of class is an upgrade, decontent, package shift, addition or removal; a change of token within a class is a fulfilment shift.">
        <table className="plain"><tbody>{TOKENS.map((t) => (
          <tr key={t}><td className="mono" style={{ width: 90 }}>{t === "" ? "(blank)" : t}</td>
            <td><select value={cls.token_classes[t]} onChange={(e) => update((b) => { b.classify.token_classes[t] = e.target.value; })}>
              {CLASSES.map((c) => <option key={c}>{c}</option>)}</select></td></tr>
        ))}</tbody></table>
      </Section>
      <Section title="Reworded conditions" hint="Two clauses with the same relation and codes count as a rewrite, not an addition plus a removal, when their wording is at least this similar (0–100).">
        <Num value={cls.rewrite_similarity} step={1} onChange={(n) => update((b) => { b.classify.rewrite_similarity = n; })} />
      </Section>
    </>
  );
}

function QuestionsTab({ body, update }: TabProps) {
  const j = body.judge;
  const [qid, setQid] = useState("alignment");
  const [ctx, setCtx] = useState({ old_year: "2026", new_year: "2027", oem: "GM" });
  const [preview, setPreview] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const last = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const t = setTimeout(() => {
      api.post("/rules/render-questions", { judge: j, ...ctx }).then((r) => { setPreview(r); setErr(null); }).catch(setErr);
    }, 300);
    return () => clearTimeout(t);
  }, [j, ctx]);
  const q = j.questions[qid];
  const insert = (ph: string) => {
    const el = last.current;
    if (!el) return;
    const { selectionStart: s, selectionEnd: e, value } = el;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(el, value.slice(0, s) + ph + value.slice(e));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
  };
  const rendered = preview?.questions?.[qid];

  return (
    <>
      <Section title="Questions put to Jev" hint="Wording is part of every judgment's cache key: changing a question re-asks exactly that question on the next run, and nothing else.">
        <div className="form-row" style={{ marginBottom: 12 }}>
          <select value={qid} onChange={(e) => setQid(e.target.value)}>
            {Object.keys(j.questions).map((k) => <option key={k} value={k}>{human(k)} ({j.questions[k].type})</option>)}
          </select>
          <span className="sub">{QUESTION_HELP[qid]}</span>
        </div>
        <div className="form-row" style={{ gap: 4, marginBottom: 10 }}>
          <span className="lbl" style={{ marginRight: 6 }}>insert</span>
          {PLACEHOLDERS.map((p) => <button key={p} className="btn sm placeholder" type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => insert(p)}>{p}</button>)}
        </div>
        <div className="field"><label>Instructions</label>
          <textarea rows={4} value={q.instructions} onFocus={(e) => (last.current = e.target)}
            onChange={(e) => update((b) => { b.judge.questions[qid].instructions = e.target.value; })} /></div>
        {Array.isArray(q.criteria) && q.criteria.map((c: string, i: number) => (
          <div className="field" key={i} style={{ marginTop: 10 }}><label>Level {i} {i === 0 ? "(lowest)" : i === q.criteria.length - 1 ? "(highest)" : ""}</label>
            <textarea rows={3} value={c} onFocus={(e) => (last.current = e.target)}
              onChange={(e) => update((b) => { b.judge.questions[qid].criteria[i] = e.target.value; })} /></div>
        ))}
        {q.criteria && !Array.isArray(q.criteria) && Object.entries(q.criteria).map(([k, c]) => (
          <div className="field" key={k} style={{ marginTop: 10 }}><label>Option “{k}”</label>
            <textarea rows={2} value={c as string} onFocus={(e) => (last.current = e.target)}
              onChange={(e) => update((b) => { b.judge.questions[qid].criteria[k] = e.target.value; })} /></div>
        ))}
      </Section>
      <Section title="Preview" hint="Rendered for a comparison you choose, exactly as it will be sent.">
        <div className="form-row" style={{ marginBottom: 10 }}>
          <div className="field"><label>old year</label><input type="text" value={ctx.old_year} style={{ width: 80 }} onChange={(e) => setCtx({ ...ctx, old_year: e.target.value })} /></div>
          <div className="field"><label>new year</label><input type="text" value={ctx.new_year} style={{ width: 80 }} onChange={(e) => setCtx({ ...ctx, new_year: e.target.value })} /></div>
          <div className="field"><label>OEM</label><input type="text" value={ctx.oem} style={{ width: 90 }} onChange={(e) => setCtx({ ...ctx, oem: e.target.value })} /></div>
        </div>
        <ErrorNote error={err} />
        {rendered && <div className="rendered">{rendered.instructions}
          {Array.isArray(rendered.criteria) && rendered.criteria.map((c: string, i: number) => `\n\n${i}. ${c}`).join("")}
          {rendered.criteria && !Array.isArray(rendered.criteria) && Object.entries(rendered.criteria).map(([k, c]) => `\n\n${k}: ${c}`).join("")}
        </div>}
        {preview && <p className="sub" style={{ marginBottom: 0 }}>State fields: <span className="mono">{Object.values(preview.state_keys).join(", ")}</span></p>}
      </Section>
      <Section title="Routing and thresholds">
        <div className="form-grid">
          <div className="field"><label>alignment outcomes (low→high)</label>
            <input type="text" value={j.alignment_outcomes.join(", ")} onChange={(e) => update((b) => { b.judge.alignment_outcomes = e.target.value.split(",").map((x: string) => x.trim()); })} /></div>
          <div className="field"><label>constraint outcomes (low→high)</label>
            <input type="text" value={j.constraint_outcomes.join(", ")} onChange={(e) => update((b) => { b.judge.constraint_outcomes = e.target.value.split(",").map((x: string) => x.trim()); })} /></div>
          <div className="field"><label>pairing confidence floor</label><Num value={j.confidence_floor} step={0.05} onChange={(n) => update((b) => { b.judge.confidence_floor = n; })} /></div>
          <div className="field"><label>candidate similarity floor</label><Num value={j.similarity_floor} step={1} onChange={(n) => update((b) => { b.judge.similarity_floor = n; })} /></div>
          {Object.entries(j.state_keys).map(([k, val]) => (
            <div className="field" key={k}><label>state key: {human(k)}</label>
              <input type="text" className="mono" value={val as string} onChange={(e) => update((b) => { b.judge.state_keys[k] = e.target.value; })} /></div>
          ))}
        </div>
      </Section>
      <Section title="Token legend sent with each change">
        <table className="plain"><tbody>{Object.entries(j.token_legend).map(([t, m]) => (
          <tr key={t}><td className="mono" style={{ width: 70 }}>{t}</td>
            <td><input type="text" style={{ width: "100%" }} value={m as string} onChange={(e) => update((b) => { b.judge.token_legend[t] = e.target.value; })} /></td></tr>
        ))}</tbody></table>
      </Section>
    </>
  );
}

function LensesTab({ body, update }: TabProps) {
  const lenses = body.lenses as Record<string, any>;
  const [name, setName] = useState("");
  const rename = (from: string, to: string) => update((b) => {
    b.lenses = Object.fromEntries(Object.entries(b.lenses).map(([k, v]) => [k === from ? to : k, v]));
  });
  const makeDefault = (n: string) => update((b) => {
    b.lenses = { [n]: b.lenses[n], ...Object.fromEntries(Object.entries(b.lenses).filter(([k]) => k !== n)) };
  });
  return (
    <>
      {Object.entries(lenses).map(([n, l], i) => (
        <Section key={n} title={`${human(n)}${i === 0 ? " (default)" : ""}`}>
          <div className="form-grid">
            <div className="field"><label>name</label><input type="text" defaultValue={n} onBlur={(e) => e.target.value && e.target.value !== n && rename(n, e.target.value)} /></div>
            {["order_risk", "content_churn", "description_kind"].filter((q) => q in l.weights || q !== "description_kind").map((q) => (
              <div className="field" key={q}><label>weight: {human(q)}</label>
                <Num value={l.weights[q] ?? 0} step={0.05} onChange={(x) => update((b) => { b.lenses[n].weights[q] = x; })} /></div>
            ))}
            <div className="field"><label>material ≥</label><Num value={l.bands.material} step={0.05} onChange={(x) => update((b) => { b.lenses[n].bands.material = x; })} /></div>
            <div className="field"><label>review ≥</label><Num value={l.bands.review} step={0.05} onChange={(x) => update((b) => { b.lenses[n].bands.review = x; })} /></div>
            <div className="field"><label>min confidence</label><Num value={l.confidence_floor} step={0.05} onChange={(x) => update((b) => { b.lenses[n].confidence_floor = x; })} /></div>
          </div>
          <div className="actions" style={{ marginTop: 10 }}>
            {i > 0 && <button className="btn sm" onClick={() => makeDefault(n)}>Make default</button>}
            {Object.keys(lenses).length > 1 && <button className="btn sm danger" onClick={() => update((b) => { delete b.lenses[n]; })}>Remove</button>}
          </div>
        </Section>
      ))}
      <div className="form-row">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="new lens name" />
        <button className="btn" disabled={!name || name in lenses} onClick={() => { update((b) => { b.lenses[name] = { weights: { order_risk: 1, content_churn: 0 }, bands: { material: 1.5, review: 0.5 }, confidence_floor: 0.75 }; }); setName(""); }}>Add lens</button>
      </div>
      <p className="sub">Lenses are arithmetic over stored judgments. Changing them never re-asks Jev, and viewers can still tune them per comparison.</p>
    </>
  );
}

function DetectionTab({ body, update }: TabProps) {
  const d = body.detection;
  return (
    <Section title="Recognising an uploaded guide" hint="Filename patterns may name groups year, brand and model. Fingerprint sheets are worksheet names whose presence marks this OEM's format.">
      <div className="field"><label>Filename patterns (one per line)</label>
        <textarea rows={3} value={d.filename_patterns.join("\n")} onChange={(e) => update((b) => { b.detection.filename_patterns = e.target.value.split("\n").filter(Boolean); })} /></div>
      <div className="field" style={{ marginTop: 10 }}><label>Fingerprint sheets (one per line)</label>
        <textarea rows={6} value={d.fingerprint_sheets.join("\n")} onChange={(e) => update((b) => { b.detection.fingerprint_sheets = e.target.value.split("\n").map((x: string) => x.trim()).filter(Boolean); })} /></div>
    </Section>
  );
}

function JsonTab({ body, onApply }: { body: Body; onApply: (b: Body) => void }) {
  const [text, setText] = useState(JSON.stringify(body, null, 2));
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setText(JSON.stringify(body, null, 2)), [body]);
  return (
    <Section title="Raw JSON" hint="The whole version as stored. Apply, then save; the server validates it with the pipeline's own model.">
      <textarea rows={30} style={{ width: "100%" }} value={text} onChange={(e) => setText(e.target.value)} />
      {err && <div className="err">{err}</div>}
      <button className="btn" style={{ marginTop: 8 }} onClick={() => {
        try { onApply(JSON.parse(text)); setErr(null); } catch (e) { setErr((e as Error).message); }
      }}>Apply</button>
    </Section>
  );
}

function DiffTab({ versionId }: { versionId: number }) {
  const diff = useApi<{ against: RulesetVersion | null; changes: { path: string[]; old: unknown; new: unknown }[] }>(
    ["diff", versionId], `/ruleset-versions/${versionId}/diff`);
  if (!diff.data) return <Loading />;
  if (!diff.data.against) return <div className="panel sub">Nothing published to compare against.</div>;
  const show = (x: unknown) => (x === undefined ? "∅" : typeof x === "string" ? x : JSON.stringify(x));
  return (
    <Section title={`Against published v${diff.data.against.version}`} hint="Saved changes only.">
      {diff.data.changes.length === 0 ? <p className="sub">No differences.</p> : diff.data.changes.map((c, i) => (
        <div className="diffline" key={i}><b>{c.path.join(".")}</b><div className="old">− {show(c.old)}</div><div className="new">+ {show(c.new)}</div></div>
      ))}
    </Section>
  );
}

function TestTab({ versionId, dirty }: { versionId: number; dirty: boolean }) {
  const docs = useApi<Doc[]>(["documents", "confirmed"], "/documents?status=confirmed");
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const [doc, setDoc] = useState("");
  const [pair, setPair] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const job = useJob(jobId);
  const start = useAction(() => api.post<Job>(`/ruleset-versions/${versionId}/test`, { document_id: Number(doc), pair_document_id: pair ? Number(pair) : null }));
  const res = job.data?.status === "succeeded" ? job.data.result : null;
  void catalog;
  const label = (d: Doc) => `${d.model_year ? `${d.model_year.year} ${d.model_year.brand} ${d.model_year.model} · ` : ""}${d.filename}`;
  const rels = res ? [...new Set([...Object.keys(res.relations), ...Object.keys(res.baseline_relations ?? {})])].sort() : [];
  return (
    <>
      <Section title="Test against a document" hint="Parses a stored document with this version — and, given a second, compares the two — then shows what moved against the published version. Nothing is saved.">
        {dirty && <div className="banner">Save the draft first; the test uses the saved version.</div>}
        <div className="form-row">
          <div className="field"><label>Document</label>
            <select value={doc} onChange={(e) => setDoc(e.target.value)}><option value="">choose…</option>
              {docs.data?.map((d) => <option key={d.id} value={d.id}>{label(d)}</option>)}</select></div>
          <div className="field"><label>Compare with (optional)</label>
            <select value={pair} onChange={(e) => setPair(e.target.value)}><option value="">—</option>
              {docs.data?.filter((d) => String(d.id) !== doc).map((d) => <option key={d.id} value={d.id}>{label(d)}</option>)}</select></div>
          <button className="btn primary" disabled={!doc || dirty || start.isPending || isActive(job.data?.status)}
            onClick={() => start.mutate(undefined, { onSuccess: (j) => setJobId(j.id) })}>Run test</button>
        </div>
        <ErrorNote error={start.error} />
        {job.data && <div style={{ marginTop: 12 }}><JobLine job={job.data} /></div>}
      </Section>
      {res && (
        <>
          <Section title="Footnote clauses by relation">
            <table className="plain">
              <thead><tr><th>Relation</th><th className="num">This version</th><th className="num">Published</th><th className="num">Δ</th></tr></thead>
              <tbody>{rels.map((r) => {
                const a = res.relations[r] ?? 0, b = res.baseline_relations?.[r];
                return <tr key={r}><td className={r === "unstructured" ? "err" : ""}>{human(r)}</td><td className="num">{a}</td>
                  <td className="num">{b ?? "—"}</td><td className="num">{b === undefined ? "" : a - b > 0 ? `+${a - b}` : a - b || ""}</td></tr>;
              })}</tbody>
            </table>
          </Section>
          {res.comparison && (
            <Section title={`Comparison ${res.comparison.labels[0]} → ${res.comparison.labels[1]}`}>
              <table className="plain">
                <thead><tr><th></th><th className="num">This version</th><th className="num">Published</th></tr></thead>
                <tbody>
                  {(["events", "residue", "constraint_judgments"] as const).map((k) => (
                    <tr key={k}><td>{human(k)}</td><td className="num">{res.comparison.draft[k]}</td><td className="num">{res.comparison.baseline?.[k] ?? "—"}</td></tr>
                  ))}
                  {[...new Set([...Object.keys(res.comparison.draft.kinds), ...Object.keys(res.comparison.baseline?.kinds ?? {})])].sort().map((k) => (
                    <tr key={k}><td>· {human(k)}</td><td className="num">{res.comparison.draft.kinds[k] ?? 0}</td><td className="num">{res.comparison.baseline?.kinds?.[k] ?? "—"}</td></tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}
          {res.unstructured.length > 0 && (
            <Section title={`Unstructured clauses · ${res.unstructured.length}${res.unstructured.length >= 40 ? "+" : ""}`} hint="No pattern matched these. Add one on the clauses tab, or accept them as unclassified prose.">
              <table className="plain"><tbody>{res.unstructured.map((u: any, i: number) => (
                <tr key={i}><td className="mono">{u.code ?? ""}</td><td>{u.text}</td><td className="sub">{u.sheet}</td></tr>
              ))}</tbody></table>
            </Section>
          )}
          {res.warnings.length > 0 && (
            <Section title={`Warnings · ${res.warnings.length}`}>
              <table className="plain"><tbody>{res.warnings.map((w: any, i: number) => (
                <tr key={i}><td><Status value={w.severity === "fatal" ? "fatal" : "warnings"} /></td><td className="mono">{w.kind}</td><td>{w.message}</td></tr>
              ))}</tbody></table>
            </Section>
          )}
        </>
      )}
    </>
  );
}

function ScopePanel({ r, onSaved }: { r: Ruleset; onSaved: () => void }) {
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const [f, setF] = useState({
    name: r.name, brand_id: r.brand_id ? String(r.brand_id) : "", model_id: r.model_id ? String(r.model_id) : "",
    year_from: r.year_from ? String(r.year_from) : "", year_to: r.year_to ? String(r.year_to) : "",
    format: r.format, priority: String(r.priority), description: r.description ?? "",
  });
  const save = useAction(() => api.patch(`/rulesets/${r.id}`, {
    name: f.name, brand_id: f.brand_id ? Number(f.brand_id) : null, model_id: f.model_id ? Number(f.model_id) : null,
    year_from: f.year_from ? Number(f.year_from) : null, year_to: f.year_to ? Number(f.year_to) : null,
    format: f.format, priority: Number(f.priority || 0), description: f.description || null,
  }));
  const oem = catalog.data?.find((o) => o.id === r.oem_id);
  const brand = oem?.brands.find((b) => String(b.id) === f.brand_id);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="panel">
      <h3>Scope</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="field"><label>Name</label><input type="text" value={f.name} onChange={set("name")} /></div>
        <div className="field"><label>Brand</label><select value={f.brand_id} onChange={(e) => setF({ ...f, brand_id: e.target.value, model_id: "" })}>
          <option value="">all {r.oem} brands</option>{oem?.brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></div>
        <div className="field"><label>Model</label><select value={f.model_id} onChange={set("model_id")} disabled={!brand}>
          <option value="">all models</option>{brand?.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></div>
        <div className="form-row">
          <div className="field"><label>From</label><input type="number" value={f.year_from} onChange={set("year_from")} style={{ width: 90 }} placeholder="any" /></div>
          <div className="field"><label>To</label><input type="number" value={f.year_to} onChange={set("year_to")} style={{ width: 90 }} placeholder="any" /></div>
        </div>
        <div className="form-row">
          <div className="field"><label>Format</label><select value={f.format} onChange={set("format")}><option value="xlsx">Excel</option><option value="pdf">PDF</option><option value="any">any</option></select></div>
          <div className="field"><label>Priority</label><input type="number" value={f.priority} onChange={set("priority")} style={{ width: 70 }} /></div>
        </div>
        <div className="field"><label>Description</label><input type="text" value={f.description} onChange={set("description")} /></div>
        <button className="btn" disabled={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onSaved })}>Save scope</button>
        <ErrorNote error={save.error} />
        <p className="sub" style={{ margin: 0 }}>Scope applies to every version and takes effect immediately. Narrower scopes win; ties go to the higher priority.</p>
      </div>
    </div>
  );
}
