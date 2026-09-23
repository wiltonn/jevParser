import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorNote, Loading, PageHead, Status } from "../components/ui";
import { api } from "../lib/api";
import { bytes, pct, when } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { CatalogOem, Doc, Suggestion } from "../lib/types";

export default function Library() {
  const qc = useQueryClient();
  const docs = useApi<Doc[]>(["documents"], "/documents", {
    refetchInterval: (d) => (d?.some((x) => x.parse?.status === "queued" || x.parse?.status === "running") ? 1500 : false),
  });
  const [uploading, setUploading] = useState(false);
  const [uploadErrors, setUploadErrors] = useState<{ filename: string; error: string }[]>([]);
  const [filter, setFilter] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["catalog"] });
  };

  async function upload(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    const form = new FormData();
    list.forEach((f) => form.append("files", f));
    setUploading(true);
    try {
      const res = await api.upload<{ documents: Doc[]; errors: { filename: string; error: string }[] }>("/documents", form);
      setUploadErrors(res.errors);
      refresh();
    } catch (e) {
      setUploadErrors([{ filename: list.map((f) => f.name).join(", "), error: (e as Error).message }]);
    } finally {
      setUploading(false);
    }
  }

  const pending = docs.data?.filter((d) => d.detection_status === "pending") ?? [];
  const rest = (docs.data ?? []).filter((d) => d.detection_status !== "pending")
    .filter((d) => !filter || `${d.filename} ${d.model_year?.brand ?? ""} ${d.model_year?.model ?? ""} ${d.model_year?.year ?? ""}`
      .toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="page">
      <PageHead title="Document library"
        sub="Preload order guides once. Each file is stored by content, identified, confirmed against the catalog, and parsed with the ruleset that covers it." />
      <Dropzone busy={uploading} onFiles={upload} />
      {uploadErrors.map((e, i) => <div key={i} className="banner error"><b>{e.filename}</b>: {e.error}</div>)}

      {pending.length > 0 && (
        <>
          <h2>Needs confirmation · {pending.length}</h2>
          {pending.map((d) => <ConfirmCard key={d.id} doc={d} onDone={refresh} />)}
        </>
      )}

      <h2>All documents</h2>
      <div className="card bar"><input className="q-in" type="search" placeholder="filename, brand, model or year"
        value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
      {docs.isLoading ? <Loading /> : rest.length === 0 ? (
        <Empty title="No documents yet">Drop order-guide exports above to start.</Empty>
      ) : (
        <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
          <table className="plain">
            <thead><tr><th>File</th><th>Model year</th><th>Format</th><th>Parse</th><th className="num">Size</th><th>Uploaded</th></tr></thead>
            <tbody>
              {rest.map((d) => <DocRow key={d.id} d={d} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DocRow({ d }: { d: Doc }) {
  const nav = useNavigate();
  return (
    <tr className="link" onClick={() => nav(`/library/${d.id}`)}>
      <td>
        <Link to={`/library/${d.id}`} onClick={(e) => e.stopPropagation()}>{d.filename}</Link>
        {d.is_primary && <> <span className="chip accent">primary</span></>}
      </td>
      <td>{d.model_year ? `${d.model_year.year} ${d.model_year.brand} ${d.model_year.model}` : <Status value={d.detection_status} />}</td>
      <td className="mono">{d.format}</td>
      <td>
        <Status value={d.parse?.status ?? null} />
        {d.parse && d.parse.fatal > 0 && <span className="err"> {d.parse.fatal} fatal</span>}
      </td>
      <td className="num">{bytes(d.size)}</td>
      <td>{when(d.uploaded_at)}</td>
    </tr>
  );
}

function Dropzone({ busy, onFiles }: { busy: boolean; onFiles: (f: FileList) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={`dropzone${over ? " over" : ""}`} style={{ marginBottom: 18 }}
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(e.dataTransfer.files); }}>
      <input ref={input} type="file" multiple accept=".xlsx,.xlsm,.pdf" hidden
        onChange={(e) => { if (e.target.files) onFiles(e.target.files); e.target.value = ""; }} />
      {busy ? <b>Uploading…</b> : <><b>Drop order guides here</b> or click to choose · Excel (.xlsx) or PDF · many at once</>}
    </div>
  );
}

function ConfirmCard({ doc, onDone }: { doc: Doc; onDone: () => void }) {
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const suggestions = doc.detection?.suggestions ?? [];
  const [choice, setChoice] = useState<number | "manual">(suggestions.length ? 0 : "manual");
  const first = suggestions[0];
  const [manual, setManual] = useState({
    oem: first?.oem ?? "", brand: first?.brand ?? "", model: first?.model ?? "", year: first?.year ? String(first.year) : "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const names = useMemo(() => {
    const oems = new Set<string>(), brands = new Set<string>(), models = new Set<string>();
    catalog.data?.forEach((o) => { oems.add(o.name); o.brands.forEach((b) => { brands.add(b.name); b.models.forEach((m) => models.add(m.name)); }); });
    return { oems: [...oems], brands: [...brands], models: [...models] };
  }, [catalog.data]);

  const picked: Suggestion | null = choice === "manual" ? null : suggestions[choice];

  async function confirm() {
    setBusy(true); setError(null);
    try {
      const body = picked?.model_year_id ? { model_year_id: picked.model_year_id }
        : picked ? { oem: picked.oem, brand: picked.brand, model: picked.model, year: picked.year }
        : { ...manual, year: Number(manual.year) };
      await api.post(`/documents/${doc.id}/confirm`, body);
      onDone();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function reject() {
    setBusy(true);
    try { await api.post(`/documents/${doc.id}/reject`); onDone(); } finally { setBusy(false); }
  }

  const willCreate = picked ? !picked.model_year_id : true;
  return (
    <div className="panel">
      <div className="form-row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>{doc.filename}</b> <span className="sub">· {bytes(doc.size)} · {doc.format}</span>
          {doc.detection?.duplicate_of && <div className="banner" style={{ margin: "8px 0 0" }}>
            This exact file was already confirmed as <Link to={`/library/${doc.detection.duplicate_of}`}>document {doc.detection.duplicate_of}</Link>. It is stored once either way.</div>}
        </div>
        <div className="actions">
          <button className="btn danger" disabled={busy} onClick={reject}>Not an order guide</button>
          <button className="btn primary" disabled={busy || (choice === "manual" && !(manual.oem && manual.brand && manual.model && manual.year))} onClick={confirm}>
            Confirm{willCreate ? " & add to catalog" : ""}
          </button>
        </div>
      </div>
      <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        {suggestions.map((s, i) => (
          <label key={i} className="chk" style={{ fontSize: 13.5 }}>
            <input type="radio" checked={choice === i} onChange={() => setChoice(i)} />
            <b>{s.year ?? "year ?"} {s.brand ?? "?"} {s.model ?? "?"}</b>
            <span className="sub" style={{ margin: 0 }}>{s.oem} · {pct(s.confidence)} from {s.source} · sheets {pct(s.sheet_match)}{!s.model_year_id && " · new to the catalog"}</span>
          </label>
        ))}
        <label className="chk" style={{ fontSize: 13.5 }}>
          <input type="radio" checked={choice === "manual"} onChange={() => setChoice("manual")} /> Something else…
        </label>
        {choice === "manual" && (
          <div className="form-grid" style={{ marginTop: 6 }}>
            {(["oem", "brand", "model", "year"] as const).map((k) => (
              <div className="field" key={k}>
                <label>{k === "oem" ? "OEM" : k}</label>
                <input type={k === "year" ? "number" : "text"} list={`dl-${k}`} value={manual[k]}
                  onChange={(e) => setManual({ ...manual, [k]: e.target.value })} />
              </div>
            ))}
            <datalist id="dl-oem">{names.oems.map((n) => <option key={n} value={n} />)}</datalist>
            <datalist id="dl-brand">{names.brands.map((n) => <option key={n} value={n} />)}</datalist>
            <datalist id="dl-model">{names.models.map((n) => <option key={n} value={n} />)}</datalist>
          </div>
        )}
      </div>
      <ErrorNote error={error} />
    </div>
  );
}
