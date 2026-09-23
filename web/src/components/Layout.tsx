import { useEffect, useRef } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../lib/api";
import { useApi, useMe } from "../lib/hooks";
import type { Doc, Job } from "../lib/types";

function Item({ to, label, count, hot, end }: { to: string; label: string; count?: number; hot?: boolean; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `nl${isActive ? " active" : ""}`}>
      <span>{label}</span>
      {!!count && <span className={`count${hot ? " hot" : ""}`}>{count}</span>}
    </NavLink>
  );
}

export default function Layout() {
  const me = useMe();
  const pending = useApi<Doc[]>(["documents", "pending"], "/documents?status=pending", { refetchInterval: 15000 });
  const active = useApi<Job[]>(["jobs", "active"], "/jobs?status=queued,running", { refetchInterval: 3000 });
  const admin = me.data?.role === "admin";

  // On a serverless host jobs run inside a drain request rather than in worker
  // threads.  Keep one drain in flight while anything is queued; with worker
  // threads the server answers "threads" and this stops asking.
  const draining = useRef(false);
  const threaded = useRef(false);
  const queued = active.data?.some((j) => j.status === "queued");
  useEffect(() => {
    if (!queued || draining.current || threaded.current) return;
    draining.current = true;
    api.post<{ mode: string }>("/jobs/drain")
      .then((r) => { threaded.current = r.mode === "threads"; })
      .catch(() => { /* retried on the next poll */ })
      .finally(() => { draining.current = false; active.refetch(); });
  }, [queued, active.dataUpdatedAt]);

  const toggleTheme = () => {
    const root = document.documentElement;
    const dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    root.setAttribute("data-theme", dark ? "light" : "dark");
    try { localStorage.setItem("theme", dark ? "light" : "dark"); } catch { /* private mode */ }
  };

  return (
    <div className="shell">
      <aside className="side">
        <NavLink to="/" className="brand">jev-diff<small>order-guide changes</small></NavLink>
        <Item to="/" label="Overview" end />
        <Item to="/compare" label="New comparison" />
        <Item to="/comparisons" label="Comparisons" />
        <div className="side-h">Data</div>
        <Item to="/library" label="Document library" count={pending.data?.length} hot />
        <Item to="/catalog" label="Catalog" />
        <Item to="/jobs" label="Jobs" count={active.data?.length} />
        {admin && (
          <>
            <div className="side-h">Admin</div>
            <Item to="/admin/rulesets" label="Rulesets" />
            <Item to="/admin/judgments" label="Judgment cache" />
            <Item to="/admin/users" label="Users" />
            <Item to="/admin/settings" label="Settings" />
            <Item to="/admin/audit" label="Audit log" />
          </>
        )}
        <div className="side-foot">
          <span>{me.data?.display_name} · {me.data?.role}</span>
          <button className="btn sm" onClick={toggleTheme} style={{ alignSelf: "flex-start" }}>theme</button>
        </div>
      </aside>
      <main className="main"><Outlet /></main>
    </div>
  );
}
