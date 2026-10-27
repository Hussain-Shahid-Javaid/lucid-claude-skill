const $ = (id) => document.getElementById(id);
const call = (m) => new Promise((r) => chrome.runtime.sendMessage(m, r));

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const host = new URL(tab.url).hostname;
  $("site").textContent = host;
  const sites = await call({ type: "api", path: "/api/capture/sites" });
  if (!sites.ok) { $("status").textContent = sites.error || "Not connected: set API URL and token in options."; return; }
  const s = (sites.data || []).find((x) => x.site === host) || { site: host, enabled: 0, auto_capture: 0, paused: 0 };
  $("enabled").checked = !!s.enabled; $("auto").checked = !!s.auto_capture; $("paused").checked = !!s.paused;
  $("status").textContent = s.enabled ? (s.paused ? "Capture paused on this site" : "Capture allowed on this site") : "Capture not enabled on this site";
  const save = async () => {
    if ($("auto").checked) {
      const ok = await chrome.permissions.request({ origins: [`https://${host}/*`] });
      if (!ok) { $("auto").checked = false; }
    }
    await call({ type: "set-auto", host, on: $("auto").checked && $("enabled").checked && !$("paused").checked });
    await call({ type: "api", method: "POST", path: "/api/capture/sites", body: { site: host, enabled: $("enabled").checked, auto_capture: $("auto").checked, paused: $("paused").checked } });
    $("status").textContent = "Saved site settings.";
  };
  ["enabled", "auto", "paused"].forEach((id) => $(id).addEventListener("change", save));
  for (const [id, kind] of [["sel", "selection"], ["page", "page"], ["conv", "conversation"]]) {
    $(id).addEventListener("click", async () => {
      const r = await call({ type: "capture", tab, kind });
      $("status").textContent = r.ok ? `Saved (${kind}).` : `Not saved: ${r.error}`;
    });
  }
})();
