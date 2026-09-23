import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { JobLine, Loading, PageHead, Status } from "../components/ui";
import { human } from "../lib/format";
import { isActive, useApi } from "../lib/hooks";
import type { Timeline } from "../lib/types";

export default function TimelinePage() {
  const { id } = useParams();
  const t = useApi<Timeline>(["timeline", id], `/timelines/${id}`, {
    refetchInterval: (d) => (d?.steps.some((s) => isActive(s.status)) ? 1500 : false),
  });
  const [code, setCode] = useState<string | null>(null);
  const history = useApi<{ code: string; steps: any[] }>(["timeline-code", id, code], code ? `/timelines/${id}/codes/${encodeURIComponent(code)}` : null);
  if (!t.data) return <div className="page"><Loading /></div>;
  const d = t.data;
  const kinds = [...new Set(d.steps.flatMap((s) => Object.keys(s.summary?.kinds ?? {})))].sort();

  return (
    <div className="page wide">
      <PageHead title={d.name} crumbs={[[`${d.model.brand} ${d.model.name}`, `/catalog/models/${d.model.id}`], ["timeline", null]]}
        sub="Each step compares adjacent model years. Codes that changed in more than one step are listed below." />
      <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
        <table className="plain">
          <thead><tr><th>Step</th><th>Status</th><th className="num">Changes</th>{kinds.map((k) => <th key={k} className="num">{human(k)}</th>)}<th className="num">Material</th></tr></thead>
          <tbody>{d.steps.map((s) => (
            <tr key={s.id}>
              <td><Link to={`/comparisons/${s.id}`}><b>{s.old.year} → {s.new.year}</b></Link></td>
              <td>{isActive(s.status) ? <JobLine job={s.job} /> : <Status value={s.status} />}</td>
              <td className="num">{s.summary?.events ?? "—"}</td>
              {kinds.map((k) => <td key={k} className="num">{s.summary?.kinds?.[k] ?? 0}</td>)}
              <td className="num">{s.summary?.bands?.ordering?.material ?? "—"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>

      <h2>Recurring codes · {d.recurring_codes.length}</h2>
      {d.recurring_codes.length === 0 ? <p className="sub">No option code changed in more than one step{d.steps.length < 2 ? " (a timeline of one step has none by definition)" : ""}.</p> : (
        <div className="bands">{d.recurring_codes.map((r) => (
          <button key={r.code} className={`pill${code === r.code ? " on" : ""}`} onClick={() => setCode(r.code)}>{r.code} · {r.steps.length}</button>
        ))}</div>
      )}
      <div className="card bar"><input className="q-in" type="search" placeholder="look up any RPO code across the timeline"
        onKeyDown={(e) => { if (e.key === "Enter") setCode((e.target as HTMLInputElement).value.trim().toUpperCase() || null); }} /></div>
      {history.data && (
        <div className="panel">
          <h3>{history.data.code}</h3>
          <table className="plain"><tbody>{history.data.steps.map((s) => (
            <tr key={s.comparison_id}>
              <td className="mono" style={{ width: 110 }}>{s.old}→{s.new}</td>
              <td>{s.events.length === 0 ? <span className="sub">no change</span> : s.events.map((e: any) => (
                <div key={e.eid}><Link to={`/comparisons/${s.comparison_id}/changes/${e.eid}`}>{human(e.kind)}</Link> · {e.sheet} · <span className="sub">{e.detail}</span></div>
              ))}</td>
            </tr>
          ))}</tbody></table>
        </div>
      )}
    </div>
  );
}
