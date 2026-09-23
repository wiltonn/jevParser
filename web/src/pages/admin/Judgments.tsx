import { useRef } from "react";
import { ErrorNote, PageHead, Stats } from "../../components/ui";
import { api } from "../../lib/api";
import { human } from "../../lib/format";
import { useAction, useApi } from "../../lib/hooks";

interface StatsView { total: number; by_source: Record<string, number>; by_question: { question_id: string | null; count: number; hits: number }[] }

export default function JudgmentsAdmin() {
  const stats = useApi<StatsView>(["judgment-stats"], "/judgments/stats");
  const file = useRef<HTMLInputElement>(null);
  const imp = useAction((form: FormData) => api.upload<{ added: number; already_present: number }>("/judgments/import", form), [["judgment-stats"]]);
  const purge = useAction((q: string) => api.del<{ deleted: number }>(`/judgments?question_id=${encodeURIComponent(q)}`), [["judgment-stats"]]);
  const d = stats.data;
  return (
    <div className="page">
      <PageHead title="Judgment cache" sub="Every answer Jev has given, keyed by the exact question wording and the exact state it was asked about. A comparison re-asks only what is not here, so reruns and overlapping timelines are free."
        actions={<>
          <a className="btn" href="/api/judgments/export">Export JSON</a>
          <button className="btn" onClick={() => file.current?.click()}>Import CLI cache…</button>
          <input ref={file} type="file" accept=".json" hidden onChange={(e) => {
            const f = e.target.files?.[0]; if (!f) return;
            const form = new FormData(); form.append("file", f); imp.mutate(form); e.target.value = "";
          }} />
        </>} />
      {imp.data && <div className="banner">Imported {imp.data.added} judgments ({imp.data.already_present} were already here).</div>}
      <ErrorNote error={imp.error || purge.error} />
      {d && <Stats items={[[d.total, "answers"], ...Object.entries(d.by_source).map(([k, v]) => [v, human(k)] as [number, string])]} />}
      <div className="card ev" style={{ padding: 0 }}>
        <table className="plain">
          <thead><tr><th>Question</th><th className="num">Answers</th><th className="num">Reuses</th><th></th></tr></thead>
          <tbody>{d?.by_question.map((q) => (
            <tr key={q.question_id ?? "?"}>
              <td>{q.question_id ? human(q.question_id) : <span className="sub">imported (question not recorded)</span>}</td>
              <td className="num">{q.count}</td><td className="num">{q.hits}</td>
              <td>{q.question_id && <button className="btn sm danger" onClick={() => {
                if (confirm(`Forget all ${q.count} answers to ${q.question_id}? The next runs will re-ask (and pay for) them.`)) purge.mutate(q.question_id!);
              }}>forget</button>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
