# Security and privacy

## Untrusted content

Transcripts, web pages, memories and connector results are treated as data:
- Retrieval output starts with a "data, not instructions" frame.
- Imported HTML is reduced to text on import (Gemini) or rendered as escaped text in the dashboard, so it is never executed. The Playwright test checks `window.__xss`.
- No tool or endpoint executes content from memory.

## Secrets

- `redact.py` scrubs common secret formats (AWS, GitHub, Anthropic, OpenAI, Google, Slack, JWTs, bearer tokens, private keys, `password=`) from memories, messages, logs, the activity log and API responses. A logging filter applies the same scrubbing.
- Credentials never go into memories, skill files, the frontend bundle or logs:
  - AWS uses the standard credential chain.
  - Google tokens are stored with mode 0600 under `$LUCID_HOME/credentials`.
  - Service tokens are stored hashed.
- The DB file is created with mode 0600.

## Local service

- Binds to `127.0.0.1` by default. Non-loopback binding requires `--allow-remote` plus TLS.
- **Authentication:** a bearer token, compared in constant time against stored hashes. Each token maps to a user, and the user scope always comes from the token, never from the request body.
- **Per-user data:** memories, conflicts, conversations, sharing rules, handoffs, captures and exports are filtered and ownership-checked per user.
- **Admin-only data:** global operational views are restricted to admin users (default: `default`). These are activity, sync queues, tasks, budgets, questions and their actions, verifications, experiments, capture-site settings and backups.
- **Conversation namespacing:** conversations are unique per user, provider, account and external id, so two users capturing "the same" conversation never write into each other's records.
- **Browser-request protection:**
  - A Host allow-list blocks DNS rebinding.
  - Requests with a foreign `Origin` or a cross-site `Sec-Fetch-Site` are rejected.
  - No CORS headers are sent.
  - The CSP is strict (`default-src 'self'`, `frame-ancestors 'none'`), with `X-Frame-Options: DENY` and `nosniff`.
- **Token handling in the dashboard:** the token arrives in the URL fragment, which is never sent to servers. The page keeps it in `sessionStorage` for that tab and strips it from the address bar.

## MCP

- The user is fixed by server configuration. Tools reject unexpected arguments, including any attempt to pass a `user`.
- Projects can be allow-listed.
- Imports are confined to `--import-dir`.
- Deletes need `confirm == id`.
- There is no shell tool.
- A remote MCP deployment would need OAuth 2.1 per the 2026-07-28 spec. Lucid does not ship one.

## Browser extension

Lucid captures only what you choose to give it:
- Nothing runs on pages until you act (activeTab) or explicitly enable automatic capture for a site. Automatic capture needs an optional host permission for that site only.
- Password fields, form values, contenteditable regions, hidden elements, cookies and storage are never read.
- The server re-checks per-site enable/pause/auto state and rejects captures whose adapter reported an extraction failure.

## Sharing

- Nothing is forwarded to another provider by default.
- Automatic handoffs require a sharing rule naming the destination and categories. Previews show exactly what would be shared.
- Cloud mirrors upload only the categories you select.
