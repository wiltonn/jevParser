import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Empty, ErrorNote, Loading, PageHead, Status } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import type { ModelOverview } from "../lib/types";
import { ComparisonTable } from "./ComparisonList";

export default function ModelPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const m = useApi<ModelOverview>(["model", id], `/models/${id}`);
  const [year, setYear] = useState("");
  const addYear = useAction(() => api.post(`/models/${id}/years`, { year: Number(year) }), [["model", id], ["catalog"]]);
  const [code, setCode] = useState("");
  const history = useApi<any[]>(["code-history", id, code], code.length >= 3 ? `/models/${id}/codes/${encodeURIComponent(code.toUpperCase())}` : null);

  if (!m.data) return <div className="page"><Loading /></div>;
  const d = m.data;
  const withDocs = d.years.filter((y) => y.documents.some((x) => x.detection_status === "confirmed"));

  return (
    <div className="page">
      <PageHead title={`${d.brand.name} ${d.name}`} crumbs={[["Catalog", "/catalog"], [d.oem.name, null]]}
        actions={<>
          <button className="btn" disabled={withDocs.length < 2}
            onClick={() => nav(`/compare?model=${d.id}&years=${withDocs.slice(-2).map((y) => y.id).join(",")}`)}>Compare latest two years</button>
          <button className="btn primary" disabled={withDocs.length < 2}
            onClick={() => nav(`/compare?model=${d.id}&years=${withDocs.map((y) => y.id).join(",")}`)}>Compare…</button>
        </>} />

      <h2>Model years</h2>
      <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
        <table className="plain">
          <thead><tr><th>Year</th><th>Documents</th><th>Parse</th></tr></thead>
          <tbody>
            {d.years.map((y) => (
              <tr key={y.id}>
                <td><b>{y.year}</b>{y.variant_label && <div className="sub" style={{ margin: 0 }}>{y.variant_label}</div>}</td>
                <td>{y.documents.length === 0 ? <span className="sub">none — <Link to="/library">upload</Link></span> :
                  y.documents.map((doc) => (
                    <div key={doc.id}><Link to={`/library/${doc.id}`}>{doc.filename}</Link>{doc.is_primary && <> <span className="chip accent">primary</span></>}</div>
                  ))}</td>
                <td>{y.documents.map((doc) => <div key={doc.id}><Status value={doc.parse?.status ?? doc.detection_status} /></div>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form-row" style={{ marginTop: 10 }}>
        <input type="number" value={year} onChange={(e) => setYear(e.target.value)} placeholder="2028" style={{ width: 110 }} />
        <button className="btn sm" disabled={!year} onClick={() => addYear.mutate(undefined, { onSuccess: () => setYear("") })}>Add model year</button>
        <ErrorNote error={addYear.error} />
      </div>

      <h2>Comparisons</h2>
      {d.comparisons.length ? <ComparisonTable rows={d.comparisons} /> : <Empty title="No comparisons yet">Pick two or more years with documents and compare them.</Empty>}

      {d.timelines.length > 0 && <>
        <h2>Timelines</h2>
        <div className="card ev" style={{ padding: 0 }}><table className="plain"><tbody>
          {d.timelines.map((t) => <tr key={t.id}><td><Link to={`/timelines/${t.id}`}>{t.name}</Link></td><td>{t.steps} steps</td><td>{when(t.created_at)}</td></tr>)}
        </tbody></table></div>
      </>}

      <h2>Option code history</h2>
      <div className="card bar"><input className="q-in" type="search" placeholder="RPO code, e.g. RSL" value={code} onChange={(e) => setCode(e.target.value)} /></div>
      {history.data && (history.data.length ? (
        <div className="card ev" style={{ padding: 0 }}><table className="plain">
          <thead><tr><th>Years</th><th>Kind</th><th>Sheet</th><th>Detail</th></tr></thead>
          <tbody>{history.data.map((h, i) => (
            <tr key={i} className="link" onClick={() => nav(`/comparisons/${h.comparison_id}/changes/${h.eid}`)}>
              <td className="mono">{h.old}→{h.new}</td><td>{h.kind.replace(/_/g, " ")}</td><td>{h.sheet}</td><td>{h.detail}</td></tr>
          ))}</tbody></table></div>
      ) : <p className="sub">No recorded change to {code.toUpperCase()} in this model's comparisons.</p>)}
    </div>
  );
}
