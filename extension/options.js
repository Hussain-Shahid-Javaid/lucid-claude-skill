const $ = (id) => document.getElementById(id);
chrome.storage.local.get(["api", "token"]).then((c) => { $("api").value = c.api || "http://127.0.0.1:8765"; $("token").value = c.token || ""; });
$("save").addEventListener("click", async () => {
  const api = $("api").value.trim();
  if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(api) && !api.startsWith("https://")) { $("msg").textContent = "Use a localhost URL or an https URL."; return; }
  await chrome.storage.local.set({ api, token: $("token").value.trim() });
  $("msg").textContent = "Saved.";
});
