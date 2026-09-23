import { PageHead } from "../../components/ui";
import { human, when } from "../../lib/format";
import { useApi } from "../../lib/hooks";

export default function Audit() {
  const rows = useApi<{ id: number; at: string; user: string | null; action: string; entity_type: string; entity_id: string | null; detail: any }[]>(["audit"], "/audit");
  return (
    <div className="page">
      <PageHead title="Audit log" sub="Who changed what: uploads, confirmations, ruleset edits and publications, settings and users." />
      <div className="card ev" style={{ padding: 0, overflowX: "auto" }}>
        <table className="plain">
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>What</th><th>Detail</th></tr></thead>
          <tbody>{rows.data?.map((r) => (
            <tr key={r.id}><td>{when(r.at)}</td><td className="mono">{r.user ?? "—"}</td><td>{human(r.action)}</td>
              <td>{human(r.entity_type)} {r.entity_id}</td><td className="mono" style={{ fontSize: 11.5 }}>{r.detail ? JSON.stringify(r.detail) : ""}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}
