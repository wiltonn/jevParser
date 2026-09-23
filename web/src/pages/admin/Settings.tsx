import { useEffect, useState } from "react";
import { ErrorNote, Loading, PageHead } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi } from "../../lib/hooks";

type View = Record<string, any>;

export default function SettingsPage() {
  const s = useApi<View>(["settings"], "/settings");
  const [key, setKey] = useState("");
  const [f, setF] = useState<View>({});
  useEffect(() => { if (s.data) setF({ ...s.data }); }, [s.data]);
  const save = useAction((body: View) => api.put<View>("/settings", body), [["settings"]]);
  const test = useAction(() => api.post<{ ok: boolean; error: string | null }>("/settings/typesafe/test"));
  if (!s.data) return <div className="page"><Loading /></div>;
  const secret = s.data["typesafe.api_key"];
  return (
    <div className="page">
      <PageHead title="Settings" />
      <div className="panel">
        <h3>TypeSafe Jev</h3>
        <p className="sub" style={{ marginTop: -4 }}>The key is encrypted in the database with a key file kept beside it, not in it. With no key here, the server falls back to the <span className="mono">TYPESAFE_API_KEY</span> environment variable.</p>
        <div className="form-grid">
          <div className="field"><label>API key</label>
            <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={secret.set ? `stored ${secret.hint ?? ""}` : "not set"} />
            <div className="actions">
              <button className="btn sm" disabled={!key} onClick={() => save.mutate({ "typesafe.api_key": key }, { onSuccess: () => setKey("") })}>Store key</button>
              {secret.set && <button className="btn sm danger" onClick={() => save.mutate({ "typesafe.api_key": null })}>Remove</button>}
            </div>
          </div>
          <div className="field"><label>Endpoint</label><input type="text" value={f["typesafe.endpoint"] ?? ""} onChange={(e) => setF({ ...f, "typesafe.endpoint": e.target.value })} /></div>
          <div className="field"><label>Model</label><input type="text" value={f["typesafe.model"] ?? ""} onChange={(e) => setF({ ...f, "typesafe.model": e.target.value })} /></div>
          <div className="field"><label>Concurrent requests</label><input type="number" value={f["judge.workers"] ?? 8} onChange={(e) => setF({ ...f, "judge.workers": Number(e.target.value) })} /></div>
          <div className="field"><label>USD per million input tokens</label><input type="number" step="0.001" value={f["judge.cost_per_mtok"] ?? 0} onChange={(e) => setF({ ...f, "judge.cost_per_mtok": Number(e.target.value) })} /></div>
        </div>
        <div className="actions" style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate({
            "typesafe.endpoint": f["typesafe.endpoint"], "typesafe.model": f["typesafe.model"],
            "judge.workers": f["judge.workers"], "judge.cost_per_mtok": f["judge.cost_per_mtok"],
          })}>Save</button>
          <button className="btn" disabled={test.isPending} onClick={() => test.mutate(undefined)}>{test.isPending ? "Testing…" : "Test connection"}</button>
          {test.data && (test.data.ok ? <span className="ok">Connected.</span> : <span className="err">{test.data.error}</span>)}
        </div>
        <ErrorNote error={save.error || test.error} />
      </div>
    </div>
  );
}
