import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorNote, Loading, PageHead, Status } from "../../components/ui";
import { api } from "../../lib/api";
import { when } from "../../lib/format";
import { useAction, useApi } from "../../lib/hooks";
import type { CatalogOem, Ruleset } from "../../lib/types";

export function scopeText(r: Ruleset) {
  const who = r.model ? `${r.brand ?? ""} ${r.model}`.trim() : r.brand ?? `all ${r.oem}`;
  const years = r.year_from || r.year_to ? `${r.year_from ?? "…"}–${r.year_to ?? "…"}` : "all years";
  return `${who} · ${years} · ${r.format}`;
}

export default function Rulesets() {
  const list = useApi<Ruleset[]>(["rulesets"], "/rulesets");
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", oem_id: "", brand_id: "", model_id: "", year_from: "", year_to: "", format: "xlsx", priority: "0", clone_from_version_id: "" });
  const create = useAction(() => api.post<Ruleset>("/rulesets", {
    name: form.name, oem_id: Number(form.oem_id),
    brand_id: form.brand_id ? Number(form.brand_id) : null, model_id: form.model_id ? Number(form.model_id) : null,
    year_from: form.year_from ? Number(form.year_from) : null, year_to: form.year_to ? Number(form.year_to) : null,
    format: form.format, priority: Number(form.priority || 0),
    clone_from_version_id: form.clone_from_version_id ? Number(form.clone_from_version_id) : null,
  }), [["rulesets"]]);

  const oem = catalog.data?.find((o) => String(o.id) === form.oem_id);
  const brand = oem?.brands.find((b) => String(b.id) === form.brand_id);
  const byOem = new Map<string, Ruleset[]>();
  list.data?.forEach((r) => byOem.set(r.oem, [...(byOem.get(r.oem) ?? []), r]));
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="page">
      <PageHead title="Rulesets" sub="Everything OEM- and year-specific: which worksheets exist and how they are shaped, the footnote clause vocabulary, pairing tiers, the availability lattice, the questions put to Jev, and the lenses. Comparisons use the narrowest published ruleset whose scope covers both years." />
      {list.isLoading && <Loading />}
      {[...byOem.entries()].map(([name, rows]) => (
        <div key={name}>
          <h2>{name}</h2>
          <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
            <table className="plain">
              <thead><tr><th>Ruleset</th><th>Scope</th><th className="num">Priority</th><th>Published</th><th>Draft</th><th>Created</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className="link" onClick={() => nav(`/admin/rulesets/${r.id}`)}>
                  <td><Link to={`/admin/rulesets/${r.id}`} onClick={(e) => e.stopPropagation()}><b>{r.name}</b></Link>
                    {r.description && <div className="sub" style={{ margin: 0 }}>{r.description}</div>}</td>
                  <td>{scopeText(r)}</td>
                  <td className="num">{r.priority}</td>
                  <td>{r.published_version ? <Status value="published" /> : <span className="sub">none</span>} {r.published_version && `v${r.published_version}`}</td>
                  <td>{r.has_draft && <Status value="draft" />}</td>
                  <td>{when(r.created_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      ))}

      <h2>New ruleset</h2>
      <div className="panel">
        <p className="sub" style={{ marginTop: 0 }}>Start from an existing version — typically the OEM's current rules — and narrow the scope to the model or year range that needs different handling. It opens as a draft.</p>
        <div className="form-grid">
          <div className="field"><label>Name</label><input type="text" value={form.name} onChange={set("name")} placeholder="GM 2028+ (new Wheels layout)" /></div>
          <div className="field"><label>Start from</label>
            <select value={form.clone_from_version_id} onChange={set("clone_from_version_id")}>
              <option value="">built-in GM rules</option>
              {list.data?.flatMap((r) => (r.versions ?? []).map((v) => (
                <option key={v.id} value={v.id}>{r.name} v{v.version} ({v.status})</option>)))}
            </select></div>
          <div className="field"><label>OEM</label>
            <select value={form.oem_id} onChange={(e) => setForm({ ...form, oem_id: e.target.value, brand_id: "", model_id: "" })}>
              <option value="">choose…</option>{catalog.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select></div>
          <div className="field"><label>Brand (optional)</label>
            <select value={form.brand_id} onChange={(e) => setForm({ ...form, brand_id: e.target.value, model_id: "" })} disabled={!oem}>
              <option value="">all brands</option>{oem?.brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select></div>
          <div className="field"><label>Model (optional)</label>
            <select value={form.model_id} onChange={set("model_id")} disabled={!brand}>
              <option value="">all models</option>{brand?.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select></div>
          <div className="field"><label>From year</label><input type="number" value={form.year_from} onChange={set("year_from")} placeholder="any" /></div>
          <div className="field"><label>To year</label><input type="number" value={form.year_to} onChange={set("year_to")} placeholder="any" /></div>
          <div className="field"><label>Format</label>
            <select value={form.format} onChange={set("format")}><option value="xlsx">Excel</option><option value="pdf">PDF</option><option value="any">any</option></select></div>
          <div className="field"><label>Priority</label><input type="number" value={form.priority} onChange={set("priority")} /></div>
        </div>
        <div className="actions" style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={!form.name || !form.oem_id || create.isPending}
            onClick={() => create.mutate(undefined, { onSuccess: (r) => nav(`/admin/rulesets/${r.id}`) })}>Create draft</button>
          <span className="sub">or import a ruleset JSON exported from another installation:</span>
          <ImportButton oemId={form.oem_id} />
        </div>
        <ErrorNote error={create.error} />
      </div>
    </div>
  );
}

function ImportButton({ oemId }: { oemId: string }) {
  const nav = useNavigate();
  const [err, setErr] = useState<unknown>(null);
  return (
    <>
      <label className={`btn${oemId ? "" : " disabled"}`} style={{ opacity: oemId ? 1 : 0.5 }}>
        Import JSON…
        <input type="file" accept=".json" hidden disabled={!oemId} onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          const form = new FormData();
          form.append("file", f);
          try {
            const r = await api.upload<Ruleset>(`/rulesets/import?oem_id=${oemId}`, form);
            nav(`/admin/rulesets/${r.id}`);
          } catch (x) { setErr(x); }
        }} />
      </label>
      <ErrorNote error={err} />
    </>
  );
}
