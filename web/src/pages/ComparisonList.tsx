import { Link, useNavigate } from "react-router-dom";
import { Empty, Loading, PageHead, Status } from "../components/ui";
import { when } from "../lib/format";
import { isActive, useApi } from "../lib/hooks";
import type { Comparison } from "../lib/types";

export function ComparisonTable({ rows }: { rows: Comparison[] }) {
  const nav = useNavigate();
  return (
    <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
      <table className="plain">
        <thead><tr><th>Comparison</th><th>Status</th><th className="num">Changes</th><th className="num">Material</th><th>Ruleset</th><th>When</th></tr></thead>
        <tbody>{rows.map((c) => (
          <tr key={c.id} className="link" onClick={() => nav(`/comparisons/${c.id}`)}>
            <td><Link to={`/comparisons/${c.id}`} onClick={(e) => e.stopPropagation()}><b>{c.old.year} → {c.new.year}</b> {c.brand} {c.model}</Link>
              {c.timeline_id && <> <span className="chip">timeline</span></>}</td>
            <td><Status value={c.status} />{isActive(c.status) && c.job && <span className="sub"> {Math.round(c.job.progress * 100)}%</span>}</td>
            <td className="num">{c.summary?.events ?? "—"}</td>
            <td className="num">{c.summary?.bands?.ordering?.material ?? "—"}</td>
            <td>{c.ruleset.name} v{c.ruleset.version}</td>
            <td>{when(c.created_at)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export default function ComparisonList() {
  const list = useApi<Comparison[]>(["comparisons"], "/comparisons", {
    refetchInterval: (d) => (d?.some((c) => isActive(c.status)) ? 1500 : false),
  });
  return (
    <div className="page">
      <PageHead title="Comparisons" actions={<Link className="btn primary" to="/compare">New comparison</Link>} />
      {list.isLoading ? <Loading /> : list.data?.length ? <ComparisonTable rows={list.data} /> :
        <Empty title="No comparisons yet"><Link to="/compare">Start one</Link> once two years of a model have documents.</Empty>}
    </div>
  );
}
