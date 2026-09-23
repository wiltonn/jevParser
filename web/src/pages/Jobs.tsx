import { Link } from "react-router-dom";
import { JobLine, Loading, PageHead } from "../components/ui";
import { api } from "../lib/api";
import { human, when } from "../lib/format";
import { isActive, useAction, useApi } from "../lib/hooks";
import type { Job } from "../lib/types";

export default function Jobs() {
  const jobs = useApi<Job[]>(["jobs"], "/jobs?limit=200", {
    refetchInterval: (d) => (d?.some((j) => isActive(j.status)) ? 1000 : 5000),
  });
  const cancel = useAction((id: number) => api.post(`/jobs/${id}/cancel`), [["jobs"]]);
  return (
    <div className="page">
      <PageHead title="Jobs" sub="Parsing, comparisons, ruleset tests and exports run in the background; this is their queue and history." />
      {jobs.isLoading ? <Loading /> : (
        <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
          <table className="plain">
            <thead><tr><th>#</th><th>Type</th><th style={{ width: "40%" }}>Progress</th><th>For</th><th>Created</th><th></th></tr></thead>
            <tbody>{jobs.data?.map((j) => (
              <tr key={j.id}>
                <td className="mono">{j.id}</td>
                <td>{human(j.type)}</td>
                <td><JobLine job={j} /></td>
                <td>{j.comparison_id ? <Link to={`/comparisons/${j.comparison_id}`}>comparison {j.comparison_id}</Link>
                  : j.document_id ? <Link to={`/library/${j.document_id}`}>document {j.document_id}</Link> : "—"}</td>
                <td>{when(j.created_at)}</td>
                <td>{isActive(j.status) && <button className="btn sm danger" onClick={() => cancel.mutate(j.id)}>cancel</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
