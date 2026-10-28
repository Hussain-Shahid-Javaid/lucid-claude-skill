/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { api, del, post } from "./api";
import { Badge, fmt, Section, statusKind, useApi, View } from "./ui";

type Any = Record<string, any>;

// ------------------------------------------------------------------ overview
export function Overview() {
  const [h] = useApi<Any>("/api/health");
  const [cap] = useApi<Any>("/api/capabilities");
  return (
    <>
      <Section title="Storage & health">
        <View v={h}>{(d) => (
          <dl className="kv" data-testid="health">
            <dt>Database</dt><dd><code>{d.db_path}</code></dd>
            <dt>Schema</dt><dd>v{d.schema_version} · integrity {d.integrity}</dd>
            <dt>Active memories</dt><dd>{d.memories_active}</dd>
            <dt>Conversations</dt><dd>{d.conversations}</dd>
            <dt>Open conflicts</dt><dd>{d.open_conflicts}</dd>
            <dt>Open questions</dt><dd>{d.open_questions}</dd>
            <dt>Decision timer</dt><dd>{d.scheduler_running ? <Badge kind="good">running</Badge> : <Badge kind="muted">not running</Badge>}</dd>
          </dl>
        )}</View>
      </Section>
      <Section title="Host capability matrix">
        <View v={cap}>{(d) => <CapMatrix m={d} />}</View>
      </Section>
    </>
  );
}

