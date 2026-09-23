import { Link } from "react-router-dom";
import { Empty, JobLine, PageHead, Stats } from "../components/ui";
import { human } from "../lib/format";
import { isActive, useApi } from "../lib/hooks";
import type { CatalogOem, Comparison, Doc, Job } from "../lib/types";
import { ComparisonTable } from "./ComparisonList";

export default function Dashboard() {
  const comparisons = useApi<Comparison[]>(["comparisons"], "/comparisons?limit=8", {
    refetchInterval: (d) => (d?.some((c) => isActive(c.status)) ? 1500 : false),
  });
  const pending = useApi<Doc[]>(["documents", "pending"], "/documents?status=pending");
  const docs = useApi<Doc[]>(["documents"], "/documents");
  const jobs = useApi<Job[]>(["jobs", "active"], "/jobs?status=queued,running", { refetchInterval: 2000 });
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const models = (catalog.data ?? []).flatMap((o) => o.brands.flatMap((b) => b.models.map((m) => ({ ...m, brand: b.name }))));
  const ready = models.filter((m) => m.years.filter((y) => y.documents > 0).length >= 2);

  return (
    <div className="page">
      <PageHead title="Overview" sub="What changed between model years of an order guide, ranked by what it means for ordering and for published content."
        actions={<Link className="btn primary" to="/compare">New comparison</Link>} />
      <Stats items={[
        [docs.data?.filter((d) => d.detection_status === "confirmed").length ?? "—", "documents"],
        [models.length, "models"],
        [comparisons.data?.length ?? "—", "recent comparisons"],
        [jobs.data?.length ?? 0, "running jobs"],
      ]} />

      {!!pending.data?.length && (
        <div className="banner">{pending.data.length} uploaded document{pending.data.length > 1 ? "s" : ""} waiting for you to confirm the model year. <Link to="/library">Review</Link></div>
      )}

      {!!jobs.data?.length && <>
        <h2>Running</h2>
        {jobs.data.map((j) => (
          <div className="panel" key={j.id}>
            <div className="form-row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
              <b>{human(j.type)}</b>
              {j.comparison_id && <Link to={`/comparisons/${j.comparison_id}`}>open</Link>}
            </div>
            <JobLine job={j} />
          </div>
        ))}
      </>}

      <h2>Ready to compare</h2>
      {ready.length ? (
        <div className="cols">{ready.map((m) => (
          <div className="panel" key={m.id}>
            <h3><Link to={`/catalog/models/${m.id}`}>{m.brand} {m.name}</Link></h3>
            <p className="sub" style={{ margin: "0 0 10px" }}>{m.years.filter((y) => y.documents).map((y) => y.year).join(" · ")}</p>
            <Link className="btn" to={`/compare?model=${m.id}&years=${m.years.filter((y) => y.documents).slice(-2).map((y) => y.id).join(",")}`}>
              Compare latest two</Link>
          </div>
        ))}</div>
      ) : <Empty title="Nothing to compare yet"><Link to="/library">Upload</Link> order guides for two years of a model.</Empty>}

      <h2>Recent comparisons</h2>
      {comparisons.data?.length ? <ComparisonTable rows={comparisons.data} /> : <p className="sub">None yet.</p>}
    </div>
  );
}
