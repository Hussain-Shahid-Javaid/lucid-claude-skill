// Pure extraction helpers, injected only into the active tab on user action
// (or into explicitly enabled sites for auto-capture). Never reads form values,
// password fields, hidden elements or cookies/storage.
(function () {
  const EXCLUDE = "input, textarea, select, [type=password], [contenteditable=true], script, style, noscript, template, [aria-hidden=true], [hidden]";

  function visibleText(root) {
    const clone = root.cloneNode(true);
    clone.querySelectorAll(EXCLUDE).forEach((n) => n.remove());
    return (clone.innerText || clone.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
  }

  function selection(win) {
    const sel = win.getSelection();
    if (!sel || sel.rangeCount === 0) return "";
    const node = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    if (node && node.closest && node.closest("input, textarea, [type=password], [contenteditable=true]")) return "";
    return String(sel).slice(0, 200000);
  }

  function page(doc) {
    const main = doc.querySelector("main, article, [role=main]") || doc.body;
    return { title: doc.title, text: visibleText(main).slice(0, 500000) };
  }

  function adapterFor(host, adapters) {
    return (adapters || []).find((a) => a.hosts.includes(host)) || null;
  }

  function conversation(doc, adapter) {
    const errors = [];
    for (const sel of adapter.required) if (!doc.querySelector(sel)) errors.push(`required selector missing: ${sel}`);
    const nodes = errors.length ? [] : Array.from(doc.querySelectorAll(adapter.message));
    const messages = nodes.map((el, i) => ({
      id: (adapter.id_attr && el.getAttribute(adapter.id_attr)) || String(i),
      role: adapter.role(el) || "unknown",
      content: visibleText(el),
    }));
    if (!errors.length && messages.length === 0) errors.push("no messages found");
    if (messages.some((m) => !["user", "assistant", "system", "tool"].includes(m.role))) errors.push("unrecognised role values: layout may have changed");
    if (messages.some((m) => !m.content)) errors.push("empty message bodies: layout may have changed");
    return {
      adapter: `${adapter.id}@${adapter.version}`, adapter_status: adapter.status, coverage: adapter.coverage,
      extraction: { ok: errors.length === 0, errors },
      conversation: { provider: adapter.id, id: adapter.conversation_id(doc) || null, messages: errors.length ? [] : messages },
    };
  }

  globalThis.LucidExtract = { visibleText, selection, page, adapterFor, conversation };
})();
