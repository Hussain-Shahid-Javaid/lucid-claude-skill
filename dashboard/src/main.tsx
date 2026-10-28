import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { initToken, setToken, useTokenValue } from "./api";
import { useApi } from "./ui";
import { Activity, Connectors, Conversations, Memory, Overview, Questions, Representation, Sharing, Sync, Verification, Work } from "./views";
import "./styles.css";

const TABS = {
  overview: ["Overview", Overview],
  memory: ["Memory", Memory],
  conversations: ["Conversations", Conversations],
  sync: ["Sync", Sync],
  sharing: ["Sharing", Sharing],
  work: ["Tasks & budgets", Work],
  questions: ["Questions", Questions],
  representation: ["Messaging & avatar", Representation],
  verification: ["Verification & research", Verification],
  connectors: ["Connectors & data", Connectors],
  activity: ["Activity", Activity],
} as const;
type Tab = keyof typeof TABS;

function App({ initial }: { initial: string | null }) {
  const [tok, setTok] = useState(initial);
  const [draft, setDraft] = useState("");
  useTokenValue(tok);
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return (t && t in TABS ? t : "overview") as Tab;
  });
  const [health] = useApi<{ demo: boolean; user: string }>(tok ? "/api/health" : null, [tok]);
  if (!tok) {
    return (
      <main className="login">
        <h1>Lucid</h1>
        <p>Open the dashboard with the link printed by <code>lucid serve</code>, or paste the access token.</p>
        <form onSubmit={(e) => { e.preventDefault(); setToken(draft); setTok(draft); }}>
          <input aria-label="Access token" type="password" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <button type="submit">Continue</button>
        </form>
      </main>
    );
  }
  const Comp = TABS[tab][1];
  return (
    <div className="shell">
      <nav aria-label="Sections">
        <div className="brand">Lucid</div>
        {(Object.keys(TABS) as Tab[]).map((k) => (
          <button key={k} className={k === tab ? "active" : ""} aria-current={k === tab} onClick={() => setTab(k)}>{TABS[k][0]}</button>
        ))}
        {health.state === "ok" && <div className="who">user: {health.data.user}</div>}
      </nav>
      <main>
        {health.state === "ok" && health.data.demo && (
          <div className="demo-banner" role="note">DEMO DATA — this dashboard is showing seeded example records, not your real memory.</div>
        )}
        <Comp />
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App initial={initToken()} /></StrictMode>);
