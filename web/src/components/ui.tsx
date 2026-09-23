import { Link, useNavigate } from "react-router-dom";
import type { MouseEvent, ReactNode } from "react";
import { DS } from "../design";
import { human, pct } from "../lib/format";
import type { Job } from "../lib/types";

/** Render one of the design system's HTML-string components.
 *  Clicks on in-app links inside it go through the router, not a page load. */
export function Html({ html, className, as: Tag = "div", onClick }: {
  html: string; className?: string; as?: "div" | "span"; onClick?: (e: MouseEvent) => void;
}) {
  const nav = useNavigate();
  const click = (e: MouseEvent) => {
    onClick?.(e);
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href");
    if (href && href.startsWith("/") && !e.metaKey && !e.ctrlKey && !e.defaultPrevented) {
      e.preventDefault();
      nav(href);
    }
  };
  return <Tag className={className} onClick={click} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Status({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <span className={`status s-${value}`}>{human(value)}</span>;
}

export function Stats({ items }: { items: [ReactNode, string][] }) {
  return (
    <div className="card stats">
      {items.map(([v, label]) => (
        <div className="stat" key={label}><b>{v}</b><span>{label}</span></div>
      ))}
    </div>
  );
}

export function PageHead({ title, sub, crumbs, actions }: {
  title: ReactNode; sub?: ReactNode; crumbs?: [string, string | null][]; actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        {crumbs && (
          <div className="crumbs">
            {crumbs.map(([label, to], i) => (
              <span key={i}>{i > 0 && " / "}{to ? <Link to={to}>{label}</Link> : label}</span>
            ))}
          </div>
        )}
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="card empty-state"><b>{title}</b>{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="banner error">{msg}</div>;
}

export function Loading() {
  return <div className="sub">Loading…</div>;
}

export function JobLine({ job }: { job: Job | null | undefined }) {
  if (!job) return null;
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className="jobline">
      <Status value={job.status} />
      {active && <div className="progress"><i style={{ width: pct(job.progress) }} /></div>}
      <span>{job.status === "failed" ? job.error : human(job.message ?? job.stage ?? "")}</span>
      {active && <span className="mono">{pct(job.progress)}</span>}
    </div>
  );
}

export function Tag({ band }: { band: string }) {
  return <Html as="span" html={DS().Tag(band)} />;
}
