import { ReactNode, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; error: ApiError };

export function useApi<T>(path: string | null, deps: unknown[] = []): [Loadable<T>, () => void] {
  const [v, setV] = useState<Loadable<T>>({ state: "loading" });
  const [n, setN] = useState(0);
  const reload = useCallback(() => setN((x) => x + 1), []);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setV({ state: "loading" });
    api<T>(path)
      .then((data) => live && setV({ state: "ok", data }))
      .catch((e: ApiError) => live && setV({ state: "error", error: e }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, n, ...deps]);
  return [v, reload];
}

export function View<T>({ v, empty, children }: { v: Loadable<T>; empty?: (d: T) => boolean; children: (d: T) => ReactNode }) {
  if (v.state === "loading") return <div className="state" role="status">Loading…</div>;
  if (v.state === "error") {
    const e = v.error;
    const kind = e.status === 0 ? "Disconnected" : e.status === 401 ? "Not authorised" : "Failed";
    return (
      <div className="state error" role="alert" data-testid="error-state">
        <strong>{kind}.</strong> {e.message}
        {e.status === 401 && <p>Open the dashboard with the link printed by <code>lucid serve</code>.</p>}
      </div>
    );
  }
  if (empty && empty(v.data)) return <div className="state empty">Nothing here yet.</div>;
  return <>{children(v.data)}</>;
}

export function Badge({ kind, children }: { kind?: string; children: ReactNode }) {
  return <span className={`badge ${kind ?? ""}`}>{children}</span>;
}

export const statusKind = (s: string) =>
  ({ supported: "good", verified: "good", completed: "good", done: "good", answered: "good", defaulted: "info",
     unsupported: "bad", refuted: "bad", failed: "bad", dead: "bad", blocked: "bad", conflict: "bad",
     experimental: "warn", unverified: "warn", disputed: "warn", outcome_unknown: "warn", partially_completed: "warn",
     budget_exhausted: "warn", unconfigured: "muted", "n/a": "muted", open: "info", pending: "info" } as Record<string, string>)[s] ?? "";

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head"><h2>{title}</h2>{actions}</header>
      {children}
    </section>
  );
}

export function fmt(ts?: string | null) {
  if (!ts) return "—";
  return ts.replace("T", " ").slice(0, 19);
}
