import { useParams } from "react-router-dom";
import { ErrorNote, Loading, PageHead, Stats, Status } from "../components/ui";
import { api } from "../lib/api";
import { bytes, human, when } from "../lib/format";
import { useAction, useApi, useJob } from "../lib/hooks";
import type { Doc, Job } from "../lib/types";
import { useState } from "react";

export default function DocumentPage() {
  const { id } = useParams();
  const doc = useApi<Doc>(["document", id], `/documents/${id}`, {
    refetchInterval: (d) => (d?.parse?.status === "queued" || d?.parse?.status === "running" ? 1000 : false),
  });
  const [jobId, setJobId] = useState<number | null>(null);
  useJob(jobId, [["document", id]]);
  const reparse = useAction(() => api.post<Job>(`/documents/${id}/reparse`, {}), []);
  const primary = useAction(() => api.post(`/documents/${id}/primary`), [["document", id]]);
  const run = doc.data?.parse;
  const warnings = useApi<{ kind: string; severity: string; message: string; where: string }[]>(
    ["warnings", run?.id], run ? `/parse-runs/${run.id}/warnings` : null);

  if (doc.isLoading || !doc.data) return <div className="page"><Loading /></div>;
  const d = doc.data;
  const my = d.model_year;
  const relations = Object.entries(run?.relations ?? {});
  const totalClauses = relations.reduce((a, [, n]) => a + n, 0);

  return (
    <div className="page">
      <PageHead title={d.filename}
        crumbs={[["Library", "/library"], [my ? `${my.year} ${my.brand} ${my.model}` : "unconfirmed", my ? `/catalog/models/${my.model_id}` : null]]}
        sub={<>{d.format} · {bytes(d.size)} · uploaded {when(d.uploaded_at)} · <span className="hash">{d.sha256.slice(0, 16)}</span></>}
        actions={<>
          <a className="btn" href={`/api/documents/${d.id}/download`}>Download</a>
          {my && !d.is_primary && <button className="btn" onClick={() => primary.mutate(undefined)}>Make primary for {my.year}</button>}
          {my && <button className="btn" disabled={reparse.isPending}
            onClick={() => reparse.mutate(undefined, { onSuccess: (j) => setJobId(j.id) })}>Re-parse</button>}
        </>} />
      <ErrorNote error={reparse.error || primary.error} />
      {run ? (
        <>
          <Stats items={[
            [<Status value={run.status} />, "parse"],
            [run.sheets?.length ?? 0, "sheets"],
            [run.sheets?.reduce((a, s) => a + s.rows, 0) ?? 0, "rows"],
            [totalClauses, "footnote clauses"],
            [run.fatal, "fatal"],
            [run.warnings, "warnings"],
          ]} />
          <p className="sub">Parsed with <b>{run.ruleset} v{run.ruleset_version}</b>{run.finished_at && <> · {when(run.finished_at)}</>}.
            {run.fatal > 0 && " Fatal problems stop any comparison that uses this document, so nothing is silently under-reported."}</p>
          {run.error && <div className="banner error">{run.error}</div>}
          <div className="cols-2">
            <div className="panel">
              <h3>Worksheets</h3>
              <table className="plain">
                <thead><tr><th>Sheet</th><th>Kind</th><th className="num">Rows</th><th className="num">Trims</th><th className="num">Notes</th></tr></thead>
                <tbody>{run.sheets?.map((s) => (
                  <tr key={s.name}><td>{s.name}</td><td className="mono">{human(s.kind)}</td>
                    <td className="num">{s.rows}</td><td className="num">{s.columns}</td><td className="num">{s.footnotes}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <div className="panel">
              <h3>Footnote clauses by relation</h3>
              <table className="plain">
                <tbody>{relations.map(([rel, n]) => (
                  <tr key={rel}><td className={rel === "unstructured" ? "err" : ""}>{human(rel)}</td>
                    <td className="num">{n}</td><td className="num sub">{Math.round((n / totalClauses) * 100)}%</td></tr>
                ))}</tbody>
              </table>
              <p className="sub" style={{ marginTop: 10 }}>"Unstructured" clauses matched no pattern in the ruleset; an administrator can add one.</p>
            </div>
          </div>
          {!!warnings.data?.length && (
            <div className="panel">
              <h3>Warnings · {warnings.data.length}</h3>
              <table className="plain"><tbody>{warnings.data.map((w, i) => (
                <tr key={i}><td><Status value={w.severity === "fatal" ? "fatal" : "warnings"} /></td>
                  <td className="mono">{w.kind}</td><td className="mono">{w.where}</td><td>{w.message}</td></tr>
              ))}</tbody></table>
            </div>
          )}
        </>
      ) : <div className="banner">Confirm this document's model year in the library to parse it.</div>}
      {d.detection && d.detection_status === "pending" && (
        <div className="panel"><h3>Sheets found</h3><p className="sub">{d.detection.sheet_names.join(" · ")}</p></div>
      )}
    </div>
  );
}
