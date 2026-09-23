import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ErrorNote, PageHead, Status } from "../components/ui";
import { api, qs } from "../lib/api";
import { useApi } from "../lib/hooks";
import type { CatalogOem, Comparison, ModelOverview, ResolveCandidate } from "../lib/types";

export default function CompareWizard() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const catalog = useApi<CatalogOem[]>(["catalog"], "/catalog");
  const [modelId, setModelId] = useState<number | null>(params.get("model") ? Number(params.get("model")) : null);
  const [yearIds, setYearIds] = useState<number[]>(
    (params.get("years") ?? "").split(",").filter(Boolean).map(Number));
  const [docs, setDocs] = useState<Record<number, number>>({});
  const [versionId, setVersionId] = useState<number | null>(null);
  const [judge, setJudge] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const models = useMemo(() => (catalog.data ?? []).flatMap((o) =>
    o.brands.flatMap((b) => b.models.map((m) => ({ ...m, brand: b.name, oem: o.name })))), [catalog.data]);
  const overview = useApi<ModelOverview>(["model", String(modelId)], modelId ? `/models/${modelId}` : null);
  const years = overview.data?.years ?? [];
  const chosen = years.filter((y) => yearIds.includes(y.id)).sort((a, b) => a.year - b.year);
  const resolve = useApi<ResolveCandidate[]>(["resolve", modelId, chosen.map((y) => y.id).join(",")],
    modelId && chosen.length >= 2 ? `/rulesets/resolve${qs({ model_id: modelId, year_ids: chosen.map((y) => y.id).join(",") })}` : null);

  useEffect(() => { setVersionId(resolve.data?.[0]?.version_id ?? null); }, [resolve.data]);
  useEffect(() => {
    // Default each chosen year to its primary document.
    setDocs((cur) => {
      const next = { ...cur };
      for (const y of chosen) {
        const confirmed = y.documents.filter((d) => d.detection_status === "confirmed");
        if (!next[y.id] || !confirmed.some((d) => d.id === next[y.id])) {
          const pick = confirmed.find((d) => d.is_primary) ?? confirmed[0];
          if (pick) next[y.id] = pick.id;
        }
      }
      return next;
    });
  }, [overview.data, yearIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (id: number) => setYearIds((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  const isTimeline = chosen.length > 2;
  const blocked = chosen.find((y) => {
    const doc = y.documents.find((d) => d.id === docs[y.id]);
    return !doc || doc.parse?.status === "fatal" || doc.parse?.status === "failed";
  });
  const ready = modelId && chosen.length >= 2 && !blocked && (isTimeline || versionId) && !busy;

  async function launch() {
    setBusy(true); setError(null);
    try {
      if (isTimeline) {
        const t = await api.post<{ id: number }>("/timelines", { model_id: modelId, year_ids: chosen.map((y) => y.id), judge });
        nav(`/timelines/${t.id}`);
      } else {
        const [a, b] = chosen;
        const c = await api.post<Comparison>("/comparisons", {
          model_id: modelId, old_year_id: a.id, new_year_id: b.id,
          old_document_id: docs[a.id], new_document_id: docs[b.id],
          ruleset_version_id: versionId, judge,
        });
        nav(`/comparisons/${c.id}`);
      }
    } catch (e) { setError(e); setBusy(false); }
  }

  return (
    <div className="page">
      <PageHead title="New comparison" sub="Two years give one comparison. Three or more give a timeline: each adjacent pair is compared, and changes are traced across the chain." />

      <div className="panel">
        <h3><span className="step-n">1</span>Model</h3>
        <select value={modelId ?? ""} onChange={(e) => { setModelId(e.target.value ? Number(e.target.value) : null); setYearIds([]); }} style={{ minWidth: 320 }}>
          <option value="">Choose a model…</option>
          {models.map((m) => <option key={m.id} value={m.id}>{m.oem} · {m.brand} {m.name}</option>)}
        </select>
      </div>

      {modelId && (
        <div className="panel">
          <h3><span className="step-n">2</span>Model years</h3>
          {years.length === 0 && <p className="sub">This model has no years yet. <Link to="/library">Upload documents</Link>.</p>}
          <div className="years">
            {years.map((y) => {
              const n = y.documents.filter((d) => d.detection_status === "confirmed").length;
              return (
                <button key={y.id} className={`year${yearIds.includes(y.id) ? " on" : ""}${n ? "" : " none"}`}
                  disabled={!n} onClick={() => toggle(y.id)}>
                  <b>{y.year}</b><span>{n ? `${n} document${n > 1 ? "s" : ""}` : "no document"}</span>
                </button>
              );
            })}
          </div>
          <p className="sub" style={{ margin: "10px 0 0" }}>
            {chosen.length < 2 ? "Select at least two." :
              isTimeline ? `Timeline: ${chosen.slice(1).map((y, i) => `${chosen[i].year}→${y.year}`).join(", ")}` :
              `${chosen[0].year} → ${chosen[1].year}`}
          </p>
        </div>
      )}

      {chosen.length >= 2 && (
        <div className="panel">
          <h3><span className="step-n">3</span>Documents</h3>
          <table className="plain"><tbody>
            {chosen.map((y) => {
              const confirmed = y.documents.filter((d) => d.detection_status === "confirmed");
              const doc = confirmed.find((d) => d.id === docs[y.id]);
              return (
                <tr key={y.id}>
                  <td style={{ width: 70 }}><b>{y.year}</b></td>
                  <td>
                    <select value={docs[y.id] ?? ""} onChange={(e) => setDocs({ ...docs, [y.id]: Number(e.target.value) })} disabled={isTimeline}>
                      {confirmed.map((d) => <option key={d.id} value={d.id}>{d.filename}{d.is_primary ? " (primary)" : ""}</option>)}
                    </select>
                  </td>
                  <td><Status value={doc?.parse?.status ?? null} />{doc?.parse?.status === "fatal" && <span className="err"> — fix parse problems first</span>}</td>
                </tr>
              );
            })}
          </tbody></table>
          {isTimeline && <p className="sub" style={{ margin: "8px 0 0" }}>Timelines use each year's primary document.</p>}
        </div>
      )}

      {chosen.length >= 2 && (
        <div className="panel">
          <h3><span className="step-n">4</span>Ruleset</h3>
          {resolve.data && resolve.data.length === 0 && <div className="banner error">No published ruleset covers these years. An administrator can add one under Rulesets.</div>}
          {isTimeline ? <p className="sub" style={{ margin: 0 }}>Each step uses the most specific ruleset that covers its two years.</p> : (
            <select value={versionId ?? ""} onChange={(e) => setVersionId(Number(e.target.value))} style={{ minWidth: 320 }}>
              {resolve.data?.map((c, i) => (
                <option key={c.version_id} value={c.version_id}>{c.ruleset} v{c.version} · {c.specificity}-wide{i === 0 ? " (best match)" : ""}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {chosen.length >= 2 && (
        <div className="panel">
          <h3><span className="step-n">5</span>Judgments</h3>
          <label className="chk" style={{ fontSize: 13.5 }}>
            <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />
            Ask Jev to rank changes by materiality, pair leftover rows, and judge reworded conditions
          </label>
          <p className="sub" style={{ margin: "6px 0 0" }}>Answers are cached by question and state, so re-running a comparison, or a timeline that shares a step, costs nothing for what was already asked. Without judgments every change is listed but unranked.</p>
        </div>
      )}

      <ErrorNote error={error} />
      <div className="actions" style={{ justifyContent: "flex-end" }}>
        <button className="btn primary" disabled={!ready} onClick={launch}>
          {busy ? "Starting…" : isTimeline ? `Run timeline (${chosen.length - 1} comparisons)` : "Run comparison"}
        </button>
      </div>
    </div>
  );
}
