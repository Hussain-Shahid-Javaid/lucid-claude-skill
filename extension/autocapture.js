// Registered ONLY for sites where the user enabled automatic capture (and granted host permission).
// Captures the visible conversation once the page settles; the server re-checks that auto-capture
// is enabled and not paused for this site before storing anything.
(function () {
  let last = "";
  const run = () => {
    const a = LucidExtract.adapterFor(location.hostname, LucidAdapters);
    if (!a) return;
    const r = LucidExtract.conversation(document, a);
    const sig = JSON.stringify(r.conversation.messages.map((m) => m.id + m.content.length));
    if (sig === last) return;
    last = sig;
    chrome.runtime.sendMessage({ type: "auto-capture", payload: { kind: "conversation", url: location.href, title: document.title, trigger: "auto", ...r } });
  };
  setTimeout(run, 3000);
  setInterval(run, 30000);
})();
