import { useState } from "react";
import { ErrorNote, PageHead } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useMe } from "../../lib/hooks";
import type { User } from "../../lib/types";

export default function Users() {
  const users = useApi<User[]>(["users"], "/users");
  const me = useMe();
  const [f, setF] = useState({ email: "", display_name: "", role: "analyst" });
  const add = useAction(() => api.post("/users", f), [["users"]]);
  const patch = useAction(({ id, ...body }: { id: number; role?: string; is_active?: boolean }) => api.patch(`/users/${id}`, body), [["users"]]);
  return (
    <div className="page">
      <PageHead title="Users" sub="Analysts upload documents and run comparisons; administrators also manage rulesets, users and settings. Sign-in is local for now — every request runs as the first administrator unless the X-Jev-User header names another user." />
      <div className="card ev" style={{ padding: 0 }}>
        <table className="plain">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Active</th></tr></thead>
          <tbody>{users.data?.map((u) => (
            <tr key={u.id}>
              <td>{u.display_name}{u.id === me.data?.id && " (you)"}</td><td className="mono">{u.email}</td>
              <td><select value={u.role} disabled={u.id === me.data?.id} onChange={(e) => patch.mutate({ id: u.id, role: e.target.value })}>
                <option value="analyst">analyst</option><option value="admin">admin</option></select></td>
              <td><input type="checkbox" checked={u.is_active} disabled={u.id === me.data?.id} onChange={(e) => patch.mutate({ id: u.id, is_active: e.target.checked })} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <ErrorNote error={patch.error} />
      <h2>Add a user</h2>
      <div className="panel">
        <div className="form-row">
          <div className="field"><label>Name</label><input type="text" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></div>
          <div className="field"><label>Email</label><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></div>
          <div className="field"><label>Role</label><select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            <option value="analyst">analyst</option><option value="admin">admin</option></select></div>
          <button className="btn primary" disabled={!f.email || !f.display_name}
            onClick={() => add.mutate(undefined, { onSuccess: () => setF({ email: "", display_name: "", role: "analyst" }) })}>Add</button>
        </div>
        <ErrorNote error={add.error} />
      </div>
    </div>
  );
}