function CapMatrix({ m }: { m: Any }) {
  const caps = Object.keys(m.hosts[0].capabilities);
  return (
    <div className="scroll">
      <table className="matrix">
        <thead><tr><th>Capability</th>{m.hosts.map((h: Any) => <th key={h.id}>{h.name}</th>)}</tr></thead>
        <tbody>
          {caps.map((c) => (
            <tr key={c}>
              <th scope="row">{c.replace(/_/g, " ")}</th>
              {m.hosts.map((h: Any) => {
                const v = h.capabilities[c];
                return <td key={h.id} title={v.note}><Badge kind={statusKind(v.status)}>{v.status}</Badge></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">Verified {m.verified} by documentation review. Hover a cell for notes; sources in docs/compatibility.md.</p>
    </div>
  );
}

// ------------------------------------------------------------------ memory
export function Memory() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [v, reload] = useApi<Any>(`/api/memories${query ? `?q=${encodeURIComponent(query)}` : ""}`);
  const [conf, reloadConf] = useApi<Any[]>("/api/conflicts");
  const [text, setText] = useState("");
  const [kind, setKind] = useState("note");
  const [msg, setMsg] = useState("");
  const add = async () => {
    if (!text.trim()) return;
    try { await post("/api/memories", { content: text, kind }); setText(""); setMsg("Saved."); reload(); }
    catch (e: any) { setMsg(e.message); }
  };
  return (
    <>
      <Section title="Memory">
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
          <input aria-label="Search memories" placeholder="Search memories…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button type="submit">Search</button>
        </form>
        <form className="row" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <input aria-label="New memory" placeholder="Add a memory (saved as something you said)" value={text} onChange={(e) => setText(e.target.value)} />
          <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["note", "preference", "decision", "fact", "lesson", "project_state", "task_state"].map((k) => <option key={k}>{k}</option>)}
          </select>
          <button type="submit">Save</button>
        </form>
        {msg && <p className="muted" role="status">{msg}</p>}
        <View v={v} empty={(d) => d.items.length === 0}>{(d) => (
          <>
            {d.explanation && <p className="muted">Ranking: {d.explanation.ranking} · ~{d.used_tokens} tokens · {d.omitted} omitted</p>}
            <table data-testid="memory-table">
              <thead><tr><th>Content</th><th>Kind</th><th>Verification</th><th>Origin</th><th>Updated</th></tr></thead>
              <tbody>{d.items.map((m: Any) => (
                <tr key={m.id} className={sel === m.id ? "sel" : ""} onClick={() => setSel(m.id)}>
                  <td>{m.content}{m.open_conflicts?.length ? <> <Badge kind="bad">conflict</Badge></> : null}</td>
                  <td>{m.kind}</td><td><Badge kind={statusKind(m.verification)}>{m.verification}</Badge></td>
                  <td>{m.origin}</td><td>{fmt(m.updated_at)}</td>
                </tr>))}
              </tbody>
            </table>
          </>
        )}</View>
      </Section>
      {sel && <MemoryDetail id={sel} onChange={() => { reload(); reloadConf(); }} onClose={() => setSel(null)} />}
      <Section title="Conflicts">
        <View v={conf} empty={(d) => d.length === 0}>{(d) => (
          <ul className="list">{d.map((c: Any) => (
            <li key={c.id}>
              <p>{c.reason}</p>
              <div className="row">
                {[c.a, c.b].map((m: Any) => (
                  <button key={m.id} onClick={async () => { await post(`/api/conflicts/${c.id}/resolve`, { keep: m.id, resolution: "chosen in dashboard" }); reload(); reloadConf(); }}>
                    Keep: {m.content}
                  </button>))}
              </div>
            </li>))}
          </ul>
        )}</View>
      </Section>
    </>
  );
}

function MemoryDetail({ id, onChange, onClose }: { id: string; onChange: () => void; onClose: () => void }) {
  const [v, reload] = useApi<Any>(`/api/memories/${id}`);
  const [fix, setFix] = useState("");
  return (
    <Section title="Provenance" actions={<button className="ghost" onClick={onClose}>Close</button>}>
      <View v={v}>{(m) => (
        <div data-testid="memory-detail">
          <dl className="kv">
            <dt>ID</dt><dd><code>{m.id}</code> (rev {m.revision}, schema v{m.schema_version})</dd>
            <dt>Scope</dt><dd>{m.user_scope} / {m.project_scope || "(personal)"} · sharing {m.sharing}</dd>
            <dt>Origin</dt><dd>{m.origin}</dd>
            <dt>Status</dt><dd>{m.status} · <Badge kind={statusKind(m.verification)}>{m.verification}</Badge></dd>
            <dt>Observed / updated</dt><dd>{fmt(m.observed_at)} / {fmt(m.updated_at)}</dd>
            <dt>Expires</dt><dd>{fmt(m.expires_at)}</dd>
            {m.uncertainty && (<><dt>Uncertainty</dt><dd>{m.uncertainty}</dd></>)}
            {m.rationale && (<><dt>Rationale</dt><dd>{m.rationale}</dd></>)}
          </dl>
          <h3>Evidence</h3>
          {m.evidence.length === 0 ? <p className="muted">No linked evidence.</p> : (
            <ul className="list">{m.evidence.map((e: Any, i: number) => (
              <li key={i}><Badge>{e.relation}</Badge> {e.uri || e.source_id || e.message_id} {e.origin_lucid ? <Badge kind="warn">Lucid-generated</Badge> : null}
                {e.quote && <blockquote>{e.quote}</blockquote>}</li>))}
            </ul>)}
          <h3>Revision history</h3>
          <ol>{m.history.map((h: Any) => <li key={h.id}><code>{h.id}</code> {h.status}: {h.content || "(deleted)"}</li>)}</ol>
          {m.status === "active" && (
            <div className="row">
              <input aria-label="Corrected text" placeholder="Corrected text" value={fix} onChange={(e) => setFix(e.target.value)} />
              <button onClick={async () => { if (fix) { await post(`/api/memories/${id}/correct`, { content: fix }); setFix(""); onChange(); onClose(); } }}>Correct</button>
              <button className="danger" onClick={async () => { if (confirm("Delete this memory? Lucid keeps a note that it was deleted, so a later import can't bring it back.")) { await del(`/api/memories/${id}`); onChange(); reload(); } }}>Delete</button>
            </div>)}
        </div>
      )}</View>
    </Section>
  );
}

// ------------------------------------------------------------------ conversations
export function Conversations() {
  const [v] = useApi<Any[]>("/api/conversations");
  const [sel, setSel] = useState<string | null>(null);
  return (
    <>
      <Section title="Conversation archive">
        <View v={v} empty={(d) => d.length === 0}>{(d) => (
          <table><thead><tr><th>Title</th><th>Provider / account</th><th>Messages</th><th>Coverage</th><th>Method</th></tr></thead>
            <tbody>{d.map((c: Any) => (
              <tr key={c.id} onClick={() => setSel(c.id)} className={sel === c.id ? "sel" : ""}>
                <td>{c.title || "(untitled)"}</td><td>{c.provider} / {c.account}</td><td>{c.message_count}</td>
                <td><Badge kind={c.coverage === "full" ? "good" : "warn"}>{c.coverage}</Badge></td><td>{c.import_method}</td>
              </tr>))}</tbody></table>
        )}</View>
      </Section>
      {sel && <ConversationTree id={sel} />}
    </>
  );
}

function ConversationTree({ id }: { id: string }) {
  const [v] = useApi<Any>(`/api/conversations/${id}`);
  return (
    <Section title="Branches">
      <View v={v}>{(c) => {
        const byExt: Record<string, Any> = {};
        c.messages.filter((m: Any) => m.status === "active").forEach((m: Any) => { byExt[m.external_id] = m; });
        const render = (ext: string, depth: number): JSX.Element => {
          const m = byExt[ext];
          const kids: string[] = c.children[ext] || [];
          return (
            <li key={ext}>
              <div className={`msg ${m.role}`}>
                <span className="role">{m.role}</span>
                {/* imported content rendered strictly as text */}
                <span className="content">{m.content}</span>
                {m.origin?.lucid_echo && <Badge kind="warn">Lucid echo</Badge>}
                {c.current_leaf === ext && <Badge kind="info">current</Badge>}
              </div>
              {kids.length > 0 && <ul className={kids.length > 1 ? "branch" : ""}>{kids.map((k) => render(k, depth + 1))}</ul>}
            </li>
          );
        };
        return (
          <div data-testid="conversation-tree">
            <p className="muted">{c.branch_count} branch(es){c.orphans.length ? ` · ${c.orphans.length} orphaned message(s)` : ""} · coverage {c.coverage}</p>
            {c.metadata?.warnings?.length ? <p className="warn-text">{c.metadata.warnings.join("; ")}</p> : null}
            <ul className="tree">{(c.children[""] || []).map((r: string) => render(r, 0))}</ul>
          </div>
        );
      }}</View>
    </Section>
  );
}

// ------------------------------------------------------------------ sync & sharing
export function Sync() {
  const [v] = useApi<Any>("/api/sync");
  return (
    <Section title="Sync queues">
      <View v={v}>{(d) => (
        <>
          {Object.keys(d.destinations).length === 0 ? <p className="state empty">Nothing is sent anywhere. Lucid is running on this computer only.</p> : (
            <table><thead><tr><th>Destination</th><th>Counts</th><th>Oldest pending (lag)</th></tr></thead>
              <tbody>{Object.entries(d.destinations).map(([k, s]: [string, any]) => (
                <tr key={k}><td>{k}</td><td>{Object.entries(s.counts).map(([st, n]) => <Badge key={st} kind={statusKind(st)}>{st}: {String(n)}</Badge>)}</td>
                  <td>{s.oldest_pending_age_s == null ? "—" : `${Math.round(s.oldest_pending_age_s)}s`}</td></tr>))}</tbody></table>)}
          <h3>Recent items</h3>
          {d.items.length === 0 ? <p className="muted">None.</p> : (
            <table><thead><tr><th>Op</th><th>Destination</th><th>Status</th><th>Attempts</th><th>Last error</th></tr></thead>
              <tbody>{d.items.map((i: Any) => <tr key={i.id}><td><code>{i.op_id}</code></td><td>{i.destination}</td>
                <td><Badge kind={statusKind(i.status)}>{i.status}</Badge></td><td>{i.attempts}</td><td>{i.last_error}</td></tr>)}</tbody></table>)}
          <h3>Imports</h3>
          {d.imports.length === 0 ? <p className="muted">No imports yet.</p> : (
            <table><thead><tr><th>Provider</th><th>Status</th><th>Started</th><th>Stats</th></tr></thead>
              <tbody>{d.imports.map((i: Any) => <tr key={i.id}><td>{i.provider}</td><td><Badge kind={statusKind(i.status)}>{i.status}</Badge></td>
                <td>{fmt(i.started_at)}</td><td><code>{i.stats}</code></td></tr>)}</tbody></table>)}
        </>
      )}</View>
    </Section>
  );
}

const CATS = ["preference", "decision", "project_state", "task_state", "lesson", "fact", "correction", "summary", "note"];

export function Sharing() {
  const [v, reload] = useApi<Any>("/api/sharing");
  const [dest, setDest] = useState("");
  const [cats, setCats] = useState<string[]>(["preference", "decision"]);
  const [goal, setGoal] = useState("");
  const [preview, setPreview] = useState<Any | null>(null);
  return (
    <>
      <Section title="Sharing rules">
        <p className="muted">Automatic handoffs only go to destinations listed here, with only these categories.</p>
        <View v={v}>{(d) => d.rules.length === 0 ? <p className="state empty">No sharing rules: nothing is shared automatically.</p> : (
          <table><thead><tr><th>Destination</th><th>Categories</th><th>Scope</th><th></th></tr></thead>
            <tbody>{d.rules.map((r: Any) => <tr key={r.id}><td>{r.destination}</td><td>{r.categories.join(", ")}</td>
              <td>{r.user_scope}/{r.project_scope || "(personal)"}</td>
              <td><button className="ghost" onClick={async () => { await post(`/api/sharing/${r.id}/disable`, {}); reload(); }}>Disable</button></td></tr>)}</tbody></table>)}
        </View>
        <div className="row">
          <input aria-label="Destination" placeholder="Destination (e.g. claude-desktop)" value={dest} onChange={(e) => setDest(e.target.value)} />
          <button onClick={async () => { if (dest) { await post("/api/sharing", { destination: dest, categories: cats }); reload(); } }}>Add rule</button>
        </div>
        <fieldset className="cats"><legend>Categories</legend>{CATS.map((c) => (
          <label key={c}><input type="checkbox" checked={cats.includes(c)} onChange={(e) => setCats(e.target.checked ? [...cats, c] : cats.filter((x) => x !== c))} /> {c}</label>))}
        </fieldset>
      </Section>
      <Section title="Handoff preview">
        <div className="row">
          <input aria-label="Goal" placeholder="Goal / query for the destination" value={goal} onChange={(e) => setGoal(e.target.value)} />
          <button onClick={async () => setPreview(await post("/api/handoff/preview", { destination: dest || "preview", goal, query: goal, categories: cats }))}>Preview</button>
        </div>
        {preview && <pre className="preview" data-testid="handoff-preview">{preview.text}</pre>}
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ tasks, budgets, questions
export function Work() {
  const [t] = useApi<Any[]>("/api/tasks");
  const [b] = useApi<Any[]>("/api/budgets");
  return (
    <>
      <Section title="Tasks">
        <View v={t} empty={(d) => d.length === 0}>{(d) => (
          <table><thead><tr><th>Task</th><th>Status</th><th>Acceptance</th><th>Depends on</th><th>Owns</th></tr></thead>
            <tbody>{d.map((x: Any) => <tr key={x.id}><td>{x.title}</td><td><Badge kind={statusKind(x.status)}>{x.status}</Badge></td>
              <td>{x.acceptance.map((a: Any) => <div key={a.id}><Badge kind={statusKind(a.status === "passed" ? "done" : a.status)}>{a.status}</Badge> {a.description}</div>)}</td>
              <td>{x.depends_on.length}</td><td>{x.owned_paths.join(", ")}</td></tr>)}</tbody></table>
        )}</View>
      </Section>
      <Section title="Budgets">
        <View v={b} empty={(d) => d.length === 0}>{(d) => (<>{d.map((l: Any) => (
          <div key={l.ledger_id} className="ledger" data-testid="ledger">
            <h3>{l.name} <Badge kind={l.mode === "hard" ? "good" : "warn"}>{l.mode}</Badge></h3>
            {l.token_limit != null && <Meter l={l} />}
            <dl className="kv">
              <dt>Used (reconciled)</dt><dd>{l.used_tokens}</dd>
              <dt>In-flight reserved</dt><dd>{l.inflight_reserved_tokens}</dd>
              <dt>Unknown (held)</dt><dd>{l.unknown_held_tokens}</dd>
              <dt>Verification reserve</dt><dd>{l.verification_reserve_tokens}</dd>
              <dt>Available</dt><dd>{l.available_tokens ?? "no token limit"}</dd>
              <dt>Cost</dt><dd>{l.cost_used}{l.cost_limit != null ? ` / ${l.cost_limit}` : ""}</dd>
            </dl>
            <p className="muted">{l.coverage_note}</p>
          </div>))}</>)}</View>
      </Section>
    </>
  );
}

function Meter({ l }: { l: Any }) {
  const pct = (n: number) => `${Math.min(100, (100 * n) / l.token_limit)}%`;
  return (
    <div className="meter" aria-label="token usage">
      <span className="used" style={{ width: pct(l.used_tokens) }} />
      <span className="inflight" style={{ width: pct(l.inflight_reserved_tokens + l.unknown_held_tokens) }} />
      <span className="reserve" style={{ width: pct(l.verification_reserve_tokens) }} />
    </div>
  );
}

export function Questions() {
  const [v, reload] = useApi<Any>("/api/questions");
  const act = async (id: string, action: string, body: Any = {}) => { await post(`/api/questions/${id}/${action}`, body); reload(); };
  return (
    <>
      <Section title="Questions & automatic decisions" actions={<button className="ghost" onClick={reload}>Refresh</button>}>
        <View v={v} empty={(d) => d.questions.length === 0}>{(d) => (
          <ul className="list" data-testid="questions">{d.questions.map((q: Any) => (
            <li key={q.id} className="question">
              <div className="row spread"><strong>{q.decision}</strong><Badge kind={statusKind(q.status)}>{q.status}</Badge></div>
              <p className="muted">Category {q.category} · deadline {fmt(q.deadline)} · {q.on_timeout}</p>
              {q.consequence && <p>{q.consequence}</p>}
              {(q.status === "open" || q.status === "paused") ? (
                <div className="row wrap">
                  {q.options.map((o: string) => <button key={o} onClick={() => act(q.id, "answer", { option: o })}>{o}{o === q.default_option ? " (default)" : ""}</button>)}
                  {q.options.map((o: string) => <button key={"r" + o} className="ghost" onClick={() => act(q.id, "answer", { option: o, remember: true })}>{o} + remember</button>)}
                  <button className="ghost" onClick={() => act(q.id, "extend", { seconds: 300 })}>+5 min</button>
                  {q.default_option && <button className="ghost" onClick={() => act(q.id, "accept-default")}>Accept default</button>}
                  {q.status === "open" ? <button className="ghost" onClick={() => act(q.id, "pause")}>Pause</button>
                    : <button className="ghost" onClick={() => act(q.id, "resume")}>Resume</button>}
                  <button className="danger" onClick={() => act(q.id, "cancel")}>Cancel</button>
                </div>
              ) : <p>Answer: <strong>{q.answer ?? "—"}</strong> by {q.resolved_by} at {fmt(q.resolved_at)}</p>}
              {q.late_responses.length > 0 && <p className="warn-text">Late response(s): {q.late_responses.map((r: Any) => r.answer).join(", ")}</p>}
            </li>))}</ul>
        )}</View>
      </Section>
      <Section title="Standing authorizations">
        <View v={v} empty={(d) => d.authorizations.length === 0}>{(d) => (
          <ul className="list">{d.authorizations.map((a: Any) => <li key={a.id}><code>{a.category}</code> scope {a.scope} {a.expires_at ? `until ${fmt(a.expires_at)}` : ""}</li>)}</ul>
        )}</View>
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ verification, research
export function Verification() {
  const [v] = useApi<Any>("/api/verifications");
  const [e] = useApi<Any[]>("/api/experiments");
  return (
    <>
      <Section title="Verification & correction records">
        <View v={v}>{(d) => (<>
          <p className="muted">Calibration: n={d.calibration.n} · Brier {d.calibration.brier ?? "—"} · ECE {d.calibration.ece ?? "—"}</p>
          {d.verifications.length === 0 ? <p className="state empty">No correction cycles recorded.</p> : (
            <table><thead><tr><th>Subject</th><th>Iterations</th><th>Changed</th><th>Initially correct</th><th>Finally correct</th><th>Stop reason</th></tr></thead>
              <tbody>{d.verifications.map((r: Any) => <tr key={r.id}><td>{r.subject}</td><td>{r.iteration}</td><td>{r.changed ? "yes" : "no"}</td>
                <td>{r.initially_correct == null ? "—" : r.initially_correct ? "yes" : "no"}</td>
                <td>{r.finally_correct == null ? "—" : r.finally_correct ? "yes" : "no"}</td><td>{r.stop_reason}</td></tr>)}</tbody></table>)}
        </>)}</View>
      </Section>
      <Section title="Evaluations & research experiments">
        <View v={e} empty={(d) => d.length === 0}>{(d) => (
          <table><thead><tr><th>Module</th><th>Title</th><th>Outcome</th><th>Hypothesis</th><th>Updated</th></tr></thead>
            <tbody>{d.map((x: Any) => <tr key={x.id}><td>{x.module}</td><td>{x.title}</td><td><Badge kind={statusKind(x.status)}>{x.status}</Badge></td>
              <td>{x.hypothesis}</td><td>{fmt(x.updated_at)}</td></tr>)}</tbody></table>
        )}</View>
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ connectors & data
export function Connectors() {
  const [v, reload] = useApi<Any>("/api/connectors");
  const [site, setSite] = useState("");
  const setSiteState = async (s: Any, patch: Any) => { await post("/api/capture/sites", { ...s, ...patch }); reload(); };
  return (
    <>
      <Section title="Import adapters">
        <View v={v}>{(d) => (
          <table><thead><tr><th>Adapter</th><th>Schema evidence</th><th>Branches</th><th>Native history sync</th></tr></thead>
            <tbody>{d.importers.map((m: Any) => <tr key={m.id}><td>{m.name}</td>
              <td><Badge kind={m.schema_status === "lucid-defined" ? "good" : "warn"}>{m.schema_status}</Badge></td>
              <td>{m.branches}</td><td>{m.native_history_sync ?? "n/a"}</td></tr>)}</tbody></table>)}</View>
      </Section>
      <Section title="Storage backends">
        <View v={v}>{(d) => (
          <table><tbody>{d.storage.map((s: Any) => <tr key={s.id}><td>{s.id}</td><td><Badge kind={s.status === "configured" ? "good" : "muted"}>{s.status}</Badge></td><td>{s.note || s.path}</td></tr>)}</tbody></table>)}</View>
      </Section>
      <Section title="Browser capture sites">
        <p className="muted">You choose what to capture. Lucid captures automatically only on sites you turn on here.</p>
        <View v={v}>{(d) => d.capture_sites.length === 0 ? <p className="state empty">No sites enabled.</p> : (
          <table><thead><tr><th>Site</th><th>Enabled</th><th>Auto capture</th><th>Paused</th></tr></thead>
            <tbody>{d.capture_sites.map((s: Any) => <tr key={s.site}><td>{s.site}</td>
              <td><input type="checkbox" aria-label={`enable ${s.site}`} checked={!!s.enabled} onChange={(e) => setSiteState(s, { enabled: e.target.checked })} /></td>
              <td><input type="checkbox" aria-label={`auto ${s.site}`} checked={!!s.auto_capture} onChange={(e) => setSiteState(s, { auto_capture: e.target.checked })} /></td>
              <td><input type="checkbox" aria-label={`pause ${s.site}`} checked={!!s.paused} onChange={(e) => setSiteState(s, { paused: e.target.checked })} /></td></tr>)}</tbody></table>)}</View>
        <div className="row">
          <input aria-label="Site hostname" placeholder="hostname, e.g. chat.example.com" value={site} onChange={(e) => setSite(e.target.value)} />
          <button onClick={async () => { if (site) { await post("/api/capture/sites", { site, enabled: true }); setSite(""); reload(); } }}>Enable site</button>
        </div>
      </Section>
      <Captures />
      <DataControls />
    </>
  );
}

function Captures() {
  const [v, reload] = useApi<Any[]>("/api/captures");
  return (
    <Section title="Captured pages & selections">
      <View v={v} empty={(d) => d.length === 0}>{(d) => (
        <table><thead><tr><th>Title</th><th>URL</th><th>Captured</th><th></th></tr></thead>
          <tbody>{d.map((c: Any) => <tr key={c.id}><td>{c.title || "(selection)"}</td><td>{c.uri}</td><td>{fmt(c.captured_at)}</td>
            <td><button className="danger" onClick={async () => { await del(`/api/captures/${c.id}`); reload(); }}>Delete</button></td></tr>)}</tbody></table>
      )}</View>
    </Section>
  );
}

function DataControls() {
  const [msg, setMsg] = useState("");
  return (
    <Section title="Backup & export">
      <div className="row">
        <button onClick={async () => { const r: Any = await post("/api/backup", {}); setMsg(`Backup written to ${r.backup}`); }}>Create backup</button>
        <a className="button ghost" href="#" onClick={async (e) => {
          e.preventDefault();
          const data = await api("/api/export");
          const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
          const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "lucid-export.json"; a.click();
        }}>Download export (JSON)</a>
      </div>
      <p className="muted">Restore from the CLI: <code>lucid restore &lt;file&gt;</code> (the current DB is backed up first).</p>
      {msg && <p role="status">{msg}</p>}
    </Section>
  );
}

export function Activity() {
  const [v, reload] = useApi<Any[]>("/api/activity");
  return (
    <Section title="Observed activity" actions={<button className="ghost" onClick={reload}>Refresh</button>}>
      <p className="muted">This shows only what actually happened. Lucid doesn't guess at progress or make up "thoughts".</p>
      <View v={v} empty={(d) => d.length === 0}>{(d) => (
        <table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th></tr></thead>
          <tbody>{d.map((a: Any) => <tr key={a.id}><td>{fmt(a.ts)}</td><td>{a.actor}</td><td>{a.action}</td><td><code>{a.entity_id}</code></td></tr>)}</tbody></table>)}</View>
    </Section>
  );
}
