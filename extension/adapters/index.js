// Site adapters. Each adapter is versioned and tested against fixtures in ../fixtures.
// Real-site adapters are UNVERIFIED: selectors were written from public descriptions, not
// validated against live pages, and sites change. If required selectors are missing,
// extraction reports failure instead of importing partial/corrupted content.
globalThis.LucidAdapters = [
  {
    id: "example-chat", version: "1.0.0", status: "fixture-verified",
    hosts: ["chat.example.com", "127.0.0.1", "localhost"],
    coverage: "visible messages in the open conversation; no hidden branches or edits",
    required: ["[data-lucid-conversation]"],
    message: "[data-role]", role: (el) => el.getAttribute("data-role"),
    id_attr: "data-message-id", conversation_id: (doc) => doc.querySelector("[data-lucid-conversation]")?.getAttribute("data-lucid-conversation"),
  },
  {
    id: "chatgpt-web", version: "0.1.0", status: "unverified",
    hosts: ["chatgpt.com", "chat.openai.com"],
    coverage: "visible messages only (current branch); attachments not captured",
    required: ["[data-message-author-role]"],
    message: "[data-message-author-role]", role: (el) => el.getAttribute("data-message-author-role"),
    id_attr: "data-message-id", conversation_id: (doc) => (doc.location.pathname.match(/\/c\/([\w-]+)/) || [])[1],
  },
  {
    id: "claude-web", version: "0.1.0", status: "unverified",
    hosts: ["claude.ai"],
    coverage: "visible messages only (current branch)",
    required: ["[data-testid='user-message']"],
    message: "[data-testid='user-message'], .font-claude-message",
    role: (el) => (el.matches("[data-testid='user-message']") ? "user" : "assistant"),
    id_attr: null, conversation_id: (doc) => (doc.location.pathname.match(/\/chat\/([\w-]+)/) || [])[1],
  },
];
