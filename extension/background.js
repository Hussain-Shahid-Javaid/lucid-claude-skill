// Lucid Capture service worker. Talks only to the local Lucid service.
const DEFAULTS = { api: "http://127.0.0.1:8765", token: "" };

async function cfg() { return { ...DEFAULTS, ...(await chrome.storage.local.get(["api", "token"])) }; }

async function send(body) {
  const { api, token } = await cfg();
  if (!token) throw new Error("Set your Lucid token in the extension options first.");
  const r = await fetch(`${api}/api/capture`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Lucid-Token": token }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`);
  return j;
}

async function runInTab(tabId, func, args = []) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["lib/extract.js", "adapters/index.js"] });
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res.result;
}

async function capture(tab, kind, trigger = "user") {
  let body;
  if (kind === "selection") {
    const text = await runInTab(tab.id, () => LucidExtract.selection(window));
    if (!text) throw new Error("Nothing selected (form fields are never captured).");
    body = { kind, url: tab.url, title: tab.title, text, trigger, extraction: { ok: true } };
  } else if (kind === "page") {
    const p = await runInTab(tab.id, () => LucidExtract.page(document));
    body = { kind, url: tab.url, title: p.title, text: p.text, trigger, extraction: { ok: true } };
  } else {
    const r = await runInTab(tab.id, () => {
      const a = LucidExtract.adapterFor(location.hostname, LucidAdapters);
      return a ? LucidExtract.conversation(document, a) : { extraction: { ok: false, errors: ["no adapter for this site"] } };
    });
    body = { kind: "conversation", url: tab.url, title: tab.title, trigger, ...r };
  }
  const res = await send(body);
  await chrome.action.setBadgeText({ tabId: tab.id, text: "✓" });
  const { history = [] } = await chrome.storage.local.get("history");
  history.unshift({ at: Date.now(), url: tab.url, kind, result: res });
  await chrome.storage.local.set({ history: history.slice(0, 50) });
  return res;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "lucid-selection", title: "Save selection to Lucid", contexts: ["selection"] });
  chrome.contextMenus.create({ id: "lucid-page", title: "Save page to Lucid", contexts: ["page"] });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const kind = info.menuItemId === "lucid-selection" ? "selection" : "page";
  capture(tab, kind).catch(async (e) => {
    await chrome.action.setBadgeText({ tabId: tab.id, text: "ERR" });
    console.warn("Lucid capture failed:", e.message);
  });
});

async function setAuto(host, on) {
  const id = `lucid-auto-${host}`;
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [id] });
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: [id] });
  if (on) {
    await chrome.scripting.registerContentScripts([{ id, matches: [`https://${host}/*`], js: ["lib/extract.js", "adapters/index.js", "autocapture.js"], runAt: "document_idle", persistAcrossSessions: true }]);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "set-auto") {
    setAuto(msg.host, msg.on).then(() => reply({ ok: true }), (e) => reply({ ok: false, error: e.message }));
    return true;
  }
  if (msg.type === "auto-capture" && sender.tab) {
    send(msg.payload).then(
      () => chrome.action.setBadgeText({ tabId: sender.tab.id, text: "AUTO" }),
      (e) => { chrome.action.setBadgeText({ tabId: sender.tab.id, text: "ERR" }); console.warn("auto capture rejected:", e.message); });
    return false;
  }
  if (msg.type === "capture") {
    capture(msg.tab, msg.kind).then((r) => reply({ ok: true, r }), (e) => reply({ ok: false, error: e.message }));
    return true;
  }
  if (msg.type === "api") {
    cfg().then(async ({ api, token }) => {
      try {
        const r = await fetch(`${api}${msg.path}`, { method: msg.method || "GET", headers: { "Content-Type": "application/json", "X-Lucid-Token": token }, body: msg.body ? JSON.stringify(msg.body) : undefined });
        reply({ ok: r.ok, data: await r.json().catch(() => null), status: r.status });
      } catch (e) { reply({ ok: false, error: "Lucid service not reachable" }); }
    });
    return true;
  }
});
