# Browser extension (Lucid Capture)

## Install (Chromium-based browsers, unpacked)

1. `chrome://extensions` → enable Developer mode → **Load unpacked** → select `extension/`.
2. Open the extension options. Set the service URL (default `http://127.0.0.1:8765`) and the token printed by `lucid serve`.

## Use

- **Right-click → Save selection to Lucid / Save page to Lucid**, or use the popup buttons.
- **Popup → Capture conversation** on a site with an adapter.
- **Per-site controls:** allow capture, automatic capture (asks for host permission for that site only) and pause. The badge shows ✓, AUTO or ERR.
- **Delete captures:** Dashboard → Connectors & data → Captured pages.

## Site adapters (`extension/adapters/index.js`)

Each adapter has an id, a version, a status, hosts, a coverage statement, required selectors and a message selector or role mapping.

| adapter | status | coverage |
|---|---|---|
| `example-chat@1.0.0` | fixture-verified | visible messages; no hidden branches/edits |
| `chatgpt-web@0.1.0` | **unverified** — selectors not validated on live pages | current branch only |
| `claude-web@0.1.0` | **unverified** | current branch only |

**Failure detection.** Each of these makes the capture report `extraction.ok = false`:
- a missing required selector
- no messages found
- unrecognised role values
- empty message bodies

The server then refuses the capture instead of importing corrupted content. Tests in `tests/test_extension.py` run the extraction library against versioned fixtures, including changed-layout fixtures, in Chromium.

**Adding an adapter.** Add an entry plus fixtures, for example `extension/fixtures/<site>.vN.html` and a changed-layout variant, and tests. Bump the version when you change selectors.

## Not supported

- Native desktop applications. They need their own supported interface.
- Sites without an adapter. Only selection and page capture work there.
- Content behind authentication you have not opened yourself.
